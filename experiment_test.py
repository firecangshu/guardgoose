"""CSI 探测器实验方法论测试（控制变量 + 对照 + 重复）：

两大类实验：
  跌倒模拟：F1(Type A×3) F2(Type B×3) F3(Type C×3) F4(空调对照×3)
  呼吸模拟：B1(基线×2) B2(憋气×2) B3(快呼吸×2) B4(空调对照×1)

闭环验证模式（--closed-loop）：
  只跑 F1-F3 跌倒实验，逐秒样本同步灌入边缘状态机（SampleProcessor v2），
  验证新算法能否真实触发跌倒告警/疑似事件。

用法：cd waveguard; python experiment_test.py [--closed-loop]
"""
import json
import subprocess
import statistics
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

import serial
import tkinter as tk

from hw.csi_parser import parse_csi_line
from hw.signal_proc import (
    ActivityEstimator, intensity_class,
    BreathDetector, classify_breathing,
)

# 闭环验证：接入边缘状态机 v2（跌倒三分类）
sys.path.insert(0, str(Path(__file__).resolve().parent))
from edge.state_machine import SampleProcessor

CLOSED_LOOP = '--closed-loop' in sys.argv
FALL_EVENT_TYPES = ("fall_breathing_ok", "fall_breathing_bad",
                    "breathing_lost", "suspected_fall")

PORT = 'COM8'
BAUD = 921600
RESULT_DIR = Path(__file__).parent / 'test_results'

PS_TTS = r'''
Add-Type -AssemblyName System.Speech
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
$s.Rate = 1
$s.Speak("__TEXT__")
'''

# ============================================================
# 实验定义
# ============================================================
# 跌倒模拟实验
# 每个 phase: (名称, 语音, 采集秒, 缓冲秒)
# 每轮结束后有固定恢复期（RECOVERY_S），让人起身休息
FALL_EXPERIMENTS = {
    "F1": {
        "name": "Type A 静止中跌倒",
        "repeats": 3,
        "enter_voice": "请进入测试区域，站到标记位置，保持站立",
        "phases": [
            ("前态-静止", "记录开始。请保持站立静止，不要动", 15, 3),
            ("跌倒动作", "现在迅速蹲下，模拟跌倒", 8, 1),
            ("后态-静止", "保持蹲下不动", 10, 1),
        ],
        "exit_voice": "记录结束。请慢慢站起来，活动一下",
    },
    "F2": {
        "name": "Type B 移动中跌倒",
        "repeats": 3,
        "enter_voice": "请进入测试区域，准备来回走动",
        "phases": [
            ("前态-走动", "记录开始。请匀速来回走动", 15, 3),
            ("跌倒动作", "现在迅速停下蹲下，模拟跌倒", 8, 1),
            ("后态-静止", "保持蹲下不动", 10, 1),
        ],
        "exit_voice": "记录结束。请慢慢站起来，活动一下",
    },
    "F3": {
        "name": "Type C 转换中跌倒",
        "repeats": 3,
        "enter_voice": "请进入测试区域，坐到椅子上",
        "phases": [
            ("前态-静坐", "记录开始。请坐在椅子上保持静止", 10, 3),
            ("缓慢起身", "请缓慢站起来，像老人起身一样", 5, 1),
            ("跌倒动作", "现在迅速蹲下，模拟跌倒", 8, 0),
            ("后态-静止", "保持蹲下不动", 10, 1),
        ],
        "exit_voice": "记录结束。请慢慢站起来，活动一下",
    },
    "F4": {
        "name": "Type A 空调对照（关空调）",
        "repeats": 3,
        "enter_voice": "请进入测试区域，站到标记位置，保持站立",
        "phases": [
            ("前态-静止", "记录开始。请保持站立静止，不要动", 15, 3),
            ("跌倒动作", "现在迅速蹲下，模拟跌倒", 8, 1),
            ("后态-静止", "保持蹲下不动", 10, 1),
        ],
        "exit_voice": "记录结束。请慢慢站起来，活动一下",
    },
}

# 呼吸模拟实验
BREATH_EXPERIMENTS = {
    "B1": {
        "name": "正常呼吸基线",
        "repeats": 2,
        "enter_voice": "请坐到椅子上，放松身体",
        "phases": [
            ("正常呼吸", "记录开始。请放松，正常呼吸，不要刻意控制", 60, 5),
        ],
        "exit_voice": "记录结束。可以活动一下",
    },
    "B2": {
        "name": "憋气对照",
        "repeats": 2,
        "enter_voice": "请坐到椅子上，放松身体",
        "phases": [
            ("正常呼吸", "记录开始。请放松，正常呼吸", 30, 5),
            ("憋气", "现在请憋气，保持身体不动。不舒服就提前恢复", 15, 1),
            ("恢复呼吸", "恢复呼吸，继续坐着不动", 15, 1),
        ],
        "exit_voice": "记录结束。可以活动一下",
    },
    "B3": {
        "name": "快速呼吸",
        "repeats": 2,
        "enter_voice": "请坐到椅子上，放松身体",
        "phases": [
            ("正常呼吸", "记录开始。请放松，正常呼吸", 20, 5),
            ("快速呼吸", "现在快速浅呼吸，像运动后喘气", 20, 1),
            ("恢复正常", "恢复正常呼吸，放松", 20, 1),
        ],
        "exit_voice": "记录结束。可以活动一下",
    },
    "B4": {
        "name": "呼吸空调对照（关空调）",
        "repeats": 1,
        "enter_voice": "请坐到椅子上，放松身体",
        "phases": [
            ("正常呼吸", "记录开始。请放松，正常呼吸", 30, 5),
        ],
        "exit_voice": "记录结束。可以活动一下",
    },
}

# 每轮之间的恢复时间（秒），让人起身、活动、准备
RECOVERY_S = 8


def speak(text):
    ps = PS_TTS.replace("__TEXT__", text)
    try:
        subprocess.run(
            ['powershell', '-NoProfile', '-Command', ps],
            capture_output=True, timeout=30
        )
    except subprocess.TimeoutExpired:
        pass


def is_port_busy():
    """非侵入式检测 COM8 是否被占用。"""
    try:
        import ctypes
        kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
        handle = kernel32.CreateFileW(
            rf'\\.\{PORT}',
            0x80000000 | 0x40000000,
            0, None, 3, 0, None
        )
        if handle == -1 or handle == 0xFFFFFFFF:
            return ctypes.get_last_error() == 5
        else:
            kernel32.CloseHandle(handle)
            return False
    except Exception:
        return False


def collect_phase(ser, activity, breath, dur, on_second, running_flag,
                  processor=None, sim_t_ref=None, events_out=None):
    """采集 dur 秒，同时运行活动强度和呼吸检测。

    闭环模式：processor 不为 None 时，逐秒样本同步灌入边缘状态机，
    跌倒类事件追加到 events_out。"""
    phase_data = []
    win_start = time.time()
    t0 = time.time()
    fps_count = 0
    fps_start = time.time()
    last_rssi = 0

    while time.time() - t0 < dur and running_flag():
        raw = ser.readline()
        if not raw:
            continue
        line = raw.decode('utf-8', errors='replace')
        frame = parse_csi_line(line)
        if frame is None:
            continue
        last_rssi = frame.rssi
        activity.add_frame(frame.amp)
        breath.add_frame(frame.amp)
        fps_count += 1

        now = time.time()
        if now - win_start >= 1.0:
            win_start = now
            intensity = activity.flush()
            br_rate, br_consensus, br_locked = breath.detect()
            br_state = classify_breathing(br_rate) if br_rate else ""
            fps = fps_count / (now - fps_start) if now > fps_start else 0
            fps_count = 0
            fps_start = now

            if intensity is not None:
                # 闭环：同步灌入状态机
                if processor is not None and sim_t_ref is not None:
                    sim_t_ref[0] += 1
                    evts = processor.process(
                        datetime.now().isoformat(timespec='seconds'),
                        sim_t_ref[0], intensity,
                        breathing_rate=br_rate or 0,
                        breathing_state=br_state)
                    if events_out is not None:
                        for e in evts:
                            if e.type in FALL_EVENT_TYPES:
                                events_out.append({
                                    "sim_t": sim_t_ref[0], "type": e.type,
                                    "fall_type": e.features.get("fall_type"),
                                    "confidence": e.confidence,
                                    "zone": e.guard_zone,
                                    "context": e.features.get("context", ""),
                                })

                phase_data.append({
                    "t": round(now - t0, 1),
                    "intensity": round(intensity, 4),
                    "fps": round(fps, 1),
                    "rssi": last_rssi,
                    "breath_rate": br_rate,
                    "breath_consensus": round(br_consensus, 2) if br_consensus else None,
                    "breath_locked": br_locked,
                    "breath_state": br_state,
                })
                elapsed = now - t0
                on_second(elapsed, intensity, fps, last_rssi,
                          br_rate, br_consensus, br_state)

    return phase_data


class ExperimentApp:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("CSI 实验方法论测试（控制变量+对照+重复）")
        self.root.geometry("960x640")
        self.root.configure(bg='#1a1a2e')

        # 顶部状态
        self.phase_label = tk.Label(
            self.root, text="初始化中...", font=('微软雅黑', 18, 'bold'),
            fg='#00ff88', bg='#1a1a2e', pady=8
        )
        self.phase_label.pack(fill='x')

        # 实验进度
        self.exp_progress = tk.Label(
            self.root, text="", font=('微软雅黑', 12),
            fg='#8888cc', bg='#1a1a2e'
        )
        self.exp_progress.pack(fill='x')

        # 信息栏
        info_frame = tk.Frame(self.root, bg='#1a1a2e')
        info_frame.pack(fill='x', padx=20)
        self.info_label = tk.Label(
            info_frame, text="", font=('Consolas', 12),
            fg='#aaaaff', bg='#1a1a2e'
        )
        self.info_label.pack(side='left')
        self.breath_label = tk.Label(
            info_frame, text="", font=('Consolas', 12),
            fg='#44ddff', bg='#1a1a2e'
        )
        self.breath_label.pack(side='right')

        # 曲线画布
        self.canvas = tk.Canvas(self.root, bg='#0f0f23', highlightthickness=0)
        self.canvas.pack(fill='both', expand=True, padx=10, pady=8)
        self.canvas.bind('<Configure>', lambda e: self._redraw())

        # 底部进度
        self.progress_bar = tk.Canvas(self.root, height=8, bg='#333355', highlightthickness=0)
        self.progress_bar.pack(fill='x', padx=10, pady=(0, 10))

        # 数据
        self.intensity_history = []
        self.phase_marks = []
        self.time_left = 0
        self.fps_val = 0
        self.rssi_val = 0
        self.intensity_val = 0
        self.cls_val = "absent"
        self.br_rate_val = None
        self.br_consensus_val = None
        self.br_state_val = ""
        self.running = False
        self.all_results = {}
        self._max_bars = 120

    def _redraw(self):
        w = self.canvas.winfo_width()
        h = self.canvas.winfo_height()
        self.canvas.delete('all')
        if w < 10 or h < 10:
            return

        # 阈值参考线（20260806 实验校准：0.05/0.15/0.90）
        for val, color, name in [(0.05, '#44ff88', 'STILL'), (0.15, '#ffaa44', 'ACTIVE'), (0.90, '#ff4444', 'SPIKE')]:
            y = h * (1 - val) - 10
            self.canvas.create_line(0, y, w, y, fill=color, dash=(3, 6))
            self.canvas.create_text(w - 5, y, anchor='e', text=f"{name}={val}", fill=color, font=('Consolas', 8))

        # 柱状图
        data = self.intensity_history[-self._max_bars:]
        if data:
            bar_w = max((w - 20) / len(data), 2)
            x0 = 10
            for i, val in enumerate(data):
                bh = val * (h - 30)
                x = x0 + i * bar_w
                y_top = h - 15 - bh
                if val >= 0.90:
                    color = '#ff2222'
                elif val >= 0.15:
                    color = '#ff6644'
                elif val >= 0.05:
                    color = '#ffaa44'
                else:
                    color = '#44ff88'
                self.canvas.create_rectangle(x, y_top, x + bar_w - 1, h - 15, fill=color, outline='')

            offset = len(self.intensity_history) - len(data)
            for idx, pname in self.phase_marks:
                adj = idx - offset
                if 0 <= adj < len(data):
                    x = x0 + adj * bar_w
                    self.canvas.create_line(x, 5, x, h - 15, fill='#6666aa', dash=(3, 3))
                    self.canvas.create_text(x + 2, 12, anchor='w', text=pname, fill='#8888cc', font=('微软雅黑', 8))

        # 进度条
        pw = self.progress_bar.winfo_width()
        self.progress_bar.delete('all')
        if pw > 10 and self.all_results:
            total_exp = len(FALL_EXPERIMENTS) + len(BREATH_EXPERIMENTS)
            done_exp = len(set(k.rsplit('_R', 1)[0] for k in self.all_results))
            frac = done_exp / total_exp if total_exp > 0 else 0
            fill_w = int(pw * min(frac, 1.0))
            if fill_w > 0:
                self.progress_bar.create_rectangle(0, 0, fill_w, 8, fill='#00ff88', outline='')

    def _update_info(self):
        self.info_label.config(
            text=f"帧率:{self.fps_val:.0f}/s  RSSI:{self.rssi_val}dBm  "
                 f"强度:{self.intensity_val:.3f}[{self.cls_val}]  剩余:{self.time_left}s"
        )
        if self.br_rate_val is not None:
            self.breath_label.config(
                text=f"呼吸:{self.br_rate_val}次/分  共识:{self.br_consensus_val}  [{self.br_state_val}]"
            )
        else:
            self.breath_label.config(text="呼吸:未锁定")
        self._redraw()

    def _handshake(self):
        """串口握手。"""
        self.phase_label.config(text="请在 CSI 查看器中连接串口", fg='#ffaa44')
        self.info_label.config(text=f"正在监测 {PORT}...")
        self._redraw()

        if not is_port_busy():
            while self.running and not is_port_busy():
                time.sleep(0.5)
        if not self.running:
            return None

        self.phase_label.config(text="感知到串口连接至探测查看器", fg='#00ff88')
        self.info_label.config(text="请断开串口，我将连接到实验探测器")
        speak("感知到串口连接至探测查看器，请断开，我将连接到实验探测器")
        self._redraw()

        while self.running and is_port_busy():
            time.sleep(0.5)
        if not self.running:
            return None

        time.sleep(1)
        try:
            ser = serial.Serial(PORT, BAUD, timeout=1.0)
        except Exception as e:
            self.phase_label.config(text=f"接管失败: {e}", fg='#ff4444')
            return None

        self.phase_label.config(text="实验探测器已接管", fg='#00ff88')
        speak("实验探测器已接管，实验即将开始")
        self._redraw()
        time.sleep(2)
        return ser

    def _run_experiment(self, ser, exp_id, exp_def, repeat_idx):
        """执行一组实验的一个重复（含进入/退出/恢复）。"""
        activity = ActivityEstimator()
        breath = BreathDetector()
        key = f"{exp_id}_R{repeat_idx}"
        self.all_results[key] = {}

        # 闭环模式：每轮独立状态机，直接假定有人（人已按语音引导就位）
        processor = None
        sim_t_ref = None
        events_out = None
        if CLOSED_LOOP:
            processor = SampleProcessor()
            processor.present = True
            sim_t_ref = [0.0]
            events_out = []
            self.all_results[key]["_events"] = events_out

        exp_name = exp_def['name']
        self.exp_progress.config(text=f"实验 {exp_id} 第{repeat_idx}轮: {exp_name}")

        # ---- 进入测试区域 ----
        self.phase_label.config(text=f"[{exp_id} R{repeat_idx}] 请进入测试区域", fg='#ffdd44')
        speak(exp_def['enter_voice'])
        time.sleep(3)  # 给人走到位的时间

        # ---- 执行各采集阶段 ----
        for pi, (pname, voice, dur, buf) in enumerate(exp_def['phases']):
            if not self.running:
                return

            self.phase_label.config(
                text=f"[{exp_id} R{repeat_idx}] {pname}",
                fg='#00ff88'
            )
            speak(voice)

            # 缓冲
            if buf > 0:
                buf_start = time.time()
                while time.time() - buf_start < buf and self.running:
                    raw = ser.readline()
                    if raw:
                        frame = parse_csi_line(raw.decode('utf-8', errors='replace'))
                        if frame:
                            activity.add_frame(frame.amp)
                            breath.add_frame(frame.amp)

            # 采集
            self.phase_marks.append((len(self.intensity_history), f"{exp_id}-{pname}"))

            def on_second(elapsed, intensity, fps, rssi, br_rate, br_cons, br_state):
                self.intensity_history.append(intensity)
                self.fps_val = fps
                self.rssi_val = rssi
                self.intensity_val = intensity
                self.cls_val = intensity_class(intensity)
                self.br_rate_val = br_rate
                self.br_consensus_val = br_cons
                self.br_state_val = br_state
                self.time_left = max(0, int(dur - elapsed))
                self.root.after(0, self._update_info)

            data = collect_phase(
                ser, activity, breath, dur,
                on_second, lambda: self.running,
                processor=processor, sim_t_ref=sim_t_ref,
                events_out=events_out
            )
            self.all_results[key][pname] = data

        # ---- 记录结束，起身恢复 ----
        self.phase_label.config(text=f"[{exp_id} R{repeat_idx}] 记录结束，请起身休息", fg='#ffdd44')
        speak(exp_def['exit_voice'])
        # 恢复期：不采集数据，让人自由活动
        time.sleep(RECOVERY_S)

    def _test_thread(self):
        ser = self._handshake()
        if ser is None:
            return

        # ---- 闭环验证模式：只跑 F1-F3 跌倒实验 ----
        if CLOSED_LOOP:
            speak("闭环验证模式：验证新跌倒算法能否真实触发告警")
            time.sleep(2)
            for exp_id in ['F1', 'F2', 'F3']:
                exp = FALL_EXPERIMENTS[exp_id]
                for r in range(1, exp['repeats'] + 1):
                    if not self.running:
                        break
                    speak(f"{exp['name']}，第{r}轮，共{exp['repeats']}轮")
                    time.sleep(1)
                    self._run_experiment(ser, exp_id, exp, r)
            ser.close()
            if not self.running:
                return
            self.running = False
            speak("闭环验证完成，感谢配合")
            self._generate_report()
            return

        # ---- 第一组：开空调 ----
        speak("第一组实验，空调开启状态")
        time.sleep(2)

        # 跌倒实验 F1-F3
        for exp_id in ['F1', 'F2', 'F3']:
            exp = FALL_EXPERIMENTS[exp_id]
            for r in range(1, exp['repeats'] + 1):
                if not self.running:
                    break
                speak(f"{exp['name']}，第{r}轮，共{exp['repeats']}轮")
                time.sleep(1)
                self._run_experiment(ser, exp_id, exp, r)

        # 呼吸实验 B1-B3
        for exp_id in ['B1', 'B2', 'B3']:
            exp = BREATH_EXPERIMENTS[exp_id]
            for r in range(1, exp['repeats'] + 1):
                if not self.running:
                    break
                speak(f"{exp['name']}，第{r}轮")
                time.sleep(1)
                self._run_experiment(ser, exp_id, exp, r)

        # ---- 第二组：关空调 ----
        if self.running:
            self.phase_label.config(text="请关闭空调，等待3分钟后继续", fg='#ffdd44')
            speak("第一组实验完成。现在请关闭空调，等待三分钟后，我们将进行空调对照实验")
            self.root.after(0, self._show_wait)
            time.sleep(180)  # 等3分钟让空调完全停止
            self._hide_wait()

            # F4 空调对照
            exp = FALL_EXPERIMENTS['F4']
            for r in range(1, exp['repeats'] + 1):
                if not self.running:
                    break
                speak(f"空调对照跌倒测试，第{r}轮")
                time.sleep(1)
                self._run_experiment(ser, 'F4', exp, r)

            # B4 呼吸空调对照
            exp = BREATH_EXPERIMENTS['B4']
            speak("空调对照呼吸测试")
            time.sleep(1)
            self._run_experiment(ser, 'B4', exp, 1)

        ser.close()
        if not self.running:
            return

        self.running = False
        speak("全部实验完成，感谢配合")
        self._generate_report()

    def _show_wait(self):
        self.info_label.config(text="等待空调完全停止... 180秒")

    def _hide_wait(self):
        self.info_label.config(text="")

    def _generate_report(self):
        """生成实验报告。"""
        RESULT_DIR.mkdir(exist_ok=True)
        ts = datetime.now().strftime('%Y%m%d_%H%M%S')
        json_path = RESULT_DIR / f'experiment_{ts}.json'

        json_data = {
            "timestamp": ts,
            "port": PORT,
            "baud": BAUD,
            "results": self.all_results,
        }
        json_path.write_text(json.dumps(json_data, ensure_ascii=False, indent=2), encoding='utf-8')

        # 打印报告
        lines = ["=" * 65, "    CSI 探测器实验方法论报告（控制变量+对照+重复）", "=" * 65]

        # ---- 跌倒实验汇总 ----
        lines.append(f"\n{'━' * 65}")
        lines.append("  跌倒模拟实验汇总")
        lines.append(f"{'━' * 65}")

        for exp_id in ['F1', 'F2', 'F3', 'F4']:
            exp_name = FALL_EXPERIMENTS[exp_id]['name']
            repeats = FALL_EXPERIMENTS[exp_id]['repeats']
            lines.append(f"\n  [{exp_id}] {exp_name}")

            all_peaks = []
            all_baselines = []
            all_deltas = []

            for r in range(1, repeats + 1):
                key = f"{exp_id}_R{r}"
                if key not in self.all_results:
                    continue
                phases = self.all_results[key]

                # 找跌倒动作阶段
                spike_data = phases.get('跌倒动作', [])
                if not spike_data:
                    continue

                intensities = [d['intensity'] for d in spike_data]
                peak = max(intensities)
                avg = statistics.mean(intensities)
                all_peaks.append(peak)

                # 找前态基线
                baseline_key = None
                for bk in ['前态-静止', '前态-走动', '前态-静坐']:
                    if bk in phases:
                        baseline_key = bk
                        break
                if baseline_key and phases[baseline_key]:
                    baseline = statistics.mean([d['intensity'] for d in phases[baseline_key]])
                    all_baselines.append(baseline)
                    delta = peak - baseline
                    all_deltas.append(delta)
                    lines.append(f"    R{r}: 峰值={peak:.3f}  基线={baseline:.3f}  增量={delta:+.3f}  均值={avg:.3f}")
                else:
                    lines.append(f"    R{r}: 峰值={peak:.3f}  均值={avg:.3f}")

            if all_peaks:
                lines.append(f"    ── 汇总: 峰值均值={statistics.mean(all_peaks):.3f}  "
                             f"峰值范围=[{min(all_peaks):.3f}, {max(all_peaks):.3f}]")
                if all_baselines:
                    lines.append(f"             基线均值={statistics.mean(all_baselines):.3f}  "
                                 f"增量均值={statistics.mean(all_deltas):+.3f}")

        # ---- 闭环验证：状态机告警触发情况 ----
        if CLOSED_LOOP:
            lines.append(f"\n{'━' * 65}")
            lines.append("  闭环验证：边缘状态机 v2 告警触发情况")
            lines.append(f"{'━' * 65}")
            expect = {"F1": ("fall_breathing_ok", "A"),
                      "F2": ("suspected_fall", "B"),
                      "F3": ("suspected_fall", "C")}
            for exp_id in ['F1', 'F2', 'F3']:
                exp_evt, exp_ft = expect[exp_id]
                hit = 0
                total = 0
                for r in range(1, FALL_EXPERIMENTS[exp_id]['repeats'] + 1):
                    key = f"{exp_id}_R{r}"
                    events = self.all_results.get(key, {}).get("_events", [])
                    if key in self.all_results:
                        total += 1
                    matched = [e for e in events if e["type"] == exp_evt
                               and e["fall_type"] == exp_ft]
                    if matched:
                        hit += 1
                    lines.append(f"  [{key}] 事件数={len(events)}  "
                                 f"命中预期({exp_evt}/{exp_ft})={'✅' if matched else '❌'}")
                    for e in events[:4]:
                        lines.append(f"      t={e['sim_t']}s {e['type']} "
                                     f"Type{e['fall_type']} conf={e['confidence']} "
                                     f"zone={e['zone']}")
                if total:
                    lines.append(f"  ── {exp_id} 触发率: {hit}/{total} ({hit / total:.0%})")

        # ---- F1 vs F4 空调对比 ----
        f1_peaks, f4_peaks = [], []
        for r in range(1, 4):
            for exp_id, plist in [('F1', f1_peaks), ('F4', f4_peaks)]:
                key = f"{exp_id}_R{r}"
                if key in self.all_results and '跌倒动作' in self.all_results[key]:
                    data = self.all_results[key]['跌倒动作']
                    if data:
                        plist.append(max(d['intensity'] for d in data))

        if f1_peaks and f4_peaks:
            lines.append(f"\n  [空调对比] F1(开)峰值均值={statistics.mean(f1_peaks):.3f}  "
                         f"F4(关)峰值均值={statistics.mean(f4_peaks):.3f}  "
                         f"差异={statistics.mean(f4_peaks) - statistics.mean(f1_peaks):+.3f}")

        # ---- 呼吸实验汇总 ----
        lines.append(f"\n{'━' * 65}")
        lines.append("  呼吸模拟实验汇总")
        lines.append(f"{'━' * 65}")

        for exp_id in ['B1', 'B2', 'B3', 'B4']:
            exp_name = BREATH_EXPERIMENTS[exp_id]['name']
            repeats = BREATH_EXPERIMENTS[exp_id]['repeats']
            lines.append(f"\n  [{exp_id}] {exp_name}")

            for r in range(1, repeats + 1):
                key = f"{exp_id}_R{r}"
                if key not in self.all_results:
                    continue
                phases = self.all_results[key]
                for pname, data in phases.items():
                    if not data or pname.startswith('_'):
                        continue
                    intensities = [d['intensity'] for d in data]
                    br_rates = [d['breath_rate'] for d in data if d['breath_rate'] is not None]
                    br_cons = [d['breath_consensus'] for d in data if d['breath_consensus'] is not None]

                    line = f"    R{r} {pname}: 强度均值={statistics.mean(intensities):.3f}"
                    if br_rates:
                        line += f"  呼吸率={statistics.mean(br_rates):.0f}次/分"
                    if br_cons:
                        line += f"  共识={statistics.mean(br_cons):.2f}"
                    lines.append(line)

        # ---- B1 vs B4 空调对比 ----
        b1_int, b4_int = [], []
        for key, plist in [('B1_R1', b1_int), ('B1_R2', b1_int), ('B4_R1', b4_int)]:
            if key in self.all_results:
                for pname, data in self.all_results[key].items():
                    plist.extend([d['intensity'] for d in data])
        if b1_int and b4_int:
            lines.append(f"\n  [呼吸空调对比] B1(开)强度均值={statistics.mean(b1_int):.3f}  "
                         f"B4(关)强度均值={statistics.mean(b4_int):.3f}  "
                         f"差异={statistics.mean(b4_int) - statistics.mean(b1_int):+.3f}")

        lines.append(f"\n{'=' * 65}")
        lines.append(f"  数据已保存: {json_path}")
        lines.append(f"{'=' * 65}")

        report = "\n".join(lines)
        print(report)

        self.phase_label.config(text="实验完成 - 报告已输出", fg='#00ff88')
        self.exp_progress.config(text=f"数据已保存: {json_path.name}")

    def run(self):
        self.running = True
        threading.Thread(target=self._test_thread, daemon=True).start()
        def _tick():
            if self.running:
                self._redraw()
                self.root.after(200, _tick)
        self.root.after(200, _tick)
        self.root.mainloop()

    def stop(self):
        self.running = False


if __name__ == '__main__':
    app = ExperimentApp()
    try:
        app.run()
    except KeyboardInterrupt:
        app.stop()
