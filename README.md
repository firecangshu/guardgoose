# 🛡️ 护院鹅 · WiFi CSI 居家安全守护系统

> 用 WiFi 信号守护独居老人 · 非视觉、非穿戴、零隐私泄露

[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.10+-green.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.104+-red.svg)](https://fastapi.tiangolo.com/)
[![Status](https://img.shields.io/badge/status-v1.0%20MVP-orange.svg)]()

---

## 📖 项目简介

**护院鹅** 是一套基于 **WiFi CSI（信道状态信息）** 的非接触式居家安全守护系统，通过分析 WiFi 信号穿过人体产生的扰动，实现：

- 🚨 **跌倒检测**：实时识别跌倒事件，分级告警
- 🫁 **呼吸监测**：非接触式呼吸频率检测（12-20 次/分正常范围）
- 🏠 **护家模式**：室内异常移动检测，访客识别自动关闭
- 📞 **语音确证**：跌倒后 90 秒语音呼叫，避免误报
- 🤖 **AI 推理**：Qwen-plus 大模型结合病历个性化判断

### 与传统方案对比

| 方案 | 隐私 | 持续性 | 洗澡区域 | 跌倒识别 | 呼吸监测 |
|------|------|--------|---------|---------|---------|
| 摄像头 | ❌ 侵犯隐私 | ✅ | ❌ 盲区 | ✅ | ❌ |
| 智能手环 | ✅ | ❌ 需充电 | ❌ 不戴 | ✅ | 部分 |
| 毫米波雷达 | ✅ | ✅ | ✅ | ✅ | ✅ 昂贵 |
| **护院鹅 CSI** | ✅ **零泄露** | ✅ 7×24 | ✅ 全屋 | ✅ | ✅ **低成本** |

---

## 🏗️ 系统架构

四层架构 · 13 张工程图 · 全链路隐私保护

```
📡 感知层（ESP32）          🖥️ 边缘层（FastAPI）          📱 子女端（Web）
┌──────────────┐          ┌──────────────────┐          ┌──────────────┐
│ TX 发射端     │          │ 数据接入          │          │ 实时状态卡    │
│ RX 接收端     │ ───────→ │ 信号处理 + 状态机 │ ───────→ │ 告警详情      │
│ 30 子载波     │  HTTPS   │ Qwen AI 推理     │  WebSocket│ 健康档案      │
│ 3 天线        │          │ SQLite 本地存储   │          │ 事件时间线    │
└──────────────┘          └──────────────────┘          └──────────────┘
                                ↓ 仅语义标签
                          🔐 原始 CSI 不出本地
```

完整架构图见 [`docs/diagrams/`](docs/diagrams/)，共 13 张：
1. 系统架构图 · 2. 组件关系图 · 3. 数据依赖图 · 4. SOP 流程图
5. Zone 状态机图 · 6. API 接口关系图 · 7. 完整时序图 · 8. 开发计划甘特图
9. 监控运维图 · 10. 隐私边界图 · 11. 版本演进路线图 · 12. 测试与成本功耗图
13. 初赛开发时序计划甘特图

---

## 🚀 快速开始

### 环境要求

- Python 3.10+
- ESP32 开发板 × 2（发送端 + 接收端）
- WiFi 路由器（802.11n 支持）

### 安装

```bash
git clone https://github.com/你的用户名/waveguard.git
cd waveguard
pip install -r requirements.txt
```

### 启动边缘服务

```bash
python run_edge.py
# 服务运行在 http://localhost:8000
# API 文档：http://localhost:8000/docs
# WebSocket：ws://localhost:8000/ws/realtime
```

### 启动子女端

```bash
cd web
python -m http.server 8766
# 浏览器打开：http://localhost:8766/home.html
```

### 模拟信号测试

```bash
# 双击运行信号测试播放器
信号测试播放器.bat
# 选择 1-9 不同场景：静止 / 走动 / 跌倒 / 呼吸异常 等
```

---

## 📂 目录结构

```
waveguard/
├── edge/                    # 边缘服务（FastAPI）
│   ├── server.py           # 主服务 + 14 个 API 端点 + WebSocket
│   ├── state_machine.py    # 六区六级状态机（18 条转换规则）
│   ├── guardian.py         # 告警引擎 + Qwen-plus 调用
│   ├── medical.py          # 病历管理（8 种病史）
│   ├── voice.py            # 语音确证（90s 倒计时）
│   ├── protocol.py         # 数据协议
│   ├── db.py               # SQLite 存储
│   └── config.py           # 配置
├── web/                    # 子女端 Web SPA
│   ├── home.html           # 首页（状态卡 + 活动 + 呼吸）
│   ├── events.html         # 事件时间线（5 类筛选）
│   ├── alert.html          # 告警详情（4 个处置按钮）
│   ├── profile.html        # 健康档案（8 病史 + 8 用药）
│   ├── settings.html       # 设置（主题 + 设备 + 阈值）
│   ├── css/common.css      # 双主题 CSS 变量
│   └── js/common.js        # WebSocket 重连 + API 封装
├── replay/                 # 信号模拟器
│   ├── player.py           # 12 场景播放器
│   └── scenarios/          # 12 个场景 JSON
├── docs/
│   └── diagrams/           # 13 张架构图（.drawio + .mmd + .png）
├── tools/                  # 硬件工具
├── requirements.txt
├── run_edge.py             # 启动脚本
└── 信号测试播放器.bat       # 测试菜单
```

---

## 🎯 核心特性

### 六区六级状态机

| Zone | 状态 | 触发条件 | 响应动作 |
|------|------|---------|---------|
| 0 | 正常 | 静止 / 正常活动 | 持续监测 |
| 1 | 注意 | 检测到活动 | 加强监测 |
| 2 | 需确认 | 跌倒冲击 + 呼吸存在 | 90s 语音呼叫 |
| 3 | 告警 | 语音超时 / 呼吸异常 | 通知子女端 |
| 4 | 紧急 | 呼吸消失 / 子女超时 | 拨打 120 |

### 隐私保护

- ✅ 原始 CSI 矩阵仅本地处理
- ✅ 仅语义标签（跌倒/呼吸异常）传输到子女端
- ✅ 无摄像头、无录音、无穿戴设备
- ✅ 健康档案加密存储

### Qwen-plus AI 推理

结合老人病历（8 种病史 + 8 种用药 + 跌倒史）个性化判断：

```python
# 示例：高血压老人跌倒 10 分钟内必须升级
if profile.diseases.includes("hypertension") and elapsed > 600:
    zone = 4  # 紧急救援
```

---

## 📊 性能指标

| 指标 | 目标 | 实测 |
|------|------|------|
| 跌倒识别准确率 | ≥ 95% | 测试中 |
| 误报率 | ≤ 2 次/天 | 测试中 |
| 告警延迟 | ≤ 5 秒 | ✅ 达标 |
| WebSocket 重连 | ≤ 10 秒 | ✅ 达标 |
| 呼吸频率误差 | ≤ ±2 次/分 | ✅ 达标 |
| Qwen 推理延迟 | ≤ 3 秒 | ✅ 达标 |

---

## 🛠️ 技术栈

| 层 | 技术 |
|----|------|
| 硬件 | ESP32-WROOM-32 · 802.11n HT20 · 30 子载波 × 3 天线 |
| 后端 | Python 3.10 · FastAPI · SQLite WAL · WebSocket |
| AI | Qwen-plus（阿里云大模型） · 8 种病史策略 |
| 前端 | 纯 HTML5 · CSS 变量双主题 · Canvas · fetch API |
| 通信 | HTTPS/TLS 1.3 · WebSocket · 心跳 30s · 指数退避重连 |
| 工具 | drawio · Mermaid.js · Manim |

---

## 📅 版本演进

- **v1.0 MVP**（2026 Q3）：跌倒检测 + 呼吸监测 + 子女端
- **v1.1 增强**（2027 Q1）：护家模式 + 多子女端同步
- **v2.0 扩展**（2027 Q2）：多房间 + 多 ESP32 节点
- **v3.0 AI**（2027 Q4）：1D-CNN + LSTM + 联邦学习

详见 [`docs/diagrams/11_版本演进路线图.mmd`](docs/diagrams/11_版本演进路线图.mmd)

---

## 📄 许可证

MIT License

## 🏆 赛事

**小有可为 2026 · AI 向善创新挑战赛 · 普惠养老赛道**

- 专利申请号：2026111120531
- 评审重点：公益价值 / 技术可行性 / 用户友好度 / 创新价值

---

## 🤝 贡献

欢迎 Issue 和 PR。本项目聚焦老年人安全守护，期待社区共同完善。

## 📧 联系

- 邮箱：15703456@qq.com
- 项目地址：[GitHub](https://github.com/你的用户名/waveguard)

---

> 🛡️ **护院鹅** —— 让科技守护每一位独居老人，让爱无延迟。
