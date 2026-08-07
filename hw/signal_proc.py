"""信号处理：逐秒活动强度 + 呼吸锁频共识检测。

算法对齐硬件实测报告（07-硬件实测报告）：
- 活动强度：逐秒各子载波幅度标准差均值（实测静止基线 0.76 / 运动峰值 9.28）
  → 自适应基线归一化到 0~1，语义对齐 edge/config.py
    （静止≈0.02、正常活动 0.3~0.7、尖峰≥0.9）
- 呼吸检测：跨子载波「锁频共识」——静止呼吸时多条子载波主频锁定同一频率
  （实测前 5 条子载波锁频、sc149-167 聚簇），憋气时共识瓦解 → 硬判据。

仅依赖 numpy，纯 CPU 实时（~72 帧/秒 × 192 子载波）。
"""
from __future__ import annotations

from collections import deque

import numpy as np

from edge import config as C

# ---- 归一化强度语义（对齐 edge/config.py，20260806 实验校准）----
STILL_MAX = 0.05
ACTIVE_MIN = 0.15
SPIKE_MIN = 0.30

# ---- 呼吸分级阈值（次/分，对齐 edge/config.py 医学 OSINT 研究）----
BR_RATE_MAX = 20       # >20 = 加快（轻度缺氧）
BR_RATE_SLOW = 8       # <8 = 浅慢（重度缺氧）
BR_RATE_LOST = 3       # <3 = 呼吸消失

# 呼吸检测参数
BR_FREQ_MIN_HZ = 0.10        # 带通下沿（6 次/分）
BR_FREQ_MAX_HZ = 0.60        # 带通上沿（36 次/分）
BR_FREQ_TOL_HZ = 0.015       # 锁频容差（±0.9 次/分）
BR_CONSENSUS_MIN = 0.40      # 锁频子载波占比 ≥40% 才认为有效
BR_SIGNIFICANCE_MIN = 3.0    # 主峰幅度 ≥ 频谱均值 3 倍（实测显著度 5×）
BR_WINDOW_S = 20.0           # 呼吸分析窗长（秒）
BR_TOP_SUBCARRIERS = 32      # 只取能量最大的子载波做 FFT（降计算量）


class ActivityEstimator:
    """逐秒活动强度估计。

    raw_sd = 每秒窗口内「各子载波幅度时序标准差」的均值（实测报告定义：
    对每个子载波取窗口内时间序列标准差，再对所有子载波求平均）。
    静止时子载波幅度平稳（基线 ~0.76），运动时幅度随呼吸/肢体扰动波动（峰值 9.28）。

    归一化：raw 相对自适应基线的倍数映射到 0~1，
    intensity = clip((raw - base) / (k * base), 0, 1)，
    其中 base 取最近 base_seconds 秒 raw 的 15 分位（抗环境漂移），
    k 对应「运动峰值 ≈ k 倍基线」的标定倍数（实测 12.2:1，取 10 留裕量）。
    """

    def __init__(self, base_seconds: int = 60, k: float = 10.0):
        self.base_seconds = base_seconds
        self.k = k
        self._buf: list[np.ndarray] = []      # 当前窗口帧序列
        self._history: list[float] = []      # 已完成窗口的 raw 值

    def add_frame(self, amp: np.ndarray) -> None:
        """加入一帧（任意子载波数）。"""
        if amp.size >= 2:
            self._buf.append(amp)

    def flush(self) -> float | None:
        """一个窗口结束：计算 raw_sd 并归一化，返回 0~1 强度（无数据返回 None）。"""
        if not self._buf:
            return None
        width = max(f.size for f in self._buf)
        mat = np.zeros((len(self._buf), width), dtype=np.float64)
        for i, f in enumerate(self._buf):
            mat[i, : f.size] = f
        self._buf.clear()
        raw = float(mat.std(axis=0).mean())   # 每子载波时序 std → 均值
        self._history.append(raw)
        if len(self._history) > self.base_seconds:
            self._history = self._history[-self.base_seconds:]
        base = float(np.percentile(self._history, 15)) if self._history else raw
        base = max(base, 1e-6)
        return float(np.clip((raw - base) / (self.k * base), 0.0, 1.0))


class NoiseFloorEstimator:
    """环境底噪动态估计（空调噪声补偿的核心）。

    背景（20260806 实验）：开空调时静止底噪比关机高 17 倍
    （B1 0.034 vs B4 0.002，空调房静止样本最高 0.169），
    固定阈值会把环境噪声误判为人体微动。

    原理：居家场景多数时段无人/静止，滑动窗内强度的中低分位
    即环境底噪；排除明确活动样本（>ACTIVE_CUT）后取分位数，
    再经 EMA 平滑避免突变。估计值驱动三类补偿：
    - 静止判定带上抬：still_threshold = max(STILL_MAX, floor × STILL_FACTOR)
    - FFT 显著度要求随底噪增益（噪声抬频谱基底，真呼吸峰需更突出）
    - 呼吸消失防抖的「静止」条件同步用自适应静止带
    """

    def __init__(self, window_s: int = C.NOISE_FLOOR_WINDOW_S,
                 percentile: float = C.NOISE_FLOOR_PERCENTILE):
        self._buf: deque[float] = deque(maxlen=window_s)
        self._percentile = percentile
        self._floor = 0.0          # EMA 平滑后的底噪估计

    def feed(self, intensity: float | None) -> None:
        """喂入逐秒归一化强度（活动样本自动排除）。"""
        if intensity is None or intensity > C.NOISE_FLOOR_ACTIVE_CUT:
            return
        self._buf.append(float(intensity))
        if len(self._buf) >= 10:   # 至少 10 秒数据才出估计
            raw = float(np.percentile(sorted(self._buf),
                                       self._percentile * 100))
            # EMA：空调开关机过渡约 1~2 分钟内收敛
            alpha = 0.10
            self._floor = alpha * raw + (1 - alpha) * self._floor \
                if self._floor > 0 else raw

    @property
    def floor(self) -> float:
        """当前底噪估计（归一化强度尺度）。"""
        return self._floor

    @property
    def noisy(self) -> bool:
        """是否处于嘈杂环境（底噪 > NOISE_FLOOR_HIGH，如空调开）。"""
        return self._floor > C.NOISE_FLOOR_HIGH

    @property
    def still_threshold(self) -> float:
        """自适应静止判定带：嘈杂环境上抬，安静环境回落固定值。"""
        return max(STILL_MAX, self._floor * C.NOISE_STILL_FACTOR)

    @property
    def significance_min(self) -> float:
        """自适应 FFT 显著度要求：仅嘈杂环境启用补偿——底噪越高，
        噪声抬升频谱基底，真呼吸峰需更突出才算锁频；安静环境保持
        固定值。线性增益（floor/HIGH 比例，封顶 2 倍输入），
        封顶 CAP 避免永远锁不定。"""
        if not self.noisy:
            return BR_SIGNIFICANCE_MIN
        gain = 1.0 + C.NOISE_SIGNIFICANCE_GAIN * min(self._floor / max(C.NOISE_FLOOR_HIGH, 1e-6), 2.0)
        return min(BR_SIGNIFICANCE_MIN * gain, C.NOISE_SIGNIFICANCE_CAP)


class BreathDetector:
    """呼吸锁频共识检测。

    维护最近 W 秒的幅度矩阵，每秒末：
    1. 选窗口内方差最大的 BR_TOP_SUBCARRIERS 条子载波；
    2. 逐条去均值 + 汉宁窗 + FFT，在带通内取主频与峰强；
    3. 主频按容差分箱聚簇，最大簇占比 = 共识度；
    4. 共识度 ≥ 阈值 且 峰显著 → 呼吸率 = 簇频率 × 60，否则判为未锁定。
    """

    def __init__(self, window_s: float = BR_WINDOW_S,
                 fs: float = 72.0):
        self.window_s = window_s
        self.fs = fs
        self._frames: list[np.ndarray] = []

    def add_frame(self, amp: np.ndarray) -> None:
        self._frames.append(amp)
        max_frames = int(self.window_s * self.fs) + 16
        if len(self._frames) > max_frames:
            self._frames.pop(0)

    def detect(self, significance_min: float | None = None) -> tuple[float | None, float | None, float]:
        """返回 (呼吸率 次/分, 共识度 0~1, 锁定子载波数)。

        significance_min：自适应显著度门槛（None=用固定 BR_SIGNIFICANCE_MIN），
        由 NoiseFloorEstimator 根据环境底噪提供。
        """
        sig_min = significance_min if significance_min is not None else BR_SIGNIFICANCE_MIN
        n = len(self._frames)
        if n < int(self.window_s * self.fs * 0.5):
            return None, None, 0.0   # 数据不足半个窗口

        # 统一子载波数为 192（不足补零、超出截断）
        width = 192
        mat = np.zeros((n, width), dtype=np.float64)
        for i, f in enumerate(self._frames):
            m = min(f.size, width)
            mat[i, :m] = f[:m]

        # 选能量最大的子载波（窗口内方差）
        var = mat.var(axis=0)
        top = np.argsort(var)[-BR_TOP_SUBCARRIERS:]

        # 逐条 FFT
        freqs = np.fft.rfftfreq(n, d=1.0 / self.fs)
        band = (freqs >= BR_FREQ_MIN_HZ) & (freqs <= BR_FREQ_MAX_HZ)
        f_band = freqs[band]
        peaks: list[tuple[float, float]] = []   # (频率, 峰强/均值比)
        for sc in top:
            x = mat[:, sc] - mat[:, sc].mean()
            rms = float(np.sqrt((x ** 2).mean()))
            if rms < 1e-6:
                continue
            x = x * np.hanning(n)
            spec = np.abs(np.fft.rfft(x))
            sub = spec[band]
            if sub.size == 0 or sub.mean() <= 0:
                continue
            idx = int(np.argmax(sub))
            ratio = float(sub[idx] / sub.mean())
            peaks.append((float(f_band[idx]), ratio))

        if not peaks:
            return None, None, 0.0

        # 频率聚簇：容差内归并，取最大簇
        freqs_arr = np.array([p[0] for p in peaks], dtype=np.float64)
        ratios = np.array([p[1] for p in peaks], dtype=np.float64)
        order = np.argsort(freqs_arr)
        freqs_arr = freqs_arr[order]
        ratios = ratios[order]
        clusters: list[list[int]] = []
        for i in range(len(freqs_arr)):
            if clusters and freqs_arr[i] - freqs_arr[clusters[-1][0]] <= BR_FREQ_TOL_HZ:
                clusters[-1].append(i)
            else:
                clusters.append([i])
        best = max(clusters, key=len)
        consensus = len(best) / BR_TOP_SUBCARRIERS
        if consensus < BR_CONSENSUS_MIN:
            return None, None, float(consensus)
        mean_ratio = float(ratios[best].mean())
        if mean_ratio < sig_min:
            return None, None, float(consensus)
        rate = round(float(freqs_arr[best[0]]) * 60.0)
        return rate, float(consensus), float(len(best))


def classify_breathing(rate: float | None) -> str:
    """呼吸率 → 后端五级状态（对齐 edge/state_machine._classify_breathing）。"""
    if rate is None:
        return ""
    if rate < BR_RATE_LOST:
        return "lost"
    if rate < BR_RATE_SLOW:
        return "shallow"
    if rate < 12:
        return "irregular"
    if rate > BR_RATE_MAX:
        return "elevated"
    return "normal"


def intensity_class(intensity: float | None) -> str:
    """归一化强度 → 语义分档（调试/日志用）。"""
    if intensity is None:
        return "absent"
    if intensity >= SPIKE_MIN:
        return "spike"
    if intensity > ACTIVE_MIN:
        return "active"
    if intensity >= STILL_MAX:
        return "still"
    return "absent"
