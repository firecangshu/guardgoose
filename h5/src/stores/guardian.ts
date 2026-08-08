/** 全局状态：WebSocket 连接、实时数据、设备连接状态、告警管理。 */
import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { api, type EventItem, type AlertItem, type ProfileData, type DeviceStatusData, type DeviceDiagnosisData, type AutoCheckData, type SourceModeData, type DemoScenarioInfo } from '../services/api'

/* Zone 显示配置 */
export const ZONE_MAP: Record<number, { label: string; color: string; bg: string; icon: string }> = {
  '-1': { label: '未检测', color: '#969799', bg: '#f2f3f5', icon: 'minus' },
  0: { label: '正常', color: '#07c160', bg: '#e8f8ef', icon: 'checked' },
  1: { label: '注意', color: '#ff976a', bg: '#fff3e8', icon: 'warning-o' },
  2: { label: '观察', color: '#ff9f00', bg: '#fff7e6', icon: 'eye-o' },
  3: { label: '告警', color: '#ee0a24', bg: '#fde8e8', icon: 'bell' },
  4: { label: '紧急', color: '#000000', bg: '#333333', icon: 'fire-o' },
}

export const BREATHING_MAP: Record<string, { label: string; color: string }> = {
  normal: { label: '正常', color: '#07c160' },
  elevated: { label: '偏快', color: '#ff9f00' },
  irregular: { label: '不规则', color: '#ff9f00' },
  shallow: { label: '浅弱', color: '#ee0a24' },
  lost: { label: '消失', color: '#ee0a24' },
}

export const DISEASE_MAP: Record<string, string> = {
  heart: '心梗/心律失常',
  stroke: '脑梗',
  epilepsy: '癫痫',
  diabetes: '糖尿病',
  parkinson: '帕金森',
  alzheimer: '阿尔茨海默',
  hypertension: '高血压',
  anemia: '贫血',
}

/* 监护人与老人的关系 */
export const RELATIONSHIP_MAP: Record<string, string> = {
  son: '儿子',
  daughter: '女儿',
  daughter_in_law: '儿媳',
  son_in_law: '女婿',
  grandchild: '孙辈',
  other: '其他亲属',
}

/* 身体状态标准分类 */
export const HEALTH_STATUS_MAP: Record<string, { label: string; desc: string }> = {
  good: { label: '健康良好', desc: '无重大疾病，生活完全自理' },
  chronic_stable: { label: '慢病稳定', desc: '有慢性病但控制稳定' },
  mobility_limited: { label: '行动不便', desc: '行走需搀扶或使用助行器' },
  frail: { label: '体弱多病', desc: '体质较弱，需要日常关注' },
  post_surgery: { label: '术后康复', desc: '手术后处于恢复期' },
  care_needed: { label: '失能/半失能', desc: '生活部分或全部需他人照料' },
}

export const EVENT_MAP: Record<string, string> = {
  presence_on: '检测到有人',
  presence_off: '离开检测区',
  motion_active: '活动检测',
  still_too_long: '久滞不动',
  suspected_fall: '疑似跌倒',
  no_wake_up: '未按时起床',
  activity_drop: '活动量骤降',
  device_offline: '设备离线',
  breathing_abnormal: '呼吸异常',
  breathing_lost: '呼吸消失',
  fall_breathing_ok: '跌倒（呼吸正常）',
  fall_breathing_bad: '跌倒（呼吸异常）',
  intrusion_suspected: '疑似入侵',
  intrusion_confirmed: '入侵确认',
}

/* 设备连接三态显示配置 */
export const DEVICE_STATE_MAP: Record<string, { label: string; color: string; icon: string }> = {
  connected: { label: '已接通 · 信号良好', color: '#07c160', icon: 'wifi' },
  weak: { label: '信号不佳 · 数据延迟', color: '#ff9f00', icon: 'warning-o' },
  disconnected: { label: '已断开 · 无信号', color: '#ee0a24', icon: 'close' },
  offline: { label: '离线 · 无法连接守护服务', color: '#ee0a24', icon: 'warning' },
}

/** 格式化ISO时间戳为 HH:mm:ss */
export function fmtTime(iso: string): string {
  if (!iso) return ''
  const d = new Date(iso)
  if (isNaN(d.getTime())) return iso.slice(0, 19)
  return d.toTimeString().slice(0, 8)
}

/** 告警提示音（Web Audio 合成，无需音频文件） */
function playAlertSound() {
  try {
    const ctx = new (window.AudioContext || (window as any).webkitAudioContext)()
    const playBeep = (freq: number, start: number) => {
      const osc = ctx.createOscillator()
      const gain = ctx.createGain()
      osc.type = 'sine'
      osc.frequency.value = freq
      gain.gain.setValueAtTime(0.001, ctx.currentTime + start)
      gain.gain.exponentialRampToValueAtTime(0.4, ctx.currentTime + start + 0.02)
      gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + start + 0.3)
      osc.connect(gain)
      gain.connect(ctx.destination)
      osc.start(ctx.currentTime + start)
      osc.stop(ctx.currentTime + start + 0.32)
    }
    playBeep(880, 0)
    playBeep(880, 0.4)
    playBeep(1175, 0.8)
  } catch { /* 浏览器阻止自动播放时静默失败 */ }
}

export const useGuardianStore = defineStore('guardian', () => {
  /* ---- 状态 ---- */
  const wsConnected = ref(false)
  const elderName = ref(localStorage.getItem('wg_elder_name') || '妈妈')
  const present = ref(false)
  const zone = ref('living')
  const guardZone = ref(-1)
  const intensity = ref(0)
  const breathingRate = ref(0)
  const breathingState = ref('normal')
  /** 语义三态：rest 静坐休憩中 / active 活动中 / fall 疑似跌倒；空串=未探测到人（不断言） */
  const semanticState = ref('')
  /** 呼吸正常带上限（已按档案修正，供守护页画医学色带） */
  const breathingBandMax = ref(20)
  /** 档案修正系数（守护页展示「已按档案调整」痕迹） */
  const profileAdjustments = ref<Record<string, any>>({})
  const intensityHistory = ref<number[]>([])
  /** 呼吸率历史（次/分，0=未检出），与强度曲线同源同步，画呼吸波动表 */
  const breathHistory = ref<number[]>([])
  const events = ref<EventItem[]>([])
  const alerts = ref<AlertItem[]>([])
  const profile = ref<ProfileData | null>(null)
  const voiceConfirmState = ref('')
  /** 确证轮次：0=无 1=第一轮等待 2=第二轮等待（报警响铃中） */
  const voiceRound = ref(0)
  /** 当前轮次等待时长（秒，随档案：第一轮 voice_timeout / 第二轮 requery_wait_s） */
  const voiceTimeoutS = ref(90)
  /** 第二轮报警响铃状态：ack_required 告警到达→响铃，已知晓→停 */
  const ackRequired = ref(false)
  const alertAcked = ref(false)
  /** 最近一次告警解除原因（守护页「已解除」汇总小结的数据源） */
  const lastClearedReason = ref('')
  const clearedSeq = ref(0)
  let ringTimer: ReturnType<typeof setInterval> | null = null
  const refreshing = ref(false)

  /* ---- 离线状态（替代演示模式）---- */
  const offline = ref(false)
  const lastSampleTime = ref('')

  /* ---- 设备连接状态 ---- */
  const deviceState = ref<string>('')  // connected / weak / disconnected
  const deviceStatus = ref<DeviceStatusData | null>(null)
  const diagnosis = ref<DeviceDiagnosisData | null>(null)
  /* 开阀自动体检：开真实接入后自动跑全链路体检，结果供三态显示区分
   * “没开阀（待机）”与“开了阀但断在某环（故障）” */
  const autoChecking = ref(false)
  const autoCheckResult = ref<AutoCheckData | null>(null)
  let devicePollTimer: ReturnType<typeof setInterval> | null = null

  /* ---- 数据源模式：真实接入 > 演示场景 > 虚拟兜底 ---- */
  const realEnabled = ref(false)
  const demoEnabled = ref(false)
  const demoScenario = ref('')
  const demoScenarios = ref<DemoScenarioInfo[]>([])

  /* ---- 演示接入过渡态：每个场景都是独立“剧本”
   * 切换场景 = 重新接入：无信号（上一场景断开）→ 接入检测中 → 接入成功 → 落定
   * 剧本接入过程是模拟演出：样本到达不提前清除，走完既定阶段才落定 ---- */
  const demoTransition = ref('')
  const demoTransLost = ref(false)  // 重新接入时的“无信号”阶段
  let demoTransTimer: ReturnType<typeof setTimeout> | null = null
  let demoLostTimer: ReturnType<typeof setTimeout> | null = null
  function clearDemoTransition() {
    demoTransition.value = ''
    demoTransLost.value = false
    if (demoTransTimer) { clearTimeout(demoTransTimer); demoTransTimer = null }
    if (demoLostTimer) { clearTimeout(demoLostTimer); demoLostTimer = null }
  }
  function startDemoTransition(switching: boolean) {
    clearDemoTransition()
    if (switching) {
      // 重新接入：先短暂“无信号”（上一场景已断开），再进入接入检测中
      demoTransLost.value = true
      demoTransition.value = '无信号'
      demoLostTimer = setTimeout(() => {
        demoTransLost.value = false
        if (demoTransition.value) demoTransition.value = '接入检测中'
      }, 900)
    } else {
      demoTransition.value = '接入检测中'
    }
    // 剧本接入：检测中约 1.5s 后桥接成功，随即落定为常驻信号良好
    // （切换场景时检测中从 0.9s 才开始，成功节点顺延，避开“无信号”阶段）
    setTimeout(() => {
      if (demoTransition.value === '接入检测中') demoTransition.value = '接入成功 · 信号良好'
    }, switching ? 2400 : 1500)
    demoTransTimer = setTimeout(clearDemoTransition, switching ? 4200 : 3500)
  }

  /* ---- 计算属性 ---- */
  const zoneInfo = computed(() => ZONE_MAP[guardZone.value] || ZONE_MAP[-1])
  const breathingInfo = computed(() => BREATHING_MAP[breathingState.value] || BREATHING_MAP.normal)
  const alertCount = computed(() => alerts.value.filter(a => a.level !== 'info').length)
  const latestAlert = computed(() => alerts.value[0] || null)
  /** 综合连接状态：离线(API全失败) > 设备三态 */
  const connectionState = computed(() => offline.value ? 'offline' : (deviceState.value || 'disconnected'))
  const connectionInfo = computed(() => DEVICE_STATE_MAP[connectionState.value] || DEVICE_STATE_MAP.disconnected)

  /* ---- 信号依赖总闸：所有实时展示的单一事实源 ----
   * standby：两个接入开关都未开启 → 待机，不产生任何数据流
   * connecting：真实接入已开但检测结果未返回 → 接入中（过渡态）
   * signalLost：真实接入开着且检测出信号弱/无 → 旧数据不可信，实时内容全部降级
   * monitoringLive：此刻确实有数据流在驱动判定（真实连上/演示在播） */
  const standby = computed(() => !offline.value && !realEnabled.value && !demoEnabled.value)
  /* 体检期间（autoChecking）即使轮询已报 disconnected 也算过渡态，
   * 避免界面闪过“已断开”再跳回“体检中”；接通后立刻退出过渡态。 */
  const connecting = computed(() => !offline.value && realEnabled.value
    && (deviceState.value === '' || (autoChecking.value && deviceState.value !== 'connected')))
  const signalLost = computed(() =>
    !offline.value && realEnabled.value && (deviceState.value === 'weak' || deviceState.value === 'disconnected'),
  )
  const monitoringLive = computed(() =>
    !offline.value && !connecting.value && !signalLost.value && !demoTransition.value
    && (realEnabled.value || demoEnabled.value),
  )

  /* ---- WebSocket ---- */
  let ws: WebSocket | null = null
  let reconnectTimer: ReturnType<typeof setTimeout> | null = null
  let reconnectDelay = 1000

  function connectWs() {
    if (ws?.readyState === WebSocket.OPEN) return
    const protocol = location.protocol === 'https:' ? 'wss' : 'ws'
    ws = new WebSocket(`${protocol}://${location.host}/ws`)

    ws.onopen = () => {
      wsConnected.value = true
      reconnectDelay = 1000
      // 心跳
      setInterval(() => ws?.send('ping'), 30000)
    }

    ws.onmessage = (e) => {
      try {
        const msg = JSON.parse(e.data)
        handleWsMessage(msg)
      } catch { /* ignore */ }
    }

    ws.onclose = () => {
      wsConnected.value = false
      scheduleReconnect()
    }

    ws.onerror = () => {
      wsConnected.value = false
    }
  }

  function scheduleReconnect() {
    if (reconnectTimer) return
    reconnectTimer = setTimeout(() => {
      reconnectTimer = null
      reconnectDelay = Math.min(reconnectDelay * 2, 30000)
      connectWs()
    }, reconnectDelay)
  }

  /** 第二轮报警响铃：每 5 秒重复提示音，直到家人点「已知晓」 */
  function startRingLoop() {
    ackRequired.value = true
    playAlertSound()
    if (ringTimer) return
    ringTimer = setInterval(playAlertSound, 5000)
  }
  function stopRingLoop() {
    ackRequired.value = false
    if (ringTimer) { clearInterval(ringTimer); ringTimer = null }
  }

  /** 清实时判定面残留：三态/语音确证/曲线/告警卡（不清事件库） */
  function clearLiveState() {
    semanticState.value = ''
    voiceConfirmState.value = ''
    voiceRound.value = 0
    alertAcked.value = false
    lastClearedReason.value = ''
    stopRingLoop()
    present.value = false
    guardZone.value = -1
    intensityHistory.value = []
    breathHistory.value = []
  }

  function handleWsMessage(msg: { kind: string; data: any }) {
    switch (msg.kind) {
      case 'sample':
        intensity.value = msg.data.intensity
        breathingRate.value = msg.data.breathing_rate
        breathingState.value = msg.data.breathing_state || 'normal'
        guardZone.value = msg.data.guard_zone
        present.value = msg.data.present
        zone.value = msg.data.zone
        if (typeof msg.data.semantic_state === 'string') semanticState.value = msg.data.semantic_state
        if (typeof msg.data.breathing_band_max === 'number') breathingBandMax.value = msg.data.breathing_band_max
        lastSampleTime.value = fmtTime(msg.data.ts || new Date().toISOString())
        intensityHistory.value.push(msg.data.intensity)
        if (intensityHistory.value.length > 60) intensityHistory.value.shift()
        breathHistory.value.push(msg.data.breathing_rate || 0)
        if (breathHistory.value.length > 60) breathHistory.value.shift()
        break
      case 'event':
        events.value.unshift(msg.data)
        if (events.value.length > 100) events.value.pop()
        break
      case 'alert':
        alerts.value.unshift(msg.data)
        if (alerts.value.length > 50) alerts.value.pop()
        if (msg.data.level === 'red' || msg.data.level === 'emergency') playAlertSound()
        // 第二轮确证报警（ack_required）→ 循环响铃直到家人知晓
        if (msg.data.ack_required && !alertAcked.value) startRingLoop()
        break
      case 'voice_confirm':
        voiceConfirmState.value = 'waiting'
        voiceRound.value = msg.data.round || 1
        if (typeof msg.data.timeout_s === 'number') voiceTimeoutS.value = msg.data.timeout_s
        break
      case 'voice_responded':
        voiceConfirmState.value = msg.data.state
        voiceRound.value = 0   // 有回应即确证链终止（解除/升级）
        break
      case 'alert_cleared':
        voiceConfirmState.value = ''
        voiceRound.value = 0
        guardZone.value = 0
        lastClearedReason.value = msg.data.reason || '告警已解除'
        clearedSeq.value += 1
        stopRingLoop()
        break
      case 'alert_escalated':
        guardZone.value = 3
        playAlertSound()
        break
      case 'alert_acked':
        alertAcked.value = true
        stopRingLoop()
        break
      case 'reset':
        events.value = []
        alerts.value = []
        clearLiveState()
        break
      case 'demo_state_cleared':
        // 后端切换演示场景时状态机归零，前端同步清残留
        clearLiveState()
        break
      case 'profile_updated':
        elderName.value = msg.data.elder_name || '妈妈'
        break
      case 'profile_adjustments':
        // 档案保存/疾病增删后，后端广播最新修正系数
        profileAdjustments.value = msg.data || {}
        if (typeof msg.data?.breathing_band_max === 'number') breathingBandMax.value = msg.data.breathing_band_max
        break
      case 'source_mode':
        // 后端广播数据源模式变更（含真实接入自动关演示）
        realEnabled.value = msg.data.real_enabled
        demoEnabled.value = msg.data.demo_enabled
        demoScenario.value = msg.data.demo_scenario || ''
        if (!msg.data.demo_enabled) clearDemoTransition()  // 演示被关（如真实接入打断），清过渡态
        break;
    }
  }

  /* ---- 数据源模式控制 ---- */
  async function loadSourceMode() {
    try {
      const m: SourceModeData = await api.getSourceMode()
      realEnabled.value = m.real_enabled
      demoEnabled.value = m.demo_enabled
      demoScenario.value = m.demo_scenario
      if (m.scenarios?.length) demoScenarios.value = m.scenarios
      offline.value = false
    } catch { /* 离线时保持默认 */ }
  }

  /** 真实接入开关（优先级最高，后端会自动关演示）。
   * 开阀即自动体检：后台跑全链路体检，结果落 autoCheckResult，
   * 界面据此区分“待机（没开阀）”与“故障（开了阀但断在某环）”。 */
  async function toggleReal(enabled: boolean) {
    const res = await api.setRealMode(enabled)
    realEnabled.value = res.real_enabled
    demoEnabled.value = res.demo_enabled
    demoScenario.value = res.demo_scenario
    if (enabled) {
      deviceState.value = ''  // 状态归零：先进入“体检中”，结果返回后再落定
      autoCheckResult.value = null
      pollDeviceStatus()  // 先拿一眼设备三态，不等体检跑完
      runAutoCheck()      // 后台体检：不阻塞开关响应，完成后刷新状态
    } else {
      autoCheckResult.value = null
    }
    return res.message
  }

  /** 后台跑开阀自动体检（最长约半分钟：含等桥接器/自检/样本到达），
   * 完成后重新拉设备状态，把结论交给守护页三态显示。失败静默，
   * 设备轮询仍会如实反映信号丢失。 */
  async function runAutoCheck() {
    autoChecking.value = true
    try {
      autoCheckResult.value = await api.autoCheck()
      await pollDeviceStatus()
    } catch { /* 后端不可达：offline 由轮询接管 */ }
    autoChecking.value = false
  }

  /** 演示开关 + 场景切换：每个场景都是完整剧本，切换即重新接入 */
  async function toggleDemo(enabled: boolean, scenario = '') {
    if (enabled) {
      startDemoTransition(demoEnabled.value)  // 已开着=切换（重接入），否则首次接入
      // 剧本归零：清掉上一场景的数据痕迹，不延续旧状态
      guardZone.value = -1
      intensity.value = 0
      breathingRate.value = 0
      breathingState.value = 'normal'
      intensityHistory.value = []
      breathHistory.value = []
      lastSampleTime.value = ''
    }
    try {
      const res = await api.setDemoMode(enabled, scenario)
      if (!res.ok) { clearDemoTransition(); return res.message }  // 被真实接入拦截等场景
      realEnabled.value = res.real_enabled
      demoEnabled.value = res.demo_enabled
      demoScenario.value = res.demo_scenario
      if (!enabled) clearDemoTransition()
      return res.message
    } catch (e) {
      clearDemoTransition()
      throw e
    }
  }

  /* ---- 设备连接状态轮询 ---- */
  async function pollDeviceStatus() {
    try {
      const s = await api.getDeviceStatus()
      deviceState.value = s.state
      deviceStatus.value = s
      offline.value = false  // API 可达即非离线
    } catch {
      offline.value = true
    }
  }

  function startDevicePoll() {
    if (devicePollTimer) return
    pollDeviceStatus()
    devicePollTimer = setInterval(pollDeviceStatus, 8000)
  }

  async function loadDiagnosis() {
    try {
      diagnosis.value = await api.getDeviceDiagnosis()
    } catch {
      offline.value = true
    }
  }

  /* ---- 数据加载 ---- */
  async function loadEvents() {
    try {
      events.value = await api.getEvents(50)
      offline.value = false
    } catch {
      offline.value = true
    }
  }

  async function loadStatus() {
    try {
      const s = await api.getStatus()
      present.value = s.present
      zone.value = s.zone
      guardZone.value = s.guard_zone
      if (typeof (s as any).semantic_state === 'string') semanticState.value = (s as any).semantic_state
      if (typeof (s as any).breathing_band_max === 'number') breathingBandMax.value = (s as any).breathing_band_max
      if ((s as any).adjustments) profileAdjustments.value = (s as any).adjustments
      offline.value = false
    } catch {
      offline.value = true
    }
  }

  async function loadProfile() {
    try {
      profile.value = await api.getProfile()
      elderName.value = profile.value.name
      offline.value = false
    } catch {
      offline.value = true
    }
  }

  async function saveProfile(p: ProfileData) {
    const res = await api.saveProfile(p)
    profile.value = p
    elderName.value = p.name
    localStorage.setItem('wg_elder_name', p.name)
    return res
  }

  async function confirmAlert() {
    try {
      await api.familyConfirm()
    } catch { /* ignore */ }
  }

  /** 家人已知晓：第二轮报警响铃的停止条件 */
  async function ackAlert() {
    try {
      await api.alertAck()
      alertAcked.value = true
      stopRingLoop()
    } catch { /* ignore */ }
  }

  /** 手动刷新：去缓存强拉最新数据，并感知数据是否卡住，返回结果提示 */
  async function refreshAll(): Promise<string> {
    refreshing.value = true
    try {
      await Promise.all([loadEvents(), loadStatus(), pollDeviceStatus(), loadSourceMode()])
    } catch { /* 单项失败不阻断，offline 由 pollDeviceStatus 兜底判定 */ } finally {
      refreshing.value = false
    }
    if (offline.value) return '守护服务不可达，请检查网络后重试'
    // 卡住感知：真实/演示数据源开着，但超过 10 秒没收到新样本 → 疑似卡住
    const lag = deviceStatus.value?.last_sample_lag_s
    if ((realEnabled.value || demoEnabled.value) && typeof lag === 'number' && lag > 10) {
      return `数据疑似卡住：已 ${Math.round(lag)} 秒未收到新样本，请核查信号与网络`
    }
    return '已刷新 · 数据为最新'
  }

  /* ---- 初始化 ---- */
  async function init() {
    await Promise.all([loadEvents(), loadStatus(), loadProfile(), pollDeviceStatus(), loadSourceMode()])
    startDevicePoll()
    // 两个接入开关都未开启时保持待机：不产生任何虚拟数据流
  }

  return {
    wsConnected, elderName, present, zone, guardZone,
    intensity, breathingRate, breathingState, intensityHistory, breathHistory,
    semanticState, breathingBandMax, profileAdjustments,
    events, alerts, profile, voiceConfirmState, refreshing,
    voiceRound, voiceTimeoutS, ackRequired, alertAcked, lastClearedReason, clearedSeq,
    offline, lastSampleTime, deviceState, deviceStatus, diagnosis,
    autoChecking, autoCheckResult,
    realEnabled, demoEnabled, demoScenario, demoScenarios, demoTransition, demoTransLost,
    zoneInfo, breathingInfo, alertCount, latestAlert,
    connectionState, connectionInfo, standby, connecting, signalLost, monitoringLive,
    connectWs, loadEvents, loadStatus, loadProfile, saveProfile,
    confirmAlert, ackAlert, init, refreshAll, pollDeviceStatus, loadDiagnosis,
    loadSourceMode, toggleReal, toggleDemo, runAutoCheck,
  }
})
