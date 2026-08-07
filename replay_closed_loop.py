"""离线闭环回放：把真实实验 JSON 逐秒灌入状态机 v2，验证告警触发。

用法：cd waveguard; python replay_closed_loop.py [json文件路径]
默认取 test_results/ 下最新的 experiment_*.json
"""
import json
import sys
from pathlib import Path

# Windows 控制台默认 GBK，强制 UTF-8 以打印 ✅/❌ 等符号
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from edge.state_machine import SampleProcessor

FALL_EVENT_TYPES = ("fall_breathing_ok", "fall_breathing_bad",
                    "breathing_lost", "suspected_fall")
# 跌倒确认后按呼吸状态分叉：呼吸正常→ok/疑似，异常→bad，消失→lost，
# 三者都算「跌倒被检出」，命中判定不区分呼吸分叉
EXPECTED = {"F1": ("fall_breathing_ok", "A"),
            "F2": ("suspected_fall", "B"),
            "F3": ("suspected_fall", "C")}


def replay_round(phases: dict) -> list:
    sp = SampleProcessor()
    sp.present = True
    events = []
    sim_t = 0.0
    for pname, data in phases.items():
        if pname.startswith('_') or not isinstance(data, list):
            continue
        for s in data:
            if not isinstance(s, dict) or 'intensity' not in s:
                continue
            sim_t += 1
            # 严格性修正：breath_rate=None 表示「呼吸检测器未输出」，
            # 不能当 0 传（会被误判为呼吸消失），也不能谎报 normal（无据）→
            # 传 rate=12：状态机分类为 normal，语义是「无异常呼吸证据」，
            # 与真实 bridge 行为一致（检测器不输出时不报呼吸异常）
            br_rate = s.get('breath_rate')
            evts = sp.process(
                "ts", sim_t, s['intensity'],
                breathing_rate=br_rate if br_rate else 12,
                breathing_state=s.get('breath_state') or "")
            for e in evts:
                if e.type in FALL_EVENT_TYPES:
                    events.append((sim_t, e))
    return events


def main():
    if len(sys.argv) > 1:
        path = Path(sys.argv[1])
    else:
        files = sorted(Path('test_results').glob('experiment_*.json'))
        path = files[-1]
    print(f"回放文件: {path}\n")
    data = json.loads(path.read_text(encoding='utf-8'))

    for exp_id in ['F1', 'F2', 'F3']:
        exp_evt, exp_ft = EXPECTED[exp_id]
        hit = total = 0
        print(f"━━━ [{exp_id}] 预期: {exp_evt}/Type{exp_ft} ━━━")
        for r in range(1, 4):
            key = f"{exp_id}_R{r}"
            if key not in data['results']:
                continue
            total += 1
            events = replay_round(data['results'][key])
            # 命中 = 检出了对应类型的跌倒事件（不区分呼吸分叉去向）
            matched = [e for _, e in events
                       if e.features.get('fall_type') == exp_ft]
            if matched:
                hit += 1
            mark = '✅' if matched else '❌'
            print(f"  {key}: 事件数={len(events)} 命中={mark}")
            for t, e in events[:4]:
                print(f"      t={t:.0f}s {e.type} Type{e.features.get('fall_type')} "
                      f"conf={e.confidence} zone={e.guard_zone}")
        if total:
            print(f"  ── {exp_id} 触发率: {hit}/{total} ({hit / total:.0%})\n")


if __name__ == '__main__':
    main()
