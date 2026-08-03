/* ============================================
   护院鹅 · 子女端公共 JS
   含 WebSocket 重连 / 主题切换 / API 封装
   ============================================ */

// ============ 配置 ============
const WAVEGUARD_CONFIG = {
  apiBase: 'http://localhost:8000',
  wsUrl: 'ws://localhost:8000/ws/realtime',
  wsHeartbeat: 30000,       // 心跳 30s
  wsRetryBase: 1000,        // 重连初始 1s
  wsRetryMax: 30000,        // 重连上限 30s
  wsRetryFactor: 2,         // 指数退避因子
};

// ============ 状态 ============
const WAVEGUARD_STATE = {
  ws: null,
  wsRetryCount: 0,
  wsRetryTimer: null,
  wsHeartbeatTimer: null,
  connected: false,
  currentPage: 'home',
  theme: localStorage.getItem('wg-theme') || 'dark',
  device: null,
};

// ============ 工具函数 ============
function $(sel) { return document.querySelector(sel); }
function $$(sel) { return document.querySelectorAll(sel); }

function formatTime(ts) {
  const d = new Date(ts);
  const pad = n => String(n).padStart(2, '0');
  return `${d.getMonth()+1}/${d.getDate()} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function formatTimeShort(ts) {
  const d = new Date(ts);
  const pad = n => String(n).padStart(2, '0');
  return `${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function showToast(msg, type = 'info') {
  const t = document.createElement('div');
  t.textContent = msg;
  t.style.cssText = `
    position: fixed; top: 20px; left: 50%; transform: translateX(-50%);
    background: ${type === 'error' ? 'var(--alarm)' : type === 'success' ? 'var(--safe)' : 'var(--info)'};
    color: white; padding: 10px 20px; border-radius: 12px;
    font-size: 14px; z-index: 1000; box-shadow: 0 4px 12px rgba(0,0,0,0.3);
    animation: slideIn 0.3s ease;
  `;
  document.body.appendChild(t);
  setTimeout(() => { t.style.opacity = '0'; t.style.transition = 'opacity 0.3s'; }, 2500);
  setTimeout(() => t.remove(), 2900);
}

// ============ 主题切换 ============
function applyTheme(theme) {
  document.documentElement.setAttribute('data-theme', theme);
  localStorage.setItem('wg-theme', theme);
  WAVEGUARD_STATE.theme = theme;
}

function toggleTheme() {
  const next = WAVEGUARD_STATE.theme === 'dark' ? 'light' : 'dark';
  applyTheme(next);
  showToast(`已切换到${next === 'dark' ? '暗色' : '亮色'}主题`, 'success');
}

// 启动时应用主题
applyTheme(WAVEGUARD_STATE.theme);

// ============ API 封装 ============
async function apiGet(path) {
  try {
    const res = await fetch(`${WAVEGUARD_CONFIG.apiBase}${path}`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return await res.json();
  } catch (e) {
    console.warn('[API GET]', path, e.message);
    return null;
  }
}

async function apiPost(path, body) {
  try {
    const res = await fetch(`${WAVEGUARD_CONFIG.apiBase}${path}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body || {}),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return await res.json();
  } catch (e) {
    console.warn('[API POST]', path, e.message);
    return null;
  }
}

// ============ WebSocket 重连机制 ============
function wsConnect() {
  if (WAVEGUARD_STATE.ws && WAVEGUARD_STATE.ws.readyState === WebSocket.OPEN) return;
  console.log('[WS] 连接中...', WAVEGUARD_CONFIG.wsUrl);
  try {
    WAVEGUARD_STATE.ws = new WebSocket(WAVEGUARD_CONFIG.wsUrl);
  } catch (e) {
    console.warn('[WS] 创建失败:', e.message);
    scheduleReconnect();
    return;
  }

  WAVEGUARD_STATE.ws.onopen = () => {
    console.log('[WS] 已连接');
    WAVEGUARD_STATE.connected = true;
    WAVEGUARD_STATE.wsRetryCount = 0;
    updateConnStatus(true);
    startHeartbeat();
  };

  WAVEGUARD_STATE.ws.onmessage = (e) => {
    try {
      const data = JSON.parse(e.data);
      handleWsMessage(data);
    } catch (err) {
      console.warn('[WS] 消息解析失败:', err.message);
    }
  };

  WAVEGUARD_STATE.ws.onerror = (e) => {
    console.warn('[WS] 错误');
    updateConnStatus(false);
  };

  WAVEGUARD_STATE.ws.onclose = () => {
    console.log('[WS] 已关闭');
    WAVEGUARD_STATE.connected = false;
    updateConnStatus(false);
    stopHeartbeat();
    scheduleReconnect();
  };
}

function scheduleReconnect() {
  if (WAVEGUARD_STATE.wsRetryTimer) return;
  const base = WAVEGUARD_CONFIG.wsRetryBase;
  const factor = WAVEGUARD_CONFIG.wsRetryFactor;
  const max = WAVEGUARD_CONFIG.wsRetryMax;
  const delay = Math.min(base * Math.pow(factor, WAVEGUARD_STATE.wsRetryCount), max);
  WAVEGUARD_STATE.wsRetryCount++;
  console.log(`[WS] ${delay}ms 后第 ${WAVEGUARD_STATE.wsRetryCount} 次重连`);
  WAVEGUARD_STATE.wsRetryTimer = setTimeout(() => {
    WAVEGUARD_STATE.wsRetryTimer = null;
    wsConnect();
  }, delay);
}

function startHeartbeat() {
  stopHeartbeat();
  WAVEGUARD_STATE.wsHeartbeatTimer = setInterval(() => {
    if (WAVEGUARD_STATE.ws && WAVEGUARD_STATE.ws.readyState === WebSocket.OPEN) {
      WAVEGUARD_STATE.ws.send(JSON.stringify({ type: 'ping', ts: Date.now() }));
    }
  }, WAVEGUARD_CONFIG.wsHeartbeat);
}

function stopHeartbeat() {
  if (WAVEGUARD_STATE.wsHeartbeatTimer) {
    clearInterval(WAVEGUARD_STATE.wsHeartbeatTimer);
    WAVEGUARD_STATE.wsHeartbeatTimer = null;
  }
}

function wsSend(data) {
  if (WAVEGUARD_STATE.ws && WAVEGUARD_STATE.ws.readyState === WebSocket.OPEN) {
    WAVEGUARD_STATE.ws.send(JSON.stringify(data));
    return true;
  }
  return false;
}

function updateConnStatus(connected) {
  const dot = $('#dot');
  const text = $('#connText');
  if (dot) dot.classList.toggle('on', connected);
  if (text) text.textContent = connected ? '已连接' : '连接中…';
}

// ============ WS 消息分发 ============
function handleWsMessage(data) {
  // 全局事件分发
  switch (data.type) {
    case 'state':
      onStateUpdate(data.payload);
      break;
    case 'alert':
      onAlertEvent(data.payload);
      break;
    case 'event':
      onNewEvent(data.payload);
      break;
    case 'pong':
      // 心跳响应，忽略
      break;
    default:
      console.log('[WS] 未知消息类型:', data.type);
  }
}

// 默认空实现，由各页面覆盖
function onStateUpdate(payload) {}
function onAlertEvent(payload) {}
function onNewEvent(payload) {}

// ============ 路由辅助 ============
function getQueryParam(name) {
  const params = new URLSearchParams(window.location.search);
  return params.get(name);
}

function navigateTo(page, params = {}) {
  const query = Object.entries(params)
    .map(([k, v]) => `${k}=${encodeURIComponent(v)}`)
    .join('&');
  window.location.href = query ? `${page}.html?${query}` : `${page}.html`;
}

// ============ 初始化 ============
document.addEventListener('DOMContentLoaded', () => {
  // 自动连接 WebSocket
  wsConnect();
  // 标记当前页面
  const path = window.location.pathname.split('/').pop().replace('.html', '');
  WAVEGUARD_STATE.currentPage = path || 'home';
  // 高亮 tab
  $$('.tabbar a').forEach(a => {
    const target = a.getAttribute('href').replace('.html', '').replace('?id=', '').replace('./', '');
    if (target === WAVEGUARD_STATE.currentPage) a.classList.add('active');
  });
});
