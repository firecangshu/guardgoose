# 真机测试数据库（test_data_db）

所有真实硬件测试数据的统一归档库。用途：**每次测试完落盘总结，积累样本找规律、优化算法**。

## 目录结构

```
test_data_db/
├── sessions/          # 新真机测试（桥接器 --session-label 自动落盘）
│   └── 时间戳_标签.csv            # 逐秒样本：强度/呼吸/锁频/运动比/底噪/RSSI/帧率
│   └── 时间戳_标签.summary.json   # 会话结束自动总结（锁频率/底噪/强度P90…）
├── legacy/            # 历史测试归档（tools/import_legacy_tests.py 一次性导入）
│   └── 每组一个目录：meta.json（原始文件索引）+ *_persecond.csv + summary.json
└── report.md          # 全库规律分析（tools/analyze_test_db.py 生成）
```

## 使用流程

1. **做测试**：`python -m hw.bridge --session-label 静坐呼吸_两板1.8米`
   逐秒样本自动落盘到 `sessions/`；Ctrl+C 退出时自动落 `summary.json`。
2. **补备注**：打开对应 `summary.json`，在 `note` 字段写清几何摆放/人员动作/结论。
3. **找规律**：`python tools/analyze_test_db.py` → 重新生成 `report.md`
   （锁频率 vs 底噪/帧率/运动比的横向对比）。

## 已归档的历史测试（原始文件只引用不复制）

| 归档目录 | 原始文件 | 内容 |
|---|---|---|
| legacy/20260731_第一次测试 | 测试数据/csi_data.csv | 首轮真机采集 |
| legacy/20260731_第二次测试 | 测试数据/第二次测试csi_data.csv | 第二轮真机采集 |
| legacy/20260731_第三次测试 | 测试数据/第三次测试.csv | 第三轮真机采集 |
| legacy/20260806_实时联调 | waveguard/test_data_realtime.csv | 状态机 v2 联调实时采集 |
| legacy/20260806_*_控制变量实验 | test_results/experiment_*.json | F1-F4 跌倒 / B1-B4 呼吸控制变量实验 |

相关文档：`test_results/实验日志_20260806.md`（实验结论）、
`小有可为2026-银发守望计划书/07-硬件实测报告/`（实测报告与参数速查表）。
