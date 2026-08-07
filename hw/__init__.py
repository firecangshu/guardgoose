"""硬件桥接层：ESP32-S3 csi_recv 串口 CSI_DATA → 边缘服务 /ingest/sample。

链路：串口采集 → CSI 帧解析（192 子载波幅度）→ 逐秒信号处理
      （活动强度归一化 + 呼吸锁频共识）→ POST /ingest/sample（source=csi_live）

与 waveguard/edge 完全解耦：边缘服务只认 /ingest/sample 接口，
硬件轨（csi_live）与模拟轨（dataset_replay）走完全相同下游。
"""

__all__ = ["csi_parser", "signal_proc", "bridge"]
