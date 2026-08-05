<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { showToast } from 'vant'

const router = useRouter()
const name = ref(localStorage.getItem('wg_elder_name') || '')

function enter() {
  if (!name.value.trim()) {
    showToast('请输入守护人姓名')
    return
  }
  localStorage.setItem('wg_elder_name', name.value.trim())
  localStorage.setItem('wg_logged', '1')
  // 首次进入 → 项目介绍引导页；否则直接进守护界面
  if (!localStorage.getItem('wg_onboarded')) {
    router.replace('/onboarding')
  } else if (!localStorage.getItem('wg_setup_done')) {
    router.replace('/setup')
  } else {
    router.replace('/home')
  }
}
</script>

<template>
  <div class="login-page">
    <div class="login-hero">
      <img class="login-logo" src="/guardgoose.png" alt="护院鹅 Guard Goose" />
      <p class="app-slogan">CSI无感守护 · 让爱不缺席</p>
    </div>

    <van-cell-group inset class="login-form">
      <van-field
        v-model="name"
        label="守护人"
        placeholder="请输入被守护老人的称呼，如：妈妈"
        clearable
        @keyup.enter="enter"
      />
    </van-cell-group>

    <div class="login-action">
      <van-button type="primary" block round size="large" @click="enter">
        进入守护
      </van-button>
      <p class="login-tip">
        首次使用将引导您了解项目功能，并为守护人建立个性化健康档案
      </p>
    </div>

    <div class="login-footer">
      <span>小有可为 · AI向善创新挑战赛</span>
    </div>
  </div>
</template>

<style scoped>
.login-page {
  min-height: 100vh;
  background: linear-gradient(160deg, #e8f5e9 0%, #f7f8fa 45%);
  display: flex;
  flex-direction: column;
  padding: 60px 24px 24px;
}

.login-hero {
  text-align: center;
  margin-bottom: 40px;
}

/* 品牌 LOGO：完整盾形徽章（含护院鹅 GUARD GOOSE 字样） */
.login-logo {
  width: 180px;
  max-width: 56vw;
  height: auto;
  display: block;
  margin: 0 auto 16px;
  filter: drop-shadow(0 6px 18px rgba(26, 58, 110, 0.18));
}

.app-slogan {
  font-size: 14px;
  color: #86909c;
  margin: 0;
}

.login-form {
  margin-bottom: 24px;
}

.login-action {
  margin-bottom: 24px;
}

.login-tip {
  font-size: 12px;
  color: #969799;
  text-align: center;
  margin-top: 12px;
  line-height: 1.6;
}

.login-footer {
  margin-top: auto;
  text-align: center;
  font-size: 12px;
  color: #c9cdd4;
}
</style>
