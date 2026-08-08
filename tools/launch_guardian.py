"""护院鹅子女端启动器：一键拉起。

双击即用：探测 8000 端口后端是否已在运行——
  - 在运行：直接开窗，不重复起服务；
  - 没运行：自动拉起后端（隐藏黑框窗口），等健康检查通过再开窗。
子女端页面由后端同端口托管（edge/server.py 静态挂载 h5/dist），
全程只依赖这一个端口，关掉本窗口不影响后端继续守护。
"""
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import webview

ROOT = Path(__file__).resolve().parent.parent
PORT = 8000
URL = f"http://127.0.0.1:{PORT}/"
HEALTH_URL = f"http://127.0.0.1:{PORT}/api/health"
WAIT_TIMEOUT_S = 30   # 后端冷启动等待上限
POLL_INTERVAL_S = 0.5


def is_backend_alive() -> bool:
    try:
        with urllib.request.urlopen(HEALTH_URL, timeout=2) as r:
            return r.status == 200
    except (urllib.error.URLError, OSError):
        return False


def _backend_python() -> str:
    """选一个能跑后端的 Python：避开 WindowsApps 商店空壳别名。
    优先同目录 python.exe（真发行版一定带解释器），其次当前解释器。"""
    exe_dir = Path(sys.executable).resolve().parent
    candidate = exe_dir / "python.exe"
    if candidate.exists():
        return str(candidate)
    if "WindowsApps" not in sys.executable:
        return sys.executable
    return "python"


def start_backend() -> None:
    """后台拉起后端：CREATE_NO_WINDOW 隐藏控制台，
    DETACHED_PROCESS 使关闭启动器窗口后服务继续守护；
    日志重定向到文件（pythonw 下 stdout 为空，不重定向会崩）。"""
    log_path = ROOT / "backend.log"
    log_file = open(log_path, "a", encoding="utf-8")
    subprocess.Popen(
        [_backend_python(), "run_edge.py"],
        cwd=ROOT,
        stdout=log_file,
        stderr=log_file,
        creationflags=subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS,
    )


def wait_backend() -> bool:
    deadline = time.time() + WAIT_TIMEOUT_S
    while time.time() < deadline:
        if is_backend_alive():
            return True
        time.sleep(POLL_INTERVAL_S)
    return False


def main() -> None:
    if not is_backend_alive():
        start_backend()
    if not wait_backend():
        # 兜底：启动超时时用系统默认浏览器打开，错误页比静默失败友好
        import webbrowser
        webbrowser.open(URL)
        return
    # 手机 H5 形态：竖屏窗口，宽度按主流手机比例
    webview.create_window(
        title="护院鹅 · 子女端",
        url=URL,
        width=430,
        height=860,
        min_size=(360, 600),
        resizable=True,
    )
    webview.start()


if __name__ == "__main__":
    main()
