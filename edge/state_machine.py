"""事件状态机 v3：六区六级完整状态机 + 呼吸驱动决策 + Zone超时升级。

核心升级（对照陈林团队状态穷举体系）：
- Zone 0→1：黄区触发条件补全（静息呼吸>20、白天静止>30min、异常时段活动）
- Zone 1→0/2/3：30分钟复查机制（恢复→降级，恶化→升级）
- Zone 2→3：语音超时90s自动升级 + 呼吸恶化立即升级
- Zone 3→4：5分钟无人确认自动升级 + 呼吸消失立即升级
- 呼吸模式五级→三级告警细分（elevated=warning, irregular=warning, shallow=emergency, lost=emergency）
- Zone -1：设备离线检测（简化版）

跌倒检测 v2（20260806 实验数据调优）：
- 三分类候选检测（FallPatternDetector）：Type A 相对尖峰 / Type B 活动→骤停 / Type C 起身→骤停
- 静止基线滑动窗 + 相对尖峰，抗空调等环境漂移
- 分类型确认时长：Type B 静止确认更严（防「走动后坐下」误报）

三态模型 + 双证据线（20260806 守护页定稿）：
- 语义三态：rest 静坐休憩中 / active 活动中 / fall 疑似跌倒；无人时不断言（空串）
- 双证据线铁律：紧急 = 运动突增 + 呼吸变化同时成立；
  只有动作冲击、呼吸无变化 → 静默记录不告警（伸懒腰/弯腰处理）
- 基础病修正系数（apply_profile 注入，见 medical.CONDITION_COEFFICIENTS）：
  呼吸警戒带上移防基线偏快误报、呼吸消失确认拉长防病态暂停误报、
  活动带下探认小碎步、Type B 加严防冻结步态误报
"""
from __future__ import annotations

from typing import Any

from . import config as C
from . import medical as M
from .fall_detect import FallPatternDetector
from .protocol import (
    Event,
    EVT_MOTION_ACTIVE,
    EVT_PRESENCE_OFF,
    EVT_PRESENCE_ON,
    EVT_STILL_TOO_LONG,
    EVT_SUSPECTED_FALL,
    EVT_BREATHING_ABNORMAL,
    EVT_BREATHING_LOST,
    EVT_FALL_BREATHING_OK,
    EVT_FALL_BREATHING_BAD,
    EVT_VOICE_REQUERY,
    EVT_FALL_RECOVERED,
    EVT_DEVICE_OFFLINE,
    EVT_INTRUSION_SUSPECTED,
    EVT_INTRUSION_CONFIRMED,
    SRC_DATASET_REPLAY,
    ZONE_GRAY,
    ZONE_GREEN,
    ZONE_YELLOW,
    ZONE_ORANGE,
    ZONE_RED,
    ZONE_BLACK,
    BR_NORMAL,
    BR_LOST,
)

ABSENT_MAX = 0.01
MOTION_ACTIVE_THROTTLE = 15

# 呼吸异常状态集合
BR_ABNORMAL_SET = {"elevated", "irregular", "shallow"}
# 呼吸重度异常（直接emergency）
BR_SEVERE_SET = {"shallow", "lost"}


class SampleProcessor:
    """六区六级状态机 v3。"""

    def __init__(self, device_id: str = "wg-node-01", zone: str = "living",
                 source: str = SRC_DATASET_REPLAY) -> None:
        self.device_id = device_id
        self.zone = zone
        self.source = source

        self.present = False
        self.guard_zone = ZONE_GREEN

        # 活动检测累积器
        self._active_accum = 0.0
        self._still_accum = 0.0
        self._absent_accum = 0.0
        self._last_sim_t: float | None = None
        self._motion_active_timer = 0.0
        self._still_too_long_fired = False
        self._fall_watch: dict[str, Any] | None = None
        self._fall_pattern = FallPatternDetector()  # 跌倒三分类候选检测器 v2

        # 呼吸监测状态
        self._breathing_abnormal_fired = False
        self._breathing_lost_fired = False
        self._last_breathing_state = BR_NORMAL
        self._elevated_breathing_accum = 0.0  # 静息呼吸加快累积时间
        # 呼吸异常态持续计时（防单次锁频噪声误报，20260807 真机实测：
        # 偶发 1~2 秒低频误锁 shallow 曾直进红区）；无锁频信息时不清零，
        # 间歇锁定场景下持续异常仍能累积成立
        self._br_abn_s: dict[str, float] = {"shallow": 0.0, "irregular": 0.0,
                                            "elevated": 0.0}
        # 存在证据计时：距最近一次「明确有人」证据（有效呼吸/明显活动）的秒数。
        # 呼吸消失告警需此证据在窗内，否则空房间会被误报呼吸消失
        # （20260806 集成测试实测：无人房间反复触发 Zone 4 告警风暴）
        self._presence_active_s = 9999.0

        # Zone 超时计时器
        self._zone_timer = 0.0              # 当前Zone停留时间
        self._zone1_check_fired = False     # Zone 1 复查是否已触发
        self._zone2_voice_timeout = 90      # 第一轮语音超时（会被病历覆盖）
        self._requery_wait_s = 20           # 第二轮确证等待（会被病历覆盖，慢性病15）
        self._voice_round = 0               # 确证轮次：0=无 1=第一轮等待 2=第二轮等待
        self._recovery_active_s = 0.0       # 告警态下运动恢复累计秒数（自解除）
        self._zone3_confirm_timeout = 300   # Zone 3 5分钟无人确认
        self._zone3_max_stay = 0            # Zone 3 最大停留时间（高血压→600s自动升级，0=不限）
        self._family_confirmed = False      # 子女是否已确认收到告警

        # 防盗监测状态
        self._intrusion_mode = False        # 是否处于防盗模式
        self._silence_accum = 0.0           # 无人/睡眠静默累积时间
        self._intrusion_motion_accum = 0.0  # 防盗模式下异常运动累积时间
        self._intrusion_fired = False       # 是否已触发入侵告警

        # 基础病修正系数（apply_profile 注入，默认=无病史基准）
        self._adj: dict[str, Any] = {**M.DEFAULT_ADJUSTMENTS}

        # 语义三态与呼吸消失确认的运行时状态
        self._last_intensity = 0.0      # 最近一个样本强度（派生语义状态）
        self._fall_state_s = 0.0        # 跌倒语义保持倒计时（确认后维持 fall 态）
        self._br_lost_s = 0.0           # 呼吸消失持续秒数（病态暂停防误报）

    # ---- 档案修正注入（千人千档）----
    def apply_profile(self, profile: M.MedicalProfile) -> None:
        """把档案病史折算成检测修正系数（server 启动与档案保存时调用）。"""
        self._adj = M.build_adjustments(profile)
        if self._adj["zone3_max_stay"] > 0:
            self._zone3_max_stay = self._adj["zone3_max_stay"]
        self._zone2_voice_timeout = self._adj["voice_timeout"] or 90
        self._requery_wait_s = self._adj["requery_wait_s"]

    @property
    def adjustments(self) -> dict[str, Any]:
        """当前生效的修正系数（供 server 广播给守护页展示修正痕迹）。"""
        return dict(self._adj)

    @property
    def breathing_band_max(self) -> int:
        """呼吸正常带上限（修正后，供前端画医学色带）。"""
        return C.BR_RATE_ELEVATED + self._adj["br_elevated_adjust"]

    @property
    def semantic_state(self) -> str:
        """语义三态：rest 静坐休憩中 / active 活动中 / fall 疑似跌倒；
        无人探测到时返回空串——页面不做有人/无人断言。"""
        if self._fall_state_s > 0:
            return "fall"
        if self._fall_watch is not None:
            return "fall"
        if not self.present:
            return ""
        if self._last_intensity > self._active_threshold():
            return "active"
        return "rest"

    def _active_threshold(self) -> float:
        """活动判定带（基础病下探后不低于静止带上沿，防两带重叠）。"""
        return max(C.ACTIVE_MIN + self._adj["active_min_adjust"], C.STILL_MAX + 0.01)

    def process(self, ts: str, sim_t: float, intensity: float,
                zone: str | None = None,
                breathing_rate: int = 0,
                breathing_state: str = "") -> list[Event]:
        """处理一个样本，返回本次触发的事件列表。"""
        z = zone or self.zone
        dt = C.SAMPLE_INTERVAL_S if self._last_sim_t is None else max(0.0, sim_t - self._last_sim_t)
        self._last_sim_t = sim_t

        br = breathing_state or self._classify_breathing(breathing_rate)

        is_absent = intensity < ABSENT_MAX
        is_still = ABSENT_MAX <= intensity < C.STILL_MAX
        is_active = intensity > self._active_threshold()
        is_spike = intensity >= self._fall_pattern.spike_threshold  # 相对尖峰（动态阈值）

        # 跌倒候选检测（三分类：A 相对尖峰 / B 活动→骤停 / C 起身→骤停）
        fall_candidate = self._fall_pattern.push(sim_t, intensity)

        events: list[Event] = []

        def mk(etype: str, confidence: float, duration_s: float, features: dict,
               br_rate: int = 0, br_state: str = "", g_zone: int = ZONE_GREEN) -> Event:
            return Event(type=etype, device_id=self.device_id, zone=z,
                         confidence=round(confidence, 2), duration_s=int(duration_s),
                         features=features, source=self.source, ts=ts,
                         breathing_rate=br_rate, breathing_state=br_state,
                         guard_zone=g_zone)

        # ---- Zone 计时器推进 ----
        self._zone_timer += dt
        self._presence_active_s += dt
        self._last_intensity = intensity
        if self._fall_state_s > 0:
            self._fall_state_s = max(0.0, self._fall_state_s - dt)
        # 存在证据刷新：有效呼吸输出 或 明确人体活动（active 带以上，
        # 不用 still 带：空房间环境噪声也会偶尔落在 0.05~0.15）
        if (breathing_rate and breathing_rate > 0 and breathing_state != BR_LOST) \
                or intensity > self._active_threshold():
            self._presence_active_s = 0.0

        # ---- 呼吸消失检测（最高优先级，Zone 4）----
        # 存在证据门槛（20260806 集成测试修复）：空房间本来就测不到呼吸，
        # 「测不到」≠「呼吸骤停」。只有近期确认有人时 lost 才有诊断意义。
        # 病态暂停防误报（基础病修正）：心衰潮式呼吸/癫痫发作期含 5~30s
        # 中枢性暂停，lost 需持续 br_lost_confirm_s 秒才告警（0=立即）。
        if br == BR_LOST:
            if self._presence_active_s <= C.PRESENCE_EVIDENCE_WINDOW_S:
                self._br_lost_s += dt
                # 确证链等待中呼吸衰竭至消失 → 不等防抖时长立即越级
                # （真跌倒引起的昏迷必然影响气息，无需病态暂停等待）
                lost_confirm = 0.0 if self._voice_round in (1, 2) \
                    else float(self._adj["br_lost_confirm_s"])
                if self._br_lost_s >= lost_confirm and not self._breathing_lost_fired:
                    self._breathing_lost_fired = True
                    self.guard_zone = ZONE_BLACK
                    self._zone_timer = 0
                    events.append(mk(EVT_BREATHING_LOST, 0.95, self._br_lost_s,
                                     {"breathing_rate": breathing_rate,
                                      "note": "呼吸信号消失",
                                      "lost_confirm_s": int(lost_confirm)},
                                     br_rate=breathing_rate, br_state=br,
                                     g_zone=ZONE_BLACK))
                    return events
            else:
                self._br_lost_s = 0.0
                br = BR_NORMAL   # 无存在证据：当作无呼吸信息，不进告警链路
        else:
            self._br_lost_s = 0.0

        # ---- 呼吸异常态持续计时（防单次锁频噪声）----
        # rate>0 的真锁频 normal 才清零；无锁频信息（rate=0）时保持，
        # 不同异常态之间切换则互相清零
        if br in self._br_abn_s:
            for k in self._br_abn_s:
                if k != br:
                    self._br_abn_s[k] = 0.0
            self._br_abn_s[br] += dt
        elif br == BR_NORMAL and breathing_rate > 0:
            for k in self._br_abn_s:
                self._br_abn_s[k] = 0.0
        br_persist_ok = br not in self._br_abn_s or \
            self._br_abn_s[br] >= C.BR_ABNORMAL_PERSIST_S

        # 呼吸恢复正常时重置标记
        if br == BR_NORMAL:
            self._breathing_abnormal_fired = False
            self._breathing_lost_fired = False
            self._elevated_breathing_accum = 0.0
        self._last_breathing_state = br

        # ---- Zone 超时升级检查 ----
        upgrade_events = self._check_zone_upgrades(ts, br, breathing_rate, dt, mk)
        events.extend(upgrade_events)
        if upgrade_events:
            return events  # 升级事件已发出，本轮不再处理其他

        # ---- 无人/离开检测 ----
        # 严格性修正（20260806 闭环数据）：呼吸检测器仍在输出有效呼吸率时，
        # 呼吸本身就是存在证据（B1 实验：静坐时呼吸持续可测），
        # 不能仅凭低强度判定无人——否则「跌倒后远场静止」会被误判为离开。
        # 仅当呼吸状态由检测器显式给出时才视为证据（rate 默认值不算）
        breathing_confirms_presence = bool(breathing_state) \
            and breathing_state != BR_LOST and breathing_rate > 0
        if is_absent:
            self._absent_accum += dt
            self._active_accum = 0.0
            if self.present and self._absent_accum >= C.PRESENCE_OFF_SECONDS \
                    and not breathing_confirms_presence:
                self.present = False
                self._reset_still()
                self._fall_watch = None
                self._reset_to_green()
                events.append(mk(EVT_PRESENCE_OFF, 0.9, self._absent_accum, {},
                                 br_rate=breathing_rate, br_state=br))
            # 有人但呼吸不可测时的防盗静默累积照常
            if C.INTRUSION_ENABLED and not self.present:
                self._silence_accum += dt
                if self._silence_accum >= C.INTRUSION_SILENCE_SECONDS and not self._intrusion_mode:
                    self._intrusion_mode = True
                    self._intrusion_fired = False
            # 跌倒观察窗在 absent 样本上的推进仅限 Type C（20260806 闭环数据驱动）：
            # F3 实测倒地后信号 0.001~0.003 全在 absent 带，不推进则永远无法确认；
            # Type A/B 若也靠 absent 累计，「走出探测范围」会被误判为跌倒
            # （单节点强度信号无法区分离开与远场倒地，如实记录为已知局限）。
            if fall_candidate is not None and self._fall_watch is None \
                    and fall_candidate["type"] == "C":
                self._open_fall_watch(fall_candidate)
            elif fall_candidate is not None and self._fall_watch is not None \
                    and fall_candidate["type"] == "A" and self._watch_unconfirmed():
                # 强尖峰替换未确认的弱候选（F1_R1 实测：站立调整身体的弱 C 候选
                # 占用观察窗，导致 6 秒后的真跌倒尖峰被错过）
                self._open_fall_watch(fall_candidate)
            elif self._fall_watch is not None and self._fall_watch["type"] == "C":
                events.extend(self._advance_fall_watch(
                    dt, intensity, is_absent=True, is_still=False,
                    br=br, br_rate=breathing_rate, ts=ts, mk=mk, z=z))
            return events
        self._absent_accum = 0.0

        # ---- 防盗模式下的入侵检测 ----
        if C.INTRUSION_ENABLED and self._intrusion_mode and not self.present:
            if is_active or is_spike:
                self._intrusion_motion_accum += dt
                if self._intrusion_motion_accum >= C.INTRUSION_MOTION_CONFIRM_S and not self._intrusion_fired:
                    self._intrusion_fired = True
                    self._set_zone(ZONE_RED)
                    events.append(mk(
                        EVT_INTRUSION_SUSPECTED, 0.8, self._intrusion_motion_accum,
                        {"context": "防盗模式下检测到异常运动，疑似入侵",
                         "silence_before_s": int(self._silence_accum),
                         "motion_s": int(self._intrusion_motion_accum),
                         "camera_link": C.INTRUSION_CAMERA_LINK},
                        br_rate=breathing_rate, br_state=br, g_zone=ZONE_RED))
            else:
                self._intrusion_motion_accum = 0.0
            return events

        # 有人活动时退出防盗模式
        if self.present and self._intrusion_mode:
            self._intrusion_mode = False
            self._silence_accum = 0.0
            self._intrusion_motion_accum = 0.0
            self._intrusion_fired = False

        # ---- 跌倒观察窗推进（三分类 v2）----
        if fall_candidate is not None and self._fall_watch is None:
            self._open_fall_watch(fall_candidate)
        elif fall_candidate is not None and self._fall_watch is not None \
                and fall_candidate["type"] == "A" \
                and fall_candidate["peak"] > self._fall_watch["peak"] \
                and (self._watch_unconfirmed()
                     or self._fall_watch["watch"] <= C.FALL_WATCH_WINDOW):
            # 强尖峰替换未确认/尚年轻的弱候选（实测 F1_R1：站立调整身体的弱候选
            # 占用观察窗 8 秒，真跌倒尖峰被错过；单测：坐下后的伪 B 观察窗
            # 不得吞掉随后的真跌倒尖峰）
            self._open_fall_watch(fall_candidate)
        elif self._fall_watch is not None:
            # 双证据呼吸线用防抖后的态：瞬时异常未达持续阈值按「无异常信息」，
            # 避免单次误锁混入跌倒确证链
            br_ev = br if br_persist_ok else ""
            events.extend(self._advance_fall_watch(
                dt, intensity, is_absent=is_absent, is_still=is_still,
                br=br_ev, br_rate=breathing_rate, ts=ts, mk=mk, z=z))

        # ---- Zone 1 黄区触发条件补全 ----
        # 条件1: 静息呼吸加快（>20次/分）累积超过60秒
        if br == "elevated" and is_still and self.present:
            self._elevated_breathing_accum += dt
            if self._elevated_breathing_accum >= 60 and self.guard_zone < ZONE_YELLOW:
                self._set_zone(ZONE_YELLOW)
                events.append(mk(
                    EVT_BREATHING_ABNORMAL, 0.75, self._elevated_breathing_accum,
                    {"breathing_rate": breathing_rate, "breathing_state": br,
                     "note": "静息呼吸加快持续>60s，进入黄区关注"},
                    br_rate=breathing_rate, br_state=br, g_zone=ZONE_YELLOW))

        # 条件2: 独立呼吸异常（非跌倒场景）
        # 已在红区（跌倒双证据线成立）时不再叠加独立呼吸异常事件
        # 异常态需持续 BR_ABNORMAL_PERSIST_S 秒才成立（防单次误锁）
        if br in BR_ABNORMAL_SET and br_persist_ok and \
                not self._breathing_abnormal_fired and \
                self._fall_watch is None and self.present and \
                self.guard_zone < ZONE_RED:
            self._breathing_abnormal_fired = True
            # shallow 级别直接emergency
            if br == "shallow":
                self._set_zone(ZONE_RED)
                events.append(mk(
                    EVT_BREATHING_ABNORMAL, 0.85, 0,
                    {"breathing_rate": breathing_rate, "breathing_state": br,
                     "note": "呼吸浅慢（重度缺氧/潮式呼吸），紧急告警"},
                    br_rate=breathing_rate, br_state=br, g_zone=ZONE_RED))
            else:
                self._set_zone(max(self.guard_zone, ZONE_YELLOW))
                events.append(mk(
                    EVT_BREATHING_ABNORMAL, 0.75, 0,
                    {"breathing_rate": breathing_rate, "breathing_state": br,
                     "note": "呼吸异常，持续关注"},
                    br_rate=breathing_rate, br_state=br, g_zone=self.guard_zone))

        # ---- 活动 / 存在 ----
        if is_active:
            self._active_accum += dt
            self._reset_still()
            # 告警态自解除：确证链进行中运动恢复均速且气息平稳累计≥10s
            # → 自动解除告警级别（Zone 4 已联动专业响应，不自动解）
            if self.guard_zone == ZONE_RED and self._voice_round in (1, 2):
                self._recovery_active_s += dt
                if self._recovery_active_s >= 10 and br == BR_NORMAL:
                    events.append(mk(
                        EVT_FALL_RECOVERED, 0.85, self._recovery_active_s,
                        {"context": "运动恢复均速且气息平稳，自动解除告警级别",
                         "recovery_s": int(self._recovery_active_s),
                         "breathing_rate": breathing_rate},
                        br_rate=breathing_rate, br_state=br, g_zone=ZONE_GREEN))
                    self._voice_round = 0
                    self._recovery_active_s = 0.0
                    self._fall_state_s = 0.0
                    self._reset_to_green()
                    return events
            if not self.present and self._active_accum >= C.PRESENCE_ON_SECONDS:
                self.present = True
                self._reset_to_green()
                events.append(mk(EVT_PRESENCE_ON, 0.9, self._active_accum, {},
                                 br_rate=breathing_rate, br_state=br))
            self._motion_active_timer += dt
            if self._motion_active_timer >= MOTION_ACTIVE_THROTTLE or \
                    (self.present and self._active_accum == dt):
                self._motion_active_timer = 0.0
                events.append(mk(EVT_MOTION_ACTIVE, 0.8, self._active_accum,
                                 {"intensity": round(intensity, 3)},
                                 br_rate=breathing_rate, br_state=br))
            return events

        # ---- 静止（有人、有呼吸微动）----
        if is_still:
            self._active_accum = 0.0
            self._still_accum += dt
            threshold = C.still_too_long_threshold(z)
            if self.present and not self._still_too_long_fired and \
                    self._fall_watch is None and self._still_accum >= threshold:
                self._still_too_long_fired = True
                # 长时间静止 → Zone 1 黄区
                self._set_zone(max(self.guard_zone, ZONE_YELLOW))
                events.append(mk(EVT_STILL_TOO_LONG, 0.85, self._still_accum,
                                 {"still_s": int(self._still_accum),
                                  "intensity": round(intensity, 3)},
                                 br_rate=breathing_rate, br_state=br,
                                 g_zone=self.guard_zone))
            return events

        # ---- 中间带（轻微活动）----
        self._active_accum = 0.0
        self._still_accum = 0.0
        self._still_too_long_fired = False
        return events

    def _watch_unconfirmed(self) -> bool:
        """观察窗是否尚未累计任何确认态（可被更强候选替换）。"""
        return (self._fall_watch is not None
                and self._fall_watch["still_after"] == 0
                and self._fall_watch.get("dwell_after", 0.0) == 0)

    def _open_fall_watch(self, candidate: dict) -> None:
        """为跌倒候选建档（观察窗）。"""
        self._fall_watch = {"peak": candidate["peak"], "watch": 0.0,
                            "still_after": 0.0, "type": candidate["type"],
                            "baseline": candidate["baseline"],
                            "pre_state": candidate.get("pre_state", "still")}

    def _advance_fall_watch(self, dt: float, intensity: float, *,
                            is_absent: bool, is_still: bool,
                            br: str, br_rate: int, ts: str, mk, z: str) -> list[Event]:
        """推进跌倒观察窗一个采样，返回触发的跌倒类事件。

        absent/still 样本累计确认态；近场滞留（倒地后信号不回落，实测 0.1~0.22）
        要求强度明显低于峰值（真倒地信号衰减，走路离开不衰减）。"""
        events: list[Event] = []
        self._fall_watch["watch"] += dt
        self._fall_watch["peak"] = max(self._fall_watch["peak"], intensity)
        in_dwell = (C.FALL_DWELL_MIN <= intensity < C.FALL_DWELL_MAX
                    and intensity < self._fall_watch["peak"] * C.FALL_DWELL_PEAK_RATIO)
        if is_still or is_absent or in_dwell:
            if is_still or is_absent:
                self._fall_watch["still_after"] += dt
            else:
                self._fall_watch.setdefault("dwell_after", 0.0)
                self._fall_watch["dwell_after"] += dt
            # 分类型确认时长：Type B（活动→骤停）更严，防「走动后坐下」误报；
            # Type A 近场滞留确认更短（实测信号特征明确）
            if self._fall_watch["type"] == "B":
                # 基础病修正：帕金森冻结步态形似「活动→骤停」，确认时长加严
                b_confirm = self._adj["type_b_still_s"] or C.FALL_B_STILL_S
                confirmed = self._fall_watch["still_after"] >= b_confirm
            elif self._fall_watch["type"] == "A":
                confirmed = (self._fall_watch["still_after"] >= C.FALL_STILL_SECONDS
                             or self._fall_watch.get("dwell_after", 0.0) >= C.FALL_DWELL_SECONDS)
            else:
                confirmed = self._fall_watch["still_after"] >= C.FALL_STILL_SECONDS
            if confirmed:
                events.extend(self._emit_fall_events(br, br_rate, ts, mk, z))
        elif self._fall_watch["watch"] > C.FALL_WATCH_WINDOW \
                and self._fall_watch["still_after"] == 0 \
                and self._fall_watch.get("dwell_after", 0.0) == 0:
            # 观察窗超时且从未进入确认态 → 视为普通活动，放弃候选
            self._fall_watch = None
        return events

    def _emit_fall_events(self, br: str, br_rate: int, ts: str, mk, z: str) -> list[Event]:
        """观察窗确认后产出跌倒类事件（含呼吸分叉），并关闭观察窗。"""
        events: list[Event] = []
        peak = self._fall_watch["peak"]
        ftype = self._fall_watch["type"]
        # 分类型置信度：A 峰值强信号，B 仅模式信号（最弱），C 弱信号
        if ftype == "A":
            conf = min(0.95, 0.5 + peak * 0.4)
        elif ftype == "B":
            conf = min(0.70, 0.45 + peak * 0.3)
        else:
            conf = min(0.85, 0.45 + peak * 0.6)
        fall_features = {"fall_type": ftype,
                         "pre_state": self._fall_watch["pre_state"],
                         "still_baseline": self._fall_watch["baseline"]}

        def feats(extra: dict) -> dict:
            return {**fall_features,
                    "amp_var_peak": round(peak, 2),
                    "still_after_s": int(self._fall_watch["still_after"]),
                    "breathing_rate": br_rate,
                    **extra}

        # 保守分叉：Type B/C 仅有模式信号无强峰值（实验 F2 峰值 0.021），
        # 呼吸正常时无法与「走动后坐下休息」区分 → 只记疑似事件入黄区关注，
        # 不触发语音确认；呼吸异常时才升级告警。Type A 强峰值直接进正常流程。
        if ftype in ("B", "C") and br not in BR_ABNORMAL_SET and br != BR_LOST:
            self._set_zone(max(self.guard_zone, ZONE_YELLOW))
            events.append(mk(
                EVT_SUSPECTED_FALL, 0.5, self._fall_watch["still_after"],
                feats({"context": f"疑似跌倒(Type {ftype})：活动模式骤停，呼吸正常，黄区关注"}),
                br_rate=br_rate, br_state=br, g_zone=self.guard_zone))
            self._fall_watch = None
            self._still_too_long_fired = True
            return events

        # 核心分叉：根据呼吸状态决定 Zone 2/3/4
        if br == BR_LOST:
            self._fall_state_s = 120.0  # 跌倒语义保持（守护页持续显示疑似跌倒）
            self._set_zone(ZONE_BLACK)
            events.append(mk(
                EVT_BREATHING_LOST, 0.98, self._fall_watch["still_after"],
                feats({"context": f"跌倒(Type {ftype})后呼吸消失"}),
                br_rate=br_rate, br_state=br, g_zone=ZONE_BLACK))
        elif br in BR_SEVERE_SET:
            self._fall_state_s = 120.0
            self._set_zone(ZONE_RED)
            events.append(mk(
                EVT_FALL_BREATHING_BAD, conf, self._fall_watch["still_after"],
                feats({"breathing_state": br,
                       "context": f"跌倒(Type {ftype})+呼吸严重异常，双证据线成立，立即告警"}),
                br_rate=br_rate, br_state=br, g_zone=ZONE_RED))
        elif br in BR_ABNORMAL_SET:
            self._fall_state_s = 120.0
            self._set_zone(ZONE_RED)
            events.append(mk(
                EVT_FALL_BREATHING_BAD, conf, self._fall_watch["still_after"],
                feats({"breathing_state": br,
                       "context": f"跌倒(Type {ftype})+呼吸异常，双证据线成立，立即告警"}),
                br_rate=br_rate, br_state=br, g_zone=ZONE_RED))
        else:
            # 双证据线铁律：只有运动冲击、呼吸无变化 → 按伸懒腰/弯腰等
            # 普通动作处理：静默记录入事件库，不发语音、不告警（不打扰）。
            # 呼吸是第一参照物：真摔倒必然引起呼吸变化（加快/衰减）。
            events.append(mk(
                EVT_FALL_BREATHING_OK, min(conf, 0.4), self._fall_watch["still_after"],
                feats({"breathing_state": br,
                       "context": f"运动冲击(Type {ftype})但呼吸无变化，"
                                  f"按普通动作处理，仅记录不打扰"}),
                br_rate=br_rate, br_state=br, g_zone=ZONE_GREEN))

        self._fall_watch = None
        self._still_too_long_fired = True
        return events

    def _check_zone_upgrades(self, ts: str, br: str, br_rate: int,
                             dt: float, mk) -> list[Event]:
        """Zone 超时升级检查（每tick调用）。"""
        events: list[Event] = []

        # 确证链第一轮超时 → 第二轮询问（暂不升级，第二轮开始即报警响铃）
        if self.guard_zone == ZONE_RED and self._voice_round == 1 and \
                self._zone_timer >= self._zone2_voice_timeout:
            self._voice_round = 2
            self._zone_timer = 0
            events.append(mk(
                EVT_VOICE_REQUERY, 0.9, 0,
                {"context": f"第一轮语音询问{self._zone2_voice_timeout}s无回应，"
                            f"发起第二轮询问并报警通知家人",
                 "requery_wait_s": self._requery_wait_s,
                 "breathing_rate": br_rate, "breathing_state": br},
                br_rate=br_rate, br_state=br, g_zone=ZONE_RED))

        # 确证链第二轮超时 → 两轮无人应答，进入危机状态
        elif self.guard_zone == ZONE_RED and self._voice_round == 2 and \
                self._zone_timer >= self._requery_wait_s:
            waited = self._zone_timer
            self._voice_round = 0
            self._set_zone(ZONE_BLACK)
            events.append(mk(
                EVT_FALL_BREATHING_BAD, 0.93, waited,
                {"context": f"两轮询问无人应答（第二轮等待{self._requery_wait_s}s），进入危机状态",
                 "breathing_rate": br_rate, "breathing_state": br},
                br_rate=br_rate, br_state=br, g_zone=ZONE_BLACK))

        # Zone 2（语音确认中）→ 超时升级 Zone 3
        elif self.guard_zone == ZONE_ORANGE and self._zone_timer >= self._zone2_voice_timeout:
            self._set_zone(ZONE_RED)
            events.append(mk(
                EVT_FALL_BREATHING_BAD, 0.9, self._zone_timer,
                {"context": f"语音确认超时{self._zone2_voice_timeout}s无回应，升级为立即告警",
                 "breathing_rate": br_rate, "breathing_state": br},
                br_rate=br_rate, br_state=br, g_zone=ZONE_RED))

        # Zone 2 → 呼吸恶化立即升级 Zone 3
        elif self.guard_zone == ZONE_ORANGE and br in BR_ABNORMAL_SET:
            self._set_zone(ZONE_RED)
            events.append(mk(
                EVT_FALL_BREATHING_BAD, 0.92, 0,
                {"context": "Zone 2期间呼吸恶化，立即升级告警",
                 "breathing_rate": br_rate, "breathing_state": br},
                br_rate=br_rate, br_state=br, g_zone=ZONE_RED))

        # Zone 3（立即告警）→ 5分钟无人确认升级 Zone 4（子女已确认则不超时；
        # 确证链进行中由两轮分支接管，不叠加超时升级）
        elif self.guard_zone == ZONE_RED and self._voice_round == 0 and \
                not self._family_confirmed and \
                self._zone_timer >= self._zone3_confirm_timeout:
            self._set_zone(ZONE_BLACK)
            events.append(mk(
                EVT_BREATHING_LOST, 0.9, self._zone_timer,
                {"context": f"Zone 3超过{self._zone3_confirm_timeout}s无人确认，升级紧急救援",
                 "breathing_rate": br_rate, "breathing_state": br},
                br_rate=br_rate, br_state=br, g_zone=ZONE_BLACK))

        # Zone 3 → 高血压病史：停留超过10分钟自动升级 Zone 4（确证链进行中不叠加）
        elif self.guard_zone == ZONE_RED and self._voice_round == 0 and \
                self._zone3_max_stay > 0 and \
                not self._family_confirmed and \
                self._zone_timer >= self._zone3_max_stay:
            self._set_zone(ZONE_BLACK)
            events.append(mk(
                EVT_BREATHING_LOST, 0.88, self._zone_timer,
                {"context": f"高血压病史：Zone 3停留{self._zone3_max_stay}s自动升级",
                 "breathing_rate": br_rate, "breathing_state": br},
                br_rate=br_rate, br_state=br, g_zone=ZONE_BLACK))

        # Zone 4（紧急救援）→ 呼吸恢复降级到 Zone 3（维持告警不解除）
        elif self.guard_zone == ZONE_BLACK and br == BR_NORMAL:
            self._set_zone(ZONE_RED)
            events.append(mk(
                EVT_BREATHING_ABNORMAL, 0.85, self._zone_timer,
                {"context": "呼吸恢复，从紧急救援降级至告警（告警不解除）",
                 "breathing_rate": br_rate, "breathing_state": br},
                br_rate=br_rate, br_state=br, g_zone=ZONE_RED))

        # Zone 1（黄区）→ 30分钟复查
        elif self.guard_zone == ZONE_YELLOW and not self._zone1_check_fired and \
                self._zone_timer >= C.ZONE1_STILL_THRESHOLD:
            self._zone1_check_fired = True
            if br == BR_NORMAL:
                # 恢复正常 → 降级 Zone 0
                self._reset_to_green()
            else:
                # 恶化 → 升级 Zone 3
                self._set_zone(ZONE_RED)
                events.append(mk(
                    EVT_BREATHING_ABNORMAL, 0.8, self._zone_timer,
                    {"context": "黄区30分钟复查：呼吸未恢复，升级告警",
                     "breathing_rate": br_rate, "breathing_state": br},
                    br_rate=br_rate, br_state=br, g_zone=ZONE_RED))

        return events

    def _set_zone(self, zone: int) -> None:
        """切换Zone并重置计时器。"""
        if zone != self.guard_zone:
            self.guard_zone = zone
            self._zone_timer = 0
            if zone == ZONE_YELLOW:
                self._zone1_check_fired = False

    def _reset_to_green(self) -> None:
        """重置到Zone 0（绿区）。"""
        self.guard_zone = ZONE_GREEN
        self._zone_timer = 0
        self._zone1_check_fired = False
        self._elevated_breathing_accum = 0.0
        self._still_too_long_fired = False  # 修复：回绿区后久滞告警可再次触发
        self._voice_round = 0               # 确证链一并归零
        self._recovery_active_s = 0.0

    def _classify_breathing(self, rate: int) -> str:
        """根据呼吸频率自动分类。

        rate=0 语义是「桥接器未给出有效呼吸」：返回空串表示无信息，
        不能当 normal 也不能当 lost（20260806 集成测试：空房间 lost 防抖
        上报后与伪锁频交替造成告警风暴）。
        """
        if rate == 0:
            return ""
        if rate < C.BR_RATE_LOST:
            return BR_LOST
        if rate < C.BR_RATE_SLOW:
            return "shallow"
        if rate < C.BR_RATE_MIN:
            return "irregular"
        # 基础病修正：贫血/心衰等静息呼吸基线偏高，警戒线上移防误报
        if rate > C.BR_RATE_ELEVATED + self._adj["br_elevated_adjust"]:
            return "elevated"
        return BR_NORMAL

    def _reset_still(self) -> None:
        self._still_accum = 0.0
        self._still_too_long_fired = False
