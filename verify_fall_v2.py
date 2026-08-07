"""跌倒三分类 v2 算法回归验证（常驻工具）。

用法：cd waveguard; python verify_fall_v2.py

用 20260806 实测数据特征模拟：
- Type A：静止基线 0.012 → 峰值 0.71 → 静止
- Type B：活动 0.4 → 骤然静止（无尖峰，实测峰值仅 0.021）
- Type C：静止 → 起身微动 0.10~0.13 + 发力瞬间 0.16 → 骤然静止
- 误报对照：走动后坐下休息（15秒内恢复活动，应不告警）
"""
from edge.state_machine import SampleProcessor


def run_sequence(seq, breathing_rate=16):
    """seq: [(intensity, 持续秒数), ...]，返回触发的跌倒类事件。"""
    sp = SampleProcessor()
    sp.present = True  # 白盒验证：直接假定有人，避免存在建立阶段污染模式检测
    events = []
    t = 0
    for intensity, dur in seq:
        for _ in range(dur):
            events += sp.process("ts", float(t), intensity, breathing_rate=breathing_rate)
            t += 1
    fall_types = ("fall_breathing_ok", "fall_breathing_bad", "breathing_lost",
                  "suspected_fall")
    return [e for e in events if e.type in fall_types]


def main():
    results = []

    # Type A：静止 30s → 尖峰 0.71 → 静止 15s（实测 F1 特征）
    evts = run_sequence([(0.012, 30), (0.71, 1), (0.01, 15)])
    results.append(("Type A 静止跌倒(远场静止确认)", evts, "A", "fall_breathing_ok"))

    # Type A 近场滞留：静止 → 尖峰 0.475 → 蹲倒后信号不回落 0.13~0.22（闭环实测 F1_R2）
    evts = run_sequence([(0.005, 14), (0.475, 1), (0.14, 3), (0.21, 2), (0.13, 6)])
    results.append(("Type A 近场滞留(蹲倒信号不回落)", evts, "A", "fall_breathing_ok"))

    # Type B：活动 10s → 骤然静止 20s（实测 F2 特征，无尖峰）
    # 呼吸正常 → 疑似跌倒（黄区关注）；呼吸异常 → 升级告警
    evts = run_sequence([(0.40, 10), (0.01, 20)])
    results.append(("Type B 移动跌倒(呼吸正常)", evts, "B", "suspected_fall"))

    evts = run_sequence([(0.40, 10), (0.01, 20)], breathing_rate=24)
    results.append(("Type B 移动跌倒(呼吸加快)", evts, "B", "fall_breathing_bad"))

    # Type C：静止 20s → 起身微动 4s → 发力瞬间 0.16 → 骤然静止 15s（闭环实测 F3_R2 特征）
    evts = run_sequence([(0.001, 20), (0.103, 1), (0.015, 1), (0.13, 2),
                         (0.161, 1), (0.001, 15)])
    results.append(("Type C 转换跌倒(呼吸正常)", evts, "C", "suspected_fall"))

    # 误报对照：走动后坐下 10s 又起身（未满 Type B 确认 15s，应不告警）
    evts = run_sequence([(0.40, 10), (0.01, 10), (0.35, 10)])
    results.append(("误报对照 走动后短暂坐下又起身", evts, None, None))

    # 误报对照：静止→起步走动→持续走动（正常活动，应不告警）
    evts = run_sequence([(0.012, 20), (0.35, 30)])
    results.append(("误报对照 起步走动", evts, None, None))

    # 误报对照：尖峰后继续走路离开（强度不衰减，非倒地，应不告警）
    evts = run_sequence([(0.012, 20), (0.45, 1), (0.35, 15)])
    results.append(("误报对照 尖峰后走开", evts, None, None))

    print("=" * 60)
    ok = True
    for name, evts, expect_type, expect_evt in results:
        if expect_type is None:
            if evts:
                ok = False
                print(f"[误报❌] {name}: 触发了 {evts[0].type}")
            else:
                print(f"[通过✅] {name}: 无跌倒事件")
        else:
            matched = [e for e in evts
                       if e.features.get("fall_type") == expect_type
                       and e.type == expect_evt]
            if matched:
                e = matched[0]
                print(f"[通过✅] {name}: {e.type} Type {expect_type} "
                      f"conf={e.confidence} zone={e.guard_zone}")
            else:
                ok = False
                print(f"[漏报❌] {name}: 未检测到 {expect_evt}/Type {expect_type}"
                      f"（实际: {[e.type for e in evts]}）")
    print("=" * 60)
    print("全部通过 ✅" if ok else "存在问题 ❌")


if __name__ == "__main__":
    main()
