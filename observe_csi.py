"""CSI 探测器观测器：直接读 COM8 串口，实时分析信号波动。

用法：cd waveguard; python observe_csi.py
观测期间请做走动（强度应升高）/静止（强度应趋近0）动作。
"""
import time
import serial

from hw.csi_parser import parse_csi_line
from hw.signal_proc import ActivityEstimator, intensity_class

PORT = 'COM8'
BAUD = 921600
DURATION = 45  # 观测时长（秒）


def main():
    activity = ActivityEstimator()
    win_start = time.time()
    fps_count = 0
    fps_start = time.time()
    last_rssi = 0
    peak = 0.0
    trough = 999.0
    t0 = time.time()

    try:
        ser = serial.Serial(PORT, BAUD, timeout=1.0)
    except Exception as e:
        print(f"[错误] 无法打开 {PORT}：{e}")
        print("请确认：1) 已在查看器中断开串口  2) 板子已插入")
        return

    print(f"[观测] {PORT} @ {BAUD}，持续 {DURATION}s，请做走动/静止动作", flush=True)
    while time.time() - t0 < DURATION:
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
                peak = max(peak, intensity)
                trough = min(trough, intensity)
            cls = intensity_class(intensity)
            val = intensity if intensity is not None else -1
            print(f"t={now-t0:5.1f}s 帧率{fps:5.1f}/s RSSI{last_rssi:>4} "
                  f"强度{val:6.2f} [{cls}]", flush=True)
    ser.close()
    print(f"[观测完成] 峰值强度 {peak:.2f}，谷值强度 {trough:.2f}", flush=True)


if __name__ == '__main__':
    main()
