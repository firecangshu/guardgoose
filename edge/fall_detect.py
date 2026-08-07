"""跌倒模式检测器 v2（20260806 实验方法论测试数据调优）。

实验结论驱动的设计：
- Type A 静止中跌倒：静止 → 相对尖峰 → 静止（实测峰值均值 0.710，信噪比最高）
  → 相对尖峰检测：峰值 ≥ max(SPIKE_MIN, 静止基线 × SPIKE_REL_RATIO)，抗环境漂移
- Type B 移动中跌倒：持续活动 → 骤然静止（实测无尖峰，峰值仅 0.021）
  → 自适应基线会跟踪运动，尖峰检测失效 → 改用「活动段→骤然静止」模式识别
- Type C 转换中跌倒：静止 → 缓慢起身(微动段) → 骤然静止（实测峰值仅 0.058）
  → 「静止→微动→骤然静止」三段式模式识别

检测器逐样本 push(sim_t, intensity)，产出跌倒候选（dict）：
    {"type": "A"/"B"/"C", "peak": float, "baseline": float, "pre_state": str}
确认逻辑（候选后静止持续多久→告警）由状态机负责，本模块只产出候选。
"""
from __future__ import annotations

from collections import deque

from . import config as C


class FallPatternDetector:
    """三分类跌倒候选检测器。

    维护两个结构：
    1. 滑动基线窗（baseline_window 秒）：取低强度值的 25 分位作为静止基线，
       供 Type A 相对尖峰判定使用（空调等环境漂移会整体抬高基线，相对判定更稳）。
    2. 连续段跟踪（run tracking）：把强度序列切成 still/mild/active 连续段，
       在段切换瞬间检查 B/C 两类跌倒模式。
    """

    def __init__(self, baseline_window: float = 60.0):
        self._baseline_window = baseline_window
        self._hist: deque[tuple[float, float]] = deque()
        self._baseline = 0.02
        # 连续段状态
        self._run_class = ""            # 当前段类别：still / mild / active
        self._run_start = 0.0           # 当前段起始时刻
        self._run_peak = 0.0            # 当前段内强度峰值
        self._recent_runs: deque[tuple[str, float, float]] = deque(maxlen=6)
        self._last_spike_t: float | None = None  # 最近一次尖峰时刻（抑制 B/C 抢戏）

    # ---- 对外属性 ----
    @property
    def baseline(self) -> float:
        """当前静止基线（供状态机做相对尖峰判定/调试）。"""
        return self._baseline

    @property
    def spike_threshold(self) -> float:
        """动态尖峰阈值 = max(绝对下限, 基线 × 相对倍数)。"""
        return max(C.SPIKE_MIN, C.SPIKE_REL_RATIO * max(self._baseline, 0.005))

    # ---- 核心入口 ----
    def push(self, sim_t: float, intensity: float) -> dict | None:
        """推入一个逐秒样本，若触发跌倒候选返回 dict，否则 None。"""
        self._update_baseline(sim_t, intensity)
        cls = self._classify(intensity)
        candidate: dict | None = None

        # 超强尖峰逐样本检查（可发生在活动段内，无需段切换）：
        # 实测走动峰值最高 0.896，≥0.90 的冲击视为跌倒（Type A 强信号）
        if intensity >= C.SPIKE_ACTIVE_MIN:
            candidate = {"type": "A", "peak": intensity,
                         "baseline": round(self._baseline, 4),
                         "pre_state": self._run_class or "still"}
            self._last_spike_t = sim_t

        # 段切换时检查 A/B/C 三类模式（基于最近段历史，容忍毛刺）
        if cls != self._run_class:
            prev_dur = sim_t - self._run_start
            prev_peak = self._run_peak
            prev_cls = self._run_class

            # 近期已有强尖峰（Type A 场景）：活动段实为跌倒瞬间的强扰动，
            # 其后的静止应由 Type A 观察窗处理，抑制 B/C 候选抢戏
            recent_spike = (self._last_spike_t is not None
                            and sim_t - self._last_spike_t <= C.FALL_WATCH_WINDOW + 4)

            # Type A：静止前态 → 突变尖峰（闭环实测峰值 0.256~0.475）
            # 前态判定容忍短毛刺：静止段被 1~2 秒微动（呼吸/晃动）打断仍算静止，
            # 排除「静止→起步走动」把正常走动误判为尖峰（起步无尖峰幅值）
            if candidate is None and intensity >= self.spike_threshold:
                still_s, gap_s = self._pre_still_stats(prev_cls, prev_dur)
                if still_s >= C.FALL_A_STILL_S and gap_s <= C.FALL_A_STILL_GAP:
                    candidate = {"type": "A", "peak": intensity,
                                 "baseline": round(self._baseline, 4),
                                 "pre_state": "still"}
                    self._last_spike_t = sim_t

            # Type B：持续活动段 → 骤然进入静止
            # 强尖峰样本自身会被划为 1 秒「活动」短段，recent_spike 抑制
            # 防止其后骤停被误产伪 B 候选（单测：伸懒腰用例）
            if candidate is None and cls == "still" \
                    and prev_cls == "active" and prev_dur >= C.FALL_B_ACTIVE_S \
                    and not recent_spike:
                candidate = {"type": "B", "peak": prev_peak,
                             "baseline": round(self._baseline, 4),
                             "pre_state": "active",
                             "pre_dur": round(prev_dur, 1)}

            # Type C：静止 → 缓慢起身(微动段) → 骤然回到静止
            # 起身段容忍短暂 active 样本（起身发力瞬间，闭环实测 0.161），
            # 且微动段中间允许 1 秒静止间歇（起身过程中的停顿）
            if candidate is None and not recent_spike and cls == "still":
                rise_s, rise_peak, has_still_before = self._rise_stats(prev_cls, prev_dur, prev_peak)
                c_min = max(C.FALL_C_PEAK_MIN,
                            C.FALL_C_PEAK_MIN_RATIO * max(self._baseline, 0.005))
                if rise_s >= C.FALL_C_RISE_S and has_still_before \
                        and rise_peak >= c_min:
                    candidate = {"type": "C", "peak": rise_peak,
                                 "baseline": round(self._baseline, 4),
                                 "pre_state": "rising",
                                 "pre_dur": round(rise_s, 1)}

            self._recent_runs.append((prev_cls, prev_dur, prev_peak))
            self._run_class = cls
            self._run_start = sim_t
            self._run_peak = intensity
        else:
            self._run_peak = max(self._run_peak, intensity)

        return candidate

    # ---- 段历史统计 ----
    def _pre_still_stats(self, prev_cls: str, prev_dur: float
                         ) -> tuple[float, float]:
        """尖峰前态统计：返回 (静止总秒, 毛刺总秒)。

        把最近段历史从新到旧扫描：连续的 still 段累计静止秒；
        短 mild 段视为毛刺（呼吸/空调晃动）；遇 active 段即停（那是真实移动）。"""
        still_s = prev_dur if prev_cls == "still" else 0.0
        gap_s = prev_dur if prev_cls == "mild" else 0.0
        if prev_cls == "active":
            return 0.0, 0.0
        for cls, dur, _peak in reversed(self._recent_runs):
            if cls == "still":
                still_s += dur
            elif cls == "mild" and dur <= C.FALL_A_STILL_GAP:
                gap_s += dur
            else:
                break
        return still_s, gap_s

    def _rise_stats(self, prev_cls: str, prev_dur: float, prev_peak: float
                    ) -> tuple[float, float, bool]:
        """起身段统计：返回 (起身总秒, 起身峰值, 起身前是否静止)。

        从刚结束的段往回看：mild 段累计为起身；短 active 段（起身发力）也计入；
        短 still 段视为起身中的停顿；遇到长 still 段则确认「起身前静止」。"""
        if prev_cls not in ("mild", "active"):
            return 0.0, 0.0, False
        if prev_cls == "active" and prev_dur > C.FALL_C_ACTIVE_MAX_S:
            return 0.0, 0.0, False
        rise_s, rise_peak = prev_dur, prev_peak
        has_still_before = False
        for cls, dur, peak in reversed(self._recent_runs):
            if cls == "mild":
                rise_s += dur
                rise_peak = max(rise_peak, peak)
            elif cls == "active" and dur <= C.FALL_C_ACTIVE_MAX_S:
                rise_s += dur
                rise_peak = max(rise_peak, peak)
            elif cls == "still" and dur <= 1.0:
                continue  # 起身中的短暂停顿
            elif cls == "still":
                has_still_before = True
                break
            else:
                break
        return rise_s, rise_peak, has_still_before

    # ---- 内部工具 ----
    def _classify(self, intensity: float) -> str:
        """强度 → 段类别：still（静止）/ mild（微动，Type C 起身段）/ active（活动）。"""
        if intensity >= C.ACTIVE_MIN:
            return "active"
        if intensity >= C.STILL_MAX:
            return "mild"
        return "still"

    def _update_baseline(self, sim_t: float, intensity: float) -> None:
        """滑动窗 25 分位基线（只统计低强度样本，排除活动段污染）。"""
        self._hist.append((sim_t, intensity))
        while self._hist and sim_t - self._hist[0][0] > self._baseline_window:
            self._hist.popleft()
        lows = sorted(v for _, v in self._hist if v < C.ACTIVE_MIN)
        if len(lows) >= 8:  # 样本足够才更新，避免冷启动抖动
            self._baseline = min(lows[len(lows) // 4], C.STILL_MAX)
