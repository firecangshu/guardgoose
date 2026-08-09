"""病历个性化模块 — 病史驱动检测策略调整。

基于陈林团队《跌倒原因与病历个性化方案》实现：
- 7种病史的Zone调整规则（心梗/脑梗/癫痫/糖尿病/帕金森/多重用药/无特殊病史）
- 语音超时时间个性化（心梗跳过语音，脑梗60s，标准30s急救导向）
- 个性化告警模板（告警内容含病史上下文+疑似原因+建议处理）
- 病历数据模型 + SQLite 持久化
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any


# ---- 病史类型枚举 ----
HX_HEART = "heart"           # 心梗/心律失常/心力衰竭
HX_STROKE = "stroke"         # 脑梗/TIA/脑出血
HX_EPILEPSY = "epilepsy"     # 癫痫
HX_DIABETES = "diabetes"     # 糖尿病
HX_PARKINSON = "parkinson"   # 帕金森
HX_ALZHEIMER = "alzheimer"   # 阿尔茨海默病
HX_HYPERTENSION = "hypertension"  # 高血压
HX_ANEMIA = "anemia"         # 严重贫血

ALL_CONDITIONS = [
    HX_HEART, HX_STROKE, HX_EPILEPSY, HX_DIABETES,
    HX_PARKINSON, HX_ALZHEIMER, HX_HYPERTENSION, HX_ANEMIA,
]

# ---- 用药类型枚举 ----
MED_ANTIHYPERTENSIVE = "antihypertensive"   # 降压药
MED_HYPOGLYCEMIC = "hypoglycemic"            # 降糖药/胰岛素
MED_SEDATIVE = "sedative"                    # 镇静催眠药
MED_ANTIDEPRESSANT = "antidepressant"        # 抗抑郁药
MED_ANTIPARKINSONIAN = "antiparkinsonian"    # 抗帕金森药
MED_ANTIEPILEPTIC = "antiepileptic"          # 抗癫痫药
MED_DIURETIC = "diuretic"                    # 利尿剂
MED_ANALGESIC = "analgesic"                  # 镇痛药

ALL_MEDICATIONS = [
    MED_ANTIHYPERTENSIVE, MED_HYPOGLYCEMIC, MED_SEDATIVE,
    MED_ANTIDEPRESSANT, MED_ANTIPARKINSONIAN, MED_ANTIEPILEPTIC,
    MED_DIURETIC, MED_ANALGESIC,
]

MULTI_MED_THRESHOLD = 4  # 多重用药阈值（≥4种）——仅档案备查，不参与实时判定

# ---- 基础病→检测修正系数表（千人千档的唯一来源）----
# 调研依据（20260806 医学OSINT）：
#   心衰 30%~60% 患者有潮式呼吸，含 5~30s 中枢性暂停；静息呼吸>25次/分提示心衰发作
#   COPD/哮喘 稳定期静息呼吸 20~24 次/分（浅快、呼气延长），>24 才是急性加重
#   贫血 代偿性呼吸加快，静息基线偏高
#   癫痫 发作期呼吸暂停 10~30s 是病症本身，发作后呼吸先恢复但不规则
#   脑梗 潮式/长吸式呼吸=脑干受累（节律异常权重加重）；偏瘫致运动幅度下降
#   帕金森 小碎步/拖步→活动强度偏低；冻结步态=活动中骤停，形似 Type B 跌倒候选
# ⚠️ 铁律（20260808 用户定稿）：基础病只会收紧各方面指标，绝不放宽——
#    有病=高风险=告警更快更敏感，宁可误报不可漏报；病理性暂停等防误报
#    放宽系数一律不得生效（build_adjustments 出口钳制兜底）。
CONDITION_COEFFICIENTS: dict[str, dict[str, Any]] = {
    HX_HEART: {},                     # 收紧项：跳过现场呼唤直升告警（build_adjustments 内实现）
    HX_STROKE: {"active_min_adjust": -0.05,
                "rhythm_abnormal_weight": True},
    HX_EPILEPSY: {},
    HX_DIABETES: {},
    HX_PARKINSON: {"active_min_adjust": -0.05},
    HX_ANEMIA: {},
    # 高血压：不加检测系数（zone3_max_stay 升级逻辑已单独实现）
}

# 字段默认值（build_adjustments 的起点，即「无病史」基准）
DEFAULT_ADJUSTMENTS: dict[str, Any] = {
    "br_elevated_adjust": 0,        # 呼吸「加快」警戒线上移量（次/分）
    "br_lost_confirm_s": 0,         # 呼吸消失需持续多少秒才告警（0=立即）
    "active_min_adjust": 0.0,       # 活动判定带下探量（负值=更灵敏认小碎步）
    "type_b_still_s": 0,            # Type B 确认时长覆盖（0=用默认 FALL_B_STILL_S）
    "skip_voice": False,            # 跳过现场语音询问（心梗）
    "voice_timeout": 15,            # 语音询问等待秒数（急救导向，20260808 定稿：90s→30s→15s）
    "requery_wait_s": 10,           # 第二轮确证等待秒数：两轮无应答即高危进救护链
    "zone3_max_stay": 0,            # 告警后多少秒未处理自动升级（0=不限）
    "rhythm_abnormal_weight": False,  # 呼吸节律异常视为恶化信号（脑梗）
    "conditions_applied": [],       # 实际生效的病史清单（供守护页展示修正痕迹）
}


def build_adjustments(profile: "MedicalProfile") -> dict[str, Any]:
    """把档案病史汇总成一份扁平修正系数（多病叠加：收紧取最严）。

    这是状态机/守护页读取修正值的唯一入口：
    - 收紧类（active_min 下探）取最严值；br_elevated_adjust 负值=下移更敏感取最低
    - 开关类（skip_voice/rhythm_weight）任一病史命中即生效
    - 铁律钳制：基础病只收紧不放宽——放宽方向的系数（加快线上移、
      呼吸消失防抖、Type B 确认拉长）无论预置还是自定义一律归零
    """
    adj = {**DEFAULT_ADJUSTMENTS, "conditions_applied": []}
    codes = []
    for code in profile.conditions:
        coef = CONDITION_COEFFICIENTS.get(code)
        if coef is None:
            coef = _custom_disease_coefficients(code)
        if coef:
            codes.append(code)
            adj["br_elevated_adjust"] = max(adj["br_elevated_adjust"],
                                            coef.get("br_elevated_adjust", 0))
            adj["br_lost_confirm_s"] = max(adj["br_lost_confirm_s"],
                                           coef.get("br_lost_confirm_s", 0))
            adj["active_min_adjust"] = min(adj["active_min_adjust"],
                                           coef.get("active_min_adjust", 0.0))
            adj["type_b_still_s"] = max(adj["type_b_still_s"],
                                        coef.get("type_b_still_s", 0))
            adj["skip_voice"] = adj["skip_voice"] or coef.get("skip_voice", False)
            adj["rhythm_abnormal_weight"] = (adj["rhythm_abnormal_weight"]
                                             or coef.get("rhythm_abnormal_weight", False))
            if coef.get("voice_timeout"):
                adj["voice_timeout"] = min(adj["voice_timeout"], coef["voice_timeout"])
            adj["zone3_max_stay"] = max(adj["zone3_max_stay"],
                                        coef.get("zone3_max_stay", 0))
    # 心梗跳过语音（与 is_high_risk 一致，兜底防系数表漏配）
    if HX_HEART in profile.conditions:
        adj["skip_voice"] = True
        adj["voice_timeout"] = 0
    # 铁律钳制（20260808 定稿）：基础病只收紧不放宽——放宽方向的系数
    # （加快警戒线上移、呼吸消失防抖、Type B 确认拉长，默认 FALL_B_STILL_S=15）
    # 无论预置还是自定义一律归零，宁误报不漏报
    if adj["br_elevated_adjust"] > 0:
        adj["br_elevated_adjust"] = 0
    adj["br_lost_confirm_s"] = 0
    if adj["type_b_still_s"] > 15:
        adj["type_b_still_s"] = 0
    adj["conditions_applied"] = codes
    return adj


def _custom_disease_coefficients(code: str) -> dict[str, Any] | None:
    """自定义疾病的检测修正系数（开放式入口，未配置则为空）。"""
    d = _custom_diseases.get(code)
    if d is None:
        return None
    coef: dict[str, Any] = {}
    if d.br_elevated_adjust:
        coef["br_elevated_adjust"] = d.br_elevated_adjust
    if d.br_lost_confirm_s:
        coef["br_lost_confirm_s"] = d.br_lost_confirm_s
    if d.active_min_adjust:
        coef["active_min_adjust"] = d.active_min_adjust
    if d.type_b_still_s:
        coef["type_b_still_s"] = d.type_b_still_s
    if d.skip_voice:
        coef["skip_voice"] = True
    if d.voice_timeout_override:
        coef["voice_timeout"] = d.voice_timeout_override
    if d.zone3_max_stay:
        coef["zone3_max_stay"] = d.zone3_max_stay
    return coef or None


# ---- 开放式疾病注册表 ----
@dataclass
class CustomDisease:
    """自定义疾病定义（开放式接口，覆盖预设7种之外的疾病）。"""
    code: str                        # 疾病代码（英文唯一标识，如 "copd"）
    name: str                        # 疾病名称（中文，如"慢性阻塞性肺病"）
    category: str = ""               # 疾病类别（如"呼吸系统"/"心血管"/"代谢"/"神经"）
    description: str = ""            # 疾病特征描述
    fall_risk_note: str = ""         # 对跌倒的影响说明
    breathing_impact: str = ""       # 对呼吸的影响说明
    advice: list[str] = field(default_factory=list)  # 告警时的建议处理
    voice_timeout_override: int = 0  # 语音超时覆盖（0=不覆盖，用默认值）
    skip_voice: bool = False         # 是否跳过语音确认
    zone3_max_stay: int = 0          # Zone 3 最大停留时间（0=不限）
    # ---- 检测修正系数（千人千档，0=不修正）----
    br_elevated_adjust: int = 0      # 呼吸「加快」警戒线调整（负值=下移更敏感；正值放宽被钳制）
    br_lost_confirm_s: int = 0       # 呼吸消失需持续秒数（铁律钳制恒为 0=立即）
    active_min_adjust: float = 0.0   # 活动判定带偏移（偏瘫/小碎步用负值下探）
    type_b_still_s: int = 0          # Type B 确认时长覆盖（>15 放宽方向被钳制）

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# 全局注册表：自定义疾病（运行时可动态增删，启动时从 SQLite 加载）
_custom_diseases: dict[str, CustomDisease] = {}


def register_disease(disease: CustomDisease, conn: sqlite3.Connection | None = None) -> None:
    """注册一个自定义疾病到全局表（可选同步持久化）。"""
    _custom_diseases[disease.code] = disease
    if conn is not None:
        _persist_custom_disease(conn, disease)


def unregister_disease(code: str, conn: sqlite3.Connection | None = None) -> bool:
    """移除一个自定义疾病（可选同步删除持久化记录）。"""
    if code in _custom_diseases:
        del _custom_diseases[code]
        if conn is not None:
            conn.execute("DELETE FROM custom_diseases WHERE code=?", (code,))
            conn.commit()
        return True
    return False


def get_custom_disease(code: str) -> CustomDisease | None:
    """获取一个自定义疾病定义。"""
    return _custom_diseases.get(code)


def list_custom_diseases() -> list[dict[str, Any]]:
    """列出所有已注册的自定义疾病。"""
    return [d.to_dict() for d in _custom_diseases.values()]


def clear_custom_diseases(conn: sqlite3.Connection | None = None) -> None:
    """清空自定义疾病注册表（注销/全部清零用）。"""
    _custom_diseases.clear()
    if conn is not None:
        conn.execute("DELETE FROM custom_diseases;")
        conn.commit()


def get_disease_strategy(code: str) -> dict[str, Any]:
    """获取疾病的告警策略（含预设疾病和自定义疾病）。

    优先查自定义注册表，找不到再查预设规则。
    """
    custom = _custom_diseases.get(code)
    if custom:
        return {
            "source": "custom",
            "name": custom.name,
            "category": custom.category,
            "description": custom.description,
            "fall_risk_note": custom.fall_risk_note,
            "breathing_impact": custom.breathing_impact,
            "advice": custom.advice,
            "voice_timeout_override": custom.voice_timeout_override,
            "skip_voice": custom.skip_voice,
            "zone3_max_stay": custom.zone3_max_stay,
            "br_elevated_adjust": custom.br_elevated_adjust,
            "br_lost_confirm_s": custom.br_lost_confirm_s,
            "active_min_adjust": custom.active_min_adjust,
            "type_b_still_s": custom.type_b_still_s,
        }

    # 预设疾病策略
    preset_map = {
        HX_HEART: {"source": "preset", "name": "心梗/心律失常", "category": "心血管",
                    "fall_risk_note": "心源性晕厥风险极高，恶化极快（20-30秒内呼吸可能停止）",
                    "breathing_impact": "心衰可导致呼吸加快或潮式呼吸",
                    "advice": ["立即电话联系老人确认意识", "如无应答立即前往查看", "准备拨打120"],
                    "voice_timeout_override": 0, "skip_voice": True, "zone3_max_stay": 0},
        HX_STROKE: {"source": "preset", "name": "脑梗/TIA", "category": "神经",
                     "fall_risk_note": "脑源性晕厥，可能意识清醒但无法回应",
                     "breathing_impact": "脑干梗死可导致呼吸节律异常",
                     "advice": ["立即电话联系老人", "可能清醒但肢体无法动弹", "不建议等待语音回应"],
                     "voice_timeout_override": 0, "skip_voice": False, "zone3_max_stay": 0},
        HX_EPILEPSY: {"source": "preset", "name": "癫痫", "category": "神经",
                       "fall_risk_note": "癫痫发作期可跌倒，发作后意识模糊",
                       "breathing_impact": "发作期呼吸可能暂停>30秒",
                       "advice": ["保护老人头部", "保持侧卧位防止误吸", "如呼吸>30s未恢复拨打120"],
                       "voice_timeout_override": 0, "skip_voice": False, "zone3_max_stay": 0},
        HX_DIABETES: {"source": "preset", "name": "糖尿病", "category": "代谢",
                       "fall_risk_note": "低血糖可导致跌倒和意识丧失",
                       "breathing_impact": "低血糖昏迷时呼吸可能浅慢",
                       "advice": ["立即电话联系", "可能为低血糖", "准备含糖食物", "如无法唤醒拨打120"],
                       "voice_timeout_override": 0, "skip_voice": False, "zone3_max_stay": 0},
        HX_PARKINSON: {"source": "preset", "name": "帕金森", "category": "神经",
                        "fall_risk_note": "体位性低血压和步态异常导致跌倒",
                        "breathing_impact": "通常不影响呼吸频率",
                        "advice": ["确认意识是否清醒", "缓慢扶起防止再次跌倒"],
                        "voice_timeout_override": 0, "skip_voice": False, "zone3_max_stay": 0},
        HX_HYPERTENSION: {"source": "preset", "name": "高血压", "category": "心血管",
                           "fall_risk_note": "高血压急症可导致跌倒",
                           "breathing_impact": "通常不影响呼吸",
                           "advice": ["确认老人意识和血压", "如意识模糊立即前往"],
                           "voice_timeout_override": 0, "skip_voice": False, "zone3_max_stay": 600},
        HX_ALZHEIMER: {"source": "preset", "name": "阿尔茨海默病", "category": "神经",
                        "fall_risk_note": "认知障碍导致判断力下降，跌倒风险增加",
                        "breathing_impact": "通常不影响呼吸",
                        "advice": ["确认老人意识和位置", "可能无法准确描述情况"],
                        "voice_timeout_override": 0, "skip_voice": False, "zone3_max_stay": 0},
        HX_ANEMIA: {"source": "preset", "name": "严重贫血", "category": "血液",
                     "fall_risk_note": "贫血导致脑供氧不足，可引起晕厥跌倒",
                     "breathing_impact": "代偿性呼吸加快",
                     "advice": ["确认老人意识", "可能为脑供氧不足", "如意识模糊拨打120"],
                     "voice_timeout_override": 0, "skip_voice": False, "zone3_max_stay": 0},
    }
    return preset_map.get(code, {"source": "unknown", "name": code, "category": "",
                                  "description": "", "fall_risk_note": "", "breathing_impact": "",
                                  "advice": [], "voice_timeout_override": 0,
                                  "skip_voice": False, "zone3_max_stay": 0})


@dataclass
class MedicalProfile:
    """老人健康档案（病历）。"""
    elder_name: str = "妈妈"
    age: int = 75
    weight_kg: float = 0.0        # 体重（0=未填写），影响跌倒冲击与久滞风险评估
    relationship: str = ""          # 监护人与老人的关系（son/daughter/…）
    health_status: str = ""         # 身体状态标准分类（good/chronic_stable/…）
    conditions: list[str] = field(default_factory=list)    # 既往疾病
    medications: list[str] = field(default_factory=list)   # 当前用药
    fall_history: int = 0          # 既往跌倒次数
    syncope_history: int = 0       # 既往晕厥次数
    family_sudden_death: bool = False  # 心脏猝死家族史
    wake_time: str = "06:30"       # 起床时间
    bed_time: str = "21:30"        # 就寝时间
    address: str = ""              # 老人居住地址（报警120时同步给急救中心）
    elder_phone: str = ""          # 守护人电话（老人直线，告警时第一时间致电确认意识；存 emergency_phone 列）
    emergency_phones: list[str] = field(default_factory=list)  # 紧急联系电话（最多3个，逐个降级拨打）
    updated_at: str = ""

    @property
    def is_multi_medication(self) -> bool:
        return len(self.medications) >= MULTI_MED_THRESHOLD

    @property
    def is_high_risk(self) -> bool:
        """高危人群：心梗/心律失常 → 跌倒时跳过语音直接Zone 3"""
        return HX_HEART in self.conditions

    @property
    def voice_timeout(self) -> int:
        """语音确认超时时间（秒）。"""
        if HX_HEART in self.conditions:
            return 0   # 跳过语音
        if HX_STROKE in self.conditions or HX_EPILEPSY in self.conditions or HX_DIABETES in self.conditions:
            return 60  # 高危延长至60s（可能意识清醒但无法回应）
        return 30      # 标准急救导向30s（20260808 定稿，原 90s 推翻）

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["is_multi_medication"] = self.is_multi_medication
        d["is_high_risk"] = self.is_high_risk
        d["voice_timeout"] = self.voice_timeout
        return d


# ---- 病史→Zone调整规则 ----
def adjust_zone_for_profile(fall_event: dict[str, Any], profile: MedicalProfile) -> dict[str, Any]:
    """根据病历调整跌倒事件的Zone分级和告警内容。

    返回: {
        "zone": int,               # 调整后的Zone
        "skip_voice": bool,        # 是否跳过语音确认
        "voice_timeout": int,      # 语音超时秒数
        "suspected_cause": str,    # 疑似原因
        "advice": list[str],       # 建议处理方式
        "alert_tag": str,          # 告警标签（心源性/脑源性/药源性等）
    }
    """
    br_state = fall_event.get("breathing_state", "normal")
    br_rate = fall_event.get("breathing_rate", 0)

    # 默认值
    result = {
        "zone": fall_event.get("guard_zone", 2),
        "skip_voice": False,
        "voice_timeout": profile.voice_timeout,
        "suspected_cause": "",
        "advice": [],
        "alert_tag": "",
    }

    # 心梗/心律失常 → 跳过语音，直接Zone 3
    if HX_HEART in profile.conditions:
        result["skip_voice"] = True
        result["voice_timeout"] = 0
        result["alert_tag"] = "心源性"
        if br_state in ("elevated", "irregular", "shallow"):
            result["zone"] = 3
            result["suspected_cause"] = "心源性晕厥（心律失常/心肌缺血）"
            result["advice"] = [
                "立即电话联系老人确认意识",
                "如无应答，立即前往查看",
                "检查脉搏和呼吸",
                "准备拨打120",
            ]
        elif br_state == "lost":
            result["zone"] = 4
            result["suspected_cause"] = "阿斯综合征/心源性猝死"
            result["advice"] = [
                "立即前往老人身边",
                "如发现无意识无呼吸，立即开始CPR",
                "如有AED，立即使用",
            ]
        else:
            # 心梗+呼吸正常 → 仍然直接Zone 3（可能是恶化前的窗口）
            result["zone"] = 3
            result["suspected_cause"] = "心源性晕厥（可能处于恶化前窗口期）"
            result["advice"] = [
                "立即电话联系老人确认意识",
                "心源性晕厥恶化极快（20-30秒内呼吸可能停止）",
                "准备拨打120",
            ]

    # 脑梗/中风 → 语音超时60s
    elif HX_STROKE in profile.conditions:
        result["voice_timeout"] = 60
        result["alert_tag"] = "脑源性"
        if br_state in ("elevated", "irregular", "shallow"):
            result["zone"] = 3
            result["suspected_cause"] = "脑源性晕厥（脑缺血加重）"
            result["advice"] = [
                "立即电话联系老人",
                "可能意识清醒但无法回应（肢体无力/言语障碍）",
                "立即前往查看",
            ]
        else:
            result["suspected_cause"] = "脑梗/中风（可能清醒但无法回应）"
            result["advice"] = [
                "立即电话联系老人",
                "脑梗老人可能意识清醒但肢体无法动弹",
                "不建议等待语音回应",
            ]

    # 癫痫 → 语音超时60s
    elif HX_EPILEPSY in profile.conditions:
        result["voice_timeout"] = 60
        result["alert_tag"] = "癫痫"
        if br_state == "lost":
            result["zone"] = 4
            result["suspected_cause"] = "癫痫发作，呼吸暂停"
            result["advice"] = [
                "癫痫发作期呼吸可能暂停>30秒",
                "保护老人头部，清理周围危险物品",
                "如呼吸超过30秒未恢复，立即拨打120",
            ]
        elif br_state in ("elevated", "irregular", "shallow"):
            result["zone"] = 3
            result["suspected_cause"] = "癫痫发作后状态"
            result["advice"] = [
                "癫痫发作后呼吸可能仍不规律",
                "持续监测呼吸状态",
                "保持侧卧位防止误吸",
            ]

    # 糖尿病 → 检查用药时间
    elif HX_DIABETES in profile.conditions:
        result["voice_timeout"] = 60
        result["alert_tag"] = "代谢性"
        if br_state == "normal":
            result["suspected_cause"] = "低血糖（排查用药后/餐前）"
            result["advice"] = [
                "立即电话联系老人",
                "如意识模糊，可能为低血糖",
                "准备含糖食物/葡萄糖",
                "如无法唤醒，拨打120",
            ]
        elif br_state == "lost":
            result["zone"] = 4
            result["suspected_cause"] = "低血糖昏迷"
            result["advice"] = [
                "可能低血糖昏迷",
                "立即前往查看",
                "如确认低血糖，静脉注射葡萄糖",
            ]

    # 帕金森 → 标准流程
    elif HX_PARKINSON in profile.conditions:
        result["alert_tag"] = "神经源性"
        result["suspected_cause"] = "体位性低血压/步态异常"
        result["advice"] = [
            "帕金森跌倒多为体位性低血压",
            "确认老人意识是否清醒",
            "缓慢扶起，防止再次跌倒",
        ]

    # 无特殊病史（用药不参与判定：药源性分支已按产品边界移除，
    # medications 仅档案备查，供急救时提供给120）
    else:
        if br_state in ("elevated", "irregular", "shallow"):
            result["zone"] = 3
            result["suspected_cause"] = "跌倒+呼吸异常"
            result["advice"] = ["立即联系老人确认情况", "如无应答前往查看"]
        elif br_state == "lost":
            result["zone"] = 4
            result["suspected_cause"] = "呼吸骤停"
            result["advice"] = ["立即拨打120", "前往老人身边"]
        else:
            result["suspected_cause"] = "环境性跌倒"
            result["advice"] = ["联系老人确认是否需要帮助"]

    return result


# ---- SQLite 病历存储 ----
_MEDICAL_SCHEMA = """
CREATE TABLE IF NOT EXISTS medical_profile (
    id          INTEGER PRIMARY KEY DEFAULT 1,
    elder_name  TEXT NOT NULL DEFAULT '妈妈',
    age         INTEGER DEFAULT 75,
    weight_kg   REAL DEFAULT 0,
    relationship TEXT DEFAULT '',
    health_status TEXT DEFAULT '',
    conditions  TEXT DEFAULT '[]',
    medications TEXT DEFAULT '[]',
    fall_history INTEGER DEFAULT 0,
    syncope_history INTEGER DEFAULT 0,
    family_sudden_death INTEGER DEFAULT 0,
    wake_time   TEXT DEFAULT '06:30',
    bed_time    TEXT DEFAULT '21:30',
    address     TEXT DEFAULT '',
    emergency_phone TEXT DEFAULT '',
    emergency_phones TEXT DEFAULT '[]',
    updated_at  TEXT
);

CREATE TABLE IF NOT EXISTS custom_diseases (
    code        TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    category    TEXT DEFAULT '',
    description TEXT DEFAULT '',
    fall_risk_note TEXT DEFAULT '',
    breathing_impact TEXT DEFAULT '',
    advice      TEXT DEFAULT '[]',
    voice_timeout_override INTEGER DEFAULT 0,
    skip_voice  INTEGER DEFAULT 0,
    zone3_max_stay INTEGER DEFAULT 0,
    br_elevated_adjust INTEGER DEFAULT 0,
    br_lost_confirm_s INTEGER DEFAULT 0,
    active_min_adjust REAL DEFAULT 0,
    type_b_still_s INTEGER DEFAULT 0,
    created_at  TEXT
);
"""


def init_medical_db(conn: sqlite3.Connection) -> None:
    """在现有 SQLite 连接上建病历表。"""
    conn.executescript(_MEDICAL_SCHEMA)
    # 旧库迁移：补充新增列
    cols = {r[1] for r in conn.execute("PRAGMA table_info(medical_profile)")}
    for col, ddl in (
        ("relationship", "ALTER TABLE medical_profile ADD COLUMN relationship TEXT DEFAULT ''"),
        ("health_status", "ALTER TABLE medical_profile ADD COLUMN health_status TEXT DEFAULT ''"),
        ("weight_kg", "ALTER TABLE medical_profile ADD COLUMN weight_kg REAL DEFAULT 0"),
        ("address", "ALTER TABLE medical_profile ADD COLUMN address TEXT DEFAULT ''"),
        ("emergency_phone", "ALTER TABLE medical_profile ADD COLUMN emergency_phone TEXT DEFAULT ''"),
        ("emergency_phones", "ALTER TABLE medical_profile ADD COLUMN emergency_phones TEXT DEFAULT '[]'"),
    ):
        if col not in cols:
            conn.execute(ddl)
    # 旧库迁移：自定义疾病表补检测修正系数列
    cd_cols = {r[1] for r in conn.execute("PRAGMA table_info(custom_diseases)")}
    for col, ddl in (
        ("br_elevated_adjust", "ALTER TABLE custom_diseases ADD COLUMN br_elevated_adjust INTEGER DEFAULT 0"),
        ("br_lost_confirm_s", "ALTER TABLE custom_diseases ADD COLUMN br_lost_confirm_s INTEGER DEFAULT 0"),
        ("active_min_adjust", "ALTER TABLE custom_diseases ADD COLUMN active_min_adjust REAL DEFAULT 0"),
        ("type_b_still_s", "ALTER TABLE custom_diseases ADD COLUMN type_b_still_s INTEGER DEFAULT 0"),
    ):
        if col not in cd_cols:
            conn.execute(ddl)
    # 插入默认记录（如果表为空）
    count = conn.execute("SELECT COUNT(*) FROM medical_profile").fetchone()[0]
    if count == 0:
        conn.execute(
            "INSERT INTO medical_profile (id, elder_name, age, conditions, medications, updated_at) "
            "VALUES (1, '妈妈', 75, '[]', '[]', ?)",
            (datetime.now().astimezone().isoformat(timespec="seconds"),),
        )
    # 启动时把已确认的自定义疾病从库里加载回注册表（档案备份恢复）
    for row in conn.execute("SELECT * FROM custom_diseases"):
        keys = row.keys()
        disease = CustomDisease(
            code=row["code"], name=row["name"], category=row["category"] or "",
            description=row["description"] or "", fall_risk_note=row["fall_risk_note"] or "",
            breathing_impact=row["breathing_impact"] or "",
            advice=json.loads(row["advice"] or "[]"),
            voice_timeout_override=row["voice_timeout_override"] or 0,
            skip_voice=bool(row["skip_voice"]),
            zone3_max_stay=row["zone3_max_stay"] or 0,
            br_elevated_adjust=(row["br_elevated_adjust"] or 0) if "br_elevated_adjust" in keys else 0,
            br_lost_confirm_s=(row["br_lost_confirm_s"] or 0) if "br_lost_confirm_s" in keys else 0,
            active_min_adjust=(row["active_min_adjust"] or 0.0) if "active_min_adjust" in keys else 0.0,
            type_b_still_s=(row["type_b_still_s"] or 0) if "type_b_still_s" in keys else 0,
        )
        _custom_diseases[disease.code] = disease
    conn.commit()


def _persist_custom_disease(conn: sqlite3.Connection, disease: CustomDisease) -> None:
    """把自定义疾病写入 SQLite（档案备份）。"""
    conn.execute(
        "INSERT OR REPLACE INTO custom_diseases "
        "(code, name, category, description, fall_risk_note, breathing_impact, "
        " advice, voice_timeout_override, skip_voice, zone3_max_stay, "
        " br_elevated_adjust, br_lost_confirm_s, active_min_adjust, type_b_still_s, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (disease.code, disease.name, disease.category, disease.description,
         disease.fall_risk_note, disease.breathing_impact,
         json.dumps(disease.advice, ensure_ascii=False),
         disease.voice_timeout_override, int(disease.skip_voice),
         disease.zone3_max_stay,
         disease.br_elevated_adjust, disease.br_lost_confirm_s,
         disease.active_min_adjust, disease.type_b_still_s,
         datetime.now().astimezone().isoformat(timespec="seconds")),
    )
    conn.commit()


def _load_emergency_phones(row: sqlite3.Row) -> list[str]:
    """读紧急电话列表（最多3个）。emergency_phone 单列现为守护人电话（老人直线），不再混入顺位名单。"""
    phones: list[str] = []
    if "emergency_phones" in row.keys() and row["emergency_phones"]:
        try:
            phones = [p for p in json.loads(row["emergency_phones"]) if p]
        except (TypeError, ValueError):
            phones = []
    return phones[:3]


def load_profile(conn: sqlite3.Connection) -> MedicalProfile:
    """从数据库加载病历。"""
    row = conn.execute("SELECT * FROM medical_profile WHERE id=1").fetchone()
    if row is None:
        return MedicalProfile()
    return MedicalProfile(
        elder_name=row["elder_name"],
        age=row["age"],
        weight_kg=float(row["weight_kg"] or 0) if "weight_kg" in row.keys() else 0.0,
        relationship=(row["relationship"] if "relationship" in row.keys() else "") or "",
        health_status=(row["health_status"] if "health_status" in row.keys() else "") or "",
        conditions=json.loads(row["conditions"]),
        medications=json.loads(row["medications"]),
        fall_history=row["fall_history"],
        syncope_history=row["syncope_history"],
        family_sudden_death=bool(row["family_sudden_death"]),
        wake_time=row["wake_time"],
        bed_time=row["bed_time"],
        address=(row["address"] if "address" in row.keys() else "") or "",
        elder_phone=(row["emergency_phone"] if "emergency_phone" in row.keys() else "") or "",
        emergency_phones=_load_emergency_phones(row),
        updated_at=row["updated_at"] or "",
    )


def save_profile(conn: sqlite3.Connection, profile: MedicalProfile) -> None:
    """保存病历到数据库。"""
    conn.execute(
        "UPDATE medical_profile SET "
        "elder_name=?, age=?, weight_kg=?, relationship=?, health_status=?, conditions=?, medications=?, "
        "fall_history=?, syncope_history=?, family_sudden_death=?, "
        "wake_time=?, bed_time=?, address=?, emergency_phone=?, emergency_phones=?, updated_at=? "
        "WHERE id=1",
        (profile.elder_name, profile.age, profile.weight_kg,
         profile.relationship, profile.health_status,
         json.dumps(profile.conditions, ensure_ascii=False),
         json.dumps(profile.medications, ensure_ascii=False),
         profile.fall_history, profile.syncope_history,
         int(profile.family_sudden_death),
         profile.wake_time, profile.bed_time,
         profile.address, profile.elder_phone,
         json.dumps(profile.emergency_phones, ensure_ascii=False),
         datetime.now().astimezone().isoformat(timespec="seconds")),
    )
    conn.commit()
