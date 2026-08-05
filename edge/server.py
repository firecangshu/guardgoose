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
import time
from collections import deque
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from . import config as C
from . import guardian
from . import medical
from .db import open_store
from .protocol import EVENT_TYPES, Event, EVT_FALL_BREATHING_OK, SRC_DEMO_INJECT
from .state_machine import SampleProcessor
from .voice import VoiceConfirmSession

WEB_DIR = Path(__file__).resolve().parent.parent / "web"

app = FastAPI(title="护院鹅 Edge")
store = open_store(C.DB_PATH)
processor = SampleProcessor(zone="living")
voice_session = VoiceConfirmSession(elder_name="奶奶", timeout_s=90)


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


async def _handle_event(ev: Event) -> None:
    """事件统一处理：入库 → 派生告警 → 广播。"""
    store.insert_event(ev)
    await manager.broadcast({"kind": "event", "data": ev.to_dict()})
    # Zone 2 语音确证触发
    if ev.type == EVT_FALL_BREATHING_OK:
        voice_data = voice_session.start()
        await manager.broadcast({"kind": "voice_confirm", "data": voice_data})
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
    _last_sample_wall = time.time()
    events = processor.process(s.ts, s.sim_t, s.intensity, s.zone,
                               s.breathing_rate, s.breathing_state)
    for ev in events:
        await _handle_event(ev)
    # 广播实时活动强度+呼吸状态，供子女端画曲线
    await manager.broadcast({
        "kind": "sample",
        "data": {"ts": s.ts, "sim_t": s.sim_t, "intensity": s.intensity,
                 "zone": s.zone or processor.zone, "present": processor.present,
                 "breathing_rate": s.breathing_rate,
                 "breathing_state": s.breathing_state or "normal",
                 "guard_zone": processor.guard_zone},
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
    await manager.broadcast({"kind": "reset", "data": {}})
    await manager.broadcast({"kind": "source_mode", "data": dict(_mode_state)})
    sys_log("warn", "系统已重置 · 事件库清空，状态机与数据源开关归零")
    return {"ok": True}


# ---- 数据源模式：真实接入 > 演示场景 > 待机（无数据流）----
SCEN_DIR = Path(__file__).resolve().parent.parent / "replay" / "scenarios"

DEMO_SCENARIOS = [
    {"scenario": "test_normal_motion", "label": "正常状态",
     "desc": "正常活动、呼吸平稳，系统绿区巡航"},
    {"scenario": "day_fall", "label": "跌倒·语音确证",
     "desc": "跌倒冲击→倒地静止→语音确证全流程"},
    {"scenario": "day_zone2_timeout", "label": "跌倒·无回应",
     "desc": "观察期无人回应，告警逐级升级"},
    {"scenario": "day_breathing_lost", "label": "呼吸消失",
     "desc": "呼吸渐弱直至消失，触发红色告警"},
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
    与真实硬件数据走完全相同的判定链路（入库→告警派生→广播）。"""
    global _last_sample_wall
    scen = json.loads((SCEN_DIR / f"{name}.json").read_text(encoding="utf-8"))
    speed = float(scen.get("speed", 10))
    zone = scen.get("zone", "living")
    await manager.broadcast({"kind": "demo", "data": {"running": True, "scenario": name}})
    sim_t = 0.0
    try:
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
                             "guard_zone": processor.guard_zone},
                })
                sim_t += 1
                await asyncio.sleep(1.0 / speed)
    except asyncio.CancelledError:
        return
    finally:
        if _mode_state["demo_scenario"] == name:
            _mode_state["demo_enabled"] = False
            _mode_state["demo_scenario"] = ""
            await manager.broadcast({"kind": "demo", "data": {"running": False, "scenario": ""}})


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
    探测器上报的 /ingest/sample 信号直接联动底层判定逻辑。"""
    _mode_state["real_enabled"] = body.enabled
    if body.enabled and _mode_state["demo_enabled"]:
        await _stop_demo()
        msg = "真实接入已开启：演示已自动关闭，探测器信号直接联动底层逻辑"
        sys_log("info", "真实接入已开启 · 演示已自动关闭")
    elif body.enabled:
        msg = "真实接入已开启，等待探测器信号接入"
        sys_log("info", "真实接入已开启 · 等待探测器信号")
    else:
        msg = "真实接入已关闭"
        sys_log("info", "真实接入已关闭")
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
            "guard_zone": processor.guard_zone}


# ---- 设备连接状态 API ----
def _device_freshness() -> tuple[str, float]:
    """返回 (连接状态, 距上次样本秒数)。"""
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


@app.get("/api/device/diagnosis")
async def api_device_diagnosis():
    """信号不佳时的诊断详情：供子女端排查面板展示。"""
    state, lag = _device_freshness()
    tips: list[str] = []
    if state == "disconnected":
        tips = [
            "超过30秒未收到探测器数据，判定为断开",
            "检查发射端(TX)与接收端(RX)是否通电、指示灯是否正常",
            "确认探测器与边缘网关接入同一路由器",
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
    # 更新状态机的语音超时
    processor._zone2_voice_timeout = profile.voice_timeout
    # 高血压病史：Zone 3 停留超过10分钟自动升级
    if medical.HX_HYPERTENSION in profile.conditions:
        processor._zone3_max_stay = 600  # 10分钟
    else:
        processor._zone3_max_stay = 0
    await manager.broadcast({"kind": "profile_updated", "data": profile.to_dict()})
    return {"ok": True, "profile": {
        "name": profile.elder_name,
        "diseases": profile.conditions,
        "is_high_risk": profile.is_high_risk,
        "voice_timeout": profile.voice_timeout,
    }}


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
        # 老人说没事 → 消警，重置到绿区
        processor._reset_to_green()
        processor._fall_watch = None
        processor._still_too_long_fired = False
        await manager.broadcast({"kind": "alert_cleared", "data": {"reason": "老人回应正常"}})
    elif state == "help":
        # 老人求助 → 升级 Zone 3
        from .protocol import ZONE_RED
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
    )
    medical.register_disease(disease, conn=store._conn)
    # 并入档案病史（去重），形成个性化医疗档案备份
    profile = medical.load_profile(store._conn)
    if disease.code not in profile.conditions:
        profile.conditions.append(disease.code)
        medical.save_profile(store._conn, profile)
    await manager.broadcast({"kind": "profile_updated", "data": profile.to_dict()})
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


@app.get("/")
async def index():
    return FileResponse(WEB_DIR / "index.html")
