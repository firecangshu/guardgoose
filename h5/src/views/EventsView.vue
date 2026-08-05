<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { showToast } from 'vant'
import { useGuardianStore, ZONE_MAP, EVENT_MAP, fmtTime } from '../stores/guardian'
import { api, type SystemLogData } from '../services/api'

const store = useGuardianStore()

/** 两个日志区各自独立：常驻只显示最新前三行，其余折叠，点开看全部 */
const eventsExpanded = ref(false)
const sysExpanded = ref(false)
const sysLogs = ref<SystemLogData[]>([])

const displayEvents = computed(() =>
  eventsExpanded.value ? store.events : store.events.slice(0, 3))
const displaySysLogs = computed(() =>
  sysExpanded.value ? sysLogs.value : sysLogs.value.slice(0, 3))

onMounted(async () => {
  store.loadEvents()
  await loadSysLogs()
})

async function loadSysLogs() {
  try {
    sysLogs.value = await api.getSystemLogs()
  } catch { /* 边缘服务离线时留空 */ }
}

async function onRefresh() {
  await Promise.all([store.refreshAll(), loadSysLogs()])
}

function eventLabel(type: string): string {
  return EVENT_MAP[type] || type
}

function zoneTag(zone: number) {
  return ZONE_MAP[zone] || ZONE_MAP[-1]
}

const SYS_LEVEL_LABEL: Record<string, string> = { info: '运行', warn: '提醒', danger: '告警' }

/* ---- 导出：浏览器本地生成文件下载，不经过云端 ---- */
function downloadText(filename: string, content: string) {
  const blob = new Blob(['\ufeff' + content], { type: 'text/plain;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  a.click()
  URL.revokeObjectURL(url)
}

function dayStamp() {
  const d = new Date()
  return `${d.getFullYear()}${String(d.getMonth() + 1).padStart(2, '0')}${String(d.getDate()).padStart(2, '0')}`
}

function exportEvents() {
  if (!store.events.length) return showToast('暂无信号检测日志')
  const lines = ['时间,区域,事件,呼吸(次/分),活动强度,详情']
  for (const ev of store.events) {
    lines.push([
      ev.ts, zoneTag(ev.guard_zone).label, eventLabel(ev.type),
      ev.breathing_rate || '', ev.intensity ? ev.intensity.toFixed(2) : '',
      (ev.detail || '').replace(/,/g, '，'),
    ].join(','))
  }
  downloadText(`信号检测日志_${dayStamp()}.csv`, lines.join('\n'))
  showToast('已导出信号检测日志')
}

function exportSysLogs() {
  if (!sysLogs.value.length) return showToast('暂无工作台日志')
  const lines = sysLogs.value.map(l => `${l.ts} [${SYS_LEVEL_LABEL[l.level] || l.level}] ${l.text}`)
  downloadText(`程序工作台日志_${dayStamp()}.txt`, lines.join('\n'))
  showToast('已导出工作台日志')
}
</script>

<template>
  <div class="events-page">
    <header class="page-header">
      事件库
      <van-icon
        name="replay" size="18" color="#1989fa"
        :class="{ spinning: store.refreshing }"
        @click="onRefresh()"
      />
    </header>

    <!-- 一、信号检测日志：状态机判定产生的事件，常驻前三行 -->
    <van-cell-group inset class="log-block">
      <div class="log-head">
        <span class="log-title">信号检测日志</span>
        <span class="log-count">{{ store.events.length }} 条</span>
        <span class="log-export" @click="exportEvents">导出</span>
      </div>
      <template v-if="store.events.length">
        <van-cell v-for="ev in displayEvents" :key="ev.event_id" class="log-row">
          <template #title>
            <div class="event-title">
              <van-tag :color="zoneTag(ev.guard_zone).color" text-color="#fff">
                {{ zoneTag(ev.guard_zone).label }}
              </van-tag>
              <span class="event-name">{{ eventLabel(ev.type) }}</span>
              <span class="event-time">{{ fmtTime(ev.ts) }}</span>
            </div>
          </template>
          <template #label v-if="ev.breathing_rate || ev.intensity || ev.detail">
            <div class="event-meta">
              <span v-if="ev.breathing_rate">呼吸 {{ ev.breathing_rate }}次/分</span>
              <span v-if="ev.intensity">强度 {{ ev.intensity.toFixed(2) }}</span>
              <span v-if="ev.detail" class="event-detail-inline">{{ ev.detail }}</span>
            </div>
          </template>
        </van-cell>
        <div
          v-if="store.events.length > 3" class="log-toggle"
          @click="eventsExpanded = !eventsExpanded"
        >
          {{ eventsExpanded ? '收起' : '展开全部 ' + store.events.length + ' 条' }}
          <van-icon :name="eventsExpanded ? 'arrow-up' : 'arrow-down'" size="12" />
        </div>
      </template>
      <van-empty v-else description="暂无信号检测日志" :image-size="50" />
    </van-cell-group>

    <!-- 二、程序工作台日志：边缘服务运行记录，常驻前三行 -->
    <van-cell-group inset class="log-block">
      <div class="log-head">
        <span class="log-title">程序工作台日志</span>
        <span class="log-count">{{ sysLogs.length }} 条</span>
        <span class="log-export" @click="exportSysLogs">导出</span>
      </div>
      <template v-if="sysLogs.length">
        <van-cell v-for="(log, i) in displaySysLogs" :key="log.ts + i" class="log-row">
          <template #title>
            <div class="event-title">
              <van-tag
                :color="log.level === 'danger' ? '#ee0a24' : log.level === 'warn' ? '#ff9f00' : '#1989fa'"
                text-color="#fff"
              >
                {{ SYS_LEVEL_LABEL[log.level] || log.level }}
              </van-tag>
              <span class="event-name">{{ log.text }}</span>
              <span class="event-time">{{ fmtTime(log.ts) }}</span>
            </div>
          </template>
        </van-cell>
        <div
          v-if="sysLogs.length > 3" class="log-toggle"
          @click="sysExpanded = !sysExpanded"
        >
          {{ sysExpanded ? '收起' : '展开全部 ' + sysLogs.length + ' 条' }}
          <van-icon :name="sysExpanded ? 'arrow-up' : 'arrow-down'" size="12" />
        </div>
      </template>
      <van-empty v-else description="暂无工作台日志" :image-size="50" />
    </van-cell-group>
  </div>
</template>

<style scoped>
.page-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 14px 16px 10px;
  font-size: 17px;
  font-weight: 700;
  color: #1f2329;
  background: #fff;
}
.events-page {
  padding: 8px 0;
}

.log-block {
  margin-top: 10px;
  overflow: hidden;
}

/* 日志区标题行：标题 + 条数 + 导出 */
.log-head {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 14px;
  background: #fafbfc;
  border-bottom: 1px solid #f0f1f3;
}

.log-title {
  font-size: 14px;
  font-weight: 700;
  color: #1f2329;
}

.log-count {
  font-size: 11px;
  color: #969799;
}

.log-export {
  margin-left: auto;
  font-size: 12px;
  color: #1989fa;
}

/* 紧凑行高：每行更矮，一屏看更多 */
.log-block :deep(.van-cell.log-row) {
  padding: 8px 12px;
}

.event-title {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
}

.event-name {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.event-time {
  flex-shrink: 0;
  font-size: 11px;
  color: #969799;
  font-variant-numeric: tabular-nums;
}

.event-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  font-size: 11px;
  color: #969799;
  margin-top: 2px;
}

.event-detail-inline {
  color: #666;
}

/* 展开/收起按钮 */
.log-toggle {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 4px;
  padding: 8px;
  font-size: 12px;
  color: #1989fa;
  border-top: 1px dashed #ebedf0;
}

.spinning {
  animation: spin 1s linear infinite;
}

@keyframes spin {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}
</style>
