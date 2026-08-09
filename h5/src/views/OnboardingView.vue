<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'

const router = useRouter()
const current = ref(0)

const SLIDES: { rich?: boolean; icon?: string; title?: string; desc?: string }[] = [
  {
    rich: true,
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

// 首屏（无感守护）富排版内容：文字与产品介绍页定稿完全一致，仅排版优化
const ALERT_STEPS = [
  '点「查看详情」',
  '按提示处理',
  '若误报点「误报，解除警报」',
  '若确有其事点「情况属实，呼叫救援」',
  '系统自动发起语音确证',
]
const FEATURES = [
  { k: '零隐私泄露：', t: '全程无图像、无录音，仅CSI信号特征' },
  { k: '无感使用：', t: '老人无需佩戴、无需操作、无需改变习惯' },
  { k: '跌倒+呼吸双监测：', t: '跌倒秒级告警，呼吸异常（过快/减弱/消失）同步预警' },
  { k: '个性化适配：', t: '根据病史档案自动调整判定阈值，减少误报' },
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
        <div class="slide" :class="{ 'slide--rich': s.rich }">
          <!-- 首屏：产品介绍富排版（文字原样保留，仅优化排版） -->
          <template v-if="s.rich">
            <h1 class="rich-title">护院鹅 · 独居老人无感守护神</h1>
            <div class="rich-intro">
              <p>不用传统的摄像头，也不用佩戴任何设备——只需在房间放置一对CSI收发探测器，即可感知老人的活动与呼吸。</p>
              <p class="intro-privacy">全程不采集任何图像和声音，只提取信号特征，守护隐私。</p>
              <p>本地运行：数据在设备端处理，仅异常事件推送到子女手机。</p>
            </div>

            <h3 class="rich-h">快速上手</h3>
            <table class="qs-table">
              <thead>
                <tr><th>您想看…</th><th>页面</th><th>操作</th></tr>
              </thead>
              <tbody>
                <tr>
                  <td>老人现在怎么样</td>
                  <td class="qs-page">守护页</td>
                  <td>看最上面的卡片：绿色正常·橙色观察·红色告警·黑色紧急</td>
                </tr>
                <tr>
                  <td>发生过什么</td>
                  <td class="qs-page">事件库</td>
                  <td>底部第二个按钮；每条事件点开可看完整过程</td>
                </tr>
              </tbody>
            </table>

            <h3 class="rich-h">收到告警</h3>
            <div class="alert-flow">
              <template v-for="(st, idx) in ALERT_STEPS" :key="idx">
                <span class="flow-step">{{ st }}</span>
                <span v-if="idx < ALERT_STEPS.length - 1" class="flow-arrow">→</span>
              </template>
            </div>
            <p class="flow-note">（通过扬声器询问老人），并按紧急联系人顺序自动拨打，30秒无应答切下一位</p>

            <h3 class="rich-h">产品特点</h3>
            <ul class="feat-list">
              <li v-for="(f, idx) in FEATURES" :key="idx"><b>{{ f.k }}</b>{{ f.t }}</li>
            </ul>
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

/* ---- 首屏富排版（无感守护产品介绍） ---- */
.slide--rich {
  align-items: stretch;
  justify-content: flex-start;
  text-align: left;
  overflow-y: auto;
  padding: 4px 4px 20px;
}

.rich-title {
  font-size: 22px;
  font-weight: 800;
  color: #1d2129;
  text-align: center;
  margin: 10px 0 14px;
}

.rich-intro {
  background: #f7f8fa;
  border-radius: 12px;
  padding: 12px 14px;
}

.rich-intro p {
  font-size: 14px;
  color: #4e5969;
  line-height: 1.7;
  margin: 0 0 8px;
}

.rich-intro p:last-child {
  margin-bottom: 0;
}

.intro-privacy {
  color: #07c160;
  font-weight: 600;
}

.rich-h {
  font-size: 16px;
  font-weight: 700;
  color: #1d2129;
  margin: 18px 0 10px;
  padding-left: 8px;
  border-left: 3px solid #07c160;
}

.qs-table {
  width: 100%;
  border-collapse: collapse;
  background: #f7f8fa;
  border-radius: 10px;
  overflow: hidden;
  font-size: 13px;
}

.qs-table th {
  background: #eef1f4;
  color: #86909c;
  font-weight: 600;
  padding: 10px 8px;
  text-align: left;
}

.qs-table td {
  padding: 12px 8px;
  color: #333;
  line-height: 1.6;
  border-top: 1px solid #eef0f3;
  vertical-align: top;
}

.qs-page {
  color: #1989fa;
  font-weight: 600;
  white-space: nowrap;
}

.alert-flow {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px 4px;
  background: #f7f8fa;
  border-radius: 12px;
  padding: 12px 14px;
}

.flow-step {
  background: #fff;
  border: 1px solid #e5e6eb;
  border-radius: 8px;
  padding: 5px 10px;
  font-size: 13px;
  color: #333;
  line-height: 1.5;
}

.flow-arrow {
  color: #ff9f0a;
  font-weight: 700;
}

.flow-note {
  font-size: 12px;
  color: #86909c;
  line-height: 1.6;
  margin: 8px 2px 0;
}

.feat-list {
  list-style: none;
  margin: 0;
  padding: 0;
}

.feat-list li {
  position: relative;
  padding: 10px 0 10px 18px;
  font-size: 14px;
  color: #333;
  line-height: 1.6;
  border-bottom: 1px solid #f2f3f5;
}

.feat-list li:last-child {
  border-bottom: none;
}

.feat-list li::before {
  content: '●';
  position: absolute;
  left: 0;
  top: 13px;
  font-size: 10px;
  color: #07c160;
}

.feat-list b {
  color: #1d2129;
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
