"""边缘服务：FastAPI + WebSocket 广播 + SQLite 入库 + 事件状态机 + 告警派生。

数据入口三选一，走完全相同的下游链路（架构 §3.1）：
  POST /ingest/sample  逐秒活动强度样本 → 状态机判定事件（模拟轨/硬件轨共用）
  POST /ingest/event   直接注入成型事件（演示注入）
下游：事件入库 → guardian 派生告警 → WebSocket 广播给所有子女端。
"""
from __future__ import annotations

import asyncio
import json
import random
import subprocess
import sys
import time
from collections import deque
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import config as C
from . import guardian
from . import medical
from .db import open_store
from .protocol import (EVENT_TYPES, Event, EVT_FALL_BREATHING_BAD, EVT_FALL_RECOVERED,
                        EVT_VOICE_REQUERY, SRC_DEMO_INJECT, SRC_CSI_LIVE, ZONE_RED)
from .state_machine import SampleProcessor
from .voice import VoiceConfirmSession

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
H5_DIST = Path(__file__).resolve().parent.parent / "h5" / "dist"

app = FastAPI(title="护院鹅 Edge")
store = open_store(C.DB_PATH)
processor = SampleProcessor(zone="living")
voice_session = VoiceConfirmSession(elder_name="奶奶", timeout_s=90)


def _apply_profile_to_processor() -> None:
    """把档案病史修正系数注入状态机 + 同步语音会话节奏（启动/存档/重置时调用）。"""
    profile = medical.load_profile(store._conn)
    processor.apply_profile(profile)
    adj = processor.adjustments
    voice_session.timeout_s = adj["voice_timeout"]


_apply_profile_to_processor()


class ConnectionManager:
    """WebSocket 广播（FastAPI 官方 ConnectionManager 模式）。"""

    def __init__(self) -> None:
        self.active: list[WebSocket] = []

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self.active.append(ws)

    def disconnect(self, ws: WebSocket) -> None:
        if ws in self.active:
            self.active.remove(ws)

    async def broadcast(self, payload: dict) -> None:
        dead = []
        for ws in self.active:
            try:
                await ws.send_text(json.dumps(payload, ensure_ascii=False))
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)


manager = ConnectionManager()

# ---- 程序工作台日志：环形缓冲只存内存，供子女端事件库展示与导出 ----
_sys_logs: deque[dict] = deque(maxlen=200)

# ---- 桥接器心跳：桥接器每 3 秒上报自身阶段态，超过 8 秒无心跳视为未运行 ----
_bridge_state: dict = {"phase": "", "port": "", "detail": "", "fps": 0.0,
                       "rssi": 0, "preflight": False, "ts": 0.0}
BRIDGE_STALE_S = 8.0
BRIDGE_PHASE_LABEL = {
    "waiting_hardware": "等待硬件插入",
    "preflight": "开机自检中",
    "preflight_passed": "自检通过",
    "preflight_failed": "自检未通过",
    "streaming": "正式监护推流中",
    "reconnecting": "串口重连中",
}


def sys_log(level: str, text: str) -> None:
    """记一条工作台日志（info/warn/danger）。"""
    _sys_logs.append({
        "ts": datetime.now().astimezone().isoformat(timespec="seconds"),
        "level": level,
        "text": text,
    })


sys_log("info", "边缘守护服务已启动 · SQLite/状态机/语音确证就绪")

# 设备数据新鲜度跟踪（供子女端连接状态判断）
_last_sample_wall: float | None = None
DEVI_WEAK_S = 10.0     # 超过10秒无样本 → 信号不佳
DEVICE_OFFLINE_S = 30.0  # 超过30秒无样本 → 已断开


class SampleIn(BaseModel):
    ts: str
    sim_t: float
    intensity: float
    zone: str | None = None
    breathing_rate: int = 0           # 呼吸频率（次/分），0=未检测
    breathing_state: str = ""          # normal/elevated/irregular/shallow/lost
    noise_floor: float = 0.0           # 环境底噪估计（桥接器动态估计，空调噪声补偿）


async def _handle_event(ev: Event) -> None:
    """事件统一处理：入库 → 派生告警 → 广播。"""
    store.insert_event(ev)
    await manager.broadcast({"kind": "event", "data": ev.to_dict()})
    # 双证据线成立（跌倒+呼吸异常）→ 现场语音询问（确证链第一轮）；
    # 心梗等 skip_voice 档案直接跳过，给老人取消机会仅适用于非高危档案
    if ev.type == EVT_FALL_BREATHING_BAD and not processor.adjustments["skip_voice"] \
            and processor._voice_round == 0 and ev.guard_zone == ZONE_RED:
        processor._voice_round = 1
        processor._zone_timer = 0
        voice_data = {**voice_session.start(), "round": 1}
        await manager.broadcast({"kind": "voice_confirm", "data": voice_data})
    # 第一轮无回应 → 第二轮询问：重播语音 + 生成「确证中」告警（ack_required）
    elif ev.type == EVT_VOICE_REQUERY:
        voice_session.timeout_s = processor._requery_wait_s
        voice_data = {**voice_session.start(), "round": 2}
        await manager.broadcast({"kind": "voice_confirm", "data": voice_data})
        requery_alert = {
            "alert_id": f"alt_{ev.event_id[-8:]}",
            "event_id": ev.event_id,
            "created_at": ev.ts,
            "level": "red",
            "state": "voice_checking",
            "agent_reason": "第一轮语音询问无回应，已提升告警级别并再次询问，"
                            f"等待 {processor._requery_wait_s} 秒，请确认老人情况",
            "agent_source": "rule",
            "alert_tag": "确证中",
            "suspected_cause": "疑似跌倒 · 二轮确证中",
            "elder_name": medical.load_profile(store._conn).elder_name,
            "ack_required": True,
            "closed_at": None,
            "closed_by": None,
        }
        store.insert_alert(requery_alert)
        sys_log("warn", "第一轮语音无回应 · 第二轮询问已发起，报警通知家人")
        await manager.broadcast({"kind": "alert", "data": requery_alert})
    # 运动恢复自解除：确证链进行中运动恢复均速且气息平稳
    elif ev.type == EVT_FALL_RECOVERED:
        processor._voice_round = 0
        voice_session.reset()
        sys_log("info", "运动恢复均速且气息平稳 · 告警已自动解除，事件库留痕")
        await manager.broadcast({"kind": "alert_cleared",
                                 "data": {"reason": "运动恢复均速且气息平稳，自动解除"}})
    # 加载病历，实现个性化告警
    profile = medical.load_profile(store._conn)
    alert = guardian.decide(ev.to_dict(), profile)
    if alert:
        store.insert_alert(alert)
        sys_log("danger", f"告警已生成 · {alert.get('suspected_cause') or alert.get('alert_tag') or alert.get('state', '')}")
        await manager.broadcast({"kind": "alert", "data": alert})


@app.post("/ingest/sample")
async def ingest_sample(s: SampleIn):
    global _last_sample_wall
    # 真实接入闸门：演示播放在进程内直注状态机，不走本接口；
    # 未开真实接入时拒收外部样本，防止两股数据流混入同一状态机
    if not _mode_state["real_enabled"]:
        return JSONResponse(
            status_code=403,
            content={"ok": False,
                     "message": "真实接入未开启，样本已拒收。请先开启真实接入开关（POST /api/source-mode/real）"})
    _last_sample_wall = time.time()
    events = processor.process(s.ts, s.sim_t, s.intensity, s.zone,
                               s.breathing_rate, s.breathing_state)
    for ev in events:
        await _handle_event(ev)
    # 广播实时活动强度+呼吸状态+语义三态，供子女端画曲线与状态条
    await manager.broadcast({
        "kind": "sample",
        "data": {"ts": s.ts, "sim_t": s.sim_t, "intensity": s.intensity,
                 "zone": s.zone or processor.zone, "present": processor.present,
                 "breathing_rate": s.breathing_rate,
                 "breathing_state": s.breathing_state or "normal",
                 "noise_floor": s.noise_floor,
                 "guard_zone": processor.guard_zone,
                 "semantic_state": processor.semantic_state,
                 "breathing_band_max": processor.breathing_band_max},
    })
    return {"events": [e.type for e in events]}


class IngestEventIn(BaseModel):
    """演示事件注入的入参校验：类型白名单 + 字段约束，拒绝任意 dict。"""

    type: str
    device_id: str = "wg-node-01"
    zone: str = "living"
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    duration_s: int = Field(default=0, ge=0)
    features: dict = Field(default_factory=dict)
    capability_level: str = "L1"
    source: str = SRC_DEMO_INJECT
    ts: str = ""
    breathing_rate: int = Field(default=0, ge=0, le=60)
    breathing_state: str = ""
    guard_zone: int = Field(default=0, ge=-1, le=4)


@app.post("/ingest/event")
async def ingest_event(ev: IngestEventIn):
    if ev.type not in EVENT_TYPES:
        raise HTTPException(status_code=400, detail=f"未知事件类型：{ev.type}")
    event = Event(**ev.model_dump())
    await _handle_event(event)
    return {"ok": True, "event_id": event.event_id}


@app.post("/api/reset")
async def api_reset():
    """系统重置：清事件库 + 状态机归零 + 停演示并清零数据源开关。"""
    global processor
    await _stop_demo()
    _mode_state["real_enabled"] = False
    store.reset()
    processor = SampleProcessor(zone="living")
    _apply_profile_to_processor()
    await manager.broadcast({"kind": "reset", "data": {}})
    await manager.broadcast({"kind": "source_mode", "data": dict(_mode_state)})
    sys_log("warn", "系统已重置 · 事件库清空，状态机与数据源开关归零")
    return {"ok": True}


# ---- 数据源模式：真实接入 > 演示场景 > 待机（无数据流）----
SCEN_DIR = Path(__file__).resolve().parent.parent / "replay" / "scenarios"

# 演示场景层级：一级=无人/有人；二级（有人之下）=正常（休憩/移动）/异常（疑似跌倒几种状态）
DEMO_SCENARIOS = [
    # 一级：无人
    {"scenario": "demo_absent", "label": "无人",
     "desc": "全程无活动、呼吸测不到，系统不断言不告警（演示对比用）"},
    # 二级：有人 · 正常
    {"scenario": "demo_rest", "label": "有人 · 正常 · 休憩中",
     "desc": "无活动、气息均匀，系统判定正常·休憩中·呼吸平稳"},
    {"scenario": "demo_active", "label": "有人 · 正常 · 移动中",
     "desc": "匀速活动、气息均匀，系统判定正常·移动中"},
    # 二级：有人 · 异常（疑似跌倒的几种状态）
    {"scenario": "demo_fall_moving", "label": "有人 · 异常 · 疑似跌倒（移动中）",
     "desc": "均速移动中突然剧烈运动+气息紊乱，双证据成立告警进入同一确证链；本场景现场无应答，演示升级危机"},
    {"scenario": "demo_fall_still", "label": "有人 · 异常 · 疑似跌倒（静止中）",
     "desc": "静止中突然剧烈运动+气息紊乱，双证据成立告警进入同一确证链；本场景现场起身恢复，演示自动解除"},
]

_mode_state = {"real_enabled": False, "demo_enabled": False, "demo_scenario": ""}
_demo_task: asyncio.Task | None = None


def _demo_breathing(seg: dict) -> tuple[int, str]:
    """按剧本段落生成呼吸样本（与 replay/player.py 同规则）。"""
    rate = random.randint(seg.get("br_low", 14), seg.get("br_high", 18))
    explicit = seg.get("br_state", "")
    if explicit:
        return rate, explicit
    if rate < 3:
        return rate, "lost"
    if rate < 8:
        return rate, "shallow"
    if rate < 12:
        return rate, "irregular"
    if rate > 20:
        return rate, "elevated"
    return rate, "normal"


async def _run_demo(name: str) -> None:
    """进程内场景播放：剧本展开为逐秒样本直接注入状态机，
    与真实硬件数据走完全相同的判定链路（入库→告警派生→广播）。
    剧本播完自动从头循环：演示接入常驻不掉线，直到手动关闭开关；
    循环回卷时状态机归零，告警类剧本不会无限叠加升级。"""
    global processor, _last_sample_wall
    scen = json.loads((SCEN_DIR / f"{name}.json").read_text(encoding="utf-8"))
    speed = float(scen.get("speed", 10))
    zone = scen.get("zone", "living")
    await manager.broadcast({"kind": "demo", "data": {"running": True, "scenario": name}})
    try:
        while True:
            # 状态机归零：每轮剧本从干净状态开场（事件历史保留）
            processor = SampleProcessor(zone="living")
            _apply_profile_to_processor()
            await manager.broadcast({"kind": "demo_state_cleared", "data": {}})
            sim_t = 0.0
            for seg in scen["segments"]:
                for _ in range(int(seg["duration_s"])):
                    if not _mode_state["demo_enabled"] or _mode_state["real_enabled"]:
                        return  # 开关关闭或被真实接入打断
                    ts = datetime.now().astimezone().isoformat(timespec="seconds")
                    intensity = round(random.uniform(seg["low"], seg["high"]), 3)
                    br_rate, br_st = _demo_breathing(seg)
                    _last_sample_wall = time.time()
                    events = processor.process(ts, sim_t, intensity, zone, br_rate, br_st)
                    for ev in events:
                        await _handle_event(ev)
                    await manager.broadcast({
                        "kind": "sample",
                        "data": {"ts": ts, "sim_t": sim_t, "intensity": intensity,
                                 "zone": zone, "present": processor.present,
                                 "breathing_rate": br_rate, "breathing_state": br_st,
                                 "guard_zone": processor.guard_zone,
                                 "semantic_state": processor.semantic_state,
                                 "breathing_band_max": processor.breathing_band_max},
                    })
                    sim_t += 1
                    await asyncio.sleep(1.0 / speed)
            sys_log("info", f"演示剧本「{name}」播完一轮 · 从头循环（演示接入保持接通）")
    except asyncio.CancelledError:
        return
    finally:
        # 只有手动关闭/被真实接入打断才落到这里：同步开关状态给前端
        if _mode_state["demo_scenario"] == name:
            _mode_state["demo_enabled"] = False
            _mode_state["demo_scenario"] = ""
            await manager.broadcast({"kind": "demo", "data": {"running": False, "scenario": ""}})
            await manager.broadcast({"kind": "source_mode", "data": dict(_mode_state)})
            sys_log("info", f"演示剧本「{name}」已停止 · 演示接入已关闭")


async def _stop_demo() -> None:
    """停止当前演示任务。"""
    global _demo_task
    _mode_state["demo_enabled"] = False
    _mode_state["demo_scenario"] = ""
    if _demo_task and not _demo_task.done():
        _demo_task.cancel()
        try:
            await _demo_task
        except (asyncio.CancelledError, Exception):
            pass
    _demo_task = None


class SourceModeDemoIn(BaseModel):
    enabled: bool
    scenario: str = ""


class SourceModeRealIn(BaseModel):
    enabled: bool


@app.get("/api/source-mode")
async def api_get_source_mode():
    """数据源模式状态 + 可选演示场景清单。"""
    return {**_mode_state, "scenarios": DEMO_SCENARIOS}


@app.post("/api/source-mode/demo")
async def api_set_demo(body: SourceModeDemoIn):
    """演示开关：开启后可切换代表性场景，系统全链路联动判定。"""
    global _demo_task, processor
    if body.enabled:
        if _mode_state["real_enabled"]:
            return {"ok": False, **_mode_state,
                    "message": "真实接入已开启（优先级最高），请先关闭真实接入再启用演示"}
        if body.scenario not in [s["scenario"] for s in DEMO_SCENARIOS]:
            raise HTTPException(status_code=400, detail=f"未知演示场景：{body.scenario}")
        await _stop_demo()
        # 状态机归零，让每个场景从干净状态开场（事件历史保留）
        processor = SampleProcessor(zone="living")
        _apply_profile_to_processor()
        # 通知前端清三态/告警卡/曲线残留（不清事件库）
        await manager.broadcast({"kind": "demo_state_cleared", "data": {}})
        _mode_state["demo_enabled"] = True
        _mode_state["demo_scenario"] = body.scenario
        _demo_task = asyncio.create_task(_run_demo(body.scenario))
        label = next((s["label"] for s in DEMO_SCENARIOS if s["scenario"] == body.scenario), body.scenario)
        sys_log("info", f"演示接入已开启 · 场景「{label}」数据进入判定链路")
        msg = "演示已开启，场景数据已接入判定链路"
    else:
        await _stop_demo()
        sys_log("info", "演示接入已关闭")
        msg = "演示已关闭"
    await manager.broadcast({"kind": "source_mode", "data": dict(_mode_state)})
    return {"ok": True, **_mode_state, "message": msg}


@app.post("/api/source-mode/real")
async def api_set_real(body: SourceModeRealIn):
    """真实接入开关（优先级最高）：开启即自动关闭演示，
    探测器上报的 /ingest/sample 信号直接联动底层判定逻辑。
    开启时状态机切换到硬件轨（source=csi_live），事件标记真实来源。"""
    global processor, _last_sample_wall
    _mode_state["real_enabled"] = body.enabled
    if body.enabled and _mode_state["demo_enabled"]:
        await _stop_demo()
        msg = "真实接入已开启：演示已自动关闭，探测器信号直接联动底层逻辑"
        sys_log("info", "真实接入已开启 · 演示已自动关闭")
    elif body.enabled:
        msg = "真实接入已开启，正在自动体检信号链路…"
        sys_log("info", "真实接入已开启 · 开阀自动体检启动")
    else:
        msg = "真实接入已关闭"
        sys_log("info", "真实接入已关闭")
    if body.enabled:
        # 状态机归零并切换硬件轨，让真实数据从干净状态开场
        processor = SampleProcessor(zone="living", source=SRC_CSI_LIVE)
        _apply_profile_to_processor()
        # 归零样本新鲜度：真实接入按实际反馈，不吃演示残留的“假良好”，
        # 无硬件时从开启那刻就如实进入检测中/断开，等真实 /ingest/sample 到达才转接通
        _last_sample_wall = None
    await manager.broadcast({"kind": "source_mode", "data": dict(_mode_state)})
    return {"ok": True, **_mode_state, "message": msg}


@app.get("/api/events")
async def api_events(limit: int = 50):
    return JSONResponse(store.recent_events(limit))


@app.get("/api/system-logs")
async def api_system_logs():
    """程序工作台日志：最新在前。"""
    return JSONResponse(list(_sys_logs)[::-1])


@app.get("/api/status")
async def api_status():
    return {"present": processor.present, "zone": processor.zone,
            "guard_zone": processor.guard_zone,
            "semantic_state": processor.semantic_state,
            "breathing_band_max": processor.breathing_band_max,
            "adjustments": processor.adjustments}


# ---- 设备连接状态 API ----
def _device_freshness() -> tuple[str, float]:
    """返回 (连接状态, 距上次样本秒数)。
    演示接入时假设探测器信号良好：剧本以倍速批次注入，
    真实墙钟间隔不能反映虚拟数据流的健康度，直接视为接通。"""
    if _mode_state["demo_enabled"]:
        lag = 0.0 if _last_sample_wall is None else time.time() - _last_sample_wall
        return "connected", lag
    if _last_sample_wall is None:
        return "disconnected", -1.0
    lag = time.time() - _last_sample_wall
    if lag <= DEVI_WEAK_S:
        return "connected", lag
    if lag <= DEVICE_OFFLINE_S:
        return "weak", lag
    return "disconnected", lag


@app.get("/api/device/status")
async def api_device_status():
    """设备连接三态：connected=接通 / weak=信号不佳 / disconnected=已断开。"""
    state, lag = _device_freshness()
    return {
        "state": state,
        "last_sample_lag_s": round(lag, 1),
        "last_sample_ts": datetime.now().astimezone().isoformat(timespec="seconds"),
        "device_id": processor.device_id,
        "edge_online": True,
    }


class BridgeHeartbeatIn(BaseModel):
    phase: str = ""
    port: str = ""
    detail: str = ""
    fps: float = 0.0
    rssi: int = 0
    preflight: bool = False


@app.post("/api/bridge/heartbeat")
async def api_bridge_heartbeat(hb: BridgeHeartbeatIn):
    """桥接器心跳：记录阶段态，供信号接通测试区分“桥接器没跑”与“板子没插”。"""
    _bridge_state.update({**hb.model_dump(), "ts": time.time()})
    return {"ok": True}


def _bridge_alive() -> tuple[bool, float]:
    """桥接器是否在运行：心跳新鲜度判定。"""
    ts = _bridge_state.get("ts") or 0.0
    if ts <= 0:
        return False, -1.0
    lag = time.time() - ts
    return lag <= BRIDGE_STALE_S, lag


@app.get("/api/bridge/state")
async def api_bridge_state():
    """桥接器运行态：供启动器判断是否需要拉起桥接器。"""
    alive, lag = _bridge_alive()
    return {"alive": alive, "lag_s": round(lag, 1), **{
        k: _bridge_state.get(k) for k in ("phase", "port", "detail", "fps", "rssi")}}


def _spawn_bridge() -> None:
    """后端同环境拉起信号桥接器（水泵）：与启动器同样的方式，
    DETACHED 隐藏窗口，日志写 bridge.log。避开 WindowsApps 商店空壳别名。"""
    root = Path(__file__).resolve().parent.parent
    exe_dir = Path(sys.executable).resolve().parent
    py = exe_dir / "python.exe"
    if not (py.exists() and "WindowsApps" not in str(py)):
        py = Path(sys.executable) if "WindowsApps" not in sys.executable else None
    if py is None:
        sys_log("warn", "自动拉起桥接器失败：未找到可用的 Python 解释器")
        return
    log_file = open(root / "bridge.log", "a", encoding="utf-8")
    subprocess.Popen(
        [str(py), "-m", "hw.bridge", "--backend", "http://127.0.0.1:8000"],
        cwd=str(root), stdout=log_file, stderr=log_file,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)
        | getattr(subprocess, "DETACHED_PROCESS", 0),
    )
    sys_log("info", "信号桥接器已自动拉起，等待其上线")


@app.get("/api/device/auto-check")
async def api_device_auto_check():
    """开阀自动体检：开真实接入后前端调用。
    先等桥接器心跳（不在则自动拉起），再等真实样本到达（含自检期），
    然后跑全链路体检。结论里给出具体断在哪一环，供子女端三态显示。"""
    global _last_sample_wall
    seen = _last_sample_wall
    # 第一环：桥接器心跳（最多等 12 秒：含自动拉起 + 串口发现 + 自检首段）
    spawned = False
    for _ in range(24):
        alive, _lag = _bridge_alive()
        if alive:
            break
        if not spawned:   # 判死即拉一次：旧心跳残留的 ts 不影响拉起决策
            _spawn_bridge()
            spawned = True
        await asyncio.sleep(0.5)
    bridge_ok, _ = _bridge_alive()
    phase = _bridge_state.get("phase", "")
    # 第二环：真实样本到达（新样本才算，不吃开启前残留；自检期不推流，给足 20 秒）
    deadline = time.time() + 20
    while time.time() < deadline:
        if _last_sample_wall is not None and _last_sample_wall != seen:
            break
        await asyncio.sleep(0.5)
    result = await api_connection_test()
    failed_at = "" if result["ok"] else next(
        (i["name"] for i in result["items"] if not i["ok"]), "")
    if result["ok"]:
        verdict = "信号已接通，水流稳定，进入正式监护"
    elif bridge_ok and phase == "preflight":
        verdict = "开机自检中，通过后自动开始监护，请稍候"
    elif not bridge_ok:
        verdict = "断在信号桥接器：中转进程未运行"
    elif failed_at == "接收板":
        verdict = "断在接收板：板子没插或 TX 未供电（TX 掉电先充电）"
    else:
        verdict = f"断在{failed_at}：请按体检明细处理后重试"
    sys_log("info", f"开阀自动体检：{'通过' if result['ok'] else '未通过'} · {verdict}")
    return {**result, "verdict": verdict, "failed_at": failed_at, "phase": phase}


@app.get("/api/device/connection-test")
async def api_connection_test():
    """信号接通测试：逐环体检“板子 → 桥接器 → 后端 → 判定”全链路，
    每项给结论与修复建议，供子女端一键检测。
    演示接入期间配合演出：不查真实硬件，按剧本假设信号良好全绿。"""
    if _mode_state["demo_enabled"]:
        items = [
            {"name": "边缘网关", "ok": True, "detail": "在线，判定服务正常"},
            {"name": "信号桥接器", "ok": True, "detail": "演示接入 · 虚拟数据流按剧本稳定上报"},
            {"name": "接收板", "ok": True, "detail": "演示信号 · 假设探测器信号良好"},
            {"name": "链路质量", "ok": True, "detail": "演示模式 · 信号质量按剧本常驻良好"},
        ]
        return {"ok": True, "verdict": "演示接入：信号链路按剧本常驻良好",
                "items": items,
                "tested_at": datetime.now().astimezone().isoformat(timespec="seconds")}
    state, lag = _device_freshness()
    bridge_ok, bridge_lag = _bridge_alive()
    phase = _bridge_state.get("phase", "")
    items: list[dict] = []

    items.append({"name": "边缘网关", "ok": True,
                  "detail": "在线，判定服务正常"})

    if bridge_ok:
        label = BRIDGE_PHASE_LABEL.get(phase, phase or "运行中")
        items.append({"name": "信号桥接器", "ok": phase != "preflight_failed",
                      "detail": f"{label}" + (f" · {_bridge_state['port']}" if _bridge_state.get("port") else "")
                      + (f" · {_bridge_state.get('detail')}" if _bridge_state.get("detail") else "")})
    else:
        items.append({"name": "信号桥接器", "ok": False,
                      "detail": "未运行——板子的信号需要桥接器中转，请双击桌面“护院鹅子女端”重新拉起"})

    if bridge_ok and phase == "waiting_hardware":
        items.append({"name": "接收板", "ok": False,
                      "detail": "未插入/未识别——把 RX 板 USB 插上电脑，桥接器会自动发现"})
    elif state == "connected":
        items.append({"name": "接收板", "ok": True,
                      "detail": f"数据实时到达（延迟 {lag:.1f} 秒）"})
    elif bridge_ok and phase == "preflight":
        items.append({"name": "接收板", "ok": True,
                      "detail": "开机自检中，自检通过前不推流属正常"})
    else:
        items.append({"name": "接收板", "ok": False,
                      "detail": "超过30秒未收到数据——检查两块板供电，TX 板可能需充电"})

    rssi = _bridge_state.get("rssi") or 0
    if bridge_ok and rssi:
        items.append({"name": "链路质量", "ok": rssi >= -60,
                      "detail": f"RSSI {rssi}dBm" + ("（健康）" if rssi >= -48 else
                                  "（偏弱：给 TX 板充电或拉近两板距离）" if rssi >= -60 else "（过低）")})

    all_ok = all(i["ok"] for i in items)
    verdict = ("信号链路接通，监护正常运行" if all_ok
               else "发现问题，请按各项提示处理后重新检测")
    return {"ok": all_ok, "verdict": verdict, "items": items,
            "tested_at": datetime.now().astimezone().isoformat(timespec="seconds")}


@app.get("/api/device/diagnosis")
async def api_device_diagnosis():
    """信号不佳时的诊断详情：供子女端排查面板展示。"""
    state, lag = _device_freshness()
    tips: list[str] = []
    if _mode_state["demo_enabled"]:
        tips = ["演示接入：假设探测器信号良好，虚拟数据流按剧本稳定上报"]
    elif state == "disconnected":
        bridge_ok, _bl = _bridge_alive()
        if not bridge_ok:
            tips = [
                "超过30秒未收到探测器数据，判定为断开",
                "信号桥接器未在运行：双击桌面“护院鹅子女端”重新拉起（板子的信号靠它中转）",
            ]
        elif _bridge_state.get("phase") == "waiting_hardware":
            tips = [
                "桥接器在运行，但未发现接收板",
                "把 RX 接收板的 USB 插上电脑，桥接器会自动识别并开始自检",
            ]
        else:
            tips = [
                "超过30秒未收到探测器数据，判定为断开",
                "检查发射端(TX)与接收端(RX)是否通电、指示灯是否正常",
                "TX 发射板电池掉电是常见原因，先充电再试",
                "路由器断电/重启会导致数据中断，请检查家中网络",
            ]
    elif state == "weak":
        tips = [
            f"数据延迟约{int(lag)}秒，正常应每秒上报一次",
            "发射端或接收端可能有一方信号不稳，检查是否被遮挡",
            "将探测器远离微波炉、冰箱等大功率电器",
            "缩短收发两端距离或调整天线朝向后重新检测",
        ]
    else:
        tips = ["信号良好，数据实时上报中"]
    return {
        "state": state,
        "last_sample_lag_s": round(lag, 1),
        "sample_interval_expected_s": C.SAMPLE_INTERVAL_S,
        "device_id": processor.device_id,
        "edge_online": True,
        "tips": tips,
    }


# ---- 病历管理 API ----
class ProfileIn(BaseModel):
    # 字段名和前端对齐
    name: str = "妈妈"
    age: int = 75
    weight_kg: float = 0        # 体重（0=未填写）
    relationship: str = ""      # 监护人与老人的关系
    health_status: str = ""     # 身体状态标准分类
    diseases: list[str] = []
    medications: list[str] = []
    fall_count: int = 0
    syncope_count: int = 0
    family_sudden_cardiac_death: bool = False
    wake_time: str = "06:30"
    sleep_time: str = "21:30"
    address: str = ""           # 老人居住地址（报警120时同步）
    emergency_phones: list[str] = []  # 紧急联系电话（最多3个，逐个降级拨打）


@app.get("/api/profile")
async def api_get_profile():
    """获取老人健康档案。"""
    profile = medical.load_profile(store._conn)
    d = profile.to_dict()
    # 转换字段名和前端对齐
    return {
        "name": profile.elder_name,
        "age": profile.age,
        "weight_kg": profile.weight_kg,
        "relationship": profile.relationship,
        "health_status": profile.health_status,
        "diseases": profile.conditions,
        "medications": profile.medications,
        "fall_count": profile.fall_history,
        "syncope_count": profile.syncope_history,
        "family_sudden_cardiac_death": profile.family_sudden_death,
        "wake_time": profile.wake_time,
        "sleep_time": profile.bed_time,
        "address": profile.address,
        "emergency_phones": profile.emergency_phones,
        "is_multi_medication": profile.is_multi_medication,
        "is_high_risk": profile.is_high_risk,
        "voice_timeout": profile.voice_timeout,
    }


@app.post("/api/profile")
async def api_save_profile(p: ProfileIn):
    """保存老人健康档案。"""
    profile = medical.MedicalProfile(
        elder_name=p.name, age=p.age, weight_kg=p.weight_kg,
        relationship=p.relationship, health_status=p.health_status,
        conditions=p.diseases, medications=p.medications,
        fall_history=p.fall_count, syncope_history=p.syncope_count,
        family_sudden_death=p.family_sudden_cardiac_death,
        wake_time=p.wake_time, bed_time=p.sleep_time,
        address=p.address, emergency_phones=[x for x in p.emergency_phones if x][:3],
    )
    medical.save_profile(store._conn, profile)
    # 档案存档即重新注入修正系数（千人千档）：语音超时/跳过语音/zone3升级一并接管
    _apply_profile_to_processor()
    await manager.broadcast({"kind": "profile_updated", "data": profile.to_dict()})
    await manager.broadcast({"kind": "profile_adjustments", "data": processor.adjustments})
    return {"ok": True, "profile": {
        "name": profile.elder_name,
        "diseases": profile.conditions,
        "is_high_risk": profile.is_high_risk,
        "voice_timeout": profile.voice_timeout,
    }}


@app.post("/api/profile/clear")
async def api_clear_profile():
    """注销档案：档案内容全部清零（病历归零 + 自定义疾病注册表清空），
    事件库不动（清事件请用系统重置）。"""
    store._conn.execute("DELETE FROM medical_profile;")
    store._conn.commit()
    medical.clear_custom_diseases(conn=store._conn)
    # 清零后立即按空档案重注入修正系数，恢复默认守护参数
    _apply_profile_to_processor()
    profile = medical.load_profile(store._conn)
    await manager.broadcast({"kind": "profile_updated", "data": profile.to_dict()})
    await manager.broadcast({"kind": "profile_adjustments", "data": processor.adjustments})
    sys_log("warn", "档案已注销 · 病历与自定义疾病注册表全部清零")
    return {"ok": True}


# ---- 开放性病史（AI 医学词条，子女确认后走 POST /api/diseases 入档）----
class DiseaseLookupIn(BaseModel):
    disease_name: str


@app.post("/api/diseases/ai-lookup")
async def api_disease_lookup(body: DiseaseLookupIn):
    """AI 查医学词条：输入疾病名 → 返回结构化疾病分析（供子女端确认）。"""
    name = body.disease_name.strip()
    if not name:
        return JSONResponse(status_code=400, content={"detail": "疾病名称不能为空"})
    profile = medical.load_profile(store._conn)
    result = guardian.lookup_disease(name, age=profile.age)
    # 没生成 code 时用随机短码兜底
    if not result.get("code"):
        import uuid as _uuid
        result["code"] = f"custom_{_uuid.uuid4().hex[:6]}"
    return result


# ---- 子女确认接口 ----
@app.post("/api/family/confirm")
async def api_family_confirm():
    """子女确认收到告警，阻止 Zone 3→4 超时升级。"""
    processor._family_confirmed = True
    await manager.broadcast({"kind": "family_confirmed", "data": {"confirmed": True}})
    return {"ok": True, "message": "已确认收到告警"}


@app.post("/api/alert-ack")
async def api_alert_ack():
    """家人已知晓（第二轮报警响铃的停止条件）。"""
    processor._family_confirmed = True
    await manager.broadcast({"kind": "alert_acked", "data": {"acked": True}})
    sys_log("info", "家人已知晓告警 · 响铃停止")
    return {"ok": True, "message": "已知晓，响铃已停止"}


# ---- 护家模式控制接口 ----
@app.get("/api/guard-mode")
async def api_get_guard_mode():
    """获取护家模式状态。"""
    return {
        "enabled": processor._intrusion_mode,
        "intrusion_fired": processor._intrusion_fired,
        "silence_s": int(processor._silence_accum),
        "can_toggle": True,
    }


@app.post("/api/guard-mode/toggle")
async def api_toggle_guard_mode():
    """子女远程开关护家模式（访客到访时手动关闭，访客离开后恢复）。"""
    if processor._intrusion_mode:
        # 关闭护家模式
        processor._intrusion_mode = False
        processor._silence_accum = 0.0
        processor._intrusion_motion_accum = 0.0
        processor._intrusion_fired = False
        msg = "护家模式已关闭（访客模式）"
    else:
        # 手动启动护家模式
        processor._intrusion_mode = True
        processor._intrusion_fired = False
        msg = "护家模式已启动"
    await manager.broadcast({"kind": "guard_mode", "data": {"enabled": processor._intrusion_mode}})
    return {"ok": True, "enabled": processor._intrusion_mode, "message": msg}


# ---- 语音确证 API ----
@app.get("/api/voice-confirm")
async def api_get_voice_confirm():
    """获取当前语音确证状态。"""
    return voice_session.to_dict()


@app.post("/api/voice-confirm/respond")
async def api_voice_respond(answer: str = "ok"):
    """老人回应语音确证：ok=我没事，help=我需要帮助。"""
    state = voice_session.respond(answer)
    await manager.broadcast({"kind": "voice_responded", "data": {"state": state, "answer": answer}})
    if state == "ok":
        # 老人说没事 → 消警，重置到绿区（跌倒语义一并解除）
        processor._reset_to_green()
        processor._fall_watch = None
        processor._fall_state_s = 0.0
        processor._still_too_long_fired = False
        processor._voice_round = 0
        await manager.broadcast({"kind": "alert_cleared", "data": {"reason": "老人回应正常"}})
    elif state == "help":
        # 老人求助（含检测到呻吟等价入口）→ 加强告警等级直升 RED，确证链终止
        from .protocol import ZONE_RED
        processor._voice_round = 0
        processor._set_zone(ZONE_RED)
        await manager.broadcast({"kind": "alert_escalated", "data": {"reason": "老人求助，升级告警"}})
    return {"ok": True, "state": state}


# ---- 开放式疾病管理 API ----
class DiseaseIn(BaseModel):
    code: str
    name: str
    category: str = ""
    description: str = ""
    fall_risk_note: str = ""
    breathing_impact: str = ""
    advice: list[str] = []
    voice_timeout_override: int = 0
    skip_voice: bool = False
    zone3_max_stay: int = 0
    # 检测修正系数（千人千档，0=不修正）
    br_elevated_adjust: int = 0
    br_lost_confirm_s: int = 0
    active_min_adjust: float = 0.0
    type_b_still_s: int = 0


@app.get("/api/diseases")
async def api_list_diseases():
    """列出所有可用疾病策略（含预设和自定义）。"""
    # 预设疾病
    preset = []
    for code in medical.ALL_CONDITIONS:
        strategy = medical.get_disease_strategy(code)
        if strategy["source"] != "unknown":
            preset.append({"code": code, **strategy})
    # 自定义疾病
    custom = medical.list_custom_diseases()
    return {"preset": preset, "custom": custom}


@app.post("/api/diseases")
async def api_add_disease(d: DiseaseIn):
    """子女确认自定义疾病：注册到策略表 + 持久化备份 + 并入档案病史。"""
    disease = medical.CustomDisease(
        code=d.code, name=d.name, category=d.category,
        description=d.description, fall_risk_note=d.fall_risk_note,
        breathing_impact=d.breathing_impact, advice=d.advice,
        voice_timeout_override=d.voice_timeout_override,
        skip_voice=d.skip_voice, zone3_max_stay=d.zone3_max_stay,
        br_elevated_adjust=d.br_elevated_adjust,
        br_lost_confirm_s=d.br_lost_confirm_s,
        active_min_adjust=d.active_min_adjust,
        type_b_still_s=d.type_b_still_s,
    )
    medical.register_disease(disease, conn=store._conn)
    # 并入档案病史（去重），形成个性化医疗档案备份
    profile = medical.load_profile(store._conn)
    if disease.code not in profile.conditions:
        profile.conditions.append(disease.code)
        medical.save_profile(store._conn, profile)
    # 新病史立即生效：重新注入修正系数
    _apply_profile_to_processor()
    await manager.broadcast({"kind": "profile_updated", "data": profile.to_dict()})
    await manager.broadcast({"kind": "profile_adjustments", "data": processor.adjustments})
    return {"ok": True, "disease": disease.to_dict()}


@app.delete("/api/diseases/{code}")
async def api_remove_disease(code: str):
    """移除一个自定义疾病（同时从档案病史中移除）。"""
    removed = medical.unregister_disease(code, conn=store._conn)
    if removed:
        profile = medical.load_profile(store._conn)
        if code in profile.conditions:
            profile.conditions.remove(code)
            medical.save_profile(store._conn, profile)
        _apply_profile_to_processor()
        await manager.broadcast({"kind": "profile_adjustments", "data": processor.adjustments})
    return {"ok": removed, "code": code}


@app.get("/api/diseases/{code}/strategy")
async def api_disease_strategy(code: str):
    """获取指定疾病的告警策略。"""
    return medical.get_disease_strategy(code)


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await manager.connect(ws)
    sys_log("info", f"子女端已连接 · 当前在线 {len(manager.active)} 端")
    try:
        while True:
            await ws.receive_text()  # 子女端只收不发，收到即忽略
    except WebSocketDisconnect:
        manager.disconnect(ws)
        sys_log("info", f"子女端已断开 · 当前在线 {len(manager.active)} 端")


@app.get("/api/health")
async def api_health():
    """健康检查：供启动器探测后端是否已在运行。"""
    return {"ok": True, "app": "护院鹅 Edge"}


# 子女端静态托管：h5 构建产物直接由 8000 端口提供（hash 路由，无 SPA 深链问题），
# 已注册的 /api/* 与 /ws 路由优先级高于静态挂载，互不干扰
if H5_DIST.is_dir():
    app.mount("/", StaticFiles(directory=H5_DIST, html=True), name="h5")
else:
    @app.get("/")
    async def index():
        return FileResponse(WEB_DIR / "index.html")
