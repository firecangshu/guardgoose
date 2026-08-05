<script setup lang="ts">
import { ref, computed, watch, onMounted, onUnmounted } from 'vue'
import { showToast, showDialog, showConfirmDialog, closeDialog } from 'vant'
import { useGuardianStore } from '../stores/guardian'

const store = useGuardianStore()
/** 折叠详情区：探测器诊断，默认收起，首屏只留核心数据；事件统一在事件库看 */
const detailOpen = ref<string[]>([])

onMounted(() => store.init())

/** 展开探测器面板时拉取诊断数据 */
async function onCollapseChange(names: string[]) {
  if (names.includes('device')) await store.loadDiagnosis()
}

/** 手动刷新：去缓存强拉最新数据，并提示是否卡住 */
async function onRefresh() {
  showToast(await store.refreshAll())
}

/** 大卡信号标签：仿手机信号格隐喻，格数表强度、实心/空心分真假——
 * 接入中（真实接入刚开启，检测中）/ 真实信号（满格绿）/ 信号弱（2格橙）/
 * 无信号（全灰）/ 虚拟信号（满格但空心蓝描边）/ 待机（两接入都未开启） */
const signalInfo = computed(() => {
  if (store.realEnabled) {
    if (store.deviceState === '') return { kind: 'connecting', bars: 0, text: '真实信号接入中' }
    if (store.deviceState === 'connected') return { kind: 'real', bars: 4, text: '真实信号' }
    if (store.deviceState === 'weak') return { kind: 'weak', bars: 2, text: '信号弱' }
    return { kind: 'none', bars: 0, text: '无信号' }
  }
  if (store.demoEnabled) {
    if (store.demoTransition) {
      // 重新接入剧本：先“无信号”（全灰），再“接入中”（呼吸动画）
      if (store.demoTransLost) return { kind: 'none', bars: 0, text: '无信号' }
      return { kind: 'connecting', bars: 0, text: store.demoTransition }
    }
    return { kind: 'virtual', bars: 4, text: '虚拟信号' }
  }
  if (store.standby) {
    return { kind: 'standby', bars: 0, text: '待机' }
  }
  return { kind: 'none', bars: 0, text: '无信号' }
})

/** 真实接入开关（优先级最高，后端自动关演示） */
async function onRealChange(v: boolean | string) {
  showToast(await store.toggleReal(Boolean(v)))
}

/** 演示开关：开启时默认接入第一个场景 */
async function onDemoChange(v: boolean | string) {
  const first = store.demoScenarios[0]?.scenario || ''
  showToast(await store.toggleDemo(Boolean(v), first))
}

/** 切换演示场景 */
async function onPickScenario(s: string) {
  showToast(await store.toggleDemo(true, s))
}

/* ---- 告警并入守护页：当前告警置顶展示，处置动作随身可用 ---- */
const ALERT_LEVEL_COLOR: Record<string, string> = {
  info: '#1989fa',
  yellow: '#ff9f00',
  red: '#ee0a24',
  black: '#000000',
}
async function handleAlertCall120() {
  // 直接跳转拨号 120
  window.location.href = 'tel:120'
}
/** 紧急联系三级降级拨打：第一个打不通自动切第二个，再打不通切第三个。
 *  每级限时等待，超时无应答自动降级；也可手动点“打不通”提前切换。 */
const EMERGENCY_CALL_TIMEOUT = 8000
const EMERGENCY_LABELS = ['第一', '第二', '第三']

async function handleAlertCallEmergency() {
  const phones = (store.profile?.emergency_phones || []).filter(p => p).slice(0, 3)
  if (!phones.length) {
    showDialog({ title: '紧急联系', message: `未填写紧急电话，正在拨打${store.elderName}的电话...` })
    return
  }
  await callEmergencyStep(phones, 0)
}

async function callEmergencyStep(phones: string[], index: number) {
  if (index >= phones.length) {
    showDialog({
      title: '紧急联系',
      message: '三个紧急电话均未接通，请直接拨打120或联系邻居、社区上门查看。',
    })
    return
  }
  const phone = phones[index]
  const last = index === phones.length - 1
  // 限时等待：超时无应答自动降级到下一个号码
  let autoDowngraded = false
  const timer = setTimeout(() => {
    autoDowngraded = true
    closeDialog()
    callEmergencyStep(phones, index + 1)
  }, EMERGENCY_CALL_TIMEOUT)
  try {
    // 确认=打不通提前切换，取消=挂断终止整条链
    const notReached = await showConfirmDialog({
      title: `紧急联系（${EMERGENCY_LABELS[index]}个）`,
      message: `正在拨打 ${phone}...${last ? '' : '\n无应答将自动切换下一个号码'}`,
      confirmButtonText: '打不通',
      cancelButtonText: '挂断',
    })
    clearTimeout(timer)
    if (!autoDowngraded && notReached) {
      await callEmergencyStep(phones, index + 1)
    }
  } catch {
    // 用户点“挂断”终止降级链；或超时已自动降级，不重复处理
    clearTimeout(timer)
  }
}

/** 真实接入信号检测三态：开启后先轮询探测信号，得出三种结果——
 * ① 接上了·信号良好（connected）
 * ② 没接上·信号微弱（weak）
 * ③ 没信号（disconnected）
 * ②③ 立即提醒核查信号问题；30 秒仍未恢复再催办一次，
 * 明示可能影响监测效果；恢复良好后全部重置 */
const realAlerted = ref(false)
const realWarned = ref(false)
let warnTimer: ReturnType<typeof setTimeout> | null = null
const REAL_FOLLOWUP_MS = 30000
function clearWarnTimer() {
  if (warnTimer) { clearTimeout(warnTimer); warnTimer = null }
}
watch(() => [store.realEnabled, store.deviceState] as const, ([real, state]) => {
  if (!real) {  // 真实接入关闭：提醒机制全部归位
    realAlerted.value = false; realWarned.value = false; clearWarnTimer()
    return
  }
  if (state === 'connected') {  // ① 接上了·信号良好
    if (realAlerted.value) showToast('信号已恢复良好，监测正常运行')
    realAlerted.value = false; realWarned.value = false; clearWarnTimer()
    return
  }
  if (!state) return  // 首次检测尚未返回，等轮询结果
  // ②③ 异常态：首次发现立即提醒核查，并自动展开诊断面板
  if (!realAlerted.value) {
    realAlerted.value = true
    if (!detailOpen.value.includes('device')) detailOpen.value = [...detailOpen.value, 'device']
    store.loadDiagnosis()
    showToast(state === 'weak'
      ? '信号微弱（不良）：请核查网络与探测器收发两端'
      : '未检测到探测器信号：请核查设备供电与网络')
    // 30 秒后若仍未恢复 → 催办提醒，明示影响监测效果
    clearWarnTimer()
    warnTimer = setTimeout(() => {
      if (!store.realEnabled || store.deviceState === 'connected') return
      if (realWarned.value) return
      realWarned.value = true
      showToast('信号问题持续未恢复，可能影响监测效果，请尽快核查网络与设备')
    }, REAL_FOLLOWUP_MS)
  }
}, { immediate: true })
onUnmounted(clearWarnTimer)

/** 当前演示场景名（解读台开播日志用） */
const currentScenarioLabel = computed(() =>
  store.demoScenarios.find(s => s.scenario === store.demoScenario)?.label || '未选择')

/** 场景长条：左侧展示当前场景状态（长条+四格位置指示），
 * 右侧切换键按顺序循环切换四个场景，切一个走一次重接入剧本 */
const scenarioIndex = computed(() =>
  store.demoScenarios.findIndex(s => s.scenario === store.demoScenario))
/** 场景主色：随风险程度加深，长条左侧色带一眼分级 */
const SCENARIO_COLORS: Record<string, string> = {
  test_normal_motion: '#07c160',
  day_fall: '#ff976a',
  day_zone2_timeout: '#ee0a24',
  day_breathing_lost: '#82101f',
}
const scenarioColor = computed(() =>
  SCENARIO_COLORS[store.demoScenario] || '#969799')
function onNextScenario() {
  const list = store.demoScenarios
  if (!list.length) return
  const next = list[(scenarioIndex.value + 1) % list.length]
  onPickScenario(next.scenario)
}

/* ---- 信号解读台：控制台式滚动日志，固定在信号展示框下方 ----
 * 规则：平稳状态不解读（不打扰）；异常波动自动解读 + 智能分析 + 决策建议 */
interface LogEntry {
  id: number
  time: string
  level: 'info' | 'warn' | 'danger'
  text: string
  analysis?: string
  advice?: string
}
const logs = ref<LogEntry[]>([])
let logSeq = 0
/** 解读台展开状态：默认收起——常驻只显示最近两行，展开看全部细节 */
const consoleOpen = ref(false)
const consoleDisplayLogs = computed(() =>
  consoleOpen.value ? logs.value : logs.value.slice(-2))
function pushLog(level: LogEntry['level'], text: string, analysis?: string, advice?: string) {
  const t = new Date()
  const pad = (n: number) => String(n).padStart(2, '0')
  logs.value.unshift({
    id: ++logSeq,
    time: `${pad(t.getHours())}:${pad(t.getMinutes())}:${pad(t.getSeconds())}`,
    level, text, analysis, advice,
  })
  if (logs.value.length > 30) logs.value.pop()  // 只留最近 30 条，防内存溢长
}

/** 守护等级变化解读：升级必解读，降回平稳只报一句“解除” */
watch(() => store.guardZone, (z, prev) => {
  if (!store.monitoringLive || z === prev || prev === undefined) return
  if (z <= 0) {
    if (prev > 0) pushLog('info', '异常解除 · 信号波动回归平稳区间，恢复静默采集')
    return  // 平稳状态不解读
  }
  const rate = store.breathingRate > 0 ? `${store.breathingRate} 次/分` : '暂无读数'
  const inten = store.intensity.toFixed(2)
  if (z === 1) {
    pushLog('warn', '监测到轻微异常波动',
      `呼吸${store.breathingInfo.label}（${rate}），活动强度 ${inten}，波动超出常态区间`,
      '系统持续观察中，暂无需介入；若波动升级会立即在此提示')
  } else if (z === 2) {
    pushLog('warn', '疑似跌倒 · 已进入语音确证流程',
      '信号出现剧烈冲击后持续静止，符合跌倒波形特征，正在向老人发起语音问询',
      '请留意语音确证结果；如长时间无回应，建议直接电话联系')
  } else if (z === 3) {
    pushLog('danger', '确认异常 · 已触发告警',
      `跌倒或呼吸异常成立（呼吸${store.breathingInfo.label}，${rate}）`,
      '请立即电话确认老人状况，联系不上时请就近亲属或邻居上门查看')
  } else {
    pushLog('danger', '紧急 · 呼吸消失或多项异常叠加',
      '长时间未检测到呼吸起伏，属最高级别风险信号',
      '请立即拨打 120，并同步通知就近亲属，保持电话持续呼叫')
  }
})

/** 呼吸节律变化解读：异常即报，恢复只报一句 */
watch(() => store.breathingState, (s, prev) => {
  if (!store.monitoringLive || s === prev || prev === undefined) return
  const rate = store.breathingRate > 0 ? `${store.breathingRate} 次/分` : '暂无读数'
  if (s === 'normal') {
    if (prev && prev !== 'normal') pushLog('info', `呼吸节律恢复正常（${rate}）`)
    return
  }
  if (s === 'elevated' || s === 'irregular') {
    pushLog('warn', `呼吸节律异常 · ${store.breathingInfo.label}`,
      `当前 ${rate}，超出安静状态 12~20 次/分的常态区间`,
      '可能是活动、情绪或身体不适引起，建议电话问询确认')
  } else if (s === 'shallow') {
    pushLog('danger', '呼吸幅度明显减弱',
      '胸廓起伏信号变浅，可能存在呼吸抑制风险',
      '请尽快电话确认老人状态，无回应立即安排上门')
  } else if (s === 'lost') {
    pushLog('danger', '呼吸信号消失',
      '持续未检测到呼吸起伏，系统同步进入紧急判定',
      '请立即拨打 120 并通知就近亲属')
  }
})

/** 活动强度尖峰解读：冲入剧烈区即报（8 秒冷却防刷屏） */
let lastSpikeAt = 0
watch(() => store.intensity, (v) => {
  if (!store.monitoringLive || v < 0.9) return
  const now = Date.now()
  if (now - lastSpikeAt < 8000) return
  lastSpikeAt = now
  pushLog('warn', `信号剧烈波动 · 强度 ${v.toFixed(2)} 冲入尖峰区`,
    '瞬时冲击是跌倒的特征之一，系统正在核验后续是否持续静止',
    '暂不必行动；若确认跌倒会立即升级告警并在此提示')
})

/** 信号源接入/中断解读：采集链路每次变化都记录在案 */
watch(signalInfo, (nv, ov) => {
  if (ov && nv.kind === ov.kind) return
  switch (nv.kind) {
    case 'connecting':
      pushLog('info', `${nv.text} · 等待首个数据样本`)
      break
    case 'real':
      pushLog('info', '真实信号已接入 · 探测器采集链路正常，开始实时解读')
      break
    case 'virtual':
      pushLog('info', `演示信号已接入 · 场景「${currentScenarioLabel.value}」开始播放`)
      break
    case 'weak':
      pushLog('warn', '信号微弱 · 采集质量下降',
        '样本间隔拉长，实时解读可信度受限',
        '请核查探测器供电与网络，持续未恢复将影响监测效果')
      break
    case 'none':
      pushLog('warn', '信号中断 · 数据采集暂停', undefined,
        store.demoEnabled ? '正在重新接入新场景，请稍等' : '请核查设备供电与网络')
      break
    case 'standby':
      pushLog('info', '系统待机 · 数据采集停止，开启接入后恢复解读')
      break
  }
})

/** 常驻指标说明浮层：点 ⓘ 弹窗解释每个数字的含义，避免家属看不懂 */
const showInfo = ref(false)
const infoTitle = ref('')
const infoLines = ref<{ k: string; v: string }[]>([])
const INFO_MAP: Record<string, { title: string; lines: { k: string; v: string }[] }> = {
  zone: {
    title: '守护等级说明',
    lines: [
      { k: '正常', v: '呼吸与活动均在常态范围，无需关注' },
      { k: '注意', v: '呼吸或活动出现轻微波动，系统持续观察' },
      { k: '观察', v: '疑似异常，系统正在进行语音确证流程' },
      { k: '告警', v: '检测到跌倒或呼吸异常，已通知家属确认' },
      { k: '紧急', v: '呼吸消失或多项异常叠加，请立即联系老人或拨打 120' },
    ],
  },
  breathing: {
    title: '呼吸频率怎么看',
    lines: [
      { k: '原理', v: '通过 CSI 信号反射变化非接触感知胸廓起伏，无摄像头、不穿戴任何设备' },
      { k: '正常', v: '安静状态成人一般 12~20 次/分' },
      { k: '偏快 / 不规则', v: '频率超出常态或节律紊乱，可能不适，建议电话确认' },
      { k: '浅弱', v: '呼吸幅度明显变小，需重点关注' },
      { k: '消失', v: '长时间未检测到呼吸起伏，系统会立即触发红色告警' },
    ],
  },
  intensity: {
    title: '活动强度怎么看',
    lines: [
      { k: '含义', v: '由 CSI 信号波动幅度合成的数值，0 表示静止，越接近 1 表示动作越明显（走动、做家务等）' },
      { k: '等级分区', v: '纵轴为等距刻度，四条等级带各占四分之一，刻度值即边缘判定引擎的真实阈值（实测校准）：静止(0~0.08)·轻微(0.08~0.25)·活动(0.25~0.90)·剧烈尖峰(≥0.90)，曲线落在哪个色带，边缘端就是同样的判定' },
      { k: '跌倒特征', v: '跌倒瞬间强度冲过 0.90 尖峰线，随后持续落入静止区（倒地后不动），两条同时满足系统才会告警，避免误报' },
    ],
  },
}
function openInfo(type: 'zone' | 'breathing' | 'intensity') {
  const info = INFO_MAP[type]
  infoTitle.value = info.title
  infoLines.value = info.lines
  showInfo.value = true
}

/** 信号异常态统一引用 store.signalLost（信号依赖总闸，单一事实源），
 * 大卡状态、指标、曲线、配色全部同步切换，不拿旧值冒充现状 */

/** 中央大卡背景：随守护等级渐变换色，一眼分级；
 * 离线=暗红、接入中/信号异常/待机=中性灰蓝（不冒充绿色正常） */
const heroGradient = computed(() => {
  if (store.offline) return 'linear-gradient(135deg, #3d1220, #6e1f2b)'
  if (store.connecting || store.demoTransition || store.signalLost || store.standby) return 'linear-gradient(135deg, #23262e, #3a4150)'
  switch (store.guardZone) {
    case 0: return 'linear-gradient(135deg, #0b3d2e, #16714f)'
    case 1: return 'linear-gradient(135deg, #46280a, #96601c)'
    case 2: return 'linear-gradient(135deg, #4a3305, #a06e10)'
    case 3: return 'linear-gradient(135deg, #4a0d14, #9c1a2e)'
    case 4: return 'linear-gradient(135deg, #17171a, #82101f)'
    default: return 'linear-gradient(135deg, #232a36, #3d4d63)'
  }
})

/** 活动强度曲线坐标系：等距刻度制——四个等级带各占 1/4 高度，
 * 刻度值标真实阈值（0.08/0.25/0.90，与边缘判定引擎一致）；
 * 曲线用同一分段线性映射，波动与色带严格匹配 */
const CHART_W = 320
const CHART_H = 60
/** 分段映射锚点：真实值 v -> 归一化高度 y（四带等分） */
const Y_BREAKS = [
  { v: 0, y: 0 },
  { v: 0.08, y: 0.25 },
  { v: 0.25, y: 0.5 },
  { v: 0.9, y: 0.75 },
  { v: 1.0, y: 1.0 },
]
function mapY(v: number): number {
  const x = Math.min(Math.max(v, 0), 1)
  for (let i = 1; i < Y_BREAKS.length; i++) {
    if (x <= Y_BREAKS[i].v) {
      const a = Y_BREAKS[i - 1]
      const b = Y_BREAKS[i]
      return a.y + ((b.y - a.y) * (x - a.v)) / (b.v - a.v)
    }
  }
  return 1
}
function fixedScalePath(data: number[]): string {
  if (!data.length) return ''
  const step = CHART_W / Math.max(data.length - 1, 1)
  const points = data.map((v, i) =>
    `${(i * step).toFixed(1)},${(CHART_H - mapY(v) * CHART_H).toFixed(1)}`,
  )
  return `M${points.join(' L')}`
}
const linePathD = computed(() => fixedScalePath(store.intensityHistory))
const areaPathD = computed(() =>
  linePathD.value ? `${linePathD.value} L${CHART_W},${CHART_H} L0,${CHART_H} Z` : '',
)

/** 活动强度等级分区：分界值与边缘判定引擎阈值严格对齐
 * （edge/config.py：STILL_MAX=0.08 / ACTIVE_MIN=0.25 / SPIKE_MIN=0.90，实测校准） */
const LEVEL_BANDS = [
  { from: 0.90, to: 1.0, name: '剧烈尖峰', color: 'rgba(255,107,107,.18)' },
  { from: 0.25, to: 0.90, name: '活动', color: 'rgba(255,200,87,.15)' },
  { from: 0.08, to: 0.25, name: '轻微', color: 'rgba(120,190,255,.15)' },
  { from: 0.0, to: 0.08, name: '静止', color: 'rgba(255,255,255,.07)' },
]
</script>

<template>
  <div class="home-page">
    <!-- 顶栏：左品牌标识 / 右守护人 -->
    <header class="home-header">
      <div class="brand">
        <img class="brand-logo" src="/guardgoose-badge.png" alt="护院鹅 Guard Goose" />
        <div class="brand-text">
          <span class="brand-name">护院鹅守护系统</span>
          <span class="brand-sub">GUARD GOOSE · CSI 无感守护</span>
        </div>
      </div>
      <div class="elder-chip" @click="$router.push('/profile')">
        <div class="elder-avatar">{{ store.elderName.charAt(0) }}</div>
        <div class="elder-info">
          <span class="elder-name">{{ store.elderName }}</span>
          <span class="elder-state">
            <i class="state-dot" :class="{ alert: store.guardZone >= 3 }"></i>
            {{ store.offline ? '离线' : store.signalLost ? '信号异常' : store.standby ? '待机' : (store.connecting || store.demoTransition) ? '接入中' : '守护中' }}
          </span>
        </div>
      </div>
    </header>

    <!-- 接入控制：真实接入 / 演示接入，都未开启时系统待机。
         置于展示框上方：先接入信号，才谈得上观测 AI 分析后的展示与结论 -->
    <div class="source-card">
      <div class="source-row">
        <div class="source-info">
          <div class="source-name">
            真实接入
            <van-tag type="danger" plain class="source-priority">优先级最高</van-tag>
          </div>
          <div class="source-desc">开启后自动关闭演示，探测器信号直接联动底层判定</div>
        </div>
        <van-switch
          :model-value="store.realEnabled" size="20px"
          @update:model-value="onRealChange"
        />
      </div>
      <div class="source-divider"></div>
      <div class="source-row">
        <div class="source-info">
          <div class="source-name">演示接入</div>
          <div class="source-desc">接入代表性场景，智能体分析数据、评估情况，演示各种场景下的实时状态</div>
        </div>
        <van-switch
          :model-value="store.demoEnabled" size="20px"
          :disabled="store.realEnabled"
          @update:model-value="onDemoChange"
        />
      </div>
      <!-- 场景长条：左侧展示当前场景状态，右侧切换键循环切换四个场景 -->
      <div v-if="store.demoEnabled" class="scenario-bar" :style="{ '--sc-color': scenarioColor }">
        <div class="scenario-bar-info">
          <span class="scenario-bar-title">{{ currentScenarioLabel }}</span>
          <span class="scenario-bar-segs">
            <i v-for="(s, i) in store.demoScenarios" :key="s.scenario" :class="{ on: i === scenarioIndex }"></i>
          </span>
        </div>
        <button class="scenario-switch-btn" @click="onNextScenario">
          切换
          <van-icon name="exchange" size="13" />
        </button>
      </div>
    </div>

    <!-- 中央大卡：状态 + 呼吸频率 + 活动强度实时曲线 -->
    <section class="hero" :style="{ background: heroGradient }">
      <div class="hero-head">
        <div class="hero-status">
          <van-icon
            :name="(store.connecting || store.demoTransition) ? 'loading' : store.standby ? 'pause-circle-o' : store.signalLost ? 'warning-o' : store.zoneInfo.icon"
            color="#fff" size="24" :class="{ spinning: store.connecting || store.demoTransition }"
          />
          <div class="hero-status-text">
            <span class="hero-status-label">
              {{ store.offline ? '离线' : store.connecting ? '真实信号接入中'
                : store.demoTransition ? (store.demoTransLost ? '无信号 · 重新接入' : store.demoTransition)
                : store.signalLost
                ? (store.deviceState === 'weak' ? '信号弱 · 监测暂停' : '无信号 · 监测暂停')
                : store.standby ? '待机' : store.zoneInfo.label }}
              <van-icon class="info-icon" name="question-o" size="14" @click="openInfo('zone')" />
            </span>
            <span class="hero-status-sub">
              <template v-if="store.connecting">
                正在检测探测器信号 · 结果即将更新
              </template>
              <template v-else-if="store.demoTransition">
                {{ store.demoTransLost ? '上一场景已断开 · 正在重新接入' : '请稍等 · 正在接入场景数据' }}
              </template>
              <template v-else-if="store.realEnabled && store.deviceState !== 'connected'">
                {{ store.deviceState === 'weak'
                  ? '信号微弱（不良）· 请核查网络与收发两端'
                  : '未检测到信号 · 请核查设备供电与网络' }}
                · 持续未恢复可能影响监测效果
              </template>
              <template v-else-if="store.standby">
                未开启接入 · 开启真实接入或演示接入后开始守护
              </template>
              <template v-else>
                {{ store.present ? '家中有人' : '家中无人' }}
                <template v-if="store.lastSampleTime"> · 更新于 {{ store.lastSampleTime }}</template>
              </template>
            </span>
          </div>
        </div>
        <div class="hero-tools">
          <!-- 信号标签：仿手机信号格，实心=真实/空心=虚拟/全灰=无信号，
               设备状态每 8 秒轮询，格数与颜色自动跟随变化 -->
          <div class="signal-tag" :class="signalInfo.kind">
            <div class="signal-bars">
              <span v-for="i in 4" :key="i" class="bar" :class="{ on: i <= signalInfo.bars }"></span>
            </div>
            <span class="signal-text">{{ signalInfo.text }}</span>
          </div>
          <van-icon
            class="refresh-btn" name="replay" size="20" color="rgba(255,255,255,.85)"
            :class="{ spinning: store.refreshing }"
            @click="onRefresh()"
          />
        </div>
      </div>

      <!-- 离线时的重连操作 -->
      <div class="hero-offline" v-if="store.offline">
        <p>无法连接守护服务，实时判断已暂停。请检查边缘网关电源与网络后重试。</p>
        <van-button type="danger" size="small" round plain :loading="store.refreshing" @click="onRefresh()">
          重新连接
        </van-button>
      </div>

      <!-- 信号异常：旧数据不可信，指标与曲线全部降级展示 -->
      <div class="hero-lost" v-else-if="store.signalLost">
        <van-icon name="warning-o" size="30" color="#ffc069" />
        <p class="lost-title">{{ store.deviceState === 'weak' ? '信号微弱，实时监测已暂停' : '未检测到探测器信号，实时监测已暂停' }}</p>
        <p class="lost-sub">以下指标为最后一次接收的数据，不代表当前状态；请核查设备供电与网络，系统持续自动检测，恢复后自动继续</p>
        <p class="lost-time" v-if="store.lastSampleTime">最后数据更新于 {{ store.lastSampleTime }}</p>
      </div>

      <!-- 接入中：真实接入检测中 / 演示场景重新接入（无信号→接入中），不展示旧数据 -->
      <div class="hero-connecting" v-else-if="store.connecting || store.demoTransition">
        <van-icon :name="store.demoTransLost ? 'warning-o' : 'loading'" size="30" :color="store.demoTransLost ? '#ffb0a3' : '#a3d8ff'" :class="{ spinning: !store.demoTransLost }" />
        <p class="connecting-title">{{ store.connecting ? '真实信号接入中'
          : store.demoTransLost ? '无信号' : store.demoTransition }}</p>
        <p class="connecting-sub">{{ store.connecting
          ? '正在检测探测器信号，根据信号情况自动切换对应状态'
          : store.demoTransLost ? '上一场景信号已断开，正在重新接入新剧本'
          : '场景数据即将接入，实时状态马上更新' }}</p>
      </div>

      <!-- 待机：两个接入开关都未开启，不产生数据流，明示如何开始 -->
      <div class="hero-standby" v-else-if="store.standby">
        <van-icon name="pause-circle-o" size="30" color="rgba(255,255,255,.55)" />
        <p class="standby-title">系统待机中</p>
        <p class="standby-sub">开启上方“真实接入”或“演示接入”后，开始实时守护</p>
      </div>

      <!-- 核心指标：呼吸频率 + 活动强度 -->
      <div class="hero-metrics" v-else>
        <div class="metric">
          <div class="metric-top">
            <span class="pulse-dot" :class="{ danger: store.breathingState !== 'normal' }" v-if="store.breathingRate > 0"></span>
            <span class="metric-num">{{ store.breathingRate > 0 ? store.breathingRate : '—' }}</span>
            <span class="metric-unit">次/分</span>
          </div>
          <div class="metric-label">
            呼吸频率
            <van-icon class="info-icon" name="question-o" size="13" @click="openInfo('breathing')" />
            <span class="metric-tag" :style="{ background: store.breathingInfo.color }">
              {{ store.breathingInfo.label }}
            </span>
          </div>
        </div>
        <div class="metric-divider"></div>
        <div class="metric">
          <div class="metric-top">
            <span class="metric-num">{{ store.intensity.toFixed(2) }}</span>
          </div>
          <div class="metric-label">
            活动强度
            <van-icon class="info-icon" name="question-o" size="13" @click="openInfo('intensity')" />
          </div>
        </div>
      </div>

      <!-- 实时活动曲线：固定 0~1 坐标系 + 四档等级分区；信号异常时降透明度并标注暂停 -->
      <div class="hero-chart-wrap" :class="{ paused: store.signalLost }" v-if="!store.offline">
        <svg viewBox="0 0 320 60" preserveAspectRatio="none" class="hero-chart">
          <defs>
            <linearGradient id="areaGrad" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stop-color="rgba(255,255,255,.35)" />
              <stop offset="100%" stop-color="rgba(255,255,255,0)" />
            </linearGradient>
          </defs>
          <!-- 等级分区色带：自下而上 静止/轻微/活动/剧烈尖峰（与曲线同一映射） -->
          <rect
            v-for="band in LEVEL_BANDS" :key="band.name"
            x="0" :y="60 * (1 - mapY(band.to))" width="320" :height="60 * (mapY(band.to) - mapY(band.from))"
            :fill="band.color"
          />
          <!-- 刻度线：与后端判定阈值对齐 0.08 / 0.25 / 0.90 -->
          <line v-for="t in [0.08, 0.25, 0.90]" :key="t"
            x1="0" :y1="60 * (1 - mapY(t))" x2="320" :y2="60 * (1 - mapY(t))"
            stroke="rgba(255,255,255,.18)" stroke-width="1" stroke-dasharray="3 3"
          />
          <path v-if="areaPathD" :d="areaPathD" fill="url(#areaGrad)" />
          <path v-if="linePathD" :d="linePathD" fill="none" stroke="rgba(255,255,255,.9)" stroke-width="2" />
        </svg>
        <!-- 坐标系标签：左侧刻度值 / 右侧等级名（HTML层不随SVG拉伸变形） -->
        <div class="axis-labels">
          <span class="axis-tick" style="top:0">1.0</span>
          <span class="axis-tick" style="top:25%;transform:translateY(-50%)">0.90</span>
          <span class="axis-tick" style="top:50%;transform:translateY(-50%)">0.25</span>
          <span class="axis-tick" style="top:75%;transform:translateY(-50%)">0.08</span>
          <span class="axis-tick" style="bottom:0">0</span>
          <span class="axis-band" style="top:12.5%;transform:translateY(-50%)">剧烈尖峰</span>
          <span class="axis-band" style="top:37.5%;transform:translateY(-50%)">活动</span>
          <span class="axis-band" style="top:62.5%;transform:translateY(-50%)">轻微</span>
          <span class="axis-band" style="top:87.5%;transform:translateY(-50%)">静止</span>
        </div>
        <div v-if="!store.intensityHistory.length" class="chart-empty">
          {{ store.connecting ? '接入中 · 等待信号检测' : store.standby ? '待机 · 等待接入信号' : '等待数据...' }}
        </div>
        <div v-else-if="store.signalLost" class="chart-paused">曲线已暂停更新</div>
      </div>

      <!-- 信号解读台：内嵌在信号展示卡内部——
           常驻只展示最近两行，点标题展开看全部细节（分析+建议） -->
      <div class="hero-console" v-if="!store.offline">
        <div class="console-head" @click="consoleOpen = !consoleOpen">
          <div class="console-title">
            <van-icon name="notes-o" size="14" />
            信号解读台
          </div>
          <span class="console-sub">{{ consoleOpen ? '点击收起' : '最近两行 · 点我看细节' }}</span>
          <van-icon :name="consoleOpen ? 'arrow-up' : 'arrow-down'" size="12" color="rgba(255,255,255,.55)" />
        </div>
        <div class="console-body">
          <div v-if="!logs.length" class="console-empty">
            <span class="console-cursor">▊</span>
            信号平稳 · 静默采集中，出现异常波动时自动解读
          </div>
          <div v-for="log in consoleDisplayLogs" :key="log.id" class="console-line" :class="log.level">
            <div class="console-main">
              <span class="console-time">{{ log.time }}</span>
              <span class="console-level">{{ log.level === 'danger' ? '紧急' : log.level === 'warn' ? '异常' : '状态' }}</span>
              <span class="console-text">{{ log.text }}</span>
            </div>
            <template v-if="consoleOpen">
              <div v-if="log.analysis" class="console-extra">分析：{{ log.analysis }}</div>
              <div v-if="log.advice" class="console-extra advice">建议：{{ log.advice }}</div>
            </template>
          </div>
        </div>
      </div>

      <div class="hero-foot">CSI 信号反射实时分析 · 无摄像头 · 无需穿戴</div>
    </section>

    <!-- 当前告警：告警并入守护页，有告时紧跟大卡置顶展示；
         无告警不占空间，历史告警默认折叠 -->
    <section
      v-if="store.latestAlert" class="alert-panel"
      :style="{ borderLeftColor: ALERT_LEVEL_COLOR[store.latestAlert.level] || '#1989fa' }"
    >
      <!-- 极简双导航：不展示任何文字信息，只有两个行动入口 -->
      <div class="alert-panel-actions">
        <van-button type="primary" round block size="small" @click="handleAlertCallEmergency">紧急联系</van-button>
        <van-button type="danger" round block size="small" @click="handleAlertCall120">报警120</van-button>
      </div>
    </section>

    <!-- 语音确证面板：依赖信号——只在监测链路实时可用时展示，
         无信号时旧状态不冒充正在进行的流程 -->
    <van-cell-group inset class="section" v-if="store.monitoringLive && store.guardZone === 2">
      <van-cell title="语音确证" center>
        <template #icon>
          <van-icon name="volume-o" color="#ff9f00" style="margin-right:8px" />
        </template>
      </van-cell>
      <div class="voice-panel">
        <p v-if="store.voiceConfirmState === 'waiting'">
          已发起语音确认“您还好吗？”，等待老人回应...
        </p>
        <p v-else-if="store.voiceConfirmState === 'ok'" style="color:#07c160">
          老人回应正常，解除告警
        </p>
        <p v-else-if="store.voiceConfirmState === 'help'" style="color:#ee0a24">
          老人求助！已升级告警
        </p>
        <p v-else>等待语音确认...</p>
      </div>
    </van-cell-group>

    <!-- 折叠详情区：次要信息默认收起，首屏只留核心数据 -->
    <van-collapse v-model="detailOpen" inset class="detail-collapse" @change="onCollapseChange">
      <!-- 探测器连接：标题行常驻展示三态，点开才看诊断详情 -->
      <van-collapse-item name="device">
        <template #title>
          <div class="collapse-title">
            <van-icon
              :name="store.connectionInfo.icon === 'close' ? 'close' : store.connectionInfo.icon"
              :color="store.connectionInfo.color" size="16"
            />
            <span>探测器检测</span>
            <span class="collapse-state" :style="{ color: store.connectionInfo.color }">
              {{ store.connectionInfo.label }}
            </span>
          </div>
        </template>
        <van-cell title="设备编号" :value="store.diagnosis?.device_id || store.deviceStatus?.device_id || '-'" />
        <van-cell title="边缘网关" :value="store.offline ? '不可达' : '在线'" />
        <van-cell
          title="数据延迟"
          :value="store.diagnosis && store.diagnosis.last_sample_lag_s >= 0
            ? store.diagnosis.last_sample_lag_s + ' 秒' : '未收到数据'"
        />
        <van-cell
          v-if="store.diagnosis"
          title="期望上报间隔"
          :value="store.diagnosis.sample_interval_expected_s + ' 秒/次'"
        />
        <div class="diag-tips" v-if="store.diagnosis?.tips?.length">
          <p class="diag-tips-title">📋 排查建议</p>
          <p v-for="(tip, i) in store.diagnosis.tips" :key="i" class="diag-tip">· {{ tip }}</p>
        </div>
        <div class="diag-tips" v-else-if="store.offline">
          <p class="diag-tips-title">📋 排查建议</p>
          <p class="diag-tip">· 检查边缘网关（树莓派）电源与网络连接</p>
          <p class="diag-tip">· 确认手机与网关在同一局域网</p>
          <p class="diag-tip">· 信号状态持续自动检测，恢复后自动更新</p>
        </div>
      </van-collapse-item>
    </van-collapse>

    <!-- 常驻指标说明浮层：解释等级/呼吸/活动强度的含义与判断标准 -->
    <van-popup v-model:show="showInfo" round :style="{ width: '85%', maxHeight: '70%' }">
      <div class="info-panel">
        <div class="info-title">{{ infoTitle }}</div>
        <div v-for="(line, i) in infoLines" :key="i" class="info-row">
          <span class="info-key">{{ line.k }}</span>
          <span class="info-val">{{ line.v }}</span>
        </div>
        <van-button type="primary" block round size="small" style="margin-top:14px" @click="showInfo = false">
          我知道了
        </van-button>
      </div>
    </van-popup>
  </div>
</template>

<style scoped>
.home-page {
  padding-bottom: 8px;
}

/* ---- 顶栏：左品牌 / 右守护人 ---- */
.home-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 16px;
  background: #fff;
}

.brand {
  display: flex;
  align-items: center;
  gap: 8px;
}

.brand-logo {
  width: 40px;
  height: 40px;
  object-fit: contain;
  filter: drop-shadow(0 2px 4px rgba(26, 58, 110, 0.18));
}

.brand-text {
  display: flex;
  flex-direction: column;
}

.brand-name {
  font-size: 15px;
  font-weight: 700;
  color: #1f2329;
  letter-spacing: 0.5px;
}

.brand-sub {
  font-size: 10px;
  color: #969799;
  letter-spacing: 1px;
}

.elder-chip {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 4px 10px 4px 4px;
  background: #f7f8fa;
  border-radius: 999px;
}

.elder-avatar {
  width: 32px;
  height: 32px;
  border-radius: 50%;
  background: linear-gradient(135deg, #1989fa, #47b2ff);
  color: #fff;
  font-size: 15px;
  font-weight: 600;
  display: flex;
  align-items: center;
  justify-content: center;
}

.elder-info {
  display: flex;
  flex-direction: column;
}

.elder-name {
  font-size: 13px;
  font-weight: 600;
  color: #1f2329;
}

.elder-state {
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: 10px;
  color: #969799;
}

.state-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #07c160;
}

.state-dot.alert {
  background: #ee0a24;
}

/* ---- 中央大卡：信号展示主体，加大加显眼 ---- */
.hero {
  margin: 10px 10px 0;
  border-radius: 22px;
  padding: 20px 18px 16px;
  color: #fff;
  box-shadow: 0 10px 30px rgba(15, 30, 40, 0.22);
}

.hero-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
}

.hero-status {
  display: flex;
  align-items: center;
  gap: 10px;
}

.hero-status-text {
  display: flex;
  flex-direction: column;
}

.hero-status-label {
  font-size: 24px;
  font-weight: 700;
  line-height: 1.2;
}

.info-icon {
  margin-left: 4px;
  color: rgba(255, 255, 255, 0.7);
  vertical-align: 1px;
}

.hero-status-sub {
  font-size: 12px;
  color: rgba(255, 255, 255, 0.72);
  margin-top: 3px;
}

.hero-tools {
  display: flex;
  align-items: center;
  gap: 8px;
}

/* 信号标签：仿手机信号格，实心格=真实信号、空心格=虚拟信号 */
.signal-tag {
  display: flex;
  align-items: center;
  gap: 5px;
  padding: 4px 9px;
  border-radius: 20px;
  background: rgba(255,255,255,.14);
}

.signal-bars {
  display: flex;
  align-items: flex-end;
  gap: 2px;
  height: 14px;
}

.signal-bars .bar {
  width: 3px;
  border-radius: 1px;
  box-sizing: border-box;
  background: rgba(255,255,255,.28);
}

/* 递增高度，同手机信号格 */
.signal-bars .bar:nth-child(1) { height: 5px; }
.signal-bars .bar:nth-child(2) { height: 8px; }
.signal-bars .bar:nth-child(3) { height: 11px; }
.signal-bars .bar:nth-child(4) { height: 14px; }

.signal-tag.real .bar.on { background: #8ff0b8; }
.signal-tag.weak .bar.on { background: #ffc069; }
/* 虚拟：满格但空心蓝描边——看着有信号，一眼辨出非真实接入 */
.signal-tag.virtual .bar.on {
  background: transparent;
  border: 1px solid #9fd4ff;
}

.signal-text { font-size: 11px; line-height: 1; }
.signal-tag.real .signal-text { color: #b7f5d0; }
.signal-tag.weak .signal-text { color: #ffe0b3; }
.signal-tag.none .signal-text { color: rgba(255,255,255,.65); }
.signal-tag.virtual .signal-text { color: #a3d8ff; }
.signal-tag.standby .signal-text { color: rgba(255,255,255,.55); }
/* 接入中：文字呼吸闪烁，传达“进行中” */
.signal-tag.connecting .signal-text {
  color: #a3d8ff;
  animation: pulse-text 1.2s ease-in-out infinite;
}

@keyframes pulse-text {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.45; }
}

.refresh-btn.spinning {
  animation: spin 1s linear infinite;
}

@keyframes spin {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}

.hero-offline {
  margin-top: 14px;
  font-size: 13px;
  line-height: 1.7;
  color: rgba(255, 255, 255, 0.85);
}

.hero-offline p {
  margin: 0 0 10px;
}

/* 信号异常降级区：替代实时指标，明示旧数据不可信 */
.hero-lost {
  margin-top: 14px;
  padding: 14px 12px;
  border-radius: 12px;
  background: rgba(255, 192, 105, 0.1);
  border: 1px dashed rgba(255, 192, 105, 0.45);
  text-align: center;
}

.hero-lost .lost-title {
  margin: 8px 0 4px;
  font-size: 14px;
  font-weight: 600;
  color: #ffe0b3;
}

.hero-lost .lost-sub {
  margin: 0 0 6px;
  font-size: 12px;
  line-height: 1.6;
  color: rgba(255, 255, 255, 0.7);
}

.hero-lost .lost-time {
  margin: 0 0 10px;
  font-size: 11px;
  color: rgba(255, 255, 255, 0.5);
}

/* 待机区：中性安静，与信号异常的警示色区分 */
.hero-standby {
  margin-top: 14px;
  padding: 18px 12px;
  border-radius: 12px;
  background: rgba(255, 255, 255, 0.06);
  text-align: center;
}

/* 接入中区：与待机同构，冷色调表达“进行中” */
.hero-connecting {
  margin-top: 14px;
  padding: 18px 12px;
  border-radius: 12px;
  background: rgba(163, 216, 255, 0.08);
  text-align: center;
}

.hero-connecting .connecting-title {
  margin: 8px 0 4px;
  font-size: 14px;
  font-weight: 600;
  color: #a3d8ff;
}

.hero-connecting .connecting-sub {
  margin: 0;
  font-size: 12px;
  color: rgba(255, 255, 255, 0.55);
}

.hero-standby .standby-title {
  margin: 8px 0 4px;
  font-size: 14px;
  font-weight: 600;
  color: rgba(255, 255, 255, 0.75);
}

.hero-standby .standby-sub {
  margin: 0;
  font-size: 12px;
  color: rgba(255, 255, 255, 0.5);
}

/* 核心指标区 */
.hero-metrics {
  display: flex;
  align-items: center;
  margin-top: 12px;
  padding: 14px 4px;
  background: rgba(255, 255, 255, 0.1);
  border-radius: 12px;
  backdrop-filter: blur(4px);
}

.metric {
  flex: 1;
  padding: 0 16px;
}

.metric-divider {
  width: 1px;
  height: 36px;
  background: rgba(255, 255, 255, 0.2);
}

.metric-top {
  display: flex;
  align-items: baseline;
  gap: 6px;
}

.metric-num {
  font-size: 40px;
  font-weight: 700;
  line-height: 1;
  font-variant-numeric: tabular-nums;
}

.metric-unit {
  font-size: 11px;
  color: rgba(255, 255, 255, 0.7);
}

.metric-label {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-top: 6px;
  font-size: 12px;
  color: rgba(255, 255, 255, 0.75);
}

.metric-tag {
  padding: 1px 6px;
  border-radius: 4px;
  font-size: 10px;
  color: #fff;
}

/* 呼吸光点：呼吸节律动效，异常变红 */
.pulse-dot {
  width: 10px;
  height: 10px;
  border-radius: 50%;
  background: #6ee7b7;
  align-self: center;
  animation: pulse 3.6s ease-in-out infinite;
}

.pulse-dot.danger {
  background: #ff8080;
  animation-duration: 1.2s;
}

@keyframes pulse {
  0%, 100% { transform: scale(1); opacity: 1; }
  50% { transform: scale(1.5); opacity: 0.55; }
}

/* 实时曲线 */
.hero-chart-wrap {
  position: relative;
  margin-top: 10px;
}

.hero-chart {
  display: block;
  width: 100%;
  height: 150px;
}

/* 坐标系标签：左侧刻度值 / 右侧等级名 */
.axis-labels {
  position: absolute;
  inset: 0;
  pointer-events: none;
}

.axis-tick {
  position: absolute;
  left: 4px;
  font-size: 9px;
  color: rgba(255, 255, 255, 0.5);
  font-variant-numeric: tabular-nums;
}

.axis-band {
  position: absolute;
  right: 6px;
  transform: translateY(-50%);
  font-size: 9px;
  letter-spacing: 1px;
  color: rgba(255, 255, 255, 0.65);
}

.chart-empty {
  position: absolute;
  top: 50%;
  left: 50%;
  transform: translate(-50%, -50%);
  color: rgba(255, 255, 255, 0.6);
  font-size: 12px;
}

/* 信号异常：曲线降透明度 + 暂停标注，旧曲线不冒充实时数据 */
.hero-chart-wrap.paused .hero-chart,
.hero-chart-wrap.paused .axis-labels {
  opacity: 0.35;
}

.chart-paused {
  position: absolute;
  top: 50%;
  left: 50%;
  transform: translate(-50%, -50%);
  padding: 3px 10px;
  border-radius: 12px;
  background: rgba(0, 0, 0, 0.45);
  color: #ffe0b3;
  font-size: 11px;
  white-space: nowrap;
}

.hero-foot {
  margin-top: 8px;
  text-align: center;
  font-size: 9px;
  letter-spacing: 1px;
  color: rgba(255, 255, 255, 0.5);
}

/* ---- 数据源模式卡片 ---- */
.source-card {
  margin: 10px 12px 0;
  padding: 4px 14px;
  background: #fff;
  border-radius: 12px;
}

.source-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 0;
}

.source-info {
  flex: 1;
  margin-right: 12px;
}

.source-name {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 14px;
  font-weight: 600;
  color: #1f2329;
}

.source-priority {
  transform: scale(0.9);
}

.source-desc {
  margin-top: 2px;
  font-size: 11px;
  color: #969799;
}

.source-divider {
  height: 1px;
  background: #f2f3f5;
}

/* 场景长条：左侧当前场景状态（色带随风险分级），右侧切换键 */
.scenario-bar {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-top: 8px;
  padding: 9px 10px;
  background: #f7f8fa;
  border-radius: 10px;
  border-left: 4px solid var(--sc-color, #07c160);
}

.scenario-bar-info {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 5px;
}

.scenario-bar-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--sc-color, #1f2329);
}

/* 四格位置指示：当前场景对应一格加宽点亮 */
.scenario-bar-segs {
  display: flex;
  gap: 4px;
}

.scenario-bar-segs i {
  width: 16px;
  height: 4px;
  border-radius: 2px;
  background: #dcdfe4;
  transition: all 0.25s;
}

.scenario-bar-segs i.on {
  width: 28px;
  background: var(--sc-color, #1989fa);
}

.scenario-switch-btn {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 6px 13px;
  border: none;
  border-radius: 999px;
  background: #1989fa;
  color: #fff;
  font-size: 12px;
  line-height: 1;
}

.scenario-switch-btn:active {
  opacity: 0.75;
}

/* ---- 当前告警卡：紧跟大卡，左色带随告警级别 ---- */
.alert-panel {
  margin: 10px 10px 0;
  padding: 12px 14px;
  background: #fff;
  border-radius: 14px;
  border-left: 4px solid #ee0a24;
  box-shadow: 0 2px 10px rgba(0, 0, 0, 0.05);
}

.alert-panel-actions {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 8px;
}

/* ---- 信号解读台：内嵌在信号展示卡内部，常驻两行可展开 ---- */
.hero-console {
  margin: 10px 12px 0;
  border-radius: 10px;
  background: rgba(0, 0, 0, 0.28);
  overflow: hidden;
}

.console-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 9px 14px;
  background: rgba(255, 255, 255, 0.05);
  cursor: pointer;
}

.console-title {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  font-weight: 600;
  color: #d6e4ff;
}

.console-sub {
  font-size: 10px;
  color: rgba(255, 255, 255, 0.4);
  letter-spacing: 1px;
}

.console-body {
  max-height: 220px;
  overflow-y: auto;
  padding: 6px 14px 8px;
}

.console-empty {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 14px 0;
  font-size: 12px;
  color: rgba(255, 255, 255, 0.45);
}

.console-cursor {
  color: #6ee7b7;
  animation: pulse-text 1.2s ease-in-out infinite;
}

.console-line {
  padding: 6px 0;
  border-bottom: 1px dashed rgba(255, 255, 255, 0.08);
}

.console-line:last-child {
  border-bottom: none;
}

.console-main {
  display: flex;
  align-items: baseline;
  gap: 6px;
}

.console-time {
  flex-shrink: 0;
  font-size: 10px;
  color: rgba(255, 255, 255, 0.38);
  font-variant-numeric: tabular-nums;
}

.console-level {
  flex-shrink: 0;
  padding: 0 5px;
  border-radius: 4px;
  font-size: 10px;
  line-height: 16px;
}

.console-line.info .console-level {
  background: rgba(110, 231, 183, 0.15);
  color: #6ee7b7;
}

.console-line.warn .console-level {
  background: rgba(255, 192, 105, 0.16);
  color: #ffc069;
}

.console-line.danger .console-level {
  background: rgba(255, 128, 128, 0.18);
  color: #ff8080;
}

.console-text {
  font-size: 12px;
  line-height: 1.5;
  color: rgba(255, 255, 255, 0.88);
}

.console-line.danger .console-text {
  color: #ffb3b3;
  font-weight: 600;
}

.console-extra {
  margin: 3px 0 0 44px;
  font-size: 11px;
  line-height: 1.6;
  color: rgba(255, 255, 255, 0.55);
}

.console-extra.advice {
  color: #9fd4ff;
}

/* 指标说明浮层 */
.info-panel {
  padding: 18px 16px;
}

.info-title {
  font-size: 16px;
  font-weight: 700;
  color: #1f2329;
  margin-bottom: 12px;
}

.info-row {
  display: flex;
  gap: 10px;
  padding: 8px 0;
  border-bottom: 1px solid #f2f3f5;
  font-size: 13px;
  line-height: 1.6;
}

.info-row:last-of-type {
  border-bottom: none;
}

.info-key {
  flex-shrink: 0;
  width: 76px;
  font-weight: 600;
  color: #1f2329;
}

.info-val {
  color: #646566;
}

.section {
  margin-top: 10px;
}

.voice-panel {
  padding: 12px 16px;
  font-size: 14px;
  color: #666;
}

/* ---- 折叠详情区：次要信息默认收起 ---- */
.detail-collapse {
  margin-top: 10px;
}

.collapse-title {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 14px;
}

.collapse-state {
  font-size: 12px;
  color: #969799;
}

.collapse-more {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 2px;
  padding: 10px 0 4px;
  font-size: 13px;
  color: #1989fa;
}

.diag-tips {
  margin: 10px 4px 0;
  background: #f7f8fa;
  border-radius: 8px;
  padding: 10px 12px;
}

.diag-tips-title {
  font-size: 13px;
  font-weight: 600;
  margin: 0 0 6px;
}

.diag-tip {
  font-size: 12px;
  color: #666;
  line-height: 1.8;
  margin: 2px 0;
}
</style>
