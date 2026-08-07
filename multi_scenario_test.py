"""CSI 多场景测试：呼吸检测 + 跌倒模拟。

场景：
1. 呼吸检测：静坐 60 秒，检测呼吸率（次/分）
2. 跌倒模拟：站立静止 → 突然运动 → 静止恢复，检测尖峰

用法：cd waveguard; python multi_scenario_test.py
"""
import subprocess
import statistics
import time
import serial

from hw.csi_parser import parse_csi_line
from hw.signal_proc import (
    ActivityEstimator, BreathDetector, classify_breathing, intensity_class,
    ACTIVE_MIN, STILL_MAX, SPIKE_MIN,
)

PORT = 'COM8'
BAUD = 921600

PS_TTS = r'''
Add-Type -AssemblyName System.Speech
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
$s.Rate = 1
$s.Speak("__TEXT__")
'''


def speak(text):
    ps = PS_TTS.replace("__TEXT__", text)
    subprocess.run(
        ['powershell', '-NoProfile', '-Command', ps],
        capture_output=True, timeout=30
    )


def collect(ser, duration, activity, breath, label=""):
    """采集 duration 秒，同时运行活动强度和呼吸检测。"""
    intensities = []
    breath_rates = []
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
        breath.add_frame(frame.amp)
        fps_count += 1

        now = time.time()
        if now - win_start >= 1.0:
            win_start = now
            intensity = activity.flush()
            rate, consensus, locked = breath.detect()
            fps = fps_count / (now - fps_start) if now > fps_start else 0
            fps_count = 0
            fps_start = now

            if intensity is not None:
                cls = intensity_class(intensity)
                state = classify_breathing(rate)
                elapsed = now - t0
                br_str = f"{rate}次/分" if rate else "--"
                print(f"  [{label}] t={elapsed:5.1f}s 帧率{fps:5.1f}/s "
                      f"RSSI{last_rssi:>4} 强度{intensity:6.2f} [{cls}] "
                      f"呼吸{br_str:>7} {state or '--':<8} 共识{locked:>2.0f}/32",
                      flush=True)
                intensities.append(intensity)
                if rate is not None:
                    breath_rates.append(rate)

    return intensities, breath_rates


def main():
    try:
        ser = serial.Serial(PORT, BAUD, timeout=1.0)
    except Exception as e:
        print(f"[错误] 无法打开 {PORT}：{e}")
        print("请确认：1) 已在查看器中断开串口  2) 板子已插入")
        return

    activity = ActivityEstimator()
    breath = BreathDetector()
    print(f"[多场景测试] {PORT} @ {BAUD}，请听语音指令", flush=True)

    # ========== 场景 1：呼吸检测 ==========
    print("\n" + "=" * 60)
    print("  场景 1：呼吸检测（静坐 60 秒）")
    print("=" * 60)

    speak("现在开始呼吸检测，请坐下，保持静止不动，持续一分钟")
    time.sleep(3)  # 缓冲

    print("[呼吸检测] 采集中...", flush=True)
    breath_intensities, breath_rates = collect(
        ser, 60, activity, breath, "呼吸"
    )

    # 呼吸检测报告
    print("\n--- 呼吸检测报告 ---")
    if breath_intensities:
        avg_int = statistics.mean(breath_intensities)
        print(f"  平均活动强度: {avg_int:.3f}")
        if avg_int < STILL_MAX:
            print(f"  ✅ 静止基线正常（<{STILL_MAX}）")
        else:
            print(f"  ⚠️ 静止基线偏高（≥{STILL_MAX}），可能有运动干扰")

    if breath_rates:
        avg_rate = statistics.mean(breath_rates)
        unique_rates = sorted(set(breath_rates))
        print(f"  检测到呼吸率: {len(breath_rates)} 次有效检测")
        print(f"  平均呼吸率: {avg_rate:.1f} 次/分")
        print(f"  呼吸率分布: {unique_rates}")
        state = classify_breathing(avg_rate)
        print(f"  呼吸状态: {state}")
        if 12 <= avg_rate <= 20:
            print(f"  ✅ 呼吸率正常（12-20 次/分）")
        elif avg_rate > 20:
            print(f"  ⚠️ 呼吸偏快（>20 次/分）")
        elif avg_rate >= 8:
            print(f"  ⚠️ 呼吸偏慢（<12 次/分）")
        else:
            print(f"  ❌ 呼吸异常（<8 次/分）")
    else:
        print("  ❌ 未检测到有效呼吸率")
        print("  可能原因：环境干扰/空调/距离过远")

    # ========== 场景 2：跌倒模拟 ==========
    print("\n" + "=" * 60)
    print("  场景 2：跌倒模拟（静止→突然运动→静止）")
    print("=" * 60)

    speak("现在开始跌倒模拟测试")
    time.sleep(3)

    # 阶段 2a：静止准备（10 秒）
    print("[跌倒] 静止准备期...", flush=True)
    speak("请保持静止")
    pre_intensities, _ = collect(ser, 10, activity, breath, "静止前")

    # 阶段 2b：突然运动（8 秒）
    print("[跌倒] 运动爆发期...", flush=True)
    speak("现在突然做出大幅度动作")
    spike_intensities, _ = collect(ser, 8, activity, breath, "运动")

    # 阶段 2c：恢复静止（10 秒）
    print("[跌倒] 恢复静止期...", flush=True)
    speak("停，恢复静止")
    post_intensities, _ = collect(ser, 10, activity, breath, "静止后")

    # 跌倒检测报告
    print("\n--- 跌倒模拟报告 ---")
    pre_avg = statistics.mean(pre_intensities) if pre_intensities else 0
    spike_avg = statistics.mean(spike_intensities) if spike_intensities else 0
    spike_max = max(spike_intensities) if spike_intensities else 0
    post_avg = statistics.mean(post_intensities) if post_intensities else 0

    print(f"  静止前强度: {pre_avg:.3f}")
    print(f"  运动强度: {spike_avg:.3f}（峰值 {spike_max:.3f}）")
    print(f"  静止后强度: {post_avg:.3f}")

    if spike_max >= SPIKE_MIN:
        print(f"  ✅ 检测到尖峰（≥{SPIKE_MIN}），跌倒可触发告警")
    elif spike_avg >= ACTIVE_MIN:
        print(f"  ⚠️ 运动响应明显，但未达尖峰阈值（{SPIKE_MIN}）")
    else:
        print(f"  ❌ 运动响应不足")

    if post_avg < STILL_MAX:
        print(f"  ✅ 恢复静止正常")
    else:
        print(f"  ⚠️ 恢复期强度偏高（{post_avg:.3f}）")

    # ========== 总结 ==========
    speak("全部测试结束")
    ser.close()

    print("\n" + "=" * 60)
    print("           多场景测试总结")
    print("=" * 60)
    br_ok = breath_rates and 12 <= statistics.mean(breath_rates) <= 20
    fall_ok = spike_max >= SPIKE_MIN
    print(f"  呼吸检测: {'✅ 正常' if br_ok else '⚠️ 需关注'}")
    print(f"  跌倒模拟: {'✅ 尖峰可检测' if fall_ok else '⚠️ 需关注'}")
    print("=" * 60)


if __name__ == '__main__':
    main()
