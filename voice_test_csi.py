"""CSI 探测器语音引导测试：TTS 发"动/停"指令，自动采集并分析。

用法：cd waveguard; python voice_test_csi.py
脚本会语音指挥：准备 → 走动 → 静止 → 走动 → 静止，自动对比各阶段强度。
"""
import subprocess
import statistics
import time
import serial

from hw.csi_parser import parse_csi_line
from hw.signal_proc import ActivityEstimator, intensity_class

PORT = 'COM8'
BAUD = 921600

# PowerShell TTS 脚本模板
PS_TTS = r'''
Add-Type -AssemblyName System.Speech
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
$s.Rate = 1
$s.Speak("__TEXT__")
'''


def speak(text):
    """用 Windows TTS 发语音指令（阻塞，等说完再返回）。"""
    ps = PS_TTS.replace("__TEXT__", text)
    subprocess.run(
        ['powershell', '-NoProfile', '-Command', ps],
        capture_output=True, timeout=10
    )


def collect(ser, duration, activity, label=""):
    """采集 duration 秒，返回每秒强度列表。每秒实时打印一行。"""
    intensities = []
    win_start = time.time()
    t0 = time.time()
    fps_count = 0
    fps_start = time.time()
    last_rssi = 0

    while time.time() - t0 < duration:
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
                elapsed = now - t0
                print(f"  [{label}] t={elapsed:4.1f}s 帧率{fps:5.1f}/s "
                      f"RSSI{last_rssi:>4} 强度{intensity:6.2f} [{cls}]",
                      flush=True)
                intensities.append(intensity)
    return intensities


def stats(name, data):
    if data:
        print(f"  {name}: 均值 {statistics.mean(data):.3f}，"
              f"最大 {max(data):.3f}，最小 {min(data):.3f}，样本 {len(data)}s")
    else:
        print(f"  {name}: 无数据")


def main():
    try:
        ser = serial.Serial(PORT, BAUD, timeout=1.0)
    except Exception as e:
        print(f"[错误] 无法打开 {PORT}：{e}")
        print("请确认：1) 已在查看器中断开串口  2) 板子已插入")
        return

    activity = ActivityEstimator()
    print(f"[语音测试] {PORT} @ {BAUD}，请听语音指令做动作", flush=True)

    # 阶段 0：准备（5 秒预热，让基线稳定）
    speak("测试即将开始，请站到探测区域，保持静止")
    print("[阶段0] 准备期 5 秒...", flush=True)
    collect(ser, 5, activity, "准备")

    # 阶段 1：走动（10 秒）
    speak("现在开始走动，在发射板和接收板之间来回走动")
    print("[阶段1] 走动期 10 秒...", flush=True)
    walk1 = collect(ser, 10, activity, "走动1")

    # 阶段 2：静止（10 秒）
    speak("停，请保持静止不动")
    print("[阶段2] 静止期 10 秒...", flush=True)
    still1 = collect(ser, 10, activity, "静止1")

    # 阶段 3：再次走动（10 秒）
    speak("再次走动")
    print("[阶段3] 走动期 10 秒...", flush=True)
    walk2 = collect(ser, 10, activity, "走动2")

    # 阶段 4：再次静止（10 秒）
    speak("停，保持静止")
    print("[阶段4] 静止期 10 秒...", flush=True)
    still2 = collect(ser, 10, activity, "静止2")

    # 结束
    speak("测试结束，感谢配合")
    ser.close()

    # 汇总报告
    print("\n" + "=" * 50)
    print("           CSI 探测器测试报告")
    print("=" * 50)
    stats("走动1", walk1)
    stats("静止1", still1)
    stats("走动2", walk2)
    stats("静止2", still2)

    walk_all = walk1 + walk2
    still_all = still1 + still2
    if walk_all and still_all:
        walk_avg = statistics.mean(walk_all)
        still_avg = statistics.mean(still_all)
        print(f"\n  走动平均强度: {walk_avg:.3f}")
        print(f"  静止平均强度: {still_avg:.3f}")
        ratio = walk_avg / still_avg if still_avg > 0 else float('inf')
        print(f"  动态范围: {ratio:.1f} 倍")
        print(f"  帧率: 正常" if len(walk_all + still_all) >= 8 else "  帧率: 偏低")

        if walk_avg > 0.25 and still_avg < 0.15:
            print("\n  ✅ 探测器正常：走动响应明显，静止趋平")
        elif walk_avg > 0.15:
            print("\n  ⚠️ 走动有响应，但静止基线偏高（可能有干扰源）")
        else:
            print("\n  ❌ 走动响应不足，请检查板子位置/距离")
    else:
        print("\n  ⚠️ 数据不足，无法判定")
    print("=" * 50)


if __name__ == '__main__':
    main()
