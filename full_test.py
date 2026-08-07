"""CSI 探测器综合测试（数据调优版）：

测试场景：
  T3a 静止中跌倒（Type A）
  T3b 移动中跌倒（Type B）
  T3c 转换中跌倒（Type C）
  T4  憋气对照
  T5  存在检测（离开/进入）
  T6  重复走动统计（×5轮）
  T8  快速呼吸切换

流程：
  1. 窗口启动 → 等待你在查看器中连接串口
  2. 检测到 COM8 被占用 → 语音播报"感知到串口连接" + 显示确认按钮
  3. 你点击确认 → 语音说"请在查看器中断开串口"
  4. 你断开查看器串口 → 脚本接管 COM8 → 按顺序执行所有测试
  5. 结束后输出综合报告 + 保存 JSON

用法：cd waveguard; python full_test.py
"""
import json
import subprocess
import statistics
import threading
import time
from datetime import datetime
from pathlib import Path

import serial
import tkinter as tk

from hw.csi_parser import parse_csi_line
from hw.signal_proc import ActivityEstimator, intensity_class

PORT = 'COM8'
BAUD = 921600
RESULT_DIR = Path(__file__).parent / 'test_results'

PS_TTS = r'''
Add-Type -AssemblyName System.Speech
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
$s.Rate = 1
$s.Speak("__TEXT__")
'''

# ---- 测试定义 ----
# 每个测试：{ "id", "name", "phases": [(阶段名, 语音, 采集秒, 缓冲秒)] }
TESTS = [
    {
        "id": "T3a",
        "name": "静止中跌倒（Type A）",
        "voice_intro": "第一项，静止中跌倒测试。请先站立静止，听到跌倒指令后迅速蹲下",
        "phases": [
            ("静止前", "请站立保持静止", 10, 3),
            ("跌倒动作", "现在模拟跌倒，迅速蹲下", 8, 1),
            ("跌倒后静止", "保持蹲下不动", 10, 1),
        ],
    },
    {
        "id": "T3b",
        "name": "移动中跌倒（Type B）",
        "voice_intro": "第二项，移动中跌倒测试。请先匀速走动，听到跌倒指令后迅速停下蹲下",
        "phases": [
            ("匀速走动", "请匀速来回走动", 10, 3),
            ("跌倒动作", "现在模拟跌倒，迅速停下蹲下", 8, 1),
            ("跌倒后静止", "保持蹲下不动", 10, 1),
        ],
    },
    {
        "id": "T3c",
        "name": "转换中跌倒（Type C）",
        "voice_intro": "第三项，转换中跌倒测试。请先坐下静止，然后缓慢起身，再突然蹲下",
        "phases": [
            ("静坐", "请坐下保持静止", 10, 3),
            ("缓慢起身", "请缓慢站起来", 5, 1),
            ("跌倒动作", "现在模拟跌倒，迅速蹲下", 8, 0),
            ("跌倒后静止", "保持蹲下不动", 10, 1),
        ],
    },
    {
        "id": "T4",
        "name": "憋气对照测试",
        "voice_intro": "第四项，憋气对照。先正常呼吸，然后憋气，最后恢复",
        "phases": [
            ("正常呼吸", "请保持静止，正常呼吸", 30, 3),
            ("憋气", "现在请憋气，保持不动", 15, 1),
            ("恢复呼吸", "恢复呼吸，继续静止", 15, 1),
        ],
    },
    {
        "id": "T5",
        "name": "存在检测（离开/进入）",
        "voice_intro": "第五项，存在检测。先静止，然后走出探测范围，再走回来",
        "phases": [
            ("静止确认", "请站立静止", 10, 3),
            ("离开范围", "现在请走出探测范围", 15, 1),
            ("返回范围", "请走回探测范围", 10, 1),
        ],
    },
    {
        "id": "T6",
        "name": "重复走动统计（5轮）",
        "voice_intro": "第六项，重复走动统计。将进行五轮走动和静止交替",
        "phases": [
            ("走动1", "请走动", 10, 2),
            ("静止1", "停，静止", 10, 2),
            ("走动2", "再走动", 10, 2),
            ("静止2", "停，静止", 10, 2),
            ("走动3", "再走动", 10, 2),
            ("静止3", "停，静止", 10, 2),
            ("走动4", "再走动", 10, 2),
            ("静止4", "停，静止", 10, 2),
            ("走动5", "最后一轮走动", 10, 2),
            ("静止5", "停，静止", 10, 2),
        ],
    },
    {
        "id": "T8",
        "name": "快速呼吸切换",
        "voice_intro": "第八项，快速呼吸测试。先正常呼吸，然后快速浅呼吸，最后恢复正常",
        "phases": [
            ("正常呼吸", "请保持静止，正常呼吸", 20, 3),
            ("快速呼吸", "现在请快速浅呼吸，像运动后喘气", 20, 1),
            ("恢复正常", "恢复正常呼吸", 20, 1),
        ],
    },
]


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
            0x80000000 | 0x40000000,  # GENERIC_READ | GENERIC_WRITE
            0, None, 3, 0, None       # 独占, OPEN_EXISTING
        )
        if handle == -1 or handle == 0xFFFFFFFF:
            err = ctypes.get_last_error()
            return err == 5  # ACCESS_DENIED = 被占用
        else:
            kernel32.CloseHandle(handle)
            return False
    except Exception:
        return False


def collect_phase(ser, activity, dur, label, on_second, running_flag):
    """采集 dur 秒，每秒回调 on_second(elapsed, intensity, fps, rssi)。"""
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
        fps_count += 1

        now = time.time()
        if now - win_start >= 1.0:
            win_start = now
            intensity = activity.flush()
            fps = fps_count / (now - fps_start) if now > fps_start else 0
            fps_count = 0
            fps_start = now

            if intensity is not None:
                phase_data.append(intensity)
                elapsed = now - t0
                on_second(elapsed, intensity, fps, last_rssi)

    return phase_data


class FullTestApp:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("CSI 探测器综合测试（数据调优）")
        self.root.geometry("960x640")
        self.root.configure(bg='#1a1a2e')

        # 顶部状态
        self.phase_label = tk.Label(
            self.root, text="初始化中...", font=('微软雅黑', 18, 'bold'),
            fg='#00ff88', bg='#1a1a2e', pady=8
        )
        self.phase_label.pack(fill='x')

        # 测试进度
        self.test_progress = tk.Label(
            self.root, text="", font=('微软雅黑', 12),
            fg='#8888cc', bg='#1a1a2e'
        )
        self.test_progress.pack(fill='x')

        # 信息栏
        info_frame = tk.Frame(self.root, bg='#1a1a2e')
        info_frame.pack(fill='x', padx=20)
        self.info_label = tk.Label(
            info_frame, text="", font=('Consolas', 13),
            fg='#aaaaff', bg='#1a1a2e'
        )
        self.info_label.pack(side='left')
        self.time_label = tk.Label(
            info_frame, text="", font=('Consolas', 13),
            fg='#ffaa44', bg='#1a1a2e'
        )
        self.time_label.pack(side='right')

        # 确认按钮
        self.confirm_btn = tk.Button(
            self.root, text="确认串口已连接",
            font=('微软雅黑', 15, 'bold'),
            fg='#ffffff', bg='#00aa55', activebackground='#00cc66',
            relief='flat', padx=30, pady=8, cursor='hand2',
            command=self._on_confirm
        )
        self.confirm_visible = False
        self.user_confirmed = threading.Event()

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
        self.running = False
        self.all_results = {}  # {test_id: {phase_name: [intensities]}}
        self._max_bars = 120

    def _show_confirm_btn(self):
        if not self.confirm_visible:
            self.confirm_btn.pack(pady=5)
            self.confirm_visible = True

    def _hide_confirm_btn(self):
        if self.confirm_visible:
            self.confirm_btn.pack_forget()
            self.confirm_visible = False

    def _on_confirm(self):
        self.user_confirmed.set()
        self._hide_confirm_btn()

    def _redraw(self):
        w = self.canvas.winfo_width()
        h = self.canvas.winfo_height()
        self.canvas.delete('all')
        if w < 10 or h < 10:
            return

        # 阈值参考线
        thresholds = [(0.08, '#44ff88', 'STILL_MAX'), (0.25, '#ffaa44', 'ACTIVE_MIN'), (0.90, '#ff4444', 'SPIKE_MIN')]
        for val, color, name in thresholds:
            y = h * (1 - val) - 10
            self.canvas.create_line(0, y, w, y, fill=color, dash=(3, 6), width=1)
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
                    color = '#ff2222'  # 尖峰
                elif val >= 0.25:
                    color = '#ff6644'  # 活动
                elif val >= 0.08:
                    color = '#ffaa44'  # 微动
                else:
                    color = '#44ff88'  # 静止
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
        if pw > 10:
            total_phases = sum(len(t['phases']) for t in TESTS)
            done = sum(1 for t in self.all_results for _ in self.all_results[t])
            frac = done / total_phases if total_phases > 0 else 0
            fill_w = int(pw * frac)
            if fill_w > 0:
                self.progress_bar.create_rectangle(0, 0, fill_w, 8, fill='#00ff88', outline='')

    def _update_info(self):
        self.info_label.config(
            text=f"帧率:{self.fps_val:.0f}/s  RSSI:{self.rssi_val}dBm  "
                 f"强度:{self.intensity_val:.3f}  [{self.cls_val}]"
        )
        self.time_label.config(text=f"剩余:{self.time_left}s")
        self._redraw()

    def _handshake(self):
        """串口握手：等待连接 → 提示断开 → 接管。"""
        self.phase_label.config(text="请在 CSI 查看器中连接串口", fg='#ffaa44')
        self.info_label.config(text=f"正在监测 {PORT}...")
        self._redraw()

        if not is_port_busy():
            while self.running and not is_port_busy():
                time.sleep(0.5)

        if not self.running:
            return None

        # 感知到连接 → 直接提示断开
        self.phase_label.config(text="感知到串口连接至探测查看器", fg='#00ff88')
        self.info_label.config(text="请断开串口，我将连接到调优探测器")
        speak("感知到串口连接至探测查看器，请断开，我将连接到调优探测器")
        self._redraw()

        # 等待用户断开
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

        self.phase_label.config(text="调优探测器已接管，开始综合测试", fg='#00ff88')
        self.info_label.config(text=f"{PORT} @ {BAUD} 已接管")
        speak("调优探测器已接管，综合测试即将开始")
        self._redraw()
        time.sleep(2)
        return ser

    def _test_thread(self):
        ser = self._handshake()
        if ser is None:
            return

        activity = ActivityEstimator()
        total_tests = len(TESTS)

        for ti, test in enumerate(TESTS):
            if not self.running:
                break

            tid = test['id']
            tname = test['name']
            self.all_results[tid] = {}

            # 测试间休息
            if ti > 0:
                self.phase_label.config(text=f"休息一下，准备下一项...", fg='#8888cc')
                time.sleep(3)

            # 测试介绍
            self.phase_label.config(text=f"[{ti+1}/{total_tests}] {tname}", fg='#ffdd44')
            self.test_progress.config(text=f"测试 {tid}: {tname}")
            speak(test['voice_intro'])
            time.sleep(2)

            # 执行各阶段
            for pi, (pname, voice, dur, buf) in enumerate(test['phases']):
                if not self.running:
                    break

                self.phase_label.config(
                    text=f"[{tid}] {pname} - 准备...",
                    fg='#44aaff'
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

                # 采集
                self.phase_label.config(text=f"[{tid}] {pname} - 采集中...", fg='#00ff88')
                self.phase_marks.append((len(self.intensity_history), f"{tid}-{pname}"))

                def on_second(elapsed, intensity, fps, rssi):
                    self.intensity_history.append(intensity)
                    self.fps_val = fps
                    self.rssi_val = rssi
                    self.intensity_val = intensity
                    self.cls_val = intensity_class(intensity)
                    self.time_left = max(0, int(dur - elapsed))
                    self.root.after(0, self._update_info)

                data = collect_phase(
                    ser, activity, dur, pname,
                    on_second, lambda: self.running
                )
                self.all_results[tid][pname] = data

                # 阶段结束播报
                if data:
                    avg = statistics.mean(data)
                    peak = max(data)
                    speak(f"{pname}完成，平均{avg:.2f}，峰值{peak:.2f}")

        ser.close()
        if not self.running:
            return

        self.running = False
        speak("全部测试完成，感谢配合")
        self._generate_report()

    def _generate_report(self):
        """生成综合报告并保存 JSON。"""
        RESULT_DIR.mkdir(exist_ok=True)
        ts = datetime.now().strftime('%Y%m%d_%H%M%S')
        json_path = RESULT_DIR / f'full_test_{ts}.json'

        # 保存原始数据
        json_data = {
            "timestamp": ts,
            "port": PORT,
            "baud": BAUD,
            "results": self.all_results,
        }
        json_path.write_text(json.dumps(json_data, ensure_ascii=False, indent=2), encoding='utf-8')

        # 打印报告
        lines = ["=" * 60, "        CSI 探测器综合测试报告（数据调优）", "=" * 60]

        for test in TESTS:
            tid = test['id']
            if tid not in self.all_results:
                continue
            lines.append(f"\n{'─' * 40}")
            lines.append(f"  [{tid}] {test['name']}")
            lines.append(f"{'─' * 40}")
            for pname, data in self.all_results[tid].items():
                if data:
                    lines.append(
                        f"    {pname}: 均值={statistics.mean(data):.3f}  "
                        f"峰值={max(data):.3f}  最低={min(data):.3f}  样本={len(data)}s"
                    )
                else:
                    lines.append(f"    {pname}: 无数据")

        # 专项分析
        lines.append(f"\n{'=' * 60}")
        lines.append("  专项分析")
        lines.append(f"{'=' * 60}")

        # T3a/T3b/T3c 跌倒分析
        for tid in ['T3a', 'T3b', 'T3c']:
            if tid in self.all_results:
                phases = self.all_results[tid]
                spike_phase = phases.get('跌倒动作', [])
                before = None
                for key in ['静止前', '匀速走动', '静坐']:
                    if key in phases:
                        before = phases[key]
                        break
                after = phases.get('跌倒后静止', [])

                if spike_phase:
                    spike_peak = max(spike_phase)
                    spike_avg = statistics.mean(spike_phase)
                    lines.append(f"\n  [{tid}] 跌倒尖峰: 峰值={spike_peak:.3f}  均值={spike_avg:.3f}")
                    if before:
                        before_avg = statistics.mean(before)
                        lines.append(f"         跌倒前基线: {before_avg:.3f}")
                        delta = spike_peak - before_avg
                        lines.append(f"         尖峰增量: {delta:.3f}")
                    if spike_peak >= 0.90:
                        lines.append(f"         ✅ 达到 SPIKE_MIN=0.90")
                    else:
                        lines.append(f"         ⚠️ 未达 SPIKE_MIN=0.90，需考虑调整")

        # T6 重复走动统计
        if 'T6' in self.all_results:
            walk_all, still_all = [], []
            for pname, data in self.all_results['T6'].items():
                if '走动' in pname:
                    walk_all.extend(data)
                elif '静止' in pname:
                    still_all.extend(data)
            if walk_all and still_all:
                lines.append(f"\n  [T6] 重复走动统计:")
                lines.append(f"    走动: 均值={statistics.mean(walk_all):.3f}  "
                             f"标准差={statistics.stdev(walk_all):.3f}  "
                             f"范围=[{min(walk_all):.3f}, {max(walk_all):.3f}]")
                lines.append(f"    静止: 均值={statistics.mean(still_all):.3f}  "
                             f"标准差={statistics.stdev(still_all):.3f}  "
                             f"范围=[{min(still_all):.3f}, {max(still_all):.3f}]")
                ratio = statistics.mean(walk_all) / statistics.mean(still_all) if statistics.mean(still_all) > 0 else 0
                lines.append(f"    动态范围: {ratio:.1f} 倍")

                # 阈值建议
                still_max_suggest = min(0.08, statistics.mean(still_all) * 2)
                active_min_suggest = max(0.25, statistics.mean(walk_all) * 0.6)
                lines.append(f"    建议 STILL_MAX ≤ {still_max_suggest:.2f}")
                lines.append(f"    建议 ACTIVE_MIN ≥ {active_min_suggest:.2f}")

        # T5 存在检测
        if 'T5' in self.all_results:
            phases = self.all_results['T5']
            leave = phases.get('离开范围', [])
            back = phases.get('返回范围', [])
            if leave:
                absent_seconds = sum(1 for v in leave if v < 0.01)
                lines.append(f"\n  [T5] 存在检测:")
                lines.append(f"    离开后 absent(<0.01) 占比: {absent_seconds}/{len(leave)}s")
            if back:
                active_seconds = sum(1 for v in back if v > 0.25)
                lines.append(f"    返回后 active(>0.25) 占比: {active_seconds}/{len(back)}s")

        # T4 憋气
        if 'T4' in self.all_results:
            phases = self.all_results['T4']
            for pname in ['正常呼吸', '憋气', '恢复呼吸']:
                if pname in phases and phases[pname]:
                    lines.append(f"\n  [T4] {pname}: 均值={statistics.mean(phases[pname]):.3f}  "
                                 f"范围=[{min(phases[pname]):.3f}, {max(phases[pname]):.3f}]")

        lines.append(f"\n{'=' * 60}")
        lines.append(f"  数据已保存: {json_path}")
        lines.append(f"{'=' * 60}")

        report = "\n".join(lines)
        print(report)

        # 显示在窗口
        self.phase_label.config(text="测试完成 - 报告已输出", fg='#00ff88')
        self.test_progress.config(text=f"数据已保存: {json_path.name}")

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
    app = FullTestApp()
    try:
        app.run()
    except KeyboardInterrupt:
        app.stop()
