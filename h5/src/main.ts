import { createApp } from 'vue'
import { createPinia } from 'pinia'
import { createRouter, createWebHashHistory } from 'vue-router'
import App from './App.vue'
import 'vant/lib/index.css'
import './style.css'

// Vant 组件
import {
  NavBar, Tabbar, TabbarItem, Card, Tag, Button, Icon,
  Cell, CellGroup, Field, Switch, Dialog, Toast, Loading,
  Empty, Divider, Badge, ActionSheet, Popup, Form,
  Checkbox, CheckboxGroup, RadioGroup, Radio, Stepper,
  Collapse, CollapseItem, Progress, Circle, Overlay,
  Swipe, SwipeItem, Step, Steps, SwipeCell, Picker
} from 'vant'

// 页面
import LoginView from './views/LoginView.vue'
import OnboardingView from './views/OnboardingView.vue'
import SetupView from './views/SetupView.vue'
import HomeView from './views/HomeView.vue'
import EventsView from './views/EventsView.vue'
import ProfileView from './views/ProfileView.vue'

const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    { path: '/', redirect: '/home' },
    { path: '/login', name: 'login', component: LoginView },
    { path: '/onboarding', name: 'onboarding', component: OnboardingView },
    { path: '/setup', name: 'setup', component: SetupView },
    { path: '/home', name: 'home', component: HomeView },
    { path: '/events', name: 'events', component: EventsView },
    { path: '/alert', redirect: '/home' },
    { path: '/profile', name: 'profile', component: ProfileView },
  ],
})

// 路由守卫：未登录 → 登录页；已登录未建档 → 档案向导
router.beforeEach((to) => {
  const loggedIn = !!localStorage.getItem('wg_logged')
  if (!loggedIn && to.path !== '/login') return '/login'
  if (loggedIn && !localStorage.getItem('wg_setup_done')) {
    if (!['/setup', '/onboarding'].includes(to.path)) {
      return localStorage.getItem('wg_onboarded') ? '/setup' : '/onboarding'
    }
  }
  return true
})

const pinia = createPinia()
const app = createApp(App)

// 注册 Vant 组件
const vantComponents = [
  NavBar, Tabbar, TabbarItem, Card, Tag, Button, Icon,
  Cell, CellGroup, Field, Switch, Dialog, Toast, Loading,
  Empty, Divider, Badge, ActionSheet, Popup, Form,
  Checkbox, CheckboxGroup, RadioGroup, Radio, Stepper,
  Collapse, CollapseItem, Progress, Circle, Overlay,
  Swipe, SwipeItem, Step, Steps, SwipeCell, Picker
]
vantComponents.forEach((c: any) => app.component(c.name || c.__name, c))

app.use(pinia)
app.use(router)
app.mount('#app')
