"""噪声补偿（底噪动态估计）单元验证。

实测依据（20260806 实验 B1/B4）：
- 安静房间静止底噪 ≈ 0.002
- 开空调静止底噪 ≈ 0.034（高 17 倍），空调房静止样本最高 0.169
"""
import random
import sys

from hw.signal_proc import NoiseFloorEstimator, STILL_MAX, BR_SIGNIFICANCE_MIN
from edge import config as C

random.seed(42)
ok = True


def check(name, cond, detail=""):
    global ok
    print(f"  {'✅' if cond else '❌'} {name}" + (f"（{detail}）" if detail else ""))
    if not cond:
        ok = False


# ---- 用例 1：安静房间，底噪应停留在低值，不启用补偿 ----
print("用例1：安静房间（底噪≈0.002）")
est = NoiseFloorEstimator()
for _ in range(60):
    est.feed(max(0.0005, random.gauss(0.002, 0.0015)))
check("底噪估计 ≈ 0.002", 0.0005 < est.floor < 0.01, f"floor={est.floor:.4f}")
check("未判嘈杂", not est.noisy)
check("静止带保持固定值", est.still_threshold == STILL_MAX,
      f"th={est.still_threshold:.3f}")
check("显著度保持固定值", est.significance_min == BR_SIGNIFICANCE_MIN,
      f"sig={est.significance_min:.2f}")

# ---- 用例 2：开空调，底噪 0.034，应启用补偿 ----
print("用例2：开空调（底噪≈0.034，高 17 倍）")
est2 = NoiseFloorEstimator()
for _ in range(120):
    est2.feed(max(0.01, random.gauss(0.034, 0.02)))
check("底噪估计 ≈ 0.03 量级", 0.02 < est2.floor < 0.08, f"floor={est2.floor:.4f}")
check("判为嘈杂", est2.noisy)
check("静止带上抬（>固定值）", est2.still_threshold > STILL_MAX,
      f"th={est2.still_threshold:.3f} vs 固定 {STILL_MAX}")
check("空调房常态静止样本 0.06 不误判为活动",
      0.06 < est2.still_threshold, f"th={est2.still_threshold:.3f}")
# 如实说明：实测空调房瞬时噪声峰 0.169 会超出静止带（floor×3≈0.12），
# 但它是短促孤峰，跌倒/运动判定靠连续秒数，孤峰不影响结论（已在 config 标注局限）
check("显著度要求提高", est2.significance_min > BR_SIGNIFICANCE_MIN,
      f"sig={est2.significance_min:.2f} vs 固定 {BR_SIGNIFICANCE_MIN}")
check("显著度未超上限", est2.significance_min <= C.NOISE_SIGNIFICANCE_CAP)

# ---- 用例 3：活动样本不污染底噪统计 ----
print("用例3：活动样本排除")
est3 = NoiseFloorEstimator()
for _ in range(60):
    est3.feed(max(0.0005, random.gauss(0.002, 0.0015)))
for _ in range(30):
    est3.feed(0.5)   # 走动（> ACTIVE_CUT，应被排除）
check("走动样本未抬底噪", est3.floor < 0.01, f"floor={est3.floor:.4f}")

# ---- 用例 4：空调关机后底噪回落（自适应能收敛回去）----
print("用例4：空调开关切换收敛")
est4 = NoiseFloorEstimator()
for _ in range(120):
    est4.feed(max(0.01, random.gauss(0.034, 0.02)))
check("空调开：嘈杂", est4.noisy)
for _ in range(300):   # 关机后持续安静 5 分钟
    est4.feed(max(0.0005, random.gauss(0.002, 0.0015)))
check("关机 5 分钟后回落安静", not est4.noisy, f"floor={est4.floor:.4f}")

# ---- 用例 5：数据不足时不输出估计 ----
print("用例5：启动保护")
est5 = NoiseFloorEstimator()
for _ in range(5):
    est5.feed(0.03)
check("不足 10 秒不出估计", est5.floor == 0.0 and not est5.noisy)

print()
print("NOISE_VERIFY_PASS" if ok else "NOISE_VERIFY_FAIL")
sys.exit(0 if ok else 1)
