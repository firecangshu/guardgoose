"""场景剧本播放器（模拟轨发动机）。

读取剧本 JSON，把每个 segment 展开成逐秒活动强度样本，按 sim 时间戳
以 speed 倍速推送到边缘服务 /ingest/sample。边缘状态机据此判定事件。

用法：
    python -m replay.player                       # 默认剧本 day_fall
    python -m replay.player --scenario day_fall --speed 10
    python -m replay.player --url http://127.0.0.1:8000
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import time
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

# Windows 控制台默认 GBK，强制 UTF-8 以打印中文与符号
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

SCEN_DIR = Path(__file__).resolve().parent / "scenarios"
BASE_TIME = datetime.now().astimezone().replace(hour=8, minute=0, second=0, microsecond=0)


def _intensity(pattern: str, low: float, high: float) -> float:
    """按段落形态生成一个样本强度值。"""
    return round(random.uniform(low, high), 3)


def _breathing(br_low: int, br_high: int, br_state: str = "") -> tuple[int, str]:
    """按段落呼吸参数生成一个呼吸样本（频率+状态）。"""
    rate = random.randint(br_low, br_high)
    if br_state:
        return rate, br_state
    # 自动分类
    if rate < 3:
        return rate, "lost"
    if rate < 8:
        return rate, "shallow"
    if rate < 12:
        return rate, "irregular"
    if rate > 20:
        return rate, "elevated"
    return rate, "normal"


def _post(url: str, payload: dict) -> None:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            resp.read()
    except Exception as e:  # 网络抖动不打断整场演示
        print(f"  ! 推送失败: {e}")


def _set_real(base_url: str, enabled: bool) -> None:
    """样本走真实轨注入：播放前自动开真实接入，结束后还原关闭。"""
    data = json.dumps({"enabled": enabled}).encode("utf-8")
    req = urllib.request.Request(base_url.rstrip("/") + "/api/source-mode/real",
                                 data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            resp.read()
    except Exception as e:
        print(f"  ! 切换真实接入失败: {e}")


def play(scenario_file: str, base_url: str, speed_override: float | None) -> None:
    path = SCEN_DIR / scenario_file
    if not path.suffix:
        path = path.with_suffix(".json")
    scen = json.loads(path.read_text(encoding="utf-8"))

    zone = scen.get("zone", "living")
    speed = speed_override or scen.get("speed", 10)
    ingest_url = base_url.rstrip("/") + "/ingest/sample"

    print(f"▶ 剧本：{scen['name']}   分区：{zone}   加速：{speed}x")
    print(f"  推送到 {ingest_url}\n")

    _set_real(base_url, True)   # 闸门：样本注入需真实接入开启（会自动关闭演示）
    sim_t = 0.0
    try:
        for seg in scen["segments"]:
            dur = int(seg["duration_s"])
            br_low = seg.get("br_low", 14)
            br_high = seg.get("br_high", 18)
            br_state = seg.get("br_state", "")
            print(f"  ├─ [{int(sim_t):>4}s] {seg['label']}  ({seg['pattern']}, {dur}s, 呼吸{br_low}-{br_high})")
            for _ in range(dur):
                ts = (BASE_TIME + timedelta(seconds=sim_t)).isoformat(timespec="seconds")
                intensity = _intensity(seg["pattern"], seg["low"], seg["high"])
                br_rate, br_st = _breathing(br_low, br_high, br_state)
                _post(ingest_url, {"ts": ts, "sim_t": sim_t,
                                   "intensity": intensity, "zone": zone,
                                   "breathing_rate": br_rate, "breathing_state": br_st})
                sim_t += 1
                time.sleep(1.0 / speed)
        print(f"\n✔ 剧本播放完毕，共 {int(sim_t)} sim-秒。")
    finally:
        _set_real(base_url, False)   # 还原：关闭真实接入，回到待机


def main() -> None:
    ap = argparse.ArgumentParser(description="护院鹅场景剧本播放器")
    ap.add_argument("--scenario", default="day_fall", help="剧本文件名（scenarios/ 下）")
    ap.add_argument("--url", default="http://127.0.0.1:8000", help="边缘服务地址")
    ap.add_argument("--speed", type=float, default=None, help="覆盖剧本加速倍数")
    args = ap.parse_args()
    play(args.scenario, args.url, args.speed)


if __name__ == "__main__":
    main()
