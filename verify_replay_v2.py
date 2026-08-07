"""回放剧本离线回归（常驻工具）：12 个剧本灌入状态机，验证关键事件。

用法：cd waveguard; python verify_replay_v2.py"""
import json
import random
from pathlib import Path

from edge.state_machine import SampleProcessor

SCEN_DIR = Path("replay/scenarios")


def replay(path: Path) -> list:
    scen = json.loads(path.read_text(encoding="utf-8"))
    random.seed(42)  # 固定随机种子，结果可复现
    sp = SampleProcessor(zone=scen.get("zone", "living"))
    events = []
    sim_t = 0.0
    for seg in scen["segments"]:
        for _ in range(int(seg["duration_s"])):
            intensity = round(random.uniform(seg["low"], seg["high"]), 3)
            rate = random.randint(seg.get("br_low", 14), seg.get("br_high", 18))
            br_state = seg.get("br_state", "")
            events += sp.process("ts", sim_t, intensity, zone=scen.get("zone"),
                                 breathing_rate=rate, breathing_state=br_state)
            sim_t += 1
    return events


def main():
    for path in sorted(SCEN_DIR.glob("*.json")):
        events = replay(path)
        key = [e for e in events if e.type in (
            "fall_breathing_ok", "fall_breathing_bad", "breathing_lost",
            "breathing_abnormal", "suspected_fall",
            "intrusion_suspected", "intrusion_confirmed",
            "still_too_long", "presence_on", "presence_off")]
        print(f"\n### {path.stem}")
        if key:
            for e in key[:8]:
                ft = e.features.get("fall_type", "")
                ft_s = f" Type{ft}" if ft else ""
                print(f"  t? {e.type}{ft_s} conf={e.confidence} "
                      f"zone={e.guard_zone} | {e.features.get('context', e.features.get('note', ''))[:40]}")
        else:
            print("  （无关键事件）")


if __name__ == "__main__":
    main()
