"""硬件桥接主程序：ESP32-S3 CSI 串口 → 边缘服务 /ingest/sample。

用法：
    python -m hw.bridge                          # 自动发现板子（等待硬件插入）
    python -m hw.bridge --port COM6              # 指定串口
    python -m hw.bridge --replay 测试数据.csv    # 无硬件：CSV 回放全链路验证
    python -m hw.bridge --lost-seconds 15        # 开启呼吸「消失」防抖上报
    python -m hw.bridge --no-preflight           # 跳过开机自检直接推流

开机自检（preflight，默认开）：连上板子后先静默采集 15 秒（不推流），
逐项检查帧率/RSSI/底噪/空场/后端闸门，全部达标才进入正式监护；
不达标会给出修复建议并每 10 秒重测。

数据源语义：真实接入（source=csi_live）优先级最高，
边缘服务侧需先开启「真实接入」开关（/api/source-mode/real），
本程序推送的样本即被状态机接受为真实信号。
"""
from __future__ import annotations

import argparse
import sys
import threading
import time
import urllib.error
import urllib.request
import json
from collections import deque
from datetime import datetime

import serial

# Windows 下 stdout 默认 GBK：重定向到日志文件时 ✓/✗ 等符号会编码崩溃，
# 统一重配 UTF-8（pythonw/DETACHED 场景同样生效）
for _stream in (sys.stdout, sys.stderr):
    if _stream is not None:
        try:
            _stream.reconfigure(encoding="utf-8")
        except (ValueError, OSError):
            pass
import numpy as np
from serial.tools import list_ports

from .csi_parser import parse_csi_line, CsiFrame
from .signal_proc import (
    ActivityEstimator, BreathDetector, NoiseFloorEstimator,
    classify_breathing, intensity_class,
    ACTIVE_MIN, BR_WINDOW_S,
)

BAUD = 921600
MAX_CONSECUTIVE_ERR = 20     # 连续坏帧/异常超过该数视为数据源故障
BR_MOTION_RATIO_MAX = 0.20   # 呼吸窗口内运动帧占比上限（运动尾巴污染抑制）

# ---- 开机自检门限（20260807 真机实测标定：健康链路 RSSI≈-48、
# 帧率 50~67/s、空场底噪 0.012~0.019；TX 掉电后 RSSI 降至 -55~-65）----
PF_SECONDS = 15              # 自检采集时长（秒）
PF_RETRY_WAIT_S = 10         # 自检失败后等待重测时长
PF_MIN_FPS = 20.0            # 实测帧率下限
PF_MIN_RSSI = -60            # 链路质量下限（低于此呼吸检测可靠性骤降）
PF_MAX_FLOOR = 0.020         # 环境底噪上限（空调房实测 0.034+）
PF_MAX_INTENSITY_P90 = 0.15  # 空场强度 90 分位上限（有人在场会持续高于此）


class CsiBridge:
    """采集循环：读行 → 解析 → 逐秒聚合 → POST /ingest/sample。"""

    def __init__(self, backend: str, zone: str, lost_seconds: int,
                 csv_out: str | None = None, verbose: bool = False,
                 session_label: str | None = None):
        self.backend = backend.rstrip("/")
        self.zone = zone
        self.lost_seconds = max(0, lost_seconds)
        self.csv_out = csv_out
        self.verbose = verbose
        self.session_label = session_label   # 真机测试数据库：带标签则逐秒落盘
        self._session_fp = None
        self.activity = ActivityEstimator()
        self.breath = BreathDetector()
        self._fps_ema = self.breath.fs   # 实测帧率 EMA（校准 FFT 采样率）
        self.noise = NoiseFloorEstimator()   # 环境底噪动态估计（空调噪声补偿）
        self._intensity_hist = deque(maxlen=int(BR_WINDOW_S))   # 最近 N 秒强度（呼吸窗口对齐）
        self._sim_t = 0
        self._win_start = time.time()
        self._win_frames = 0
        self._last_rssi = 0
        self._bad = 0
        self._frame_total = 0
        self._fps_start = time.time()
        self._fps_count = 0
        self._lost_since: float | None = None   # 共识瓦解起始时间（秒，None=未瓦解）
        self._start_wall = time.time()          # 启动时刻（呼吸窗就绪前不报 lost）
        self.preflight_mode = False             # 自检模式：只观测不推流
        self._pf: dict = {}                     # 自检采集缓冲
        self._hb_phase = "init"                 # 心跳上报的阶段态（供后端信号接通测试）
        self._hb_port = ""
        self._csv_fp = None
        if csv_out:
            self._csv_fp = open(csv_out, "w", encoding="utf-8")

    # ---- 帧接入 ----
    def feed(self, frame: CsiFrame) -> None:
        self._frame_total += 1
        self._fps_count += 1
        self._last_rssi = frame.rssi
        if self._csv_fp and frame.raw:
            self._csv_fp.write(frame.raw + "\n")
        self.activity.add_frame(frame.amp)
        self.breath.add_frame(frame.amp)
        self._maybe_flush()

    def _maybe_flush(self) -> None:
        """每 ~1 秒墙钟聚合一次并推送。"""
        now = time.time()
        if now - self._win_start < 1.0:
            return
        self._win_start = now
        self._sim_t += 1

        intensity = self.activity.flush()
        if intensity is not None:
            self._intensity_hist.append(intensity)
            self.noise.feed(intensity)   # 底噪估计：活动样本自动排除
            if self.preflight_mode:
                self._pf.setdefault("intensities", []).append(intensity)
        # FFT 采样率校准：实测帧率（本批帧数/窗口时长）EMA 更新，
        # 偏离固件标称 72fps 时避免频率估计失真（20260807 实测 ~50fps）
        inst_fps = float(self._fps_count)
        if inst_fps >= 5:
            self._fps_ema = 0.7 * self._fps_ema + 0.3 * inst_fps
            self.breath.fs = max(20.0, min(100.0, self._fps_ema))
        # FFT 显著度门槛随底噪自适应（空调噪声抬频谱基底）
        rate, consensus, locked = self.breath.detect(
            significance_min=self.noise.significance_min)

        # 运动污染抑制：肢体运动的节奏会污染 FFT 主频产生伪锁频
        # ① 当前秒强度高（≥ACTIVE_MIN）→ 直接不可信
        # ② 呼吸窗口内运动帧占比超上限（运动刚停、尾巴仍在窗口内）→ 同样不可信
        motion_ratio = 0.0
        if self._intensity_hist:
            motion_ratio = sum(1 for v in self._intensity_hist
                               if v is not None and v >= ACTIVE_MIN) / len(self._intensity_hist)
        if ((intensity is not None and intensity >= ACTIVE_MIN)
                or motion_ratio > BR_MOTION_RATIO_MAX):
            rate, consensus, locked = None, 0.0, 0.0

        state = classify_breathing(rate)

        # 空调噪声补偿：推送前重标定——底噪带内的强度不代表人体活动，
        # 压回静止带内（避免噪声孤峰被状态机误判为「起身微动」）。
        # 实测：集成测试中空调房噪声峰 0.27 触发 Type C 误报，修复于此处。
        report_intensity = intensity
        if (intensity is not None and self.noise.noisy
                and intensity < self.noise.still_threshold):
            report_intensity = 0.02   # 静止带内低调值，不改变语义

        # 呼吸「消失」防抖：静止 + 共识瓦解持续 lost_seconds → lost
        # 「静止」判定用自适应静止带（空调房底噪高，固定 STILL_MAX 会误判）
        # 启动保护：呼吸分析窗（20s）未就绪前 rate=None 是「无数据」而非「消失」，
        # 不报 lost（集成测试实测：启动 15s 就误报呼吸消失）
        still_th = self.noise.still_threshold
        breath_window_ready = (time.time() - self._start_wall) >= BR_WINDOW_S
        if (self.lost_seconds > 0 and breath_window_ready
                and intensity is not None and intensity < still_th):
            if rate is None:
                if self._lost_since is None:
                    self._lost_since = now
                elif now - self._lost_since >= self.lost_seconds:
                    state, rate = "lost", 0
            else:
                self._lost_since = None
        else:
            self._lost_since = None

        # 自检模式：只记录指标不推流（自检数据不得进状态机）
        fps = self._fps_count / (now - self._fps_start) if now > self._fps_start else 0
        if self.preflight_mode:
            pf = self._pf
            pf.setdefault("fps", []).append(fps)
            pf.setdefault("rssi", []).append(self._last_rssi)
        else:
            self._post(report_intensity, rate, state, consensus, locked,
                       motion_ratio)
            self._session_log(report_intensity, rate, state, locked,
                              motion_ratio, fps)

        # 状态行
        self._fps_count = 0
        self._fps_start = now
        print(
            f"[{datetime.now().strftime('%H:%M:%S')}] t={self._sim_t:>5}s "
            f"帧率 {fps:5.1f}/s RSSI {self._last_rssi:>4} "
            f"强度 {intensity if intensity is not None else -1:5.2f} "
            f"({intensity_class(intensity)}) "
            f"呼吸 {rate if rate is not None else '--':>3}/min {state or '--':<8} "
            f"共识 {locked:>2.0f}/32 运动比 {motion_ratio:.2f} "
            f"底噪 {self.noise.floor:.3f}{'↑' if self.noise.noisy else ' '}",
            flush=True,
        )

    # ---- 推送 ----
    def heartbeat(self, phase: str, port: str | None = None,
                  detail: str = "") -> None:
        """向后端上报桥接器自身状态（阶段/串口/帧率/RSSI）。
        后端据此区分“桥接器没在跑”与“板子没插”，信号接通测试依赖它。
        失败静默：心跳不能阻塞主采集链。"""
        if port is not None:
            self._hb_port = port
        self._hb_phase = phase
        body = {
            "phase": phase, "port": self._hb_port, "detail": detail,
            "fps": round(float(self._fps_ema), 1), "rssi": self._last_rssi,
            "preflight": self.preflight_mode,
        }
        req = urllib.request.Request(
            f"{self.backend}/api/bridge/heartbeat",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=2):
                pass
        except Exception:
            pass

    def _post(self, intensity, rate, state, consensus, locked,
              motion_ratio: float = 0.0) -> None:
        if intensity is None:
            return
        body = {
            "ts": datetime.now().astimezone().isoformat(timespec="seconds"),
            "sim_t": self._sim_t,
            "intensity": round(float(intensity), 4),
            "zone": self.zone,
            "breathing_rate": int(rate) if rate is not None else 0,
            "breathing_state": state or "normal",
            # 锁频诊断（真机调参取证）：共识子载波数/窗口内运动帧占比
            "br_locked": float(locked or 0.0),
            "br_motion_ratio": round(float(motion_ratio), 3),
            "noise_floor": round(float(self.noise.floor), 4),
        }

        req = urllib.request.Request(
            f"{self.backend}/ingest/sample",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                if self.verbose:
                    print(f"    → {resp.status} {resp.read().decode()[:120]}")
        except urllib.error.HTTPError as e:
            if e.code == 403 and not getattr(self, "_gate_warned", False):
                self._gate_warned = True
                print("    ⚠️ 样本被拒收：边缘服务未开启「真实接入」开关，"
                      "请在守护页开启后再推（后续不再重复提示）", flush=True)
        except Exception as e:
            print(f"    ⚠️ 推送失败（边缘服务未启动？）: {e}", flush=True)

    def close(self) -> None:
        if self._csv_fp:
            self._csv_fp.close()
        if self._session_fp:
            self._session_fp.close()
            self._session_fp = None
            self._session_summarize()

    # ---- 真机测试数据库：逐秒样本落盘（test_data_db/sessions）----
    def _session_log(self, intensity, rate, state, locked,
                     motion_ratio, fps) -> None:
        if not self.session_label:
            return
        if self._session_fp is None:
            from pathlib import Path
            db_dir = Path(__file__).resolve().parent.parent / "test_data_db" / "sessions"
            db_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d_%H%M")
            path = db_dir / f"{stamp}_{self.session_label}.csv"
            self._session_fp = open(path, "w", encoding="utf-8")
            self._session_fp.write("ts,sim_t,intensity,br_rate,br_state,"
                                   "br_locked,br_motion_ratio,noise_floor,rssi,fps\n")
            print(f"[测试数据库] 逐秒样本落盘 → {path}", flush=True)
        self._session_fp.write(
            f"{datetime.now().astimezone().isoformat(timespec='seconds')},"
            f"{self._sim_t},{intensity if intensity is not None else ''},"
            f"{int(rate) if rate is not None else 0},{state or ''},"
            f"{locked or 0:.0f},{motion_ratio:.3f},"
            f"{self.noise.floor:.4f},{self._last_rssi},{fps:.1f}\n")
        self._session_fp.flush()

    def _session_summarize(self) -> None:
        """会话收尾：读回本次逐秒 CSV，落一份 summary.json 到会话库，
        供离线找规律（锁频率/底噪/RSSI 分布等）。失败不影响主链。"""
        try:
            from pathlib import Path
            db_dir = Path(__file__).resolve().parent.parent / "test_data_db" / "sessions"
            stamp = datetime.now().strftime("%Y%m%d_%H%M")
            csv_path = db_dir / f"{stamp}_{self.session_label}.csv"
            # 落盘文件按启动分钟命名，收尾可能跨分钟：找最近一个同名前缀文件
            if not csv_path.exists():
                cands = sorted(db_dir.glob(f"*_{self.session_label}.csv"))
                if not cands:
                    return
                csv_path = cands[-1]
            import csv as _csv
            rows = []
            with open(csv_path, "r", encoding="utf-8") as f:
                for r in _csv.DictReader(f):
                    rows.append(r)
            if not rows:
                return

            def _col(key, cast=float):
                out = []
                for r in rows:
                    try:
                        out.append(cast(r[key]))
                    except (ValueError, TypeError, KeyError):
                        pass
                return out

            locked_cnt = _col("br_locked")
            locked_rate = (sum(1 for v in locked_cnt if v >= 13) / len(locked_cnt)
                           if locked_cnt else 0.0)
            intens = _col("intensity")
            summary = {
                "label": self.session_label,
                "csv": str(csv_path.name),
                "closed_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                "duration_s": len(rows),
                "fps_avg": round(sum(_col("fps")) / max(1, len(_col("fps"))), 1),
                "rssi_last": self._last_rssi,
                "noise_floor_last": round(float(self.noise.floor), 4),
                "noise_noisy": bool(self.noise.noisy),
                "br_lock_rate": round(locked_rate, 3),
                "br_rate_avg": round(sum(_col("br_rate")) / max(1, len(_col("br_rate"))), 1),
                "intensity_p90": round(float(np.percentile(intens, 90)), 4) if intens else 0.0,
                "motion_ratio_avg": round(sum(_col("br_motion_ratio")) /
                                            max(1, len(_col("br_motion_ratio"))), 3),
                "note": "",   # 测试后人工补充：几何摆放/人员动作/结论
            }
            out = csv_path.with_suffix(".summary.json")
            out.write_text(json.dumps(summary, ensure_ascii=False, indent=2),
                           encoding="utf-8")
            print(f"[测试数据库] 会话总结落盘 → {out}", flush=True)
        except Exception as e:
            print(f"[测试数据库] 总结落盘失败（不影响主链）: {e}", flush=True)

    def reset_state(self) -> None:
        """自检通过后清零所有估计器：自检期数据不得污染正式监护的基线。"""
        self.activity = ActivityEstimator()
        self.breath = BreathDetector()
        self._fps_ema = self.breath.fs
        self.noise = NoiseFloorEstimator()
        self._intensity_hist.clear()
        self._sim_t = 0
        self._win_start = time.time()
        self._lost_since = None
        self._start_wall = time.time()
        self.preflight_mode = False
        self._pf = {}


# ---- 串口发现 ----
def _is_bt_port(p) -> bool:
    desc = (p.description or "").lower()
    return "bluetooth" in desc or "蓝牙" in desc


def find_csi_port(timeout_hint: str = "") -> str | None:
    """扫描 USB 串口，复位抓启动日志识别 csi_recv 板。"""
    cands = [p for p in list_ports.comports() if not _is_bt_port(p)]
    if not cands:
        print(f"[等待硬件] {timeout_hint}未发现 USB 串口，每 3 秒重扫…", flush=True)
        return None
    for p in cands:
        print(f"  检测 {p.device}（{p.description}）…", flush=True)
        for baud in (BAUD, 115200):
            try:
                s = serial.Serial(p.device, baud, timeout=0.5)
            except Exception as e:
                print(f"    无法打开 {p.device}: {e}", flush=True)
                break
            s.setDTR(False)
            s.setRTS(True)
            time.sleep(0.2)
            s.setRTS(False)
            buf = b""
            t0 = time.time()
            while time.time() - t0 < 3:
                buf += s.read(2048)
            s.close()
            text = buf.decode("utf-8", errors="replace")
            if "csi_recv" in text or "CSI_DATA" in text:
                print(f"  ✓ {p.device} 是 RX 接收板（csi_recv）", flush=True)
                return p.device
            if "csi_send" in text:
                print(f"  ! {p.device} 是 TX 发射板（不接电脑，请换另一块）", flush=True)
                return None
    return None


def _read_serial_loop(bridge: CsiBridge, port: str, baud: int,
                      stop: list) -> None:
    while not stop[0]:
        try:
            with serial.Serial(port, baud, timeout=1.0) as ser:
                print(f"[连接] {port} @ {baud} baud，开始采集…", flush=True)
                bridge._bad = 0
                last_hb = 0.0
                while not stop[0]:
                    try:
                        raw = ser.readline()
                    except Exception:
                        break
                    # 心跳每 3 秒一次：自检期/推流期都报，后端据此知道链路活着
                    now = time.time()
                    if now - last_hb >= 3.0:
                        last_hb = now
                        bridge.heartbeat(
                            "preflight" if bridge.preflight_mode else "streaming",
                            port)
                    if not raw:
                        continue
                    try:
                        line = raw.decode("utf-8", errors="replace")
                    except Exception:
                        continue
                    frame = parse_csi_line(line)
                    if frame is None:
                        bridge._bad += 1
                        if bridge._bad >= MAX_CONSECUTIVE_ERR:
                            print("  ⚠️ 连续坏帧过多，重启采集…", flush=True)
                            break
                        continue
                    bridge._bad = 0
                    bridge.feed(frame)
        except serial.SerialException as e:
            print(f"[断开] {port}: {e}", flush=True)
        if not stop[0]:
            bridge.heartbeat("reconnecting", port, "串口断开，3秒后重试")
            print("  3 秒后重试（拔插板子会自动重连）…", flush=True)
            time.sleep(3)


def _run_preflight(bridge: CsiBridge, port: str, baud: int) -> bool:
    """开机自检：静默采集 PF_SECONDS 秒逐项体检，通过返回 True。

    自检期不推流（数据不进状态机）；每轮开始前 reset_state 清零，
    避免上一轮数据污染本轮判定（底噪 EMA 会跨轮残留）。"""
    bridge.reset_state()
    bridge.preflight_mode = True
    print(f"\n[开机自检] 静默采集 {PF_SECONDS} 秒（自检期不推流）…", flush=True)
    stop = [False]
    th = threading.Thread(target=_read_serial_loop,
                          args=(bridge, port, baud, stop), daemon=True)
    th.start()
    th.join(PF_SECONDS)
    stop[0] = True
    th.join(timeout=3)
    bridge.preflight_mode = False

    pf = bridge._pf
    checks: list[tuple[bool, str, str]] = []   # (通过, 项目, 说明/建议)

    # ① 帧率
    fps_list = [v for v in pf.get("fps", []) if v > 0]
    fps_ok = bool(fps_list)
    fps_avg = float(np.mean(fps_list)) if fps_ok else 0.0
    checks.append((fps_ok and fps_avg >= PF_MIN_FPS, "串口帧率",
                   f"{fps_avg:.0f}/s（要求 ≥{PF_MIN_FPS:.0f}）"
                   if fps_ok else "无数据——检查 RX 板供电与串口"))

    # ② 链路质量（RSSI）
    rssi_list = [v for v in pf.get("rssi", []) if v != 0]
    rssi_ok = bool(rssi_list)
    rssi_med = int(np.median(rssi_list)) if rssi_ok else 0
    checks.append((rssi_ok and rssi_med >= PF_MIN_RSSI, "链路质量 RSSI",
                   f"{rssi_med}dBm（要求 ≥{PF_MIN_RSSI}）" if rssi_ok
                   else "无 RSSI 数据"))

    # ③ 环境底噪
    floor = bridge.noise.floor
    checks.append((floor <= PF_MAX_FLOOR, "环境底噪",
                   f"{floor:.3f}（要求 ≤{PF_MAX_FLOOR}）"))

    # ④ 空场确认
    ints = pf.get("intensities", [])
    p90 = float(np.percentile(ints, 90)) if ints else 0.0
    checks.append((p90 <= PF_MAX_INTENSITY_P90, "视场空场",
                   f"强度 P90={p90:.2f}（要求 ≤{PF_MAX_INTENSITY_P90}）"))

    # ⑤ 后端闸门：可达性 + 真实接入开关（自动开启）
    gate_msg, gate_ok = "", False
    try:
        with urllib.request.urlopen(
                f"{bridge.backend}/api/source-mode", timeout=3) as resp:
            mode = json.loads(resp.read().decode())
        if mode.get("real_enabled"):
            gate_ok, gate_msg = True, "真实接入已开启"
        else:
            req = urllib.request.Request(
                f"{bridge.backend}/api/source-mode/real",
                data=json.dumps({"enabled": True}).encode("utf-8"),
                headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(req, timeout=3):
                pass
            gate_ok, gate_msg = True, "真实接入未开，已自动开启"
    except Exception as e:
        gate_msg = f"边缘服务不可达（{e}）——先跑 python run_edge.py"
    checks.append((gate_ok, "后端闸门", gate_msg))

    print("[自检结果]", flush=True)
    all_ok = True
    for ok, name, msg in checks:
        all_ok &= ok
        print(f"  {'✓' if ok else '✗'} {name}: {msg}", flush=True)
    if not all_ok:
        bridge.heartbeat("preflight_failed", port,
                         "; ".join(name for (ok_, name, _m) in checks if not ok_))
        hints = []
        if not checks[0][0]:
            hints.append("帧率不足：检查 RX 板 USB 供电/换直连口，确认固件在发 CSI_DATA")
        if not checks[1][0]:
            hints.append(f"RSSI 偏低：给 TX 发射板充电/换电源，两板间距收回到 1~3 米，天线竖直朝上正对")
        if not checks[2][0]:
            hints.append("底噪偏高：关空调/风扇等 3 分钟再试（空调噪声实测抬底噪 17 倍）")
        if not checks[3][0]:
            hints.append("视场内有人/物：请离开两板连线区域、移开连线附近遮挡物后重测")
        for h in hints:
            print(f"  → {h}", flush=True)
        print(f"[自检未通过] {PF_RETRY_WAIT_S} 秒后重测（Ctrl+C 退出）…", flush=True)
        return False
    bridge.reset_state()
    bridge.heartbeat("preflight_passed", port, "自检通过，进入正式监护")
    print("[自检通过] 条件就绪，进入正式监护推流\n", flush=True)
    return True


def _replay_loop(bridge: CsiBridge, csv_path: str, stop: list) -> None:
    """CSV 回放：按原始行文本喂给解析器（保持 data 引号完整性）。"""
    with open(csv_path, "r", encoding="utf-8") as f:
        next(f, None)   # 跳过表头
        for line in f:
            if stop[0]:
                break
            line = line.strip()
            if not line or not line.startswith("CSI_DATA"):
                continue
            frame = parse_csi_line(line)
            if frame:
                bridge.feed(frame)
                time.sleep(1.0 / 72.0)   # 按 ~72fps 节奏回放
    print("[回放] 播放完毕", flush=True)


def main() -> None:
    ap = argparse.ArgumentParser(description="护院鹅 CSI 硬件桥接器")
    ap.add_argument("--port", help="串口号（如 COM6）；缺省自动发现")
    ap.add_argument("--baud", type=int, default=BAUD)
    ap.add_argument("--backend", default="http://127.0.0.1:8000",
                    help="边缘服务地址")
    ap.add_argument("--zone", default="living", help="部署分区")
    ap.add_argument("--lost-seconds", type=int, default=0,
                    help="静止且锁频共识瓦解持续 N 秒上报呼吸消失（默认关）")
    ap.add_argument("--csv-out", help="原始帧落盘（调试）")
    ap.add_argument("--replay", help="CSV 回放模式（无需硬件）")
    ap.add_argument("--no-preflight", action="store_true",
                    help="跳过开机自检直接推流（默认先自检 15 秒）")
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--session-label",
                    help="真机测试标签：带上则逐秒样本自动落盘到 test_data_db/sessions")
    args = ap.parse_args()

    bridge = CsiBridge(args.backend, args.zone, args.lost_seconds,
                       args.csv_out, args.verbose,
                       session_label=args.session_label)
    stop = [False]
    try:
        if args.replay:
            print(f"[回放模式] {args.replay} → {args.backend}/ingest/sample")
            _replay_loop(bridge, args.replay, stop)
        else:
            print("护院鹅 CSI 桥接器 v1.0 ｜ 波特率 "
                  f"{args.baud} ｜ 分区 {args.zone} ｜ 边缘 {args.backend}")
            print("提示：开机自检通过后自动推流；--no-preflight 可跳过自检。")
            while not stop[0]:
                port = args.port or find_csi_port()
                if port is None:
                    bridge.heartbeat("waiting_hardware", "",
                                     "未发现接收板，等待硬件插入")
                    time.sleep(3)
                    continue
                bridge.heartbeat("preflight" if not args.no_preflight
                                 else "streaming", port, "已发现接收板")
                if not args.no_preflight:
                    if not _run_preflight(bridge, port, args.baud):
                        time.sleep(PF_RETRY_WAIT_S)
                        args.port = port   # 重测沿用已发现串口，避免反复复位板子
                        continue
                _read_serial_loop(bridge, port, args.baud, stop)
                args.port = None   # 断开后回到自动发现
    except KeyboardInterrupt:
        print("\n[退出] 收到 Ctrl+C")
    finally:
        bridge.close()


if __name__ == "__main__":
    main()
