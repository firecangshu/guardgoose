<script setup lang="ts">
/** 守护页（v4 样稿落地版）：语义三态状态条 + 双证据线 + 响应措施时间线
 * + 双数据表（运动分段压缩 Y 轴 / 呼吸医学色带，正常带上限随档案修正）。
 * 双证据线铁律：运动突增 + 呼吸变化同时成立才预警；呼吸是第一参照物。 */
import { ref, computed, watch, onMounted, onUnmounted, nextTick } from 'vue'
import { showToast, showDialog, showConfirmDialog, closeDialog } from 'vant'
import { useGuardianStore } from '../stores/guardian'
import { api } from '../services/api'
import type { ConnectionTestData } from '../services/api'

const store = useGuardianStore()
const detailOpen = ref<string[]>([])

onMounted(() => {
  store.init()
  nextTick(drawAll)
})

async function onCollapseChange(names: string[]) {
  if (names.includes('device')) await store.loadDiagnosis()
}

/* ---- 信号接通测试：板子 → 桥接器 → 后端 全链路逐环体检 ---- */
const connTesting = ref(false)
const connTestResult = ref<ConnectionTestData | null>(null)
async function onConnectionTest() {
  connTesting.value = true
  try {
    connTestResult.value = await api.connectionTest()
  } catch {
    showToast('检测失败：守护服务不可达')
  } finally {
    connTesting.value = false
  }
}

async function onRefresh() {
  showToast(await store.refreshAll())
}

/* ---- 接入控制（真实接入 > 演示场景 > 待机） ---- */
async function onRealChange(v: boolean | string) {
  showToast(await store.toggleReal(Boolean(v)))
}
async function onDemoChange(v: boolean | string) {
  const first = store.demoScenarios[0]?.scenario || ''
  showToast(await store.toggleDemo(Boolean(v), first))
}
async function onPickScenario(s: string) {
  showToast(await store.toggleDemo(true, s))
}
const currentScenarioLabel = computed(() =>
  store.demoScenarios.find(s => s.scenario === store.demoScenario)?.label || '未选择')
const scenarioIndex = computed(() =>
  store.demoScenarios.findIndex(s => s.scenario === store.demoScenario))
const SCENARIO_COLORS: Record<string, string> = {
  demo_active: '#16a34a',
  demo_rest: '#2563eb',
  demo_fall_still: '#0d9488',
  demo_fall_moving: '#dc2626',
  demo_fall_noresponse: '#7f1d1d',
}
const scenarioColor = computed(() => SCENARIO_COLORS[store.demoScenario] || '#969799')
function onNextScenario() {
  const list = store.demoScenarios
  if (!list.length) return
  const next = list[(scenarioIndex.value + 1) % list.length]
  onPickScenario(next.scenario)
}

/** 大卡信号标签：仿手机信号格隐喻 */
const signalInfo = computed(() => {
  if (store.realEnabled) {
    if (store.deviceState === '') {
      return store.autoChecking
        ? { kind: 'connecting', bars: 0, text: '信号链路体检中' }
        : { kind: 'connecting', bars: 0, text: '真实信号接入中' }
    }
    if (store.deviceState === 'connected') return { kind: 'real', bars: 4, text: '信号正常' }
    if (store.deviceState === 'weak') return { kind: 'weak', bars: 2, text: '信号弱' }
    // 体检期间：轮询虽报断开但属过渡态，继续显示体检中
    if (store.autoChecking) return { kind: 'connecting', bars: 0, text: '信号链路体检中' }
    // 故障态：体检结果给出具体断在哪一环，不再笼统说“无信号”
    const r = store.autoCheckResult
    if (r && !r.ok) {
      if (r.phase === 'preflight') return { kind: 'connecting', bars: 0, text: '开机自检中' }
      if (r.failed_at) return { kind: 'none', bars: 0, text: `故障 · 断在${r.failed_at}` }
      return { kind: 'none', bars: 0, text: '故障 · 链路未接通' }
    }
    return { kind: 'none', bars: 0, text: '无信号' }
  }
  if (store.demoEnabled) {
    if (store.demoTransition) {
      if (store.demoTransLost) return { kind: 'none', bars: 0, text: '无信号' }
      return { kind: 'connecting', bars: 0, text: store.demoTransition }
    }
    return { kind: 'virtual', bars: 4, text: '接入成功 · 信号良好' }
  }
  if (store.standby) return { kind: 'standby', bars: 0, text: '待机' }
  return { kind: 'none', bars: 0, text: '无信号' }
})

/** 探测器桥接状态：置于接入控制与显示盘之间，接入信号后先看桥接成功没。
 * 真实接入显示真实情况；演示接入分阶段呈现（接入检测中 → 接入成功 · 信号良好常驻） */
const bridgeVM = computed(() => {
  if (store.offline) return { icon: 'warning-o', color: '#ee0a24', text: '已断开 · 守护服务不可达', sub: '请检查边缘网关电源与网络后重连' }
  if (store.standby) return { icon: 'pause-circle-o', color: '#969799', text: '待机 · 未接入', sub: '开启真实接入或演示接入后开始桥接检测' }
  if (store.realEnabled) {
    if (store.connecting) {
      return store.autoChecking
        ? { icon: 'search', color: '#2563eb', text: '信号链路体检中…', sub: '正在逐环检查：桥接器 → 接收板 → 数据流' }
        : { icon: 'wifi', color: '#2563eb', text: '接入检测中…', sub: '真实信号接入 · 显示真实情况' }
    }
    if (store.deviceState === 'connected') return { icon: 'wifi', color: '#07c160', text: '接入成功 · 信号良好', sub: '真实信号接入 · 显示真实情况' }
    if (store.deviceState === 'weak') return { icon: 'warning-o', color: '#ff9f00', text: '信号不佳 · 数据延迟', sub: '真实信号接入 · 显示真实情况' }
    // 故障态：体检结论直接上屏，告诉用户断在哪一环、怎么办
    const r = store.autoCheckResult
    if (r && !r.ok) return { icon: 'warning-o', color: '#ee0a24', text: r.verdict, sub: '开阀自动体检未通过 · 处理后可点信号接通测试复检' }
    return { icon: 'close', color: '#ee0a24', text: '已断开 · 无信号', sub: '真实信号接入 · 显示真实情况' }
  }
  // 演示接入：分阶段呈现接入过程
  if (store.demoTransLost) return { icon: 'close', color: '#969799', text: '无信号 · 上一场景已断开', sub: '正在重新接入…' }
  if (store.demoTransition) {
    const ok = store.demoTransition.startsWith('接入成功')
    return { icon: 'wifi', color: ok ? '#07c160' : '#2563eb',
      text: store.demoTransition === '接入检测中' ? '接入检测中…' : store.demoTransition,
      sub: '请稍候…' }
  }
  return { icon: 'wifi', color: '#07c160', text: '接入成功 · 信号良好', sub: '探测器在线 · 数据持续上报' }
})

/** 真实接入信号异常提醒（首次提醒 + 30 秒催办） */
const realAlerted = ref(false)
const realWarned = ref(false)
let warnTimer: ReturnType<typeof setTimeout> | null = null
function clearWarnTimer() {
  if (warnTimer) { clearTimeout(warnTimer); warnTimer = null }
}
watch(() => [store.realEnabled, store.deviceState] as const, ([real, state]) => {
  if (!real) { realAlerted.value = false; realWarned.value = false; clearWarnTimer(); return }
  if (state === 'connected') {
    if (realAlerted.value) showToast('信号已恢复良好，监测正常运行')
    realAlerted.value = false; realWarned.value = false; clearWarnTimer()
    return
  }
  if (!state) return
  if (!realAlerted.value) {
    realAlerted.value = true
    if (!detailOpen.value.includes('device')) detailOpen.value = [...detailOpen.value, 'device']
    store.loadDiagnosis()
    showToast(state === 'weak'
      ? '信号微弱（不良）：请核查网络与探测器收发两端'
      : '未检测到探测器信号：请核查设备供电与网络')
    clearWarnTimer()
    warnTimer = setTimeout(() => {
      if (!store.realEnabled || store.deviceState === 'connected') return
      if (realWarned.value) return
      realWarned.value = true
      showToast('信号问题持续未恢复，可能影响监测效果，请尽快核查网络与设备')
    }, 30000)
  }
}, { immediate: true })
onUnmounted(clearWarnTimer)

/* ---- 显示盘三态：无人 / 正常 / 告警（识别状态≠显示内容，只显示有价值的态） ---- */
const ZONE_CN: Record<string, string> = {
  living: '客厅', bedroom: '卧室', bathroom: '卫生间', kitchen: '厨房',
}
/* 状态时长：正常态副文案「已 X 分钟」（前端累计，语义态变化即重新计时） */
const stateSince = ref(Date.now())
watch(() => [store.semanticState, store.present] as const, () => { stateSince.value = Date.now() })
const nowTs = ref(Date.now())
let tickTimer: ReturnType<typeof setInterval> | null = null
onMounted(() => {
  tickTimer = setInterval(() => { nowTs.value = Date.now() }, 1000)
})
onUnmounted(() => { if (tickTimer) clearInterval(tickTimer) })
const stateDurationText = computed(() => {
  const m = Math.floor((nowTs.value - stateSince.value) / 60000)
  return m > 0 ? `已 ${m} 分钟` : '刚开始'
})
interface StatusVM {
  cls: string          // '' 正常 / danger 告警 / quiet 无断言
  emoji: string
  main: string
  mainRed: boolean
  sub: string
}
const statusVM = computed<StatusVM>(() => {
  if (store.offline) {
    return { cls: 'danger', emoji: '📡', main: '离线 · 守护服务不可达', mainRed: true,
      sub: '请检查边缘网关电源与网络后，点右上角重连' }
  }
  if (store.connecting) {
    return { cls: 'quiet', emoji: '⏳', main: '真实信号接入中', mainRed: false,
      sub: '正在检测探测器信号 · 结果即将更新' }
  }
  if (store.demoTransition) {
    return { cls: 'quiet', emoji: '⏳',
      main: store.demoTransLost ? '无信号 · 重新接入' : store.demoTransition, mainRed: false,
      sub: store.demoTransLost ? '上一场景已断开 · 正在重新接入' : '场景数据即将接入，实时状态马上更新' }
  }
  if (store.signalLost) {
    return { cls: 'danger', emoji: '📶', main: '信号异常 · 监测暂停', mainRed: false,
      sub: `${store.deviceState === 'weak' ? '信号微弱' : '未检测到探测器信号'} · 请核查设备供电与网络` }
  }
  if (store.standby) {
    return { cls: 'quiet', emoji: '🌙', main: '系统待机中', mainRed: false,
      sub: '开启上方「真实接入」或「演示接入」后，开始实时守护' }
  }
  // 监测链路实时可用 → 显示盘三态：告警 > 无人 > 正常
  if (store.semanticState === 'fall' || store.guardZone >= 3) {
    let sub = '双证据线成立 · 确证链启动'
    if (store.guardZone >= 4) sub = '两轮无人应答 · 已进入危机状态，递进措施执行中'
    else if (store.voiceConfirmState === 'help') sub = '老人求助（或检测到呻吟）· 告警已升级'
    else if (store.voiceConfirmState === 'ok') sub = '老人回应正常 · 正在解除'
    else if (store.voiceRound === 2) sub = `第 1 轮无回应 · 已报警并发起第 2 轮询问（${store.voiceTimeoutS}s），请尽快确认`
    else if (store.voiceConfirmState === 'waiting') sub = '已发起语音询问（第 1 轮），等待老人回应…'
    return { cls: 'danger', emoji: '🚨', main: '告警 · 疑似跌倒', mainRed: true, sub }
  }
  if (!store.present) {
    return { cls: 'quiet', emoji: '🚪', main: '无人 · 环境安静', mainRed: false,
      sub: '未探测到人体活动与呼吸信号' }
  }
  if (store.semanticState === 'active') {
    return { cls: '', emoji: '✅', main: '正常', mainRed: false,
      sub: `有人 · 移动中 · ${stateDurationText.value}` }
  }
  if (store.semanticState === 'rest') {
    return { cls: '', emoji: '✅', main: '正常', mainRed: false,
      sub: `有人 · 休憩中 · 呼吸平稳 · ${stateDurationText.value}` }
  }
  return { cls: '', emoji: '✅', main: '正常', mainRed: false,
    sub: '有人 · 状态识别中' }
})

/** 状态条左侧色带：随守护等级加深 */
const statusBorderColor = computed(() => {
  if (store.semanticState === 'fall' || store.guardZone >= 3) return '#dc2626'
  if (store.guardZone === 2) return '#f59e0b'
  if (store.guardZone === 1) return '#fbbf24'
  if (statusVM.value.cls === 'quiet') return '#9ca3af'
  return '#16a34a'
})

/** 档案修正痕迹小字（千人千档：基础病只收紧，不放宽） */
const adjustNotes = computed(() => {
  const adj = store.profileAdjustments || {}
  const notes: string[] = []
  if (adj.br_elevated_adjust < 0) notes.push(`呼吸加快警戒线已按档案下调至 ≤${store.breathingBandMax} 次/分（更敏感）`)
  if (adj.skip_voice) notes.push('已按档案跳过现场语音询问')
  else if (typeof adj.voice_timeout === 'number' && adj.voice_timeout > 0 && adj.voice_timeout !== 15) notes.push(`语音等待 ${adj.voice_timeout} 秒`)
  if (adj.active_min_adjust < 0) notes.push('运动判定带已按档案下探（动作幅度偏低）')
  return notes.join(' · ')
})

/* ---- 双证据线（疑似跌倒/红区以上才展示） ---- */
const showEvidence = computed(() =>
  store.monitoringLive && (store.semanticState === 'fall' || store.guardZone >= 3))
/** 证据线 1 · 运动突增：近期强度峰值是否越过相对尖峰阈值 */
const motionPeak = computed(() => store.intensityHistory.length
  ? Math.max(...store.intensityHistory) : store.intensity)
const evidenceMotionOk = computed(() => motionPeak.value >= 0.22)
/** 证据线 2 · 呼吸变化：加快/浅弱/消失任一成立（正常带上限随档案修正） */
const evidenceBreathOk = computed(() =>
  store.breathingState !== 'normal' || store.breathingRate > store.breathingBandMax)
const SPIKE_THRESHOLD = 0.22

/* ---- 递进式响应时间线（8 步：双证据→两轮确证→危机递进措施） ---- */
interface RespStep { name: string; desc: string; tag: string; cls: 'done' | 'doing' | 'todo' | 'skip'; action?: string }

/* 危机递进模拟：进入危机后每 8s 点亮一级（拨打/监控/第二顺位/120） */
const crisisSince = ref(0)
watch(() => store.guardZone, (z) => {
  if (z >= 4) { if (!crisisSince.value) crisisSince.value = Date.now() }
  else crisisSince.value = 0
})
const crisisElapsed = computed(() => crisisSince.value
  ? Math.floor((nowTs.value - crisisSince.value) / 1000) : 0)

/* 确证等待逐秒倒计时：本轮总时长 − 已过秒数（随 nowTs 每秒刷新） */
const voiceWaitLeft = computed(() => {
  if (store.voiceConfirmState !== 'waiting' || !store.voiceRoundStartTs) return 0
  const used = Math.floor((nowTs.value - store.voiceRoundStartTs) / 1000)
  return Math.max(0, store.voiceTimeoutS - used)
})

/* 救护链：单顺位拨打等待秒数，到时未接通自动降级下一顺位。
   依据（20260808 定稿）：运营商无应答判定默认 20~30 秒（约 4~6 声铃，回铃音 1 秒送 4 秒断），
   lone worker 升级惯例（man-down 30s 升级）；取 30 秒≈ 6 声铃，给家人最大接听窗口 */
const RESCUE_CONTACT_S = 30
/* 案件完结：某顺位接通 → 冻结救护链倒计时（记录接通时的秒数） */
const rescueClosedAt = ref(0)
const rescueDismissed = ref(false)   // 已知晓后收起完结栏
const caseSummaryOpen = ref(false)   // 案情小结默认收起，点开头条才展开
watch(() => store.guardZone, (z) => {
  if (z < 4) {
    rescueClosedAt.value = 0; rescueDismissed.value = false; caseSummaryOpen.value = false
    // 回归常态（结案后被解除/静默自解除）：全程记录清零，等待下一案
    caseSince.value = 0; caseEndS.value = 0; caseMarks.value = []
  }
})

/* ---- 事态发展全程记录：双证据成立即开始记秒，结案冻结；节点时间线 = 病情发展一手素材，
   结案时上报事件库归档（POST /api/case-close），便于家人打 120 时汇报具体情况 ---- */
const caseSince = ref(0)
const caseEndS = ref(0)   // 结案冻结的全程秒数；0=进行中
const caseMarks = ref<{ s: number; t: string; key?: boolean }[]>([])
const caseElapsed = computed(() => caseEndS.value > 0
  ? caseEndS.value
  : (caseSince.value ? Math.floor((nowTs.value - caseSince.value) / 1000) : 0))
function pushCaseMark(t: string, key = false) {
  caseMarks.value.push({ s: caseElapsed.value, t, key })
}
/* 开案：双证据成立那一刻（T+0 记录当时的运动/呼吸实况） */
watch(showEvidence, (on) => {
  if (on && !caseSince.value) {
    caseSince.value = Date.now()
    caseEndS.value = 0
    caseMarks.value = []
    const br = store.breathingRate > 0
      ? `呼吸 ${store.breathingRate} 次/分（${store.breathingInfo.label}）` : '呼吸信号消失'
    pushCaseMark(`双证据成立：运动峰值 ${motionPeak.value.toFixed(2)} · ${br}`, true)
  }
})
/* 确证链节点：呼唤发起 / 提升级别 */
watch(() => store.voiceRound, (r) => {
  if (!caseSince.value || caseEndS.value) return
  if (r === 1) pushCaseMark('现场呼唤「您还好吗？」· AI 收音识别中')
  else if (r === 2) pushCaseMark('第 1 轮无回应 · 提升级别再次呼唤 · 报警通知家人', true)
})
/* 确证结果：呼救越级 / 回应没事 */
watch(() => store.voiceConfirmState, (vc) => {
  if (!caseSince.value || caseEndS.value) return
  if (vc === 'help') pushCaseMark('侦测到呼救/呻吟 · 跳过等待 · 越级危机', true)
  else if (vc === 'ok') pushCaseMark('老人回应「没事」· 警报解除')
})
/* 进入危机 = 开始拨打第一顺位（救护链各顺位小倒计时依旧保留在步骤内） */
watch(() => store.guardZone, (z) => {
  if (z >= 4 && caseSince.value && !caseEndS.value) {
    pushCaseMark('升级高危危机 · 开始拨打第一顺位（子女）', true)
  }
})
watch(rescueDismissed, (d) => {
  if (d) { caseSince.value = 0; caseEndS.value = 0; caseMarks.value = [] }
})
/* 演示剧本自动结案：后端在剧本设定秒数广播顺位接通（真机接入后由通话系统发同款消息） */
watch(() => store.rescueAnsweredSeq, () => {
  if (store.rescueAnsweredS > 0 && rescueClosedAt.value === 0 && store.guardZone >= 4) {
    rescueClosedAt.value = store.rescueAnsweredS
    if (store.ackRequired && !store.alertAcked) void store.ackAlert()
  }
})

/* 监控排查：平时不开，危机态下手动开启 */
const monitorChecked = ref(false)
watch(() => store.guardZone, (z) => { if (z <= 0) monitorChecked.value = false })
function onMonitorCheck() { monitorChecked.value = true }

const respSteps = computed<RespStep[]>(() => {
  const adj = store.profileAdjustments || {}
  const z = store.guardZone
  const vc = store.voiceConfirmState
  const round = store.voiceRound
  const elapsed = crisisElapsed.value
  const steps: RespStep[] = []
  // 1 双证据判定（两条证据各自展示，随监测实时刷新）
  const brText = store.breathingRate > 0
    ? `${store.breathingRate} 次/分 · ${store.breathingInfo.label}` : '呼吸信号消失'
  steps.push({ name: '双证据判定',
    desc: `冲击峰值 ${motionPeak.value.toFixed(2)}（阈值 ${SPIKE_THRESHOLD}）· 呼吸 ${brText}`,
    tag: '成立', cls: 'done' })
  // 2 语音唤醒询问（第 1 轮）
  if (adj.skip_voice) {
    steps.push({ name: '语音唤醒询问', desc: '档案含心脑血管病史 · 跳过语音直接升级，避免延误', tag: '已按档案跳过', cls: 'skip' })
  } else if (vc === 'ok') {
    steps.push({ name: '语音唤醒询问', desc: `老人第 ${store.voiceRespondedElapsedS} 秒回应「没事」 · 警报解除 · 事件库留痕`, tag: '回应正常 · 已解除', cls: 'done' })
  } else if (vc === 'help') {
    steps.push({ name: '语音唤醒询问', desc: `第 ${store.voiceRespondedElapsedS} 秒侦测到呼救/呻吟 · 跳过一切等待 · 直接越级危机`, tag: '已求助 · 直升危机', cls: 'done' })
  } else if (vc === 'waiting' && round === 1) {
    steps.push({ name: '语音唤醒询问', desc: `设备播放「您还好吗？」 · 等待回应 · 倒计时 ${voiceWaitLeft.value} 秒`, tag: '进行中', cls: 'doing' })
  } else if (round >= 2 || z >= 4) {
    steps.push({ name: '语音唤醒询问', desc: `${store.voicePrevElapsedS || store.voiceTimeoutS} 秒无回应 · 提升预警级别 · 进入第二轮询问`, tag: '无人应答', cls: 'done' })
  } else {
    steps.push({ name: '语音唤醒询问', desc: '双证据线成立后自动发起', tag: '待执行', cls: 'todo' })
  }
  // 3 提升级别 · 再次询问 + 报警通知（第 2 轮，等待时长随档案）
  if (vc === 'ok' || vc === 'help') {
    steps.push({ name: '提升级别 · 再次询问 + 报警通知', desc: '老人已回应，无需第 2 轮', tag: '未触发', cls: 'todo' })
  } else if (z >= 4) {
    steps.push({ name: '提升级别 · 再次询问 + 报警通知', desc: `再等 ${store.voiceTimeoutS} 秒仍无回应 · 直升高危危机 · 进入救护链`, tag: '直升高危', cls: 'done' })
  } else if (round === 2 && vc === 'waiting') {
    steps.push({ name: '提升级别 · 再次询问 + 报警通知',
      desc: `第 1 轮 ${store.voicePrevElapsedS || 15} 秒无回应 · 倒计时 ${voiceWaitLeft.value} 秒 · ${store.alertAcked ? '家人已知晓' : '报警响铃中，响至家人知晓'}`,
      tag: store.alertAcked ? '已知晓' : '响铃中', cls: 'doing' })
  } else {
    steps.push({ name: '提升级别 · 再次询问 + 报警通知', desc: '第 1 轮无回应时自动启动，等待时长随档案', tag: '待执行', cls: 'todo' })
  }
  /* 救护链倒计时：单顺位等待 RESCUE_CONTACT_S 秒，到时未接通自动降级；
     联系上任一顺位（结案）则冻结在 rescueClosedAt */
  const chainS = rescueClosedAt.value > 0 ? Math.min(rescueClosedAt.value, elapsed) : elapsed
  const closed = rescueClosedAt.value > 0
  // 4 通知子女（第一顺位）：倒计时到点未接通 → 降级
  if (z < 4) {
    steps.push({ name: '通知子女 · 第一顺位', desc: 'APP 推送 + 短信送达 + 自动拨打电话', tag: '待执行', cls: 'todo' })
  } else if (chainS < RESCUE_CONTACT_S) {
    steps.push({ name: '通知子女 · 第一顺位',
      desc: closed ? `子女第 ${chainS} 秒接通 · 案件完结`
        : `正在拨打子女电话 · 等待接听 · 倒计时 ${RESCUE_CONTACT_S - chainS} 秒 · 接通即结案`,
      tag: closed ? '已接通 · 案件完结' : '拨打中', cls: closed ? 'done' : 'doing' })
  } else {
    steps.push({ name: '通知子女 · 第一顺位',
      desc: `${RESCUE_CONTACT_S} 秒未接通 · 降级联系第二顺位`, tag: '未接通', cls: 'done' })
  }
  // 5 顺位联系（第二 → 第三顺位，各倒计时 RESCUE_CONTACT_S 秒，未接通逐级降级）
  if (z < 4 || chainS < RESCUE_CONTACT_S) {
    steps.push({ name: '顺位联系 · 第二/第三顺位', desc: '上一顺位倒计时结束未接通时自动降级，逐个联系附近关系人上门排查', tag: '待执行', cls: 'todo' })
  } else if (chainS < RESCUE_CONTACT_S * 2) {
    steps.push({ name: '顺位联系 · 第二顺位',
      desc: closed
        ? `第一顺位 ${RESCUE_CONTACT_S} 秒未接通 · 第二顺位第 ${chainS - RESCUE_CONTACT_S} 秒接通 · 案件完结`
        : `第一顺位 ${RESCUE_CONTACT_S} 秒未接通 · 正在联系第二顺位 · 倒计时 ${RESCUE_CONTACT_S * 2 - chainS} 秒`,
      tag: closed ? '已接通 · 案件完结' : '拨打中', cls: closed ? 'done' : 'doing' })
  } else if (chainS < RESCUE_CONTACT_S * 3) {
    steps.push({ name: '顺位联系 · 第三顺位',
      desc: closed
        ? `第二顺位 ${RESCUE_CONTACT_S} 秒未接通 · 第三顺位第 ${chainS - RESCUE_CONTACT_S * 2} 秒接通 · 案件完结`
        : `第二顺位 ${RESCUE_CONTACT_S} 秒未接通 · 正在联系第三顺位 · 倒计时 ${RESCUE_CONTACT_S * 3 - chainS} 秒`,
      tag: closed ? '已接通 · 案件完结' : '拨打中', cls: closed ? 'done' : 'doing' })
  } else {
    steps.push({ name: '顺位联系 · 第二/第三顺位',
      desc: `第二/第三顺位各拨打 ${RESCUE_CONTACT_S} 秒均未接通`, tag: '全部未接通', cls: 'done' })
  }
  // 6 拨打120 · 同步家庭地址（降级处理：仅顺位全部联系不上才启动）
  if (z < 4 || chainS < RESCUE_CONTACT_S * 3) {
    steps.push({ name: '拨打120 · 同步家庭地址', desc: '降级处理 · 仅三个顺位全部未接通时启动', tag: '待执行', cls: 'todo' })
  } else {
    steps.push({ name: '拨打120 · 同步家庭地址', desc: '顺位全部未接通 · 直拨 120 · 已同步家庭地址',
      tag: closed ? '案件完结' : '已拨打 · 降级处理', cls: 'done' })
  }
  // 7 监控排查（平时不开，危机态手动开启）
  steps.push(monitorChecked.value
    ? { name: '监控排查', desc: '已开启摄像头查看现场（平时不开）', tag: '查看中', cls: 'done' }
    : { name: '监控排查', desc: '平时不开 · 危机时可手动开启', tag: z >= 4 ? '可开启' : '待执行',
        cls: z >= 4 ? 'doing' : 'todo', action: z >= 4 ? '开启监控排查' : '' })
  return steps
})

/* ---- 案情小结：危机链实际读秒复盘（随救护链进度每秒更新）；
   结案口径 = 联系上任一顺位即案件完结，完结栏只留已知晓收尾（120 去重，统一用底部拨打120） ---- */
const rescueCloseLabel = computed(() => {
  const s = rescueClosedAt.value
  if (s <= 0) return ''
  if (s < RESCUE_CONTACT_S) return `第一顺位（子女）· 第 ${s} 秒接通`
  if (s < RESCUE_CONTACT_S * 2) return `第二顺位 · 第 ${s - RESCUE_CONTACT_S} 秒接通`
  if (s < RESCUE_CONTACT_S * 3) return `第三顺位 · 第 ${s - RESCUE_CONTACT_S * 2} 秒接通`
  return '120 急救中心'
})
/** 顺位人接通（与语音面板回应按钮同位的模拟入口）→ 冻结倒计时、案件完结，顺手停掉二轮报警响铃 */
function onRescueReached() {
  if (rescueClosedAt.value > 0 || store.guardZone < 4) return
  rescueClosedAt.value = Math.max(1, crisisElapsed.value)
  if (store.ackRequired && !store.alertAcked) void store.ackAlert()
}
/* 结案：冻结全程读秒 + 补收尾节点 + 时间线上报事件库归档 */
watch(rescueClosedAt, (s) => {
  if (s > 0 && caseSince.value && !caseEndS.value) {
    caseEndS.value = Math.max(caseElapsed.value, 1)
    pushCaseMark(`${rescueCloseLabel.value} · 案件完结`, true)
    api.reportCaseClose({
      close_label: `案件完结 · ${rescueCloseLabel.value} · 全程 ${caseEndS.value} 秒`,
      total_s: caseEndS.value,
      marks: caseMarks.value.map(m => `T+${m.s}s ${m.t}`),
    }).catch(() => { /* 归档失败不阻断收尾 */ })
  }
})
const crisisSummary = computed(() => {
  const vc = store.voiceConfirmState
  let prefix = ''
  if (vc === 'help')
    prefix = `案情小结：双证据成立 → 第 ${store.voiceRespondedElapsedS} 秒侦测到呼救/呻吟 · 跳过一切等待 · 直接越级危机`
  else if (store.guardZone >= 4)
    prefix = `案情小结：双证据成立 → ${store.voicePrevElapsedS || store.voiceTimeoutS} 秒无回应 → 再等 ${store.voiceTimeoutS} 秒仍无回应 · 直升高危危机`
  else return ''
  if (rescueClosedAt.value > 0)
    return `${prefix} → ${rescueCloseLabel.value} · 案件完结（救护链共 ${rescueClosedAt.value} 秒）`
  const s = crisisElapsed.value
  const stage = s < RESCUE_CONTACT_S
    ? `通知子女中 · 倒计时 ${RESCUE_CONTACT_S - s} 秒`
    : s < RESCUE_CONTACT_S * 2
      ? `第一顺位未接通 · 联系第二顺位中 · 倒计时 ${RESCUE_CONTACT_S * 2 - s} 秒`
      : s < RESCUE_CONTACT_S * 3
        ? `第二顺位未接通 · 联系第三顺位中 · 倒计时 ${RESCUE_CONTACT_S * 3 - s} 秒`
        : '顺位全部未接通 · 降级处理 · 已直拨 120 · 地址已同步'
  return `${prefix} → 救护链已启动 ${s} 秒 · ${stage}`
})

/* ---- 告警链中的呼吸持续监测（巩固信息，趋势文案） ---- */
const breathTrend = computed(() => {
  const st = store.breathingState
  if (st === 'lost') return { text: '呼吸消失 · 可能呼吸暂停，建议立即处置 · 持续监测中', cls: 'bad' }
  if (st === 'shallow') return { text: '呼吸浅弱 · 持续监测中', cls: 'bad' }
  const win = store.breathHistory.filter(r => r > 0).slice(-20)
  if (win.length >= 5) {
    const first = win[0], last = win[win.length - 1]
    if (last - first >= 3 && last > store.breathingBandMax)
      return { text: `气息紊乱加剧 · 呼吸急速 ${last} 次/分`, cls: 'bad' }
    if (first - last >= 3)
      return { text: `呼吸慢慢衰竭（${first} → ${last} 次/分）`, cls: 'bad' }
    if (last > store.breathingBandMax)
      return { text: `呼吸急促持续 ${last} 次/分`, cls: 'warn' }
    return { text: `呼吸趋稳 ${last} 次/分 · 等待确证结果`, cls: 'ok' }
  }
  return { text: '呼吸持续监测中…', cls: 'ok' }
})

/* ---- 告警卡显示与解除小结（解除不瞬消，保留 30 秒后淡出） ---- */
const respActive = ref(false)
const alertStartTs = ref(0)
const clearedInfo = ref<{ reason: string; durS: number; br: number; respondS: number } | null>(null)
let clearedTimer: ReturnType<typeof setTimeout> | null = null

watch(() => store.latestAlert, (a) => {
  if (a && a.level !== 'info' && store.monitoringLive) {
    if (!respActive.value) alertStartTs.value = Date.now()
    respActive.value = true
    if (clearedInfo.value) {
      clearedInfo.value = null
      if (clearedTimer) { clearTimeout(clearedTimer); clearedTimer = null }
    }
  }
})
watch(() => store.clearedSeq, () => {
  if (!respActive.value) return
  respActive.value = false
  clearedInfo.value = {
    reason: store.lastClearedReason,
    durS: Math.max(1, Math.round((Date.now() - alertStartTs.value) / 1000)),
    br: store.breathingRate,
    respondS: store.voiceRespondedElapsedS,   // 确证读秒：多久回话解除（运动自解除为 0）
  }
  if (clearedTimer) clearTimeout(clearedTimer)
  clearedTimer = setTimeout(() => { clearedInfo.value = null }, 30000)
})
/* 场景切换/退出监测：告警卡与小结一并收起 */
watch(() => [store.demoScenario, store.monitoringLive] as const, () => {
  respActive.value = false
  clearedInfo.value = null
  rescueClosedAt.value = 0
  rescueDismissed.value = false
})
onUnmounted(() => { if (clearedTimer) clearTimeout(clearedTimer) })
const showRespCard = computed(() => respActive.value && store.monitoringLive)

/* 家人已知晓：停止第二轮报警响铃 */
async function onAck() {
  await store.ackAlert()
  showToast('已知晓，响铃已停止')
}

/** 误报处置：子女确认收到（后端解除告警链路） */
async function onFalseAlarm() {
  await store.confirmAlert()
  showToast('已确认收到，告警已解除')
}

/* ---- 紧急呼叫 ---- */
function handleAlertCall120() {
  const addr = store.profile?.address || '未登记地址（档案页可填写）'
  showDialog({ title: '拨打 120', message: `正在拨打 120…\n家庭地址同步给急救中心：\n${addr}` })
  window.location.href = 'tel:120'
}
const EMERGENCY_CALL_TIMEOUT = 30000
const EMERGENCY_LABELS = ['第一', '第二', '第三']
async function handleAlertCallEmergency() {
  const phones = (store.profile?.emergency_phones || []).filter(p => p).slice(0, 3)
  if (!phones.length) {
    const elder = store.profile?.elder_phone
    showDialog({ title: '紧急联系', message: elder
      ? `未填写紧急电话，请先在档案页填写。正在拨打守护人电话（${store.elderName}直线）：${elder}`
      : `未填写紧急电话，请先在档案页填写，或立即拨打${store.elderName}的电话` })
    if (elder) window.location.href = `tel:${elder}`
    return
  }
  await callEmergencyStep(phones, 0)
}
async function callEmergencyStep(phones: string[], index: number) {
  if (index >= phones.length) {
    showDialog({ title: '紧急联系', message: `三个紧急电话均未接通，直接拨打120并同步家庭地址：\n${store.profile?.address || '未登记地址'}` })
    return
  }
  const phone = phones[index]
  const last = index === phones.length - 1
  let autoDowngraded = false
  const timer = setTimeout(() => {
    autoDowngraded = true
    closeDialog()
    callEmergencyStep(phones, index + 1)
  }, EMERGENCY_CALL_TIMEOUT)
  try {
    const notReached = await showConfirmDialog({
      title: `紧急联系（${EMERGENCY_LABELS[index]}个）`,
      message: `正在拨打 ${phone}...${last ? '' : '\n无应答将自动切换下一个号码'}`,
      confirmButtonText: '打不通',
      cancelButtonText: '挂断',
    })
    clearTimeout(timer)
    if (!autoDowngraded && notReached) await callEmergencyStep(phones, index + 1)
  } catch {
    clearTimeout(timer)
  }
}

/* ---- 双数据表：canvas 绘制（阈值与边缘 config 对齐） ---- */
const STILL_MAX = 0.05
const ACTIVE_MIN = 0.15
const BR_LOST = 3
const BR_SLOW = 8
const BR_TOP = 32
const motionCanvas = ref<HTMLCanvasElement | null>(null)
const breathCanvas = ref<HTMLCanvasElement | null>(null)

/** 画布自适应：物理分辨率 = 实际显示尺寸 × dpr，绘图保持 700 逻辑坐标系。
 * 轴文字/色带与卡片宽度永远成固定比例：窗口变化不拉伸变形、不跑出界面，高分屏不发虚。 */
function fitCanvas(cv: HTMLCanvasElement, logicalW: number, logicalH: number) {
  const dpr = window.devicePixelRatio || 1
  const cssW = cv.clientWidth || logicalW
  const cssH = Math.round(cssW * logicalH / logicalW)
  const bw = Math.round(cssW * dpr)
  const bh = Math.round(cssH * dpr)
  if (cv.width !== bw) cv.width = bw
  if (cv.height !== bh) cv.height = bh
  if (cv.style.height !== `${cssH}px`) cv.style.height = `${cssH}px`
  const ctx = cv.getContext('2d')
  if (ctx) ctx.setTransform(bw / logicalW, 0, 0, bh / logicalH, 0, 0)
  return ctx
}

function drawMotion() {
  const cv = motionCanvas.value
  if (!cv) return
  const ctx = fitCanvas(cv, 700, 260)
  if (!ctx) return
  const data = store.intensityHistory
  const W = 700, H = 260, padL = 62, padR = 8, padT = 8, padB = 16
  const cw = W - padL - padR, ch = H - padT - padB
  ctx.clearRect(0, 0, W, H)
  // 分段压缩 Y 轴：静止带占 30% 高度放大微动，尖峰带压缩防顶破
  const segs: [number, number, number][] = [
    [0, STILL_MAX, 0.30], [STILL_MAX, ACTIVE_MIN, 0.25],
    [ACTIVE_MIN, SPIKE_THRESHOLD, 0.15], [SPIKE_THRESHOLD, 1, 0.30]]
  const yOf = (v: number) => {
    let acc = 0
    for (const [lo, hi, frac] of segs) {
      if (v <= hi || hi === 1) { acc += (Math.min(v, hi) - lo) / (hi - lo) * frac; return padT + ch - acc * ch }
      acc += frac
    }
    return padT
  }
  const bandCols = ['#f1f5f9', '#fefce8', '#f0fdf4', '#fef2f2']
  let y0 = padT + ch
  segs.forEach(([, , frac], k) => { const h = ch * frac; y0 -= h; ctx.fillStyle = bandCols[k]; ctx.fillRect(padL, y0, cw, h) })
  ctx.font = '10px sans-serif'; ctx.fillStyle = '#9ca3af'; ctx.textAlign = 'right'
  const ticks: [number, string][] = [[0, '0'], [STILL_MAX, '0.05'], [ACTIVE_MIN, '0.15'], [SPIKE_THRESHOLD, '0.22 突增'], [1, '1.0']]
  for (const [v, t] of ticks) {
    const y = yOf(v)
    ctx.strokeStyle = '#e2e8f0'; ctx.beginPath(); ctx.moveTo(padL, y); ctx.lineTo(W - padR, y); ctx.stroke()
    ctx.fillText(t, padL - 6, y + 3)
  }
  if (!data.length) return
  const bw = cw / data.length
  for (let i = 0; i < data.length; i++) {
    const v = data[i], x = padL + i * bw, y = yOf(v), h = padT + ch - y
    ctx.fillStyle = v >= SPIKE_THRESHOLD ? '#ef4444' : v >= ACTIVE_MIN ? '#22c55e' : '#cbd5e1'
    ctx.fillRect(x, y, Math.max(bw * 0.7, 1), Math.max(h, 1))
  }
}

function drawBreath() {
  const cv = breathCanvas.value
  if (!cv) return
  const ctx = fitCanvas(cv, 700, 240)
  if (!ctx) return
  const data = store.breathHistory
  const bandMax = store.breathingBandMax
  const W = 700, H = 240, padL = 62, padR = 8, padT = 8, padB = 8
  const cw = W - padL - padR, ch = H - padT - padB
  ctx.clearRect(0, 0, W, H)
  const yOf = (v: number) => padT + ch - (Math.min(v, BR_TOP) / BR_TOP) * ch
  // 医学色带：正常带上限随档案修正（breathingBandMax）
  const bands: [number, number, string][] = [
    [0, BR_LOST, '#fef2f2'], [BR_LOST, BR_SLOW, '#fff7ed'],
    [BR_SLOW, bandMax, '#f0fdf4'], [bandMax, BR_TOP, '#fff7ed']]
  for (const [lo, hi, c] of bands) {
    ctx.fillStyle = c; ctx.fillRect(padL, yOf(hi), cw, yOf(lo) - yOf(hi))
  }
  ctx.strokeStyle = '#86efac'; ctx.setLineDash([4, 4])
  for (const v of [BR_SLOW, bandMax]) {
    ctx.beginPath(); ctx.moveTo(padL, yOf(v)); ctx.lineTo(W - padR, yOf(v)); ctx.stroke()
  }
  ctx.setLineDash([])
  ctx.font = '10px sans-serif'; ctx.fillStyle = '#9ca3af'; ctx.textAlign = 'right'
  // 刻度文案收短防出界（「已按档案调整」痕迹已由状态条 adjustNotes 展示）
  const ticks: [number, string][] = [[0, '0'], [BR_LOST, '3 衰减'], [BR_SLOW, '8'],
    [bandMax, `${bandMax} 加快线`], [BR_TOP, '32']]
  for (const [v, t] of ticks) ctx.fillText(t, padL - 6, yOf(v) + 3)
  if (!data.length) return
  const bw = cw / data.length
  // 折线（0=暂未测到 → 断线留白）
  ctx.strokeStyle = '#16a34a'; ctx.lineWidth = 2; ctx.beginPath()
  let prevIdx: number | null = null
  for (let i = 0; i < data.length; i++) {
    if (!data[i]) { prevIdx = null; continue }
    const x = padL + i * bw, y = yOf(data[i])
    if (prevIdx !== null && i - prevIdx <= 3) ctx.lineTo(x, y)
    else ctx.moveTo(x, y)
    prevIdx = i
  }
  ctx.stroke()
  for (let i = 0; i < data.length; i++) {
    if (!data[i]) continue
    const x = padL + i * bw, y = yOf(data[i])
    let c = '#16a34a'
    if (data[i] < BR_LOST) c = '#ef4444'
    else if (data[i] < BR_SLOW || data[i] > bandMax) c = '#fb923c'
    ctx.fillStyle = c
    if (i % 2 === 0) { ctx.beginPath(); ctx.arc(x, y, 2.4, 0, 7); ctx.fill() }
  }
  ctx.lineWidth = 1
}

function drawAll() {
  drawMotion()
  drawBreath()
}
watch(() => [store.lastSampleTime, store.breathingBandMax, store.monitoringLive], () => nextTick(drawAll))
/* 窗口尺寸变化 → 按新壳宽重绘，保持图表与背景固定比例 */
function onWinResize() { drawAll() }
onMounted(() => window.addEventListener('resize', onWinResize))
onUnmounted(() => window.removeEventListener('resize', onWinResize))

const motionNow = computed(() => {
  const v = store.intensity
  const tag = v >= SPIKE_THRESHOLD ? '突增' : v >= ACTIVE_MIN ? '活动' : '平稳'
  return `当前 ${v.toFixed(2)} · ${tag}`
})
const breathNow = computed(() => {
  if (store.breathingRate > 0) return `${store.breathingRate} 次/分 · ${store.breathingInfo.label}`
  return '暂未测到（活动打断或无呼吸信号）'
})
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

    <!-- 接入控制：真实接入 / 演示接入，都未开启时系统待机 -->
    <div class="source-card">
      <div class="source-row">
        <div class="source-info">
          <div class="source-name">
            真实接入
            <van-tag type="danger" plain class="source-priority">优先级最高</van-tag>
          </div>
          <div class="source-desc">开启后自动关闭演示，探测器信号直接联动底层判定</div>
        </div>
        <van-switch :model-value="store.realEnabled" size="20px" @update:model-value="onRealChange" />
      </div>
      <div class="source-divider"></div>
      <div class="source-row">
        <div class="source-info">
          <div class="source-name">演示接入</div>
          <div class="source-desc">接入代表性场景，实时展示各种情况下的守护状态</div>
        </div>
        <van-switch
          :model-value="store.demoEnabled" size="20px"
          :disabled="store.realEnabled" @update:model-value="onDemoChange"
        />
      </div>
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

    <!-- 探测器桥接：接入控制与显示盘之间，接入信号后先看桥接成功没
         真实接入显示真实情况 / 演示接入分阶段呈现（接入检测中 → 接入成功信号良好） -->
    <van-collapse v-model="detailOpen" inset class="detail-collapse bridge-collapse" @change="onCollapseChange">
      <van-collapse-item name="device">
        <template #title>
          <div class="bridge-title">
            <div class="collapse-title">
              <van-icon :name="bridgeVM.icon" :color="bridgeVM.color" size="16" />
              <span>探测器检测</span>
              <span class="collapse-state" :style="{ color: bridgeVM.color }">{{ bridgeVM.text }}</span>
            </div>
            <div class="bridge-sub">{{ bridgeVM.sub }}</div>
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
          v-if="store.diagnosis" title="期望上报间隔"
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
        </div>
        <!-- 信号接通测试：显示接通但收不到数据时，一键体检全链路 -->
        <div class="conn-test">
          <van-button
            size="small" type="primary" plain round
            :loading="connTesting" loading-text="检测中…"
            @click="onConnectionTest()"
          >
            🔌 信号接通测试
          </van-button>
          <div v-if="connTestResult" class="conn-test-result">
            <p class="conn-verdict" :class="connTestResult.ok ? 'ok' : 'bad'">
              {{ connTestResult.ok ? '✅' : '⚠️' }} {{ connTestResult.verdict }}
            </p>
            <p v-for="(it, i) in connTestResult.items" :key="i" class="conn-item">
              <span class="conn-item-name">{{ it.ok ? '✓' : '✗' }} {{ it.name }}</span>
              <span class="conn-item-detail">{{ it.detail }}</span>
            </p>
            <p class="conn-tested-at">检测于 {{ connTestResult.tested_at.replace('T', ' ') }}</p>
          </div>
        </div>
      </van-collapse-item>
    </van-collapse>

    <!-- 状态条：语义三态（静坐休憩中 / 活动中 / 疑似跌倒），无人时留空 -->
    <div class="status-card" :class="statusVM.cls" :style="{ borderLeftColor: statusBorderColor }">
      <div class="status-line">
        <span class="status-emoji">{{ statusVM.emoji }}</span>
        <span class="status-main" :class="{ red: statusVM.mainRed }">{{ statusVM.main }}</span>
        <div class="status-tools">
          <div class="signal-tag" :class="signalInfo.kind">
            <div class="signal-bars">
              <span v-for="i in 4" :key="i" class="bar" :class="{ on: i <= signalInfo.bars }"></span>
            </div>
            <span class="signal-text">{{ signalInfo.text }}</span>
          </div>
          <van-icon
            class="refresh-btn" name="replay" size="18" color="#6b7280"
            :class="{ spinning: store.refreshing }" @click="onRefresh()"
          />
        </div>
      </div>
      <div class="status-sub">{{ statusVM.sub }}</div>
      <div class="status-meta">
        <span class="pill" :class="{
          g: store.breathingState === 'normal' && store.breathingRate > 0,
          o: ['elevated', 'irregular'].includes(store.breathingState),
          r: ['shallow', 'lost'].includes(store.breathingState),
        }">
          {{ store.breathingRate > 0 ? `呼吸 ${store.breathingRate} 次/分` : '呼吸 暂未测到' }}
        </span>
        <span class="pill">运动幅度 {{ motionNow.split('·')[1]?.trim() || '平稳' }}</span>
        <span class="pill">{{ ZONE_CN[store.zone] || store.zone }}</span>
      </div>
      <!-- 档案修正痕迹（千人千档） -->
      <div v-if="adjustNotes" class="adjust-notes">🩺 {{ adjustNotes }}</div>
    </div>

    <!-- 双证据线：为什么判定为疑似跌倒（仅预警出现） -->
    <div class="evidence" v-if="showEvidence">
      <div class="evidence-title">为什么判定为疑似跌倒（双证据线）</div>
      <div class="ev-row">
        <span class="ev-icon" :class="{ off: !evidenceMotionOk }">{{ evidenceMotionOk ? '✓' : '⋯' }}</span>
        证据线 1 · 运动幅度突然增大：近期峰值 {{ motionPeak.toFixed(2) }}（阈值 {{ SPIKE_THRESHOLD }}）
      </div>
      <div class="ev-row">
        <span class="ev-icon" :class="{ off: !evidenceBreathOk }">{{ evidenceBreathOk ? '✓' : '⋯' }}</span>
        证据线 2 · 呼吸变化：{{ store.breathingRate > 0 ? `${store.breathingRate} 次/分 · ${store.breathingInfo.label}` : '呼吸信号消失' }}（正常 ≤{{ store.breathingBandMax }}）
      </div>
      <div class="ev-note">判定规则：两条证据线同时成立才报警。若只有动作冲击、呼吸无变化，按「伸懒腰/弯腰」处理，仅记录不打扰。</div>
    </div>

    <!-- 响应措施时间线（8 步：双证据→两轮确证→危机递进措施） -->
    <div class="resp" v-if="showRespCard">
      <div class="resp-title">🚨 已自动启动的应对措施</div>
      <!-- 告警链中的呼吸持续监测：巩固信息，展示收集情况与趋势 -->
      <div class="breath-monitor" :class="breathTrend.cls">🫁 呼吸持续监测：{{ breathTrend.text }}</div>
      <!-- 事态发展全程记录：双证据成立即记秒、结案冻结；救护链各顺位小倒计时仍保留在下方步骤内 -->
      <div class="case-timer" v-if="caseSince > 0">
        <div>
          <div class="ct-lbl">⏱ {{ caseEndS > 0 ? '事态发展 · 已结案' : '事态发展 · 进行中' }}</div>
          <div class="ct-from">自双证据成立起计{{ caseEndS > 0 ? ' · 已冻结' : ' · 结案自动冻结' }}</div>
        </div>
        <div class="ct-sec">{{ caseElapsed }} 秒</div>
      </div>
      <div class="case-timeline" v-if="caseMarks.length">
        <div class="ct-title">📈 病情发展实时记录</div>
        <div class="ct-row" v-for="(m, i) in caseMarks" :key="i" :class="{ key: m.key }">
          <span class="ct-ts">T+{{ m.s }}s</span><span class="ct-tx">{{ m.t }}</span>
        </div>
        <div class="ct-row pending" v-if="!caseEndS"><span class="ct-ts">—</span><span class="ct-tx">接通/处置结果 · 待记录…</span></div>
      </div>
      <div class="step" v-for="(st, i) in respSteps" :key="i">
        <div class="step-dot" :class="st.cls">{{ st.cls === 'done' ? '✓' : st.cls === 'doing' ? '⋯' : '○' }}</div>
        <div class="step-body">
          <div class="step-name">
            {{ st.name }}
            <span class="step-tag" :class="'tag-' + st.cls">{{ st.tag }}</span>
          </div>
          <div class="step-desc">{{ st.desc }}</div>
          <button v-if="st.action" class="step-action" @click="onMonitorCheck">📹 {{ st.action }}</button>
        </div>
      </div>
      <!-- 案件完结：某顺位接通 → 冻结倒计时，只留已知晓收尾（120 统一用下方拨打120，去重）；案情小结结案后才展示且默认收起，点开才展开（避免演出中剧透） -->
      <div class="rescue-close" v-if="rescueClosedAt > 0 && !rescueDismissed">
        <div class="rescue-close-title">✅ 案件完结 · {{ rescueCloseLabel }} · 救护链终止</div>
        <div class="case-summary-head" :class="{ open: caseSummaryOpen }" @click="caseSummaryOpen = !caseSummaryOpen">
          <span>📋 案情小结 · {{ caseSummaryOpen ? '点击收起' : '点开展开' }}</span>
          <span class="chev">{{ caseSummaryOpen ? '▴' : '▾' }}</span>
        </div>
        <div class="case-summary" v-if="caseSummaryOpen">
          <template v-if="caseMarks.length">
            <div class="cs-line cs-total">全程用时 {{ caseEndS || caseElapsed }} 秒（双证据成立 → 案件完结）</div>
            <div class="cs-line" v-for="(m, i) in caseMarks" :key="i">T+{{ m.s }}s {{ m.t }}</div>
            <div class="cs-line cs-note">以上时间节点可作为拨打 120 时汇报病情的第一手材料</div>
          </template>
          <template v-else>{{ crisisSummary }}</template>
        </div>
        <div class="resp-btns">
          <button class="btn ghost" @click="rescueDismissed = true">已知晓 · 收尾</button>
        </div>
      </div>
      <!-- 顺位人接通（模拟入口）：联系上任一顺位即案件完结 -->
      <button v-if="store.guardZone >= 4 && rescueClosedAt === 0" class="btn ack reached" @click="onRescueReached">📞 顺位人已接通 · 本案完结</button>
      <!-- 第二轮报警：响铃直到家人知晓 -->
      <button v-if="store.ackRequired && !store.alertAcked" class="btn ack" @click="onAck">🔔 报警中 · 点我确认已知晓</button>
      <div class="resp-btns">
        <button class="btn primary" @click="handleAlertCallEmergency">📞 紧急联系</button>
        <button class="btn danger" @click="handleAlertCall120">🚑 拨打120</button>
        <button class="btn ghost" @click="onFalseAlarm">已确认/误报</button>
      </div>
    </div>
    <!-- 解除小结：告警解除不瞬消，保留 30 秒，事件库全程可查 -->
    <div class="resp cleared-card" v-else-if="clearedInfo">
      <div class="resp-title ok">✅ 告警已解除</div>
      <div class="cleared-line">解除原因：{{ clearedInfo.reason || '告警已解除' }}</div>
      <div class="cleared-line" v-if="clearedInfo.respondS > 0">确证读秒：语音询问后 {{ clearedInfo.respondS }} 秒老人回应 → 警报解除</div>
      <div class="cleared-line">持续时长：{{ clearedInfo.durS }} 秒 · 最新呼吸：{{ clearedInfo.br > 0 ? `${clearedInfo.br} 次/分` : '暂未测到' }}</div>
      <div class="cleared-line note">全过程已在事件库留痕，可随时查看</div>
    </div>

    <!-- 语音确证面板：双证据成立后自动发起，AI 收音识别老人回应并反馈中枢综合判定。
         只在确证链进行中（等待回应且未进危机）显示，一出结果即收起；
         界面无任何模拟按钮，真机由语音模块将识别结果接入 /api/voice-answer -->
    <van-cell-group inset class="section" v-if="store.monitoringLive && store.voiceConfirmState === 'waiting' && store.guardZone < 4">
      <van-cell title="语音确证 · AI 识别" center>
        <template #icon>
          <van-icon name="volume-o" color="#ff9f00" style="margin-right:8px" />
        </template>
      </van-cell>
      <div class="voice-panel">
        <p v-if="store.voiceRound === 2">第 1 轮未识别到回应 · 已提升告警级别并再次呼唤 · AI 持续收音识别中…</p>
        <p v-else>已发起现场呼唤“您还好吗？” · AI 收音识别老人回应中，识别结果将反馈中枢综合判定…</p>
      </div>
    </van-cell-group>

    <!-- 运动幅度表：分段压缩 Y 轴 -->
    <div class="chart-card">
      <div class="chart-head">
        <span class="chart-title">📶 运动幅度（近 60 秒）</span>
        <span class="chart-now">{{ motionNow }}</span>
      </div>
      <canvas ref="motionCanvas" width="700" height="260"></canvas>
      <div class="chart-foot"><span>60秒前</span><span>30秒前</span><span>现在</span></div>
      <div class="legend">
        <span><i class="dot" style="background:#cbd5e1"></i>平稳</span>
        <span><i class="dot" style="background:#22c55e"></i>活动</span>
        <span><i class="dot" style="background:#ef4444"></i>突然增大</span>
      </div>
    </div>

    <!-- 呼吸频率表：医学色带，正常带上限随档案修正 -->
    <div class="chart-card">
      <div class="chart-head">
        <span class="chart-title">🫁 呼吸频率（近 60 秒）</span>
        <span class="chart-now">{{ breathNow }}</span>
      </div>
      <canvas ref="breathCanvas" width="700" height="240"></canvas>
      <div class="chart-foot"><span>60秒前</span><span>30秒前</span><span>现在</span></div>
      <div class="legend">
        <span><i class="dot" style="background:#22c55e"></i>平稳 8~{{ store.breathingBandMax }}</span>
        <span><i class="dot" style="background:#fb923c"></i>加快&gt;{{ store.breathingBandMax }} / 浅慢&lt;8</span>
        <span><i class="dot" style="background:#ef4444"></i>衰减&lt;3</span>
        <span class="legend-note">空白 = 暂未测到</span>
      </div>
    </div>

    <div class="page-note">
      守护层级：无人 / 有人（正常·休憩中·移动中 | 异常·疑似跌倒）<br>
      双证据线成立才预警 · CSI 信号反射实时分析 · 无摄像头 · 无需穿戴
    </div>
  </div>
</template>

<style scoped>
.home-page {
  min-height: 100vh;
  background: #f4f6f8;
  padding-bottom: 16px;
}

/* ---- 顶栏 ---- */
.home-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 16px;
  background: #fff;
}
.brand { display: flex; align-items: center; gap: 8px; }
.brand-logo {
  width: 40px; height: 40px; object-fit: contain;
  filter: drop-shadow(0 2px 4px rgba(26, 58, 110, 0.18));
}
.brand-text { display: flex; flex-direction: column; }
.brand-name { font-size: 15px; font-weight: 700; color: #1f2937; letter-spacing: 0.5px; }
.brand-sub { font-size: 10px; color: #9ca3af; letter-spacing: 1px; }
.elder-chip {
  display: flex; align-items: center; gap: 8px;
  padding: 4px 10px 4px 4px; background: #f7f8fa; border-radius: 999px;
}
.elder-avatar {
  width: 32px; height: 32px; border-radius: 50%;
  background: linear-gradient(135deg, #1989fa, #47b2ff);
  color: #fff; font-size: 15px; font-weight: 600;
  display: flex; align-items: center; justify-content: center;
}
.elder-info { display: flex; flex-direction: column; }
.elder-name { font-size: 13px; font-weight: 600; color: #1f2937; }
.elder-state { display: flex; align-items: center; gap: 4px; font-size: 10px; color: #969799; }
.state-dot { width: 6px; height: 6px; border-radius: 50%; background: #16a34a; }
.state-dot.alert { background: #dc2626; }

/* ---- 接入控制卡 ---- */
.source-card {
  margin: 10px 10px 0; padding: 4px 14px;
  background: #fff; border-radius: 14px;
}
.source-row { display: flex; align-items: center; justify-content: space-between; padding: 10px 0; }
.source-info { flex: 1; margin-right: 10px; }
.source-name { font-size: 13px; font-weight: 700; color: #1f2937; display: flex; align-items: center; gap: 6px; }
.source-priority { transform: scale(0.9); }
.source-desc { font-size: 11px; color: #9ca3af; margin-top: 2px; }
.source-divider { height: 1px; background: #f1f5f9; }
.scenario-bar {
  display: flex; align-items: center; justify-content: space-between;
  margin: 2px 0 10px; padding: 8px 10px;
  border-radius: 10px; background: #f8fafc;
  border-left: 4px solid var(--sc-color, #969799);
}
.scenario-bar-info { display: flex; flex-direction: column; gap: 4px; }
.scenario-bar-title { font-size: 12px; font-weight: 700; color: #1f2937; }
.scenario-bar-segs { display: flex; gap: 3px; }
.scenario-bar-segs i { width: 14px; height: 3px; border-radius: 2px; background: #e2e8f0; }
.scenario-bar-segs i.on { background: var(--sc-color, #1f2937); }
.scenario-switch-btn {
  display: flex; align-items: center; gap: 3px;
  font-size: 12px; color: #2563eb; background: #eff6ff;
  border: none; border-radius: 8px; padding: 6px 10px;
}

/* ---- 状态条（语义三态） ---- */
.status-card {
  background: #fff; border-radius: 16px;
  padding: 16px 14px; margin: 10px 10px 0;
  border-left: 6px solid #16a34a;
}
.status-line { display: flex; align-items: center; gap: 10px; }
.status-emoji { font-size: 30px; line-height: 1; }
.status-main { font-size: 21px; font-weight: 800; color: #1f2937; flex: 1; }
.status-main.red { color: #dc2626; }
.status-tools { display: flex; align-items: center; gap: 8px; }
.status-sub { font-size: 12px; color: #6b7280; margin-top: 8px; line-height: 1.6; }
.status-meta { display: flex; gap: 8px; margin-top: 10px; flex-wrap: wrap; }
.pill { font-size: 11px; padding: 4px 10px; border-radius: 99px; background: #f1f5f9; color: #6b7280; }
.pill.g { background: #ecfdf5; color: #047857; }
.pill.o { background: #fffbeb; color: #b45309; }
.pill.r { background: #fef2f2; color: #b91c1c; }
.adjust-notes { margin-top: 10px; font-size: 11px; color: #0369a1; background: #f0f9ff; border-radius: 8px; padding: 6px 8px; line-height: 1.6; }

/* 信号标签 */
.signal-tag {
  display: flex; align-items: center; gap: 5px;
  padding: 4px 9px; border-radius: 20px; background: #f1f5f9;
}
.signal-bars { display: flex; align-items: flex-end; gap: 2px; height: 14px; }
.signal-bars .bar { width: 3px; border-radius: 1px; box-sizing: border-box; background: #d1d5db; }
.signal-bars .bar:nth-child(1) { height: 5px; }
.signal-bars .bar:nth-child(2) { height: 8px; }
.signal-bars .bar:nth-child(3) { height: 11px; }
.signal-bars .bar:nth-child(4) { height: 14px; }
.signal-tag.real .bar.on { background: #16a34a; }
.signal-tag.weak .bar.on { background: #f59e0b; }
.signal-tag.virtual .bar.on { background: transparent; border: 1px solid #2563eb; }
.signal-text { font-size: 11px; line-height: 1; color: #6b7280; }
.signal-tag.real .signal-text { color: #047857; }
.signal-tag.weak .signal-text { color: #b45309; }
.signal-tag.virtual .signal-text { color: #2563eb; }
.signal-tag.connecting .signal-text { color: #2563eb; animation: pulse-text 1.2s ease-in-out infinite; }
@keyframes pulse-text { 0%, 100% { opacity: 1; } 50% { opacity: 0.45; } }
.refresh-btn.spinning { animation: spin 1s linear infinite; }
@keyframes spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }

/* ---- 双证据线 ---- */
.evidence {
  background: #fff; border: 1px solid #fecaca; border-radius: 12px;
  padding: 12px; margin: 10px 10px 0;
}
.evidence-title { font-size: 13px; font-weight: 700; color: #dc2626; margin-bottom: 8px; }
.ev-row { display: flex; align-items: center; gap: 8px; font-size: 13px; color: #1f2937; padding: 5px 0; }
.ev-icon {
  width: 20px; height: 20px; border-radius: 50%; flex: none;
  display: flex; align-items: center; justify-content: center;
  font-size: 12px; color: #fff; background: #dc2626;
}
.ev-icon.off { background: #d1d5db; }
.ev-note { font-size: 11px; color: #9ca3af; margin-top: 6px; line-height: 1.5; }

/* ---- 响应措施时间线 ---- */
.resp {
  background: #fff5f5; border: 1px solid #fecaca; border-radius: 16px;
  padding: 14px; margin: 10px 10px 0;
}
.resp-title { font-size: 14px; font-weight: 800; color: #dc2626; margin-bottom: 10px; }
.step { display: flex; gap: 10px; position: relative; padding-bottom: 14px; }
.step:last-child { padding-bottom: 0; }
.step::before { content: ''; position: absolute; left: 11px; top: 24px; bottom: 0; width: 2px; background: #fecaca; }
.step:last-child::before { display: none; }
.step-dot {
  width: 24px; height: 24px; border-radius: 50%; flex: none; z-index: 1;
  display: flex; align-items: center; justify-content: center; font-size: 13px;
}
.step-dot.done { background: #dc2626; color: #fff; }
.step-dot.doing { background: #fff; border: 2px solid #dc2626; color: #dc2626; animation: pulse 1.2s infinite; }
.step-dot.todo { background: #fff; border: 2px solid #fca5a5; color: #fca5a5; }
.step-dot.skip { background: #f3f4f6; color: #9ca3af; }
@keyframes pulse { 0%, 100% { box-shadow: 0 0 0 0 rgba(220, 38, 38, .35); } 50% { box-shadow: 0 0 0 6px rgba(220, 38, 38, 0); } }
.step-body { flex: 1; }
.step-name { font-size: 13px; font-weight: 700; color: #1f2937; }
.step-desc { font-size: 12px; color: #6b7280; margin-top: 2px; line-height: 1.5; }
.step-tag { font-size: 11px; padding: 1px 8px; border-radius: 99px; margin-left: 6px; vertical-align: 1px; }
.tag-done { background: #fee2e2; color: #b91c1c; }
.tag-doing { background: #fef3c7; color: #b45309; }
.tag-todo { background: #f3f4f6; color: #9ca3af; }
.tag-skip { background: #e0f2fe; color: #0369a1; }
.resp-btns { display: flex; gap: 8px; margin-top: 12px; }
.btn { flex: 1; padding: 11px 0; border-radius: 12px; border: none; font-size: 13px; font-weight: 700; }
.btn.primary { background: #dc2626; color: #fff; }
.btn.danger { background: #111827; color: #fff; }
.btn.ghost { background: #fff; color: #6b7280; border: 1px solid #e5e7eb; }
.btn.ack {
  width: 100%; margin-top: 12px; padding: 13px 0;
  background: #f59e0b; color: #fff; font-size: 14px;
  animation: pulse 1.2s infinite;
}
/* 呼吸持续监测条 */
.breath-monitor {
  font-size: 12px; line-height: 1.6; border-radius: 8px;
  padding: 7px 10px; margin-bottom: 12px;
  background: #ecfdf5; color: #047857;
}
.breath-monitor.warn { background: #fffbeb; color: #b45309; }
.breath-monitor.bad { background: #fee2e2; color: #b91c1c; font-weight: 700; }
/* 步骤内手动操作按钮（监控排查） */
.step-action {
  margin-top: 6px; padding: 6px 12px;
  font-size: 12px; font-weight: 600; color: #b45309;
  background: #fef3c7; border: 1px solid #fcd34d; border-radius: 8px;
}
/* 解除小结卡 */
.cleared-card { background: #f0fdf4; border-color: #bbf7d0; }
.resp-title.ok { color: #16a34a; }
.cleared-line { font-size: 12px; color: #374151; line-height: 1.8; }
.cleared-line.note { color: #9ca3af; margin-top: 4px; }
/* 案情小结：结案后展示的危机链实际读秒复盘（演出中不出现，避免剧透）；默认收起一行头条，点开才展开全文 */
.case-summary-head {
  margin-top: 8px; padding: 9px 12px; border-radius: 12px;
  background: #fff; border: 1px dashed #fca5a5;
  font-size: 12px; font-weight: 700; color: #7f1d1d;
  display: flex; justify-content: space-between; align-items: center;
}
.case-summary-head .chev { color: #b91c1c; font-size: 11px; }
.case-summary-head.open { border-radius: 12px 12px 0 0; }
.case-summary {
  padding: 10px 12px; border-radius: 0 0 12px 12px;
  background: #fff; border: 1px dashed #fca5a5; border-top: none;
  font-size: 12px; line-height: 1.8; color: #7f1d1d; font-weight: 600;
}
.cs-line { padding: 1px 0; }
.cs-total { font-weight: 800; }
.cs-note { color: #9ca3af; font-weight: 400; font-size: 11px; margin-top: 4px; }
/* 事态发展全程记录：计时条 + 节点时间线（双证据成立→结案） */
.case-timer {
  display: flex; justify-content: space-between; align-items: center;
  background: #fff; border: 1px solid #fca5a5; border-radius: 12px;
  padding: 9px 12px; margin-top: 8px;
}
.ct-lbl { font-size: 12px; font-weight: 800; color: #b91c1c; }
.ct-from { font-size: 10.5px; color: #9ca3af; margin-top: 2px; }
.ct-sec { font-size: 16px; font-weight: 800; color: #dc2626; font-variant-numeric: tabular-nums; }
.case-timeline {
  background: #fff; border: 1px solid #fecaca; border-radius: 12px;
  padding: 10px 12px; margin-top: 8px;
}
.ct-title { font-size: 12px; font-weight: 800; color: #7f1d1d; margin-bottom: 6px; }
.ct-row { display: flex; gap: 8px; font-size: 11.5px; line-height: 1.7; padding: 2px 0; }
.ct-ts { flex-shrink: 0; width: 52px; font-weight: 800; color: #b91c1c; font-variant-numeric: tabular-nums; }
.ct-tx { color: #374151; }
.ct-row.key .ct-tx { font-weight: 700; color: #7f1d1d; }
.ct-row.pending .ct-ts { color: #d1d5db; }
.ct-row.pending .ct-tx { color: #9ca3af; }
/* 案件完结栏：顺位接通后只留已知晓收尾 */
.rescue-close {
  margin-top: 12px; padding: 12px; border-radius: 12px;
  background: #f0fdf4; border: 1px solid #86efac;
}
.rescue-close-title { font-size: 13px; font-weight: 800; color: #16a34a; }
.btn.ack.reached { background: #16a34a; }

/* ---- 数据表 ---- */
.chart-card {
  background: #fff; border-radius: 16px;
  padding: 14px 12px 10px; margin: 10px 10px 0;
}
.chart-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; }
.chart-title { font-size: 14px; font-weight: 700; color: #1f2937; }
.chart-now { font-size: 12px; color: #6b7280; }
canvas { display: block; width: 100%; }
.chart-foot { display: flex; justify-content: space-between; font-size: 11px; color: #9ca3af; margin-top: 4px; padding: 0 2px; }
.legend { display: flex; gap: 10px; margin-top: 6px; flex-wrap: wrap; }
.legend span { font-size: 11px; color: #6b7280; display: flex; align-items: center; gap: 4px; }
.legend-note { color: #9ca3af; }
.dot { width: 8px; height: 8px; border-radius: 2px; display: inline-block; }

/* ---- 语音确证 ---- */
.section { margin-top: 10px; }
.voice-panel { padding: 4px 16px 12px; font-size: 13px; color: #1f2937; }
.voice-panel p { margin: 0; }

/* ---- 折叠详情 ---- */
.detail-collapse { margin-top: 10px; }
/* 探测器桥接：紧贴接入控制卡，与显示盘之间形成「接入 → 桥接 → 显示」阅读顺序 */
.bridge-collapse { margin-top: 8px; }
.bridge-title { display: flex; flex-direction: column; gap: 2px; }
.bridge-sub { font-size: 11px; color: #9ca3af; padding-left: 22px; }
.collapse-title { display: flex; align-items: center; gap: 6px; font-size: 13px; }
.collapse-state { font-size: 11px; margin-left: 4px; }
.diag-tips { padding: 8px 16px 12px; }

/* ---- 信号接通测试 ---- */
.conn-test { padding: 8px 16px 14px; }
.conn-test-result { margin-top: 10px; padding: 10px 12px; background: #f8fafc; border-radius: 10px; }
.conn-verdict { font-size: 13px; font-weight: 600; margin-bottom: 8px; }
.conn-verdict.ok { color: #047857; }
.conn-verdict.bad { color: #b91c1c; }
.conn-item { display: flex; gap: 8px; font-size: 12px; padding: 3px 0; }
.conn-item-name { flex-shrink: 0; color: #1f2937; font-weight: 500; }
.conn-item-detail { color: #6b7280; }
.conn-tested-at { margin-top: 8px; font-size: 10px; color: #9ca3af; }
.diag-tips-title { font-size: 12px; font-weight: 700; color: #1f2937; margin: 0 0 6px; }
.diag-tip { font-size: 12px; color: #6b7280; margin: 2px 0; }

.page-note {
  font-size: 11px; color: #9ca3af; text-align: center;
  margin: 14px 10px 4px; line-height: 1.7;
}
</style>
