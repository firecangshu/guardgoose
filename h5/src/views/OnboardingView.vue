<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'

const router = useRouter()
const current = ref(0)

const SLIDES = [
  {
    icon: '📡',
    title: '无感守护',
    desc: '通过一对CSI收发探测器感知老人活动，无需佩戴任何设备、不装摄像头，隐私安全。',
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
    desc: '根据老人病史与用药情况，定制个性化的判定阈值与守护方案。',
  },
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
        <div class="slide">
          <div class="slide-icon">{{ s.icon }}</div>
          <h2 class="slide-title">{{ s.title }}</h2>
          <p class="slide-desc">{{ s.desc }}</p>
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
