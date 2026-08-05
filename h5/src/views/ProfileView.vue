<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { showToast, showDialog, showSuccessToast, showFailToast } from 'vant'
import { useGuardianStore, DISEASE_MAP, MEDICATION_MAP, RELATIONSHIP_MAP, HEALTH_STATUS_MAP } from '../stores/guardian'
import { api, type ProfileData, type DiseaseLookupResult, type DiseaseInfo } from '../services/api'

const store = useGuardianStore()

/** 折叠区展开状态：默认全部收起——档案都是已填写内容，直接展示摘要，点开才修改 */
const activeSections = ref<string[]>([])

/** 标题行摘要：不展开也能看到已填写的内容 */
const basicSummary = computed(() => {
  const rel = RELATIONSHIP_MAP[form.value.relationship]
  return '基本信息 · ' + form.value.name
    + (rel ? ' · ' + rel : '')
    + ' · ' + form.value.age + ' 岁'
})
const healthSummary = computed(() =>
  '身体状态 · ' + (HEALTH_STATUS_MAP[form.value.health_status]?.label || '未选择'))

const form = ref<ProfileData>({
  name: '妈妈',
  age: 75,
  weight_kg: 0,
  relationship: '',
  health_status: '',
  diseases: [],
  medications: [],
  fall_count: 0,
  syncope_count: 0,
  family_sudden_cardiac_death: false,
  wake_time: '06:30',
  sleep_time: '21:30',
  address: '',
  emergency_phones: ['', '', ''],
})

const diseaseOptions = Object.entries(DISEASE_MAP).map(([value, label]) => ({ value, label }))
const medicationOptions = Object.entries(MEDICATION_MAP).map(([value, label]) => ({ value, label }))
const relationshipOptions = Object.entries(RELATIONSHIP_MAP).map(([value, label]) => ({ value, label }))
const healthOptions = Object.entries(HEALTH_STATUS_MAP).map(([value, v]) => ({ value, label: v.label, desc: v.desc }))

/** 已选内容的标签名：预设查表 + 自定义病史查注册表，直观看得到 */
function diseaseLabel(code: string): string {
  return DISEASE_MAP[code]
    || customDiseases.value.find(d => d.code === code)?.name
    || code
}
function medicationLabel(code: string): string {
  return MEDICATION_MAP[code] || code
}

/* ---- 已确认的自定义疾病（个性化医疗档案备份）---- */
const customDiseases = ref<DiseaseInfo[]>([])

async function loadCustomDiseases() {
  try {
    const res = await api.listDiseases()
    customDiseases.value = res.custom || []
  } catch { /* 后端离线时忽略 */ }
}

/* ---- 开放性病史：AI 医学词条查询 ---- */
const customDiseaseInput = ref('')
const lookupLoading = ref(false)
const lookupPopup = ref(false)
const lookupResult = ref<DiseaseLookupResult | null>(null)
const confirmLoading = ref(false)
const edited = ref(false)
const adviceText = ref('')

async function lookupDisease() {
  const name = customDiseaseInput.value.trim()
  if (!name) return showToast('请输入疾病名称')
  lookupLoading.value = true
  try {
    lookupResult.value = { ...(await api.diseaseLookup(name)) }
    adviceText.value = lookupResult.value.advice.join('\n')
    edited.value = false
    lookupPopup.value = true
  } catch {
    showFailToast('AI 查询失败，请重试')
  } finally {
    lookupLoading.value = false
  }
}

async function confirmCustomDisease() {
  const r = lookupResult.value
  if (!r) return
  r.advice = adviceText.value.split('\n').map(s => s.trim()).filter(Boolean)
  if (!r.name.trim()) return showToast('疾病名称不能为空')
  confirmLoading.value = true
  try {
    await api.addDisease({
      code: r.code, name: r.name.trim(), category: r.category, description: r.description,
      fall_risk_note: r.fall_risk_note, breathing_impact: r.breathing_impact, advice: r.advice,
    })
    if (!form.value.diseases.includes(r.code)) form.value.diseases.push(r.code)
    lookupResult.value = null
    lookupPopup.value = false
    customDiseaseInput.value = ''
    showSuccessToast(edited.value ? '已按您的修改纳入档案' : '已确认并加入档案')
    await loadCustomDiseases()
  } catch {
    showFailToast('保存失败，请重试')
  } finally {
    confirmLoading.value = false
  }
}

async function removeCustomDisease(d: DiseaseInfo) {
  const ok = await showDialog({
    title: '移除疾病',
    message: `确认从档案中移除「${d.name}」吗？`,
    showCancelButton: true,
  })
  if (!ok) return
  try {
    await api.removeDisease(d.code)
    form.value.diseases = form.value.diseases.filter(c => c !== d.code)
    await loadCustomDiseases()
    showToast('已移除')
  } catch {
    showFailToast('移除失败')
  }
}

onMounted(async () => {
  try {
    const p = await api.getProfile()
    form.value = { ...form.value, ...p }
    // 紧急电话固定三个槽位，不足补空，超出截断
    form.value.emergency_phones = [...(p.emergency_phones || []), '', '', ''].slice(0, 3)
  } catch { /* 首次使用 */ }
  await loadCustomDiseases()
})

async function handleSave() {
  try {
    await store.saveProfile(form.value)
    showToast({ type: 'success', message: '档案已保存' })
  } catch {
    showToast({ type: 'fail', message: '保存失败' })
  }
}

async function handleReset() {
  const ok = await showDialog({
    title: '确认重置',
    message: '将清空所有事件和告警记录，确认吗？',
    showCancelButton: true,
  })
  if (ok) {
    await api.reset()
    showToast({ type: 'success', message: '已重置' })
  }
}
</script>

<template>
  <div class="profile-page">
    <header class="page-header">档案本</header>
    <!-- 风险等级：系统判定结果，常驻醒目，不折叠 -->
    <van-cell-group inset v-if="store.profile?.is_high_risk">
      <van-cell title="风险等级" center>
        <template #value>
          <van-tag type="danger" size="large">高风险</van-tag>
        </template>
        <template #label>
          <template v-if="store.profile?.voice_timeout">语音确认超时 {{ store.profile.voice_timeout }} 秒</template>
        </template>
      </van-cell>
    </van-cell-group>

    <!-- 档案内容：全部默认折叠，标题行直接展示已填写的内容摘要，点开才修改 -->
    <van-collapse v-model="activeSections" class="profile-collapse">
      <van-collapse-item name="basic" :title="basicSummary">
      <van-field v-model="form.name" label="姓名" placeholder="老人姓名" />
      <van-cell title="与您的关系" />
      <van-radio-group v-model="form.relationship" class="radio-grid">
        <van-radio
          v-for="opt in relationshipOptions" :key="opt.value"
          :name="opt.value" icon-size="16px"
        >
          {{ opt.label }}
        </van-radio>
      </van-radio-group>
      <van-field label="年龄">
        <template #input>
          <van-stepper v-model="form.age" min="50" max="120" />
        </template>
      </van-field>
      <van-cell title="体重（kg）" center label="用于跌倒冲击与久滞风险评估">
        <template #right-icon>
          <van-stepper v-model="form.weight_kg" min="0" max="150" step="0.5" :decimal-length="1" />
        </template>
      </van-cell>
      <van-field v-model="form.wake_time" label="起床时间" placeholder="06:30" />
      <van-field v-model="form.sleep_time" label="入睡时间" placeholder="21:30" />
      <van-field v-model="form.address" label="居住地址" placeholder="告警时同步给急救中心" />
      <van-field v-model="form.emergency_phones[0]" label="紧急电话 1" type="tel" placeholder="首选联系人（建议必填）" />
      <van-field v-model="form.emergency_phones[1]" label="紧急电话 2" type="tel" placeholder="备用：电话1打不通自动切到它" />
      <van-field v-model="form.emergency_phones[2]" label="紧急电话 3" type="tel" placeholder="备用：电话2打不通自动切到它" />
      </van-collapse-item>

      <van-collapse-item name="health" :title="healthSummary">
      <van-radio-group v-model="form.health_status">
        <van-cell
          v-for="opt in healthOptions" :key="opt.value"
          :title="opt.label" :label="opt.desc" clickable
          @click="form.health_status = opt.value"
        >
          <template #right-icon>
            <van-radio :name="opt.value" />
          </template>
        </van-cell>
      </van-radio-group>
      </van-collapse-item>

      <van-collapse-item name="disease" :title="'病史（可多选）' + (form.diseases.length ? ' · 已选 ' + form.diseases.length + ' 项' : '')">
      <van-checkbox-group v-model="form.diseases">
        <van-cell
          v-for="opt in diseaseOptions"
          :key="opt.value"
          :title="opt.label"
          clickable
          @click="() => {
            const idx = form.diseases.indexOf(opt.value)
            idx >= 0 ? form.diseases.splice(idx, 1) : form.diseases.push(opt.value)
          }"
        >
          <template #right-icon>
            <van-checkbox :name="opt.value" />
          </template>
        </van-cell>
      </van-checkbox-group>
      <van-cell title="其他疾病（AI 查询医学词条）" />
      <div class="custom-disease-row">
        <van-field
          v-model="customDiseaseInput"
          placeholder="如：帕金森病"
          clearable
          @keyup.enter="lookupDisease"
        />
        <van-button
          size="small" type="primary" plain
          :loading="lookupLoading" loading-text="AI查询中"
          @click="lookupDisease"
        >
          AI查询
        </van-button>
      </div>
      </van-collapse-item>
    </van-collapse>

    <!-- 已确认的其他病史：个性化医疗档案核心成果，常驻展示 -->
    <van-cell-group inset v-if="customDiseases.length" title="已确认的其他病史">
      <van-swipe-cell v-for="d in customDiseases" :key="d.code">
        <van-cell :title="d.name" :label="d.category ? d.category + ' · ' + d.fall_risk_note : d.fall_risk_note" />
        <template #right>
          <van-button square type="danger" text="移除" style="height:100%" @click="removeCustomDisease(d)" />
        </template>
      </van-swipe-cell>
    </van-cell-group>

    <!-- AI 医学词条分析结果弹窗（可编辑，修改或确认后才纳入个性化守护档案） -->
    <van-popup v-model:show="lookupPopup" round position="bottom" :style="{ maxHeight: '85%' }">
      <div class="lookup-panel" v-if="lookupResult">
        <div class="lookup-title">
          AI 医学词条分析
          <van-tag :type="lookupResult.ai_generated ? 'primary' : 'warning'" style="margin-left:8px">
            {{ lookupResult.ai_generated ? 'AI 生成' : '通用模板（AI 暂不可用）' }}
          </van-tag>
        </div>
        <p class="lookup-hint">以下内容由 AI 查询生成，请逐项核对。可直接修改，修改完成或确认无误后，方可纳入个性化守护档案。</p>
        <van-cell-group inset>
          <van-field v-model="lookupResult.name" label="疾病名称" @update:model-value="edited = true" />
          <van-field v-model="lookupResult.category" label="疾病类别" @update:model-value="edited = true" />
          <van-field v-model="lookupResult.description" label="疾病描述" type="textarea" rows="2" autosize @update:model-value="edited = true" />
          <van-field v-model="lookupResult.fall_risk_note" label="对跌倒的影响" type="textarea" rows="2" autosize @update:model-value="edited = true" />
          <van-field
            v-model="lookupResult.breathing_impact" label="对呼吸监测的影响"
            type="textarea" rows="2" autosize class="breathing-field" @update:model-value="edited = true"
          />
          <van-field v-model="adviceText" label="告警时建议" type="textarea" rows="3" autosize placeholder="每行一条建议" @update:model-value="edited = true" />
        </van-cell-group>
        <div class="lookup-actions">
          <van-button plain block round @click="lookupPopup = false">再看看</van-button>
          <van-button
            type="primary" block round
            :loading="confirmLoading" loading-text="保存中"
            @click="confirmCustomDisease"
          >
            {{ edited ? '修改完成，纳入档案' : '确认无误，纳入档案' }}
          </van-button>
        </div>
      </div>
    </van-popup>

    <van-collapse v-model="activeSections" class="profile-collapse">
      <van-collapse-item name="medication" :title="'用药（可多选）' + (form.medications.length ? ' · 已选 ' + form.medications.length + ' 项' : '')">
      <van-checkbox-group v-model="form.medications">
        <van-cell
          v-for="opt in medicationOptions"
          :key="opt.value"
          :title="opt.label"
          clickable
          @click="() => {
            const idx = form.medications.indexOf(opt.value)
            idx >= 0 ? form.medications.splice(idx, 1) : form.medications.push(opt.value)
          }"
        >
          <template #right-icon>
            <van-checkbox :name="opt.value" />
          </template>
        </van-cell>
      </van-checkbox-group>

      </van-collapse-item>

      <van-collapse-item
        name="history"
        :title="'历史记录' + ((form.fall_count || form.syncope_count || form.family_sudden_cardiac_death) ? ' · 有记录' : '')"
      >
      <van-field label="跌倒次数">
        <template #input>
          <van-stepper v-model="form.fall_count" min="0" max="20" />
        </template>
      </van-field>
      <van-field label="晕厥次数">
        <template #input>
          <van-stepper v-model="form.syncope_count" min="0" max="20" />
        </template>
      </van-field>
        <van-cell title="家族猝死史">
          <template #right-icon>
            <van-switch v-model="form.family_sudden_cardiac_death" size="20" />
          </template>
        </van-cell>
      </van-collapse-item>
    </van-collapse>

    <!-- 已选病史/用药标签：选中内容常驻展示，不随折叠隐藏，直观看得到 -->
    <van-cell-group
      inset class="tag-summary"
      v-if="form.diseases.length || form.medications.length"
    >
      <div v-if="form.diseases.length" class="tag-row">
        <span class="tag-row-label">病史</span>
        <van-tag
          v-for="c in form.diseases" :key="c"
          type="primary" class="tag-chip"
        >
          {{ diseaseLabel(c) }}
        </van-tag>
      </div>
      <div v-if="form.medications.length" class="tag-row">
        <span class="tag-row-label">用药</span>
        <van-tag
          v-for="c in form.medications" :key="c"
          type="warning" class="tag-chip"
        >
          {{ medicationLabel(c) }}
        </van-tag>
      </div>
    </van-cell-group>

    <!-- 操作按钮 -->
    <div class="action-area">
      <van-button type="primary" size="large" round block @click="handleSave">
        保存档案
      </van-button>
      <van-button
        type="danger" plain size="large" round block
        style="margin-top:12px"
        @click="handleReset"
      >
        重置系统数据
      </van-button>
    </div>
  </div>
</template>

<style scoped>
.page-header {
  padding: 14px 16px 10px;
  font-size: 17px;
  font-weight: 700;
  color: #1f2329;
  background: #fff;
}
.profile-page {
  padding: 8px 0;
}

.radio-grid {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  padding: 12px 16px;
}

.custom-disease-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 0 12px 12px;
}

.custom-disease-row .van-field {
  flex: 1;
  padding-left: 4px;
}

/* 折叠区：与其他卡片同风格，标题行加粗便扫视 */
.profile-collapse {
  margin: 12px 12px 0;
  border-radius: 12px;
  overflow: hidden;
}

.profile-collapse :deep(.van-collapse-item__title) {
  padding: 13px 16px;
  font-weight: 600;
  color: #1f2329;
}

.profile-collapse :deep(.van-collapse-item__content) {
  padding-bottom: 8px;
}

.lookup-panel {
  padding: 16px 0 calc(16px + env(safe-area-inset-bottom));
}

.lookup-title {
  font-size: 16px;
  font-weight: 600;
  text-align: center;
  padding-bottom: 12px;
}

.lookup-actions {
  display: flex;
  gap: 12px;
  padding: 16px 16px 0;
}

.lookup-hint {
  margin: 0 16px 8px;
  font-size: 12px;
  color: #969799;
  line-height: 1.6;
}

.breathing-field {
  border-left: 3px solid #07c160;
}

/* 已选病史/用药标签卡：常驻展示选中内容 */
.tag-summary {
  margin-top: 12px;
  padding: 10px 14px 12px;
}

.tag-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
  padding: 4px 0;
}

.tag-row-label {
  flex-shrink: 0;
  font-size: 13px;
  font-weight: 600;
  color: #1f2329;
  margin-right: 2px;
}

.tag-chip {
  font-size: 12px;
}

.action-area {
  padding: 16px;
}
</style>
