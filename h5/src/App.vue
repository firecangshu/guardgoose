<script setup lang="ts">
import { ref, watch, computed } from 'vue'
import { useRoute } from 'vue-router'
import { useGuardianStore } from './stores/guardian'

const route = useRoute()
const store = useGuardianStore()
const activeTab = ref('home')

// 无导航页面（登录/引导/建档向导）
const AUTH_PATHS = ['/login', '/onboarding', '/setup']
const isAuthPage = computed(() => AUTH_PATHS.includes(route.path))

// 监听路由变化同步tab
watch(() => route.path, (path) => {
  if (path.includes('events')) activeTab.value = 'events'
  else if (path.includes('profile')) activeTab.value = 'profile'
  else activeTab.value = 'home'
}, { immediate: true })

// 连接WebSocket
store.connectWs()
</script>

<template>
  <div class="app-container">
    <!-- 顶部导航已下放：首页用自己的品牌栏，其余页由底部Tab导航，避免双重顶栏 -->

    <!-- 离线警示横幅：全局常驻告警，任何页面都不可错过 -->
    <div v-if="store.offline && !isAuthPage" class="offline-banner" @click="$router.push('/home')">
      ⚠️ 已离线：无法连接守护服务，请及时检查设备与网络信号
    </div>

    <!-- 页面内容 -->
    <router-view />

    <!-- 底部Tab栏（登录/引导/建档页隐藏）：三栏结构，告警并入守护页 -->
    <van-tabbar v-if="!isAuthPage" v-model="activeTab" fixed placeholder>
      <van-tabbar-item name="home" icon="home-o" to="/home" :badge="store.alertCount || ''">守护</van-tabbar-item>
      <van-tabbar-item name="events" icon="todo-list-o" to="/events">事件库</van-tabbar-item>
      <van-tabbar-item name="profile" icon="user-o" to="/profile">档案本</van-tabbar-item>
    </van-tabbar>
  </div>
</template>

<style scoped>
.app-container {
  min-height: 100vh;
  background: #f7f8fa;
  padding-bottom: 50px;
}

.offline-banner {
  position: fixed;
  top: 0;
  left: 50%;
  transform: translateX(-50%);
  width: 100%;
  max-width: var(--app-max-width, 480px);
  z-index: 999;
  background: #ee0a24;
  color: #fff;
  font-size: 13px;
  text-align: center;
  padding: calc(8px + env(safe-area-inset-top)) 12px 8px;
  line-height: 1.4;
}
</style>
