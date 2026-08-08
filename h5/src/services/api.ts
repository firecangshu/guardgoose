/** API 服务层：封装与护院鹅(Guard Goose)后端的所有 HTTP 通信。 */

const BASE = '/api'

async function get<T>(path: string): Promise<T> {
  // 去缓存：时间戳参数 + no-store，保证每次刷新拿到的都是最新数据
  const sep = path.includes('?') ? '&' : '?'
  const res = await fetch(`${BASE}${path}${sep}_t=${Date.now()}`, { cache: 'no-store' })
  if (!res.ok) throw new Error(`GET ${path} → ${res.status}`)
  return res.json()
}

async function post<T>(path: string, body?: unknown): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: body ? JSON.stringify(body) : undefined,
  })
  if (!res.ok) throw new Error(`POST ${path} → ${res.status}`)
  return res.json()
}

async function del<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`, { method: 'DELETE' })
  if (!res.ok) throw new Error(`DELETE ${path} → ${res.status}`)
  return res.json()
}

/* ---- 类型定义 ---- */

export interface EventItem {
  event_id: string
  ts: string
  type: string
  zone: string
  intensity: number
  duration_s: number
  breathing_rate: number
  breathing_state: string
  guard_zone: number
  source: string
  detail: string
  created_at: string
}

export interface AlertItem {
  id: number
  event_id: string
  ts: string
  level: string
  level_label: string
  title: string
  message: string
  breathing_rate: number
  breathing_state: string
  guard_zone: number
  agent_source: string
  suspected_cause: string
  alert_tag: string
  elder_name: string
  created_at: string
}

export interface ProfileData {
  name: string
  age: number
  weight_kg: number
  relationship: string
  health_status: string
  diseases: string[]
  medications: string[]
  fall_count: number
  syncope_count: number
  family_sudden_cardiac_death: boolean
  wake_time: string
  sleep_time: string
  address?: string
  emergency_phones: string[]
  is_multi_medication?: boolean
  is_high_risk?: boolean
  voice_timeout?: number
}

export interface DiseaseInfo {
  code: string
  name: string
  category: string
  description: string
  fall_risk_note: string
  breathing_impact: string
  advice: string[]
  voice_timeout_override: number
  skip_voice: boolean
  zone3_max_stay: number
  source: string
}

/** AI 医学词条查询结果（供子女端确认） */
export interface DiseaseLookupResult {
  code: string
  name: string
  category: string
  description: string
  fall_risk_note: string
  breathing_impact: string
  advice: string[]
  ai_generated: boolean
}

export interface StatusData {
  present: boolean
  zone: string
  guard_zone: number
}

export interface GuardModeData {
  enabled: boolean
  intrusion_fired: boolean
  silence_s: number
  can_toggle: boolean
}

export interface DeviceStatusData {
  state: 'connected' | 'weak' | 'disconnected'
  last_sample_lag_s: number
  last_sample_ts: string
  device_id: string
  edge_online: boolean
}

export interface DeviceDiagnosisData {
  state: 'connected' | 'weak' | 'disconnected'
  last_sample_lag_s: number
  sample_interval_expected_s: number
  device_id: string
  edge_online: boolean
  tips: string[]
}

/** 信号接通测试：板子 → 桥接器 → 后端 → 判定 全链路逐环体检 */
export interface ConnectionTestItem {
  name: string
  ok: boolean
  detail: string
}

export interface ConnectionTestData {
  ok: boolean
  verdict: string
  items: ConnectionTestItem[]
  tested_at: string
}

/* 数据源模式：真实接入 > 演示场景 > 无数据 */
export interface DemoScenarioInfo {
  scenario: string
  label: string
  desc: string
}

export interface SourceModeData {
  real_enabled: boolean
  demo_enabled: boolean
  demo_scenario: string
  scenarios?: DemoScenarioInfo[]
}

export interface SourceModeResp extends SourceModeData {
  ok: boolean
  message: string
}

/** 程序工作台日志（边缘服务运行记录） */
export interface SystemLogData {
  ts: string
  level: 'info' | 'warn' | 'danger' | string
  text: string
}

/* ---- API 接口 ---- */

export const api = {
  getEvents: (limit = 50) => get<EventItem[]>(`/events?limit=${limit}`),
  getSystemLogs: () => get<SystemLogData[]>('/system-logs'),
  getStatus: () => get<StatusData>('/status'),
  getProfile: () => get<ProfileData>('/profile'),
  saveProfile: (p: ProfileData) => post<{ ok: boolean; profile: any }>('/profile', p),
  clearProfile: () => post<{ ok: boolean }>('/profile/clear'),
  familyConfirm: () => post<{ ok: boolean }>('/family/confirm'),
  alertAck: () => post<{ ok: boolean }>('/alert-ack'),
  getGuardMode: () => get<GuardModeData>('/guard-mode'),
  toggleGuardMode: () => post<{ ok: boolean; enabled: boolean }>('/guard-mode/toggle'),
  getVoiceConfirm: () => get<any>('/voice-confirm'),
  voiceRespond: (answer: string) => post<any>(`/voice-confirm/respond?answer=${answer}`),
  listDiseases: () => get<{ preset: DiseaseInfo[]; custom: DiseaseInfo[] }>('/diseases'),
  addDisease: (d: Partial<DiseaseInfo>) => post<{ ok: boolean }>('/diseases', d),
  removeDisease: (code: string) => del<{ ok: boolean }>(`/diseases/${code}`),
  diseaseLookup: (disease_name: string) => post<DiseaseLookupResult>('/diseases/ai-lookup', { disease_name }),
  reset: () => post<{ ok: boolean }>('/reset'),
  getDeviceStatus: () => get<DeviceStatusData>('/device/status'),
  getDeviceDiagnosis: () => get<DeviceDiagnosisData>('/device/diagnosis'),
  connectionTest: () => get<ConnectionTestData>('/device/connection-test'),
  getSourceMode: () => get<SourceModeData>('/source-mode'),
  setDemoMode: (enabled: boolean, scenario: string) =>
    post<SourceModeResp>('/source-mode/demo', { enabled, scenario }),
  setRealMode: (enabled: boolean) =>
    post<SourceModeResp>('/source-mode/real', { enabled }),
}
