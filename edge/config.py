"""边缘层配置：分区、活动强度阈值、状态机时间门槛。

时间门槛以「仿真秒（sim-seconds）」为单位。回放器带一个 speed 加速因子，
让"卫生间静止 15 分钟"这类真实门槛在演示时几秒内走完，而阈值语义不变。

活动强度 intensity 语义（20260806 实验方法论测试校准）：
    静止基线 ≈ 0.01~0.02   |   正常活动 ≈ 0.25~0.55   |   Type A 跌倒峰值 0.39~1.0

跌倒三分类（实验数据支撑）：
    Type A 静止中跌倒：静止 → 相对尖峰 → 静止（峰值均值 0.710，最可靠）
    Type B 移动中跌倒：持续活动 → 骤然静止（无尖峰，模式识别）
    Type C 转换中跌倒：静止 → 缓慢起身(微动) → 骤然静止（弱信号，模式识别）
"""
from __future__ import annotations

# ---- 活动强度阈值（归一化 0~1，20260806 实验校准）----
STILL_MAX = 0.05      # 低于此值视为「静止」（实测静止基线 0.002~0.016）
ACTIVE_MIN = 0.15     # 高于此值视为「活动」（Type C 微弱信号需更灵敏）
SPIKE_MIN = 0.22      # 绝对尖峰下限（闭环实测 Type A 峰值范围 0.256~0.475）
SPIKE_REL_RATIO = 4.0 # 相对尖峰：峰值 ≥ 静止基线 × 此倍数（抗环境漂移）
SPIKE_ACTIVE_MIN = 0.90  # 活动中超强尖峰（实测走动峰值最高 0.896，≥0.90 视为跌倒冲击）

# ---- 存在检测去抖（借鉴 ESPresense 迟滞逻辑）----
PRESENCE_ON_SECONDS = 3    # 连续活动多少 sim-秒判定「有人进入」
PRESENCE_OFF_SECONDS = 20  # 连续静止多少 sim-秒判定「无人离开」

# ---- 疑似跌倒判定（三分类，20260806 实验校准）----
FALL_STILL_SECONDS = 8     # Type A/C：尖峰/骤停后持续静止多少 sim-秒确认「疑似跌倒」
FALL_WATCH_WINDOW = 8      # 候选后多少 sim-秒内进入确认态才算跌倒（否则视为普通活动）
FALL_B_ACTIVE_S = 3        # Type B：跌倒前需持续活动 ≥ 此秒数（排除偶发移动）
FALL_B_STILL_S = 15        # Type B：活动后骤然静止确认时长（比 A 更严，防「走动后坐下」误报）
FALL_C_RISE_S = 2          # Type C：跌倒前「缓慢起身」微动段需持续 ≥ 此秒数
FALL_A_STILL_S = 2         # Type A：尖峰前需已静止 ≥ 此秒数（排除「起步走动」误判）
FALL_A_STILL_GAP = 3       # Type A：静止前态容忍的最大微动毛刺总秒数（静站时呼吸微动实测会产生 1 秒级毛刺）
FALL_C_ACTIVE_MAX_S = 2    # Type C：起身段内允许的短暂 active 样本数（起身发力瞬间）
FALL_C_PEAK_MIN_RATIO = 3.0  # Type C：起身峰值需 ≥ max(基线,0.005)×此倍（防「坐起又靠回」误报）
FALL_C_PEAK_MIN = 0.10    # Type C：起身峰值绝对下限（闭环实测：真候选 0.103~0.161，站立调整假候选 0.085）

# ---- 近场倒地确认（20260806 闭环验证新增）----
# 实测发现：蹲倒/跌倒在收发板近场时信号不回落（人体散射+呼吸微动，强度 0.1~0.22），
# 旧「尖峰后等静止」策略被取消。改为「尖峰后持续中高幅值」确认近场倒地。
FALL_DWELL_MIN = 0.08      # 近场滞留下沿：高于静止基线，低于此值回落视为起身恢复
FALL_DWELL_MAX = 0.80      # 近场滞留上沿：高于此值视为继续活动（非倒地）
FALL_DWELL_PEAK_RATIO = 0.6  # 滞留强度需 < 峰值×此比（真倒地信号衰减，走路离开不衰减）
FALL_DWELL_SECONDS = 6     # Type A：尖峰后滞留态持续多少 sim-秒确认近场倒地

# ---- 久滞阈值（按分区，sim-秒）----
STILL_TOO_LONG_BY_ZONE = {
    "bathroom": 15 * 60,   # 卫生间 15 分钟
    "bedroom": 120 * 60,   # 卧室白天 120 分钟
    "living": 90 * 60,     # 客厅 90 分钟
    "default": 60 * 60,
}

# ---- 呼吸频率阈值（次/分，基于医学OSINT研究）----
BR_RATE_MIN = 12       # 正常下限
BR_RATE_MAX = 20       # 正常上限
BR_RATE_ELEVATED = 20  # >此值=加快（轻度缺氧）
BR_RATE_SLOW = 8       # <此值=浅慢（重度缺氧/潮式呼吸）
BR_RATE_LOST = 3       # <此值=呼吸消失（呼吸停止）
BR_ABNORMAL_PERSIST_S = 5   # 呼吸异常态需持续 N 秒才参与告警（防单次锁频噪声，20260807 真机实测）

# ---- Zone 超时配置（sim-秒）----
ZONE2_VOICE_TIMEOUT = 15    # Zone 2 语音确认超时：急救导向（20260808 定稿：90s→30s→15s）
ZONE2_VOICE_TIMEOUT_ROUND2 = 10  # 第二轮确证等待：两轮无应答即高危进救护链
ZONE1_STILL_THRESHOLD = 30 * 60  # Zone 1 静止超时（30分钟），升级关注

# ---- Qwen 认知层配置 ----
import os
from pathlib import Path

def _load_env_file() -> None:
    """启动时解析项目根目录 .env 文件（KEY=VALUE，支持 # 注释），
    仅填充尚未设置的环境变量，不覆盖已有值。"""
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if not env_path.exists():
        return
    try:
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value
    except OSError:
        pass

_load_env_file()

QWEN_API_KEY = os.environ.get(
    "DASHSCOPE_API_KEY",
    ""  # 请设置环境变量 DASHSCOPE_API_KEY，或在项目根目录 .env 文件中配置
)
QWEN_BASE_URL = os.environ.get(
    "QWEN_BASE_URL",
    "https://dashscope.aliyuncs.com/compatible-mode/v1",  # 阿里云 DashScope 官方域名
)
QWEN_MODEL = "qwen-plus"
QWEN_ENABLED = True
QWEN_TIMEOUT_S = 10

# ---- 采样周期（回放器每隔多少 sim-秒发一个样本）----
SAMPLE_INTERVAL_S = 1

# ---- 存在证据窗口（呼吸消失告警门槛，20260806 集成测试修复）----
# 距最近一次明确有人证据（有效呼吸/活动）超过此秒数时，
# 「呼吸测不到」不再触发 Zone 4 告警（空房间本来就测不到）
PRESENCE_EVIDENCE_WINDOW_S = 120

# ---- 环境底噪动态估计（20260806 实验：开空调底噪高 17 倍，B1 0.034 vs B4 0.002）----
NOISE_FLOOR_WINDOW_S = 180   # 底噪估计滑动窗（秒）
NOISE_FLOOR_PERCENTILE = 0.60  # 窗内排序取此分位作底噪（居家多数时段静止，中低分位即底噪带）
NOISE_FLOOR_ACTIVE_CUT = 0.25  # 高于此值的样本视为活动，不参与底噪统计
NOISE_FLOOR_HIGH = 0.02      # 底噪高于此值视为嘈杂环境（空调开），启用补偿
NOISE_STILL_FACTOR = 3.0     # 嘈杂环境静止判定带上抬系数（floor×3；实测空调底噪 0.034→带 0.12，
                             # 已知局限：与噪声同量级的弱微动 ≈0.10 在空调房不可区分）
NOISE_SIGNIFICANCE_GAIN = 1.0  # 嘈杂环境 FFT 显著度要求增益（空调房 ratio≈2 → sig≈6，
                               # 补偿不宜过激，否则真呼吸也锁不定）
NOISE_SIGNIFICANCE_CAP = 8.0   # 显著度要求上限（过高则永远锁不定）

DB_PATH = "waveguard.db"

# ---- 防盗监测配置 ----
INTRUSION_ENABLED = True              # 是否启用防盗监测
INTRUSION_SILENCE_SECONDS = 300       # 无人/睡眠静默超过多少sim-秒后进入防盗模式
INTRUSION_MOTION_CONFIRM_S = 10       # 防盗模式下持续运动多少sim-秒确认入侵
INTRUSION_CAMERA_LINK = True          # 是否联动摄像头确认


def still_too_long_threshold(zone: str) -> int:
    return STILL_TOO_LONG_BY_ZONE.get(zone, STILL_TOO_LONG_BY_ZONE["default"])
