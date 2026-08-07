"""CSI 探测器语音引导测试（可视化版）：

流程：
1. 窗口启动 → 等待你在查看器中连接串口
2. 检测到 COM8 被占用 → 语音播报"感知到串口连接" + 显示确认按钮
3. 你点击确认 → 语音说"请在查看器中断开串口，我将接管"
4. 你断开查看器串口 → 脚本接管 COM8 → 语音播报"串口已接管，开始测试"
5. 语音引导走动/静止测试，实时柱状图显示

用法：cd waveguard; python visual_voice_test.py
"""
import subprocess
import statistics
import threading
import time
import serial
import tkinter as tk

from hw.csi_parser import parse_csi_line
from hw.signal_proc import ActivityEstimator, intensity_class

PORT = 'COM8'
BAUD = 921600

PS_TTS = r'''
Add-Type -AssemblyName System.Speech
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
$s.Rate = 1
$s.Speak("__TEXT__")
'''

# 测试阶段定义：(阶段名, 语音指令, 采集秒数, 缓冲秒数)
PHASES = [
    ("准备", "测试即将开始，请站到探测区域，保持静止", 5, 2),
    ("走动1", "现在开始走动，在发射板和接收板之间来回走动", 12, 3),
    ("静止1", "停，请保持静止不动", 12, 2),
    ("走动2", "再次走动", 12, 3),
    ("静止2", "停，保持静止", 12, 2),
]


def speak(text):
    ps = PS_TTS.replace("__TEXT__", text)
    subprocess.run(
        ['powershell', '-NoProfile', '-Command', ps],
        capture_output=True, timeout=10
    )


def is_port_busy():
    """检测 COM8 是否被占用（不实际打开端口，用 CreateFile 试探）。"""
    try:
        import ctypes
        from ctypes import wintypes
        kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
        GENERIC_READ = 0x80000000
        GENERIC_WRITE = 0x40000000
        OPEN_EXISTING = 3
        # 尝试以独占方式打开端口（不实际读写）
        handle = kernel32.CreateFileW(
            rf'\\.\{PORT}',
            GENERIC_READ | GENERIC_WRITE,
            0,  # 独占模式
            None,
            OPEN_EXISTING,
            0,
            None
        )
        if handle == -1 or handle == 0xFFFFFFFF:
            err = ctypes.get_last_error()
            # 错误码 5 = ACCESS_DENIED（端口被占用）
            # 错误码 2 = FILE_NOT_FOUND（端口不存在）
            return err == 5  # True = 被占用
        else:
            kernel32.CloseHandle(handle)
            return False  # 端口空闲
    except Exception:
        return False


class CSIMonitorApp:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("CSI 探测器语音引导测试")
        self.root.geometry("900x560")
        self.root.configure(bg='#1a1a2e')

        # 顶部状态栏
        self.phase_label = tk.Label(
            self.root, text="初始化中...", font=('微软雅黑', 20, 'bold'),
            fg='#00ff88', bg='#1a1a2e', pady=10
        )
        self.phase_label.pack(fill='x')

        # 中间信息栏
        info_frame = tk.Frame(self.root, bg='#1a1a2e')
        info_frame.pack(fill='x', padx=20)
        self.info_label = tk.Label(
            info_frame, text="",
            font=('Consolas', 14), fg='#aaaaff', bg='#1a1a2e'
        )
        self.info_label.pack(side='left')
        self.time_label = tk.Label(
            info_frame, text="",
            font=('Consolas', 14), fg='#ffaa44', bg='#1a1a2e'
        )
        self.time_label.pack(side='right')

        # 确认按钮（默认隐藏）
        self.confirm_btn = tk.Button(
            self.root, text="确认串口已连接",
            font=('微软雅黑', 16, 'bold'),
            fg='#ffffff', bg='#00aa55', activebackground='#00cc66',
            activeforeground='#ffffff', relief='flat',
            padx=30, pady=10, cursor='hand2',
            command=self._on_confirm
        )
        self.confirm_visible = False
        self.user_confirmed = threading.Event()

        # 曲线画布
        self.canvas = tk.Canvas(
            self.root, bg='#0f0f23', highlightthickness=0
        )
        self.canvas.pack(fill='both', expand=True, padx=10, pady=10)
        self.canvas.bind('<Configure>', self._on_resize)

        # 底部进度条
        self.progress_var = tk.DoubleVar()
        self.progress_bar = tk.Canvas(self.root, height=8, bg='#333355', highlightthickness=0)
        self.progress_bar.pack(fill='x', padx=10, pady=(0, 10))

        # 数据
        self.intensity_history = []
        self.phase_marks = []
        self.current_phase = "等待"
        self.time_left = 0
        self.fps_val = 0
        self.rssi_val = 0
        self.intensity_val = 0
        self.cls_val = "absent"
        self.running = False
        self.results = {}
        self._max_bars = 80

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

    def _on_resize(self, event):
        self._redraw()

    def _redraw(self):
        w = self.canvas.winfo_width()
        h = self.canvas.winfo_height()
        self.canvas.delete('all')

        if w < 10 or h < 10:
            self.root.after(100, self._redraw)
            return

        # 网格线
        for frac in [0.25, 0.5, 0.75]:
            y = h * (1 - frac)
            self.canvas.create_line(0, y, w, y, fill='#2a2a4e', dash=(2, 4))
            self.canvas.create_text(5, y, anchor='w',
                                    text=f"{frac:.0%}", fill='#555577', font=('Consolas', 9))

        # 柱状图
        data = self.intensity_history[-self._max_bars:]
        if data:
            bar_w = max((w - 20) / len(data), 3)
            x0 = 10
            for i, val in enumerate(data):
                bh = val * (h - 20)
                x = x0 + i * bar_w
                y_top = h - 10 - bh
                if val >= 0.25:
                    color = '#ff4444'
                elif val >= 0.08:
                    color = '#ffaa44'
                else:
                    color = '#44ff88'
                self.canvas.create_rectangle(x, y_top, x + bar_w - 1, h - 10,
                                             fill=color, outline='')

            offset = len(self.intensity_history) - len(data)
            for idx, pname in self.phase_marks:
                adj = idx - offset
                if 0 <= adj < len(data):
                    x = x0 + adj * bar_w
                    self.canvas.create_line(x, 5, x, h - 10, fill='#6666aa', dash=(3, 3))
                    self.canvas.create_text(x + 2, 12, anchor='w', text=pname,
                                             fill='#8888cc', font=('微软雅黑', 8))

        # 进度条
        pw = self.progress_bar.winfo_width()
        self.progress_bar.delete('all')
        frac = self.progress_var.get()
        fill_w = int(pw * frac) if pw > 10 else 0
        if fill_w > 0:
            self.progress_bar.create_rectangle(0, 0, fill_w, 8, fill='#00ff88', outline='')

    def _update_info(self):
        self.info_label.config(
            text=f"帧率: {self.fps_val:.1f}/s  RSSI: {self.rssi_val}dBm  "
                 f"强度: {self.intensity_val:.2f}  状态: {self.cls_val}"
        )
        self.time_label.config(text=f"剩余: {self.time_left}s")
        self._redraw()

    def _handshake_thread(self):
        """串口握手流程：感知连接 → 等确认 → 等断开 → 接管。"""

        # ---- 阶段 1：等待用户在查看器中连接串口 ----
        self.phase_label.config(
            text="请在 CSI 查看器中连接串口", fg='#ffaa44'
        )
        self.info_label.config(text=f"正在监测 {PORT} 状态...")
        self.time_label.config(text="")
        self._redraw()

        # 如果 COM8 已被占用，直接跳到感知阶段（用户已经连了）
        if not is_port_busy():
            # COM8 空闲 → 等待用户连接
            self.info_label.config(text=f"监测 {PORT} 中... 请在查看器连接串口")
            check_count = 0
            while self.running:
                if is_port_busy():
                    break
                check_count += 1
                if check_count % 10 == 0:
                    self.info_label.config(text=f"监测中... {PORT} 空闲，等待连接")
                time.sleep(0.5)

        if not self.running:
            return None

        # ---- 阶段 2：感知到串口连接 ----
        self.phase_label.config(text="感知到串口连接！", fg='#00ff88')
        self.info_label.config(text="请在查看器中确认看到 CSI 数据后，点击下方按钮")
        speak("感知到串口连接，请点击确认按钮")
        self.root.after(0, self._show_confirm_btn)

        # 等待用户点击确认
        while self.running and not self.user_confirmed.is_set():
            time.sleep(0.3)

        if not self.running:
            return None

        # ---- 阶段 3：收到确认，请用户断开查看器串口 ----
        self.phase_label.config(text="收到确认！请在查看器中断开串口", fg='#ffaa44')
        self.info_label.config(text="我将在你断开后接管 COM8")
        speak("收到确认，请在查看器中断开串口，我将接管串口进行测试")
        self._redraw()

        # 等待 COM8 从"被占用"变成"空闲" = 用户断开了
        wait_count = 0
        while self.running:
            if is_port_busy():
                # 还被占用，继续等
                wait_count += 1
                if wait_count % 10 == 0:
                    self.info_label.config(text=f"等待查看器断开... {PORT} 仍被占用")
                time.sleep(0.5)
            else:
                # 端口空闲了 = 用户断开了！
                break

        if not self.running:
            return None

        # ---- 阶段 4：接管串口 ----
        # 短暂等待确保端口完全释放
        time.sleep(1)
        try:
            ser = serial.Serial(PORT, BAUD, timeout=1.0)
        except Exception as e:
            self.phase_label.config(text=f"接管失败: {e}", fg='#ff4444')
            return None

        self.phase_label.config(text="串口已接管！准备开始测试", fg='#00ff88')
        self.info_label.config(text=f"{PORT} @ {BAUD} 已接管")
        self._redraw()
        speak("串口已接管，测试即将开始")
        time.sleep(2)

        return ser

    def _test_thread(self):
        # 串口握手
        ser = self._handshake_thread()
        if ser is None:
            return

        activity = ActivityEstimator()
        total_phases = len(PHASES)

        for pi, (pname, voice, dur, buf) in enumerate(PHASES):
            if not self.running:
                break

            self.current_phase = pname
            self.phase_label.config(
                text=f"[阶段 {pi+1}/{total_phases}] {pname}",
                fg='#00ff88' if '走动' in pname else '#44aaff'
            )

            speak(voice)

            # 缓冲期
            if buf > 0:
                self.phase_label.config(text=f"[阶段 {pi+1}] {pname} - 准备...")
                buf_start = time.time()
                while time.time() - buf_start < buf and self.running:
                    raw = ser.readline()
                    if raw:
                        line = raw.decode('utf-8', errors='replace')
                        frame = parse_csi_line(line)
                        if frame:
                            activity.add_frame(frame.amp)
                    self.time_left = int(buf - (time.time() - buf_start))
                    self.root.update_idletasks()

            # 采集期
            self.phase_label.config(text=f"[阶段 {pi+1}] {pname} - 采集中...")
            self.phase_marks.append((len(self.intensity_history), pname))
            phase_data = []
            win_start = time.time()
            t0 = time.time()
            fps_count = 0
            fps_start = time.time()
            last_rssi = 0

            while time.time() - t0 < dur and self.running:
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
                        cls = intensity_class(intensity)
                        self.intensity_history.append(intensity)
                        phase_data.append(intensity)
                        self.fps_val = fps
                        self.rssi_val = last_rssi
                        self.intensity_val = intensity
                        self.cls_val = cls
                        self.time_left = int(dur - (now - t0))
                        self.progress_var.set((pi + (now - t0) / dur) / total_phases)
                        self.root.after(0, self._update_info)

            self.results[pname] = phase_data

        ser.close()

        if not self.running:
            return

        self.running = False
        speak("测试结束，感谢配合")
        self._show_report()

    def _show_report(self):
        walk_all = []
        still_all = []
        for pname, data in self.results.items():
            if '走动' in pname:
                walk_all.extend(data)
            elif '静止' in pname:
                still_all.extend(data)

        report_lines = ["=" * 50, "           CSI 探测器测试报告", "=" * 50]
        for pname in self.results:
            data = self.results[pname]
            if data:
                report_lines.append(
                    f"  {pname}: 均值 {statistics.mean(data):.3f}，"
                    f"最大 {max(data):.3f}，最小 {min(data):.3f}，样本 {len(data)}s"
                )
            else:
                report_lines.append(f"  {pname}: 无数据")

        if walk_all and still_all:
            walk_avg = statistics.mean(walk_all)
            still_avg = statistics.mean(still_all)
            ratio = walk_avg / still_avg if still_avg > 0 else float('inf')
            report_lines.append("")
            report_lines.append(f"  走动平均强度: {walk_avg:.3f}")
            report_lines.append(f"  静止平均强度: {still_avg:.3f}")
            report_lines.append(f"  动态范围: {ratio:.1f} 倍")

            if walk_avg > 0.25 and still_avg < 0.15:
                report_lines.append("\n  ✅ 探测器正常：走动响应明显，静止趋平")
            elif walk_avg > 0.15:
                report_lines.append("\n  ⚠️ 走动有响应，但静止基线偏高（可能有干扰源）")
            else:
                report_lines.append("\n  ❌ 走动响应不足，请检查板子位置/距离")
        else:
            report_lines.append("\n  ⚠️ 数据不足，无法判定")
        report_lines.append("=" * 50)

        report = "\n".join(report_lines)
        print(report)

        self.phase_label.config(text="测试完成 - 见终端报告", fg='#00ff88')
        self.info_label.config(text=report.split('\n')[-3] if len(report_lines) > 5 else "完成")

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
    app = CSIMonitorApp()
    try:
        app.run()
    except KeyboardInterrupt:
        app.stop()
