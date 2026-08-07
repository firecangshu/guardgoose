"""CSI 帧解析器：ESP32-S3 csi_recv 串口输出行 → 192 子载波幅度向量。

串口格式为 25 列 CSV（与官方 esp-csi csi_data_read_parse.py 一致）：
  type,id,mac,rssi,rate,sig_mode,mcs,bandwidth,smoothing,not_sounding,
  aggregation,stbc,fec_coding,sgi,noise_floor,ampdu_cnt,channel,
  secondary_channel,local_timestamp,ant,sig_len,rx_format,len,first_word,data

data 列为带引号的交替 I/Q 整数序列（N16R8 为 384 个值 = 192 子载波 × 2）。
坏帧（长度不匹配 / 非 CSI_DATA / 解析失败）一律返回 None，不中断数据流。
"""
from __future__ import annotations

import csv
import io
from dataclasses import dataclass

import numpy as np

CSI_PREFIX = "CSI_DATA"

# 25 列按官方顺序
CSI_COLUMNS = [
    "type", "id", "mac", "rssi", "rate", "sig_mode", "mcs", "bandwidth",
    "smoothing", "not_sounding", "aggregation", "stbc", "fec_coding",
    "sgi", "noise_floor", "ampdu_cnt", "channel", "secondary_channel",
    "local_timestamp", "ant", "sig_len", "rx_format", "len", "first_word",
    "data",
]

# 常用列索引
IDX_TYPE = 0
IDX_RSSI = 3
IDX_CHANNEL = 16
IDX_TIMESTAMP = 18
IDX_SIG_LEN = 21
IDX_DATA = 24


@dataclass
class CsiFrame:
    """一帧解析结果。"""

    ts_us: int          # local_timestamp（微秒）
    rssi: int           # 接收信号强度 dBm
    channel: int        # WiFi 信道
    amp: np.ndarray     # (n_subcarriers,) 幅度 = sqrt(I^2 + Q^2)
    raw: str = ""       # 原始行（调试/落盘用）


def parse_csi_line(line: str) -> CsiFrame | None:
    """解析一行串口 CSI_DATA，坏帧返回 None。"""
    line = line.strip()
    if not line or not line.startswith(CSI_PREFIX):
        return None
    try:
        rows = next(csv.reader(io.StringIO(line)))
    except Exception:
        return None
    if len(rows) < 25 or rows[IDX_TYPE] != CSI_PREFIX:
        return None
    try:
        ts_us = int(rows[IDX_TIMESTAMP])
        rssi = int(rows[IDX_RSSI])
        channel = int(rows[IDX_CHANNEL])
        # data 列形如 "[0,0,...]"：先剥外层引号，再剥首尾方括号
        data = rows[IDX_DATA].strip().strip('"').lstrip('[').rstrip(']')
        vals = [int(x) for x in data.split(",")]
    except (ValueError, IndexError):
        return None
    n = len(vals) // 2
    if n < 1 or len(vals) != n * 2:
        return None  # I/Q 数量不匹配
    arr = np.asarray(vals, dtype=np.int16)
    amp = np.sqrt(arr[0::2].astype(np.float64) ** 2
                  + arr[1::2].astype(np.float64) ** 2)
    return CsiFrame(ts_us=ts_us, rssi=rssi, channel=channel, amp=amp, raw=line)
