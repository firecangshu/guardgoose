<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'

const router = useRouter()
const current = ref(0)

const SLIDES = [
  {
    icon: '🗺️',
    title: '无感守护 · 程序结构',
    desc: '',
    custom: 'structure',   // 程序结构一图流：五类色卡+数据流主线
  },
  {
    icon: '🫁',
    title: '呼吸监测',
    desc: '实时监测呼吸频率与节律，呼吸异常（过快/浅弱/消失）自动预警。',
  },
  {
    icon: '🚨',
    title: '跌倒秒级告警',
    desc: 'AI分析运动幅度+呼吸变化，识别跌倒后立即推送到您的手机，支持一键处置。',
  },
  {
    icon: '📋',
    title: '千人千面',
    desc: '根据老人病史，定制个性化的判定阈值与守护方案。',
  },
]

/* 程序结构五类色卡：颜色对应结构地图的五类划分 */
const STRUCT_CARDS = [
  { icon: '🟦', name: '源文件', path: 'edge/ · hw/ · h5/src', desc: '判定后端 · 硬件接入 · 子女端——改产品只动这里', color: '#2563eb' },
  { icon: '🟪', name: '剧本', path: 'replay/scenarios/', desc: '18 个演示剧本 JSON，演出时逐段注入状态机', color: '#7c3aed' },
  { icon: '🟩', name: '真数据引入', path: 'hw/bridge.py', desc: '串口采集 → /ingest/sample，真实接入唯一闸门', color: '#16a34a' },
  { icon: '🟨', name: '测试数据库', path: 'test_data_db/', desc: '真机测试逐秒落盘+自动总结，找规律优化算法', color: '#d97706' },
  { icon: '⬜', name: '缓存/可清理', path: '*.log · dist · __pycache__', desc: '运行产物随时可清，waveguard.db 为运行事件库', color: '#9ca3af' },
]

function finish() {
  localStorage.setItem('wg_onboarded', '1')
  if (!localStorage.getItem('wg_setup_done')) {
    router.replace('/setup')
  } else {
    router.replace('/home')
  }
}

function onChange(i: number) {
  current.value = i
}
</script>

<template>
  <div class="onboarding-page">
    <div class="skip-bar">
      <span class="back-btn" @click="router.replace('/login')">‹ 返回</span>
      <span class="skip-btn" @click="finish">跳过</span>
    </div>

    <van-swipe class="swipe" :show-indicators="false" @change="onChange">
      <van-swipe-item v-for="(s, i) in SLIDES" :key="i">
        <div class="slide" :class="{ 'slide-structure': s.custom === 'structure' }">
          <template v-if="s.custom === 'structure'">
            <h2 class="slide-title">{{ s.title }}</h2>
            <!-- 数据流主线：真机一条链，演示一条支线，共用同一状态机 -->
            <div class="flow-line">
              <span class="flow-node">ESP32 板子</span><span class="flow-arrow">→</span>
              <span class="flow-node">bridge.py</span><span class="flow-arrow">→</span>
              <span class="flow-node">状态机</span><span class="flow-arrow">→</span>
              <span class="flow-node">子女端</span>
            </div>
            <div class="flow-sub">演示支线：剧本 JSON → 演示循环，同一状态机</div>
            <!-- 五类色卡：一眼分清谁是什么 -->
            <div class="struct-cards">
              <div
                v-for="c in STRUCT_CARDS" :key="c.name"
                class="struct-card" :style="{ borderLeftColor: c.color }"
              >
                <span class="sc-icon">{{ c.icon }}</span>
                <div class="sc-body">
                  <div class="sc-name">{{ c.name }}<span class="sc-path">{{ c.path }}</span></div>
                  <div class="sc-desc">{{ c.desc }}</div>
                </div>
              </div>
            </div>
            <div class="struct-loop">🔁 优化闭环：测试落盘 → 补备注 → 找规律 → 调参 → 回归验证</div>
          </template>
          <template v-else>
            <div class="slide-icon">{{ s.icon }}</div>
            <h2 class="slide-title">{{ s.title }}</h2>
            <p class="slide-desc">{{ s.desc }}</p>
          </template>
        </div>
      </van-swipe-item>
    </van-swipe>

    <div class="dots">
      <span
        v-for="i in SLIDES.length"
        :key="i"
        class="dot"
        :class="{ active: current === i - 1 }"
      />
    </div>

    <div class="onboarding-action">
      <van-button
        v-if="current < SLIDES.length - 1"
        type="primary" plain block round size="large"
        @click="finish"
      >
        我已了解，直接开始
      </van-button>
      <van-button
        v-else
        type="primary" block round size="large"
        @click="finish"
      >
        开始设置守护档案
      </van-button>
    </div>
  </div>
</template>

<style scoped>
.onboarding-page {
  min-height: 100vh;
  background: #fff;
  display: flex;
  flex-direction: column;
  padding: 16px 24px 32px;
}

.skip-bar {
  display: flex;
  justify-content: space-between;
}

.back-btn {
  font-size: 14px;
  color: #1989fa;
  padding: 4px 8px;
}

.skip-btn {
  font-size: 14px;
  color: #969799;
  padding: 4px 8px;
}

.swipe {
  flex: 1;
  min-height: 320px;
}

.slide {
  height: 100%;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  text-align: center;
  padding: 0 16px;
}

.slide-icon {
  font-size: 72px;
  margin-bottom: 24px;
}

.slide-title {
  font-size: 24px;
  font-weight: 700;
  color: #1d2129;
  margin: 0 0 12px;
}

.slide-desc {
  font-size: 15px;
  color: #666;
  line-height: 1.8;
  max-width: 280px;
  margin: 0;
}

/* ---- 程序结构一图流（第一屏）：内容密，改左对齐紧凑排布 ---- */
.slide-structure {
  align-items: stretch;
  justify-content: flex-start;
  text-align: left;
  overflow-y: auto;
  padding: 8px 4px;
}

.slide-structure .slide-title {
  text-align: center;
  font-size: 20px;
  margin: 0 0 10px;
}

/* 数据流主线：横向节点链 */
.flow-line {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 4px;
  flex-wrap: wrap;
}

.flow-node {
  background: #eef4ff;
  color: #2563eb;
  border-radius: 6px;
  padding: 3px 8px;
  font-size: 12px;
  font-weight: 600;
}

.flow-arrow {
  color: #9ca3af;
  font-size: 12px;
}

.flow-sub {
  text-align: center;
  font-size: 11px;
  color: #999;
  margin: 6px 0 10px;
}

/* 五类色卡：左边框颜色即分类色 */
.struct-cards {
  display: flex;
  flex-direction: column;
  gap: 7px;
}

.struct-card {
  display: flex;
  gap: 8px;
  align-items: flex-start;
  background: #fafbfc;
  border: 1px solid #f0f1f3;
  border-left: 3px solid #ccc;
  border-radius: 8px;
  padding: 7px 10px;
}

.sc-icon {
  font-size: 14px;
  line-height: 1.4;
}

.sc-name {
  font-size: 13px;
  font-weight: 700;
  color: #1d2129;
}

.sc-path {
  font-weight: 400;
  font-size: 11px;
  color: #999;
  margin-left: 6px;
}

.sc-desc {
  font-size: 11px;
  color: #666;
  line-height: 1.5;
  margin-top: 1px;
}

.struct-loop {
  margin-top: 10px;
  text-align: center;
  font-size: 11px;
  color: #d97706;
  background: #fffbeb;
  border-radius: 8px;
  padding: 6px 8px;
}

.dots {
  display: flex;
  justify-content: center;
  gap: 6px;
  margin: 24px 0;
}

.dot {
  width: 6px;
  height: 6px;
  border-radius: 3px;
  background: #e5e6eb;
  transition: all 0.3s;
}

.dot.active {
  width: 18px;
  background: #07c160;
}

.onboarding-action {
  padding: 0 8px;
}
</style>
