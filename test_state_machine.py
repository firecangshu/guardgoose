"""状态机核心判定单元测试（三态 + 双证据线 + 基础病修正系数 + 双轮确证链）。

用例对应计划验收线：
1. 伸懒腰：尖峰 + 呼吸无变化 → 静默记录不告警（双证据线未成立）
2. 双证据：尖峰 + 呼吸加快 → 告警成立（双证据线成立）
3. 系数铁律：基础病只收紧不放宽（自定义病史上移加快线被钳制归零）
4. 存在证据：空房间呼吸测不到 → 不告警（有人时呼吸消失才告警，防回归）
5. 双轮确证：第一轮超时 → requery 不升黑区；第二轮超时 → 危机（黑区）
6. 第二轮时长：急救导向默认 10s，慢性病档案同为 10s
7. 告警态运动恢复 ≥10s 且呼吸正常 → EVT_FALL_RECOVERED 自动解除归绿
8. 确证等待中呼吸 lost → 不等防抖直接越级黑区
9. Type B 观察窗放弃：歇脚 <15s 确认线又起身走动 → 候选放弃，不锁死疑似跌倒

运行：python -m pytest test_state_machine.py -v（或直接 python test_state_machine.py）
"""
from __future__ import annotations

import sys

from edge import config as C
from edge import medical
from edge.medical import CustomDisease, MedicalProfile
from edge.protocol import (
    BR_NORMAL,
    EVT_BREATHING_LOST,
    EVT_FALL_BREATHING_BAD,
    EVT_FALL_BREATHING_OK,
    EVT_FALL_RECOVERED,
    EVT_SUSPECTED_FALL,
    EVT_VOICE_REQUERY,
    ZONE_BLACK,
    ZONE_GREEN,
    ZONE_RED,
)
from edge.state_machine import SampleProcessor


def _feed(p: SampleProcessor, t: int, intensity: float,
          br_rate: int = 0, br_state: str = "") -> list:
    """按 1 sim-秒步进喂一个样本（ts 按分秒展开，支持 t≥60 的长用例）。"""
    return p.process(ts=f"2026-01-01T00:{t // 60:02d}:{t % 60:02d}", sim_t=float(t),
                     intensity=intensity, breathing_rate=br_rate,
                     breathing_state=br_state)


def _establish_presence(p: SampleProcessor, t0: int = 0) -> int:
    """连续活动 4 秒建立「有人」（PRESENCE_ON_SECONDS=3），同时刷新存在证据。"""
    for i in range(4):
        _feed(p, t0 + i, 0.4, br_rate=15)
    assert p.present, "前置条件失败：未建立有人状态"
    return t0 + 4


def _spike_then_still(p: SampleProcessor, t0: int, still_s: int,
                      br_rate: int, br_state: str = "") -> list:
    """一次强尖峰（Type A 候选）+ still_s 秒静止确认段，返回全部事件。"""
    events = list(_feed(p, t0, 0.95, br_rate=15))          # 跌倒冲击
    for i in range(1, still_s + 1):
        events.extend(_feed(p, t0 + i, 0.03,
                            br_rate=br_rate, br_state=br_state))
    return events


def _fall_red_round1(p: SampleProcessor) -> int:
    """走到双证据线成立（红区），并按 server 行为启动第一轮确证，
    返回下一可用时间戳。"""
    t = _establish_presence(p)
    for i in range(6):
        _feed(p, t + i, 0.03, br_rate=15)
    t += 6
    events = _spike_then_still(p, t, still_s=10, br_rate=24)
    assert any(e.type == EVT_FALL_BREATHING_BAD for e in events), "前置条件失败：双证据线未成立"
    assert p.guard_zone == ZONE_RED
    # server 收到告警事件后启动第一轮确证（状态机只消费轮次）
    p._voice_round = 1
    p._zone_timer = 0.0
    return t + 11


# ---- 用例1：伸懒腰（双证据线未成立 → 静默记录不告警）----

def test_stretch_no_alarm_when_breathing_unchanged():
    p = SampleProcessor(zone="living")
    t = _establish_presence(p)
    for i in range(6):                                     # 静坐（呼吸平稳）
        _feed(p, t + i, 0.03, br_rate=15)
    t += 6
    events = _spike_then_still(p, t, still_s=10, br_rate=15)  # 冲击后呼吸无变化

    bad = [e for e in events if e.type == EVT_FALL_BREATHING_BAD]
    ok = [e for e in events if e.type == EVT_FALL_BREATHING_OK]
    assert not bad, "呼吸无变化不应产生告警事件"
    assert len(ok) == 1, "应仅产生一条静默记录事件"
    assert ok[0].guard_zone == ZONE_GREEN, "静默记录应保持绿区"
    assert "普通动作" in ok[0].features.get("context", ""), "记录需注明按普通动作处理"
    assert p.guard_zone == ZONE_GREEN, "守护等级不应升级"
    assert p.semantic_state in ("rest", "active"), "伸懒腰后不应停留在 fall 语义"


# ---- 用例2：双证据线成立（尖峰 + 呼吸加快 → 告警）----

def test_fall_alarm_when_breathing_elevated():
    p = SampleProcessor(zone="living")
    t = _establish_presence(p)
    for i in range(6):
        _feed(p, t + i, 0.03, br_rate=15)
    t += 6
    events = _spike_then_still(p, t, still_s=10, br_rate=24)  # 倒地后呼吸 9→24 加快

    bad = [e for e in events if e.type == EVT_FALL_BREATHING_BAD]
    assert bad, "双证据线成立必须产生告警事件"
    assert bad[0].guard_zone == ZONE_RED, "双证据线成立应进入红区立即告警"
    assert p.guard_zone == ZONE_RED
    assert p.semantic_state == "fall", "确认后守护页应保持疑似跌倒语义"
    assert "双证据线成立" in bad[0].features.get("context", "")


# ---- 用例3：系数铁律（基础病只收紧不放宽：放宽方向系数被钳制归零）----

def test_copd_profile_shifts_elevated_threshold():
    disease = CustomDisease(code="copd", name="慢性阻塞性肺病",
                            category="呼吸系统", br_elevated_adjust=4)
    medical.register_disease(disease)
    try:
        # 铁律：自定义病史试图上移呼吸加快线（放宽）→ 出口钳制归零，
        # rate=22 与无档案一样判 elevated，有病只会更严不会更松
        p_copd = SampleProcessor(zone="living")
        p_copd.apply_profile(MedicalProfile(conditions=["copd"]))
        assert p_copd.breathing_band_max == C.BR_RATE_ELEVATED, \
            "放宽方向的加快线上移不得生效（铁律钳制）"
        assert p_copd._classify_breathing(22) == "elevated", \
            "有档案时 rate=22 仍应判加快（不放宽）"

        # 无档案基准：rate=22 即算 elevated
        p_base = SampleProcessor(zone="living")
        assert p_base.breathing_band_max == C.BR_RATE_ELEVATED
        assert p_base._classify_breathing(22) == "elevated"
    finally:
        medical.unregister_disease("copd")


# ---- 用例4：存在证据（空房间呼吸测不到不告警；有人呼吸消失才告警）----

def test_empty_room_breathing_lost_no_alarm():
    p = SampleProcessor(zone="living")
    all_events = []
    for i in range(30):  # 空房间：低强度 + 呼吸不可测（检测器报 lost）
        all_events.extend(_feed(p, i, 0.005, br_rate=2, br_state="lost"))

    lost = [e for e in all_events if e.type == EVT_BREATHING_LOST]
    assert not lost, "无存在证据时「呼吸测不到」不得触发告警（防空气呼吸消失误报）"
    assert p.guard_zone == ZONE_GREEN, "空房间应保持绿区"
    assert p.semantic_state == "", "无人时语义三态应为空串（页面留空）"


def test_breathing_lost_alarm_when_present():
    p = SampleProcessor(zone="living")
    _establish_presence(p)                     # 先确认有人（存在证据在窗内）
    all_events = []
    for i in range(4, 12):                     # 随后呼吸信号消失
        all_events.extend(_feed(p, i, 0.03, br_rate=2, br_state="lost"))

    lost = [e for e in all_events if e.type == EVT_BREATHING_LOST]
    assert lost, "有人且存在证据在窗内时，呼吸消失必须告警"
    assert p.guard_zone == ZONE_BLACK, "呼吸消失应进入紧急救援区"


# ---- 用例5：双轮确证链（第一轮超时 requery 不升级；第二轮超时进危机）----

def test_two_round_confirmation_chain():
    p = SampleProcessor(zone="living")
    t = _fall_red_round1(p)

    # 第一轮等待 15s 无回应（倒地静止、呼吸仍紊乱，避免走自解除分支）
    events_r1 = []
    for i in range(16):
        events_r1.extend(_feed(p, t + i, 0.03, br_rate=24, br_state="elevated"))
    requery = [e for e in events_r1 if e.type == EVT_VOICE_REQUERY]
    assert requery, "第一轮超时必须发出第二轮询问事件"
    assert requery[0].guard_zone == ZONE_RED, "第二轮询问阶段不应升级守护等级"
    assert p.guard_zone == ZONE_RED, "requery 后仍应保持红区等待回应"
    assert p._voice_round == 2

    # 第二轮等待 10s 仍无回应 → 两轮无人应答，进入危机状态（黑区）
    t += 16
    events_r2 = []
    for i in range(11):
        events_r2.extend(_feed(p, t + i, 0.03, br_rate=24, br_state="elevated"))
    crisis = [e for e in events_r2
              if e.type == EVT_FALL_BREATHING_BAD and e.guard_zone == ZONE_BLACK]
    assert crisis, "第二轮超时必须进入危机状态"
    assert p.guard_zone == ZONE_BLACK
    assert p._voice_round == 0
    assert "两轮询问无应答" in crisis[0].features.get("context", "")
    assert crisis[0].duration_s > 0, "危机事件应携带第二轮等待时长"


# ---- 用例6：第二轮等待时长（急救导向默认 10s，慢性病档案同为 10s）----

def test_requery_wait_duration_by_profile():
    p_base = SampleProcessor(zone="living")
    assert p_base._requery_wait_s == 10, "无档案第二轮等待默认 10s（急救导向）"

    p_chronic = SampleProcessor(zone="living")
    p_chronic.apply_profile(MedicalProfile(conditions=["hypertension"]))
    assert p_chronic._requery_wait_s == 10, "慢性病档案第二轮等待应缩短至 10s"

    # 功能验证：9s 未超时，10s 准时进危机
    t = _fall_red_round1(p_chronic)
    events_wait = []
    for i in range(16):  # 先走完第一轮（第一轮超时 15s，多喂不亏）
        events_wait.extend(_feed(p_chronic, t + i, 0.03, br_rate=24, br_state="elevated"))
    assert p_chronic._voice_round == 2
    t += 16
    early = []
    for i in range(8):  # 第二轮计时 1~9s 不得升级
        early.extend(_feed(p_chronic, t + i, 0.03, br_rate=24, br_state="elevated"))
    assert not any(e.guard_zone == ZONE_BLACK for e in early), "10s 内不得提前进危机"
    late = _feed(p_chronic, t + 8, 0.03, br_rate=24, br_state="elevated")  # 计时到 10s
    assert any(e.type == EVT_FALL_BREATHING_BAD and e.guard_zone == ZONE_BLACK
               for e in late), "慢性病档案第二轮 10s 应准时进危机"


# ---- 用例7：告警态运动恢复自解除（确证中起身，气息平稳 → 自动解除）----

def test_motion_recovery_auto_clear():
    p = SampleProcessor(zone="living")
    t = _fall_red_round1(p)

    # 确证中自行起身恢复活动（匀速），呼吸回正常带
    events = []
    for i in range(12):
        events.extend(_feed(p, t + i, 0.4, br_rate=15))
    rec = [e for e in events if e.type == EVT_FALL_RECOVERED]
    assert rec, "运动恢复均速且气息平稳累计≥10s 必须自动解除"
    assert rec[0].guard_zone == ZONE_GREEN
    assert p.guard_zone == ZONE_GREEN, "自解除后守护等级应归绿"
    assert p._voice_round == 0
    assert "自动解除" in rec[0].features.get("context", "")


# ---- 用例8：确证等待中呼吸衰竭至消失 → 不等语音超时直接越级 ----

def test_breathing_lost_during_confirmation_escalates_immediately():
    p = SampleProcessor(zone="living")
    t = _fall_red_round1(p)

    # 第一轮等待中呼吸信号消失：确证链内跳过病态暂停防抖，立即危机
    events = _feed(p, t, 0.03, br_rate=2, br_state="lost")
    lost = [e for e in events if e.type == EVT_BREATHING_LOST]
    assert lost, "确证等待中呼吸消失必须立即告警"
    assert lost[0].features.get("lost_confirm_s") == 0, "确证链中应跳过防抖等待"
    assert p.guard_zone == ZONE_BLACK, "呼吸衰竭至消失应直接越级到危机状态"


# ---- 用例9：Type B 观察窗放弃（歇脚未达确认线又起身 → 不锁死疑似跌倒）----

def test_type_b_watch_abandons_partial_still():
    p = SampleProcessor(zone="living")
    t = _establish_presence(p)                     # 活动 4s：建立有人 + 活动段
    for i in range(4):                             # 继续走动，活动段 ≥ FALL_B_ACTIVE_S
        _feed(p, t + i, 0.4, br_rate=15)
    t += 4

    events = []
    for i in range(10):                            # 骤然歇脚 10s（< Type B 确认线 15s）
        events.extend(_feed(p, t + i, 0.03, br_rate=15))
    t += 10
    assert p.semantic_state == "fall", "歇脚期间观察窗应呈现疑似跌倒"
    assert not events, "未达确认时长不得产出任何跌倒类事件"

    for i in range(3):                             # 起身恢复走动
        _feed(p, t + i, 0.4, br_rate=15)
    assert p.semantic_state == "active", \
        "歇脚未达确认线又起身，观察窗必须放弃，不得锁死疑似跌倒"
    assert p._fall_watch is None


if __name__ == "__main__":
    failures = 0
    for fn in [test_stretch_no_alarm_when_breathing_unchanged,
               test_fall_alarm_when_breathing_elevated,
               test_copd_profile_shifts_elevated_threshold,
               test_empty_room_breathing_lost_no_alarm,
               test_breathing_lost_alarm_when_present,
               test_two_round_confirmation_chain,
               test_requery_wait_duration_by_profile,
               test_motion_recovery_auto_clear,
               test_breathing_lost_during_confirmation_escalates_immediately,
               test_type_b_watch_abandons_partial_still]:
        try:
            fn()
            print(f"PASS  {fn.__name__}")
        except AssertionError as exc:
            failures += 1
            print(f"FAIL  {fn.__name__}: {exc}")
    sys.exit(1 if failures else 0)
