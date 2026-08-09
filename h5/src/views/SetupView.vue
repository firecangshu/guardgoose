<script setup lang="ts">
import { ref, reactive } from 'vue'
import { useRouter } from 'vue-router'
import { showToast, showSuccessToast, showFailToast } from 'vant'
import { useGuardianStore, DISEASE_MAP, RELATIONSHIP_MAP, HEALTH_STATUS_MAP } from '../stores/guardian'
import { api, type DiseaseLookupResult } from '../services/api'

const router = useRouter()
const store = useGuardianStore()

const step = ref(0)
const TOTAL_STEPS = 6
const saving = ref(false)

const form = reactive({
  name: localStorage.getItem('wg_elder_name') || '',
  relationship: '',
  age: 75,
  weight_kg: 0,
  health_status: '',
  wake_time: '06:30',
  sleep_time: '21:30',
  diseases: [] as string[],
  fall_count: 0,
  syncope_count: 0,
  family_sudden_cardiac_death: false,
  address: '',
  elder_phone: '',
  emergency_phones: ['', '', ''] as string[],
})

const diseaseOptions = Object.entries(DISEASE_MAP).map(([value, text]) => ({ text, value }))
const relationshipOptions = Object.entries(RELATIONSHIP_MAP).map(([value, text]) => ({ text, value }))
const healthOptions = Object.entries(HEALTH_STATUS_MAP).map(([value, v]) => ({ value, label: v.label, desc: v.desc }))

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

/** 子女核对：可直接修改 AI 分析结果，修改完成或确认无误后才写入个性化医疗档案 */
async function confirmCustomDisease() {
  const r = lookupResult.value
  if (!r) return
  r.advice = adviceText.value.split('\n').map(s => s.trim()).filter(Boolean)
  if (!r.name.trim()) return showToast('疾病名称不能为空')
  confirmLoading.value = true
  try {
    await api.addDisease({
      code: r.code,
      name: r.name.trim(),
      category: r.category,
      description: r.description,
      fall_risk_note: r.fall_risk_note,
      breathing_impact: r.breathing_impact,
      advice: r.advice,
    })
    if (!form.diseases.includes(r.code)) form.diseases.push(r.code)
    lookupResult.value = null
    lookupPopup.value = false
    customDiseaseInput.value = ''
    showSuccessToast(edited.value ? '已按您的修改纳入档案' : '已确认并加入档案')
  } catch {
    showFailToast('保存失败，请重试')
  } finally {
    confirmLoading.value = false
  }
}

/** 返回上一页：第一步回登录页，其余回上一步 */
function prev() {
  if (step.value === 0) {
    router.replace('/login')
    return
  }
  step.value -= 1
}

function next() {
  if (step.value === 0) {
    if (!form.name.trim()) return showToast('请输入守护人姓名')
    if (!form.relationship) return showToast('请选择您与守护人的关系')
  }
  if (step.value === 1 && !form.health_status) {
    return showToast('请选择守护人的身体状态')
  }
  step.value = Math.min(step.value + 1, TOTAL_STEPS - 1)
}

async function finish() {
  /* 紧急联系全部必填（冗余原则）：住址同步120，守护人电话致电确认意识，三个顺位电话逐级拨打 */
  if (!form.address.trim()) return showToast('请填写家庭住址（拨120时同步给急救中心）')
  if (!form.elder_phone.trim()) return showToast('请填写守护人电话（老人直线）')
  if (!form.emergency_phones[0].trim()) return showToast('请填写紧急电话 1（第一顺位）')
  if (!form.emergency_phones[1].trim()) return showToast('请填写紧急电话 2（冗余备用）')
  if (!form.emergency_phones[2].trim()) return showToast('请填写紧急电话 3（冗余备用）')
  saving.value = true
  try {
    await store.saveProfile({
      name: form.name.trim(),
      age: form.age,
      weight_kg: form.weight_kg,
      relationship: form.relationship,
      health_status: form.health_status,
      diseases: form.diseases,
      medications: [],   // 产品不做用药提醒，档案不再采集用药
      fall_count: form.fall_count,
      syncope_count: form.syncope_count,
      family_sudden_cardiac_death: form.family_sudden_cardiac_death,
      wake_time: form.wake_time,
      sleep_time: form.sleep_time,
      address: form.address.trim(),
      elder_phone: form.elder_phone.trim(),
      emergency_phones: form.emergency_phones.map(p => p.trim()),
    })
    showSuccessToast('档案已建立')
  } catch {
    showFailToast('档案保存失败，可稍后在档案页重试')
  } finally {
    saving.value = false
    localStorage.setItem('wg_setup_done', '1')
    router.replace('/home')
  }
}
</script>

<template>
  <div class="setup-page">
    <van-nav-bar title="建立守护档案" left-arrow fixed placeholder @click-left="prev">
      <template #left>
        <span class="nav-left">返回</span>
      </template>
    </van-nav-bar>

    <van-steps :active="step" class="steps">
      <van-step>基本信息</van-step>
      <van-step>身体状态</van-step>
      <van-step>作息</van-step>
      <van-step>病史</van-step>
      <van-step>跌倒史</van-step>
      <van-step>紧急联系</van-step>
    </van-steps>

    <!-- Step 0: 基本信息 -->
    <div v-if="step === 0" class="step-body">
      <van-cell-group inset>
        <van-field v-model="form.name" label="姓名" placeholder="请输入守护人姓名" required />
        <van-cell title="与您的关系" required />
        <van-radio-group v-model="form.relationship" class="radio-grid">
          <van-radio
            v-for="opt in relationshipOptions" :key="opt.value"
            :name="opt.value" icon-size="16px"
          >
            {{ opt.text }}
          </van-radio>
        </van-radio-group>
        <van-cell title="年龄" center>
          <template #right-icon>
            <van-stepper v-model="form.age" min="50" max="120" />
          </template>
        </van-cell>
        <van-cell title="体重（kg）" center label="用于跌倒冲击与久滞风险评估，可不填">
          <template #right-icon>
            <van-stepper v-model="form.weight_kg" min="0" max="150" step="0.5" :decimal-length="1" />
          </template>
        </van-cell>
      </van-cell-group>
      <p class="step-hint">姓名与关系用于个性化称呼与告警推送。</p>
    </div>

    <!-- Step 1: 身体状态 -->
    <div v-if="step === 1" class="step-body">
      <van-cell-group inset>
        <van-cell title="守护人的身体状态" />
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
      </van-cell-group>
      <p class="step-hint">身体状态影响判定灵敏度：状态越弱，系统越敏感。</p>
    </div>

    <!-- Step 2: 作息 -->
    <div v-if="step === 2" class="step-body">
      <van-cell-group inset>
        <van-field v-model="form.wake_time" label="通常起床时间" placeholder="如 06:30" />
        <van-field v-model="form.sleep_time" label="通常入睡时间" placeholder="如 21:30" />
      </van-cell-group>
      <p class="step-hint">作息用于识别"未按时起床"与"异常时段活动"。</p>
    </div>

    <!-- Step 3: 病史 -->
    <div v-if="step === 3" class="step-body">
      <van-cell-group inset>
        <van-cell title="请选择守护人的病史（可多选）" />
        <van-checkbox-group v-model="form.diseases" class="checkbox-grid">
          <van-checkbox
            v-for="opt in diseaseOptions" :key="opt.value"
            :name="opt.value" shape="square" icon-size="16px"
          >
            {{ opt.text }}
          </van-checkbox>
        </van-checkbox-group>
        <van-cell title="其他疾病（不在上述列表）" />
        <div class="custom-disease-row">
          <van-field
            v-model="customDiseaseInput"
            placeholder="如：帕金森病，AI 将查询医学词条"
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
      </van-cell-group>
      <p class="step-hint">病史会影响跌倒判定阈值与告警等级（千人千面）。非常见病由 AI 查询医学词条，您核对修改或确认后，方可纳入个性化医疗档案。</p>
    </div>

    <!-- AI 医学词条分析结果弹窗（可编辑，修改或确认后才入档） -->
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

    <!-- Step 4: 跌倒史 -->
    <div v-if="step === 4" class="step-body">
      <van-cell-group inset>
        <van-cell title="近半年跌倒次数" center>
          <template #right-icon>
            <van-stepper v-model="form.fall_count" min="0" max="20" />
          </template>
        </van-cell>
        <van-cell title="近半年晕厥次数" center>
          <template #right-icon>
            <van-stepper v-model="form.syncope_count" min="0" max="20" />
          </template>
        </van-cell>
        <van-cell title="家族猝死史" center>
          <template #right-icon>
            <van-switch v-model="form.family_sudden_cardiac_death" size="20px" />
          </template>
        </van-cell>
      </van-cell-group>
      <p class="step-hint">跌倒史越多，系统判定越敏感，告警越及时。</p>
    </div>

    <!-- Step 5: 紧急联系（住址 + 守护人电话 + 三个顺位紧急电话，全部必填·冗余原则） -->
    <div v-if="step === 5" class="step-body">
      <van-cell-group inset>
        <van-field v-model="form.address" label="家庭住址" placeholder="如：幸福小区 3 栋 2 单元 501" required />
        <van-field v-model="form.elder_phone" label="守护人电话" type="tel" placeholder="老人直线，告警时第一时间致电确认意识" required />
        <van-field v-model="form.emergency_phones[0]" label="紧急电话 1" type="tel" placeholder="第一顺位联系人" required />
        <van-field v-model="form.emergency_phones[1]" label="紧急电话 2" type="tel" placeholder="冗余备用：电话1打不通自动切换" required />
        <van-field v-model="form.emergency_phones[2]" label="紧急电话 3" type="tel" placeholder="冗余备用：电话2打不通自动切换" required />
      </van-cell-group>
      <p class="step-hint">住址在拨 120 时同步给急救中心；紧急电话按顺位逐个拨打，打不通自动切换下一个，多一路备份多一分把握。以上均为必填。</p>
    </div>

    <!-- 底部按钮 -->
    <div class="setup-action">
      <van-button
        v-if="step < TOTAL_STEPS - 1"
        type="primary" block round size="large"
        @click="next"
      >
        下一步
      </van-button>
      <van-button
        v-else
        type="primary" block round size="large"
        :loading="saving"
        @click="finish"
      >
        完成，开始守护
      </van-button>
    </div>
  </div>
</template>

<style scoped>
.setup-page {
  min-height: 100vh;
  background: #f7f8fa;
  padding-bottom: 100px;
}

.nav-left {
  color: #1989fa;
  font-size: 14px;
}

.steps {
  margin: 12px 0;
}

.step-body {
  margin-top: 12px;
}

.step-hint {
  font-size: 12px;
  color: #969799;
  padding: 12px 28px 0;
  line-height: 1.6;
}

.radio-grid {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  padding: 12px 16px;
}

.checkbox-grid {
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

.setup-action {
  position: fixed;
  bottom: 0;
  left: 0;
  right: 0;
  padding: 12px 24px calc(12px + env(safe-area-inset-bottom));
  background: #fff;
  box-shadow: 0 -2px 8px rgba(0, 0, 0, 0.04);
}
</style>
