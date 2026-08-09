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

// 首屏（无感守护）富排版内容：文字为用户定稿，一字不动（见 docs/无感守护页_排版预览.html）
const FEATURES = [
  { k: '零隐私泄露：', t: '无摄像头、无录音，家人收到的只有"平安/异常"结果信息；' },
  { k: '智能防误报：', t: '告警前设备先语音问候老人，确认无恙自动消警，不轻易惊动家人；' },
  { k: '病历个性化：', t: '同样的跌倒，依据病史给出不同的处置策略与告警等级；' },
  { k: '本地运行：', t: '原始信号不出家门，断网后数据本地留存，网络恢复自动续传。' },
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
          <!-- 首屏：产品介绍富排版（文字定稿一字不动，排版以本地预览为准） -->
          <template v-if="s.rich">
            <h1 class="rich-title">护院鹅 · 独居老人无感守护神</h1>
            <div class="rich-intro">不用传统的摄像头，不用传统的手环，该设备无需复杂安装，插上电源即可守护老人，感知是否跌倒、呼吸是否紊乱等异常状态，并且第一时间通知给远在他方的您，为老人平添一份关怀。全程不采集任何图像和声音，本地运行，完美保护每一位独居老人的隐私！</div>

            <h3 class="rich-h">快速上手</h3>
            <table class="qs-table">
              <thead>
                <tr><th>页面</th><th>用途</th><th>操作</th></tr>
              </thead>
              <tbody>
                <tr>
                  <td class="qs-page">🏠 守护</td>
                  <td>看实时状态与告警</td>
                  <td><span class="c-green">绿色正常</span>·<span class="c-orange">橙色观察</span>·<span class="c-red">红色告警</span>·<span class="c-black">黑色紧急</span></td>
                </tr>
                <tr>
                  <td class="qs-page">📋 事件库</td>
                  <td>查历史记录</td>
                  <td>点「展开」看全部，点「导出」存文件</td>
                </tr>
                <tr>
                  <td class="qs-page">❤️ 档案本</td>
                  <td>填病史与紧急联系人</td>
                  <td>填一次，告警判定自动因人而异</td>
                </tr>
              </tbody>
            </table>

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

/* ---- 首屏富排版（无感守护产品介绍，样式与本地预览一致）---- */
.slide--rich {
  align-items: stretch;
  justify-content: flex-start;
  text-align: left;
  overflow-y: auto;
  padding: 4px 4px 20px;
}

.rich-title {
  font-size: 18px;
  font-weight: 800;
  color: #1d2129;
  text-align: center;
  margin: 4px 0 14px;
}

/* 简介整段加黑加粗（用户定稿要求） */
.rich-intro {
  background: #f7f8fa;
  border-radius: 14px;
  padding: 14px 15px;
  font-size: 14px;
  color: #1d2129;
  font-weight: 700;
  line-height: 1.8;
  text-align: justify;
}

.rich-h {
  font-size: 14px;
  font-weight: 700;
  color: #1d2129;
  margin: 20px 0 9px;
  padding-left: 8px;
  border-left: 3px solid #07c160;
}

.qs-table {
  width: 100%;
  border-collapse: collapse;
  background: #f7f8fa;
  border-radius: 12px;
  overflow: hidden;
  font-size: 11.5px;
}

.qs-table th {
  background: #eef1f4;
  color: #86909c;
  font-weight: 600;
  padding: 7px 9px;
  text-align: left;
}

.qs-table td {
  padding: 8px 9px;
  color: #333;
  line-height: 1.5;
  border-top: 1px solid #eef0f3;
  vertical-align: top;
}

.qs-page {
  color: #1989fa;
  font-weight: 600;
  white-space: nowrap;
}

.c-green { color: #07c160; font-weight: 600; }
.c-orange { color: #ff9f0a; font-weight: 600; }
.c-red { color: #ee0a24; font-weight: 600; }
.c-black { color: #1d2129; font-weight: 700; }

.feat-list {
  list-style: none;
  margin: 0;
  padding: 0;
}

.feat-list li {
  position: relative;
  padding: 6px 0 6px 18px;
  font-size: 12.5px;
  color: #333;
  line-height: 1.55;
  border-bottom: 1px solid #f2f3f5;
}

.feat-list li:last-child {
  border-bottom: none;
}

.feat-list li::before {
  content: '●';
  position: absolute;
  left: 0;
  top: 10px;
  font-size: 9px;
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
