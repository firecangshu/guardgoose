"""CSI 探测查看器启动器：PyWebView 独立窗口 + 内置 HTTP 服务器。"""
import threading
import http.server
import socketserver
import webview
import os

VIEWER_DIR = r'e:\小有可为\小有可为2026-银发守望计划书\工具'
VIEWER_FILE = 'csi_viewer_守望版.html'
PORT = 8081

class QuietHandler(http.server.SimpleHTTPRequestHandler):
    """静默 HTTP 请求处理器（不输出日志）。"""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=VIEWER_DIR, **kwargs)
    def log_message(self, format, *args):
        pass  # 静默

def start_server():
    """后台启动 HTTP 服务器。"""
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(('127.0.0.1', PORT), QuietHandler) as httpd:
        httpd.serve_forever()

# 后台线程启动 HTTP 服务器
threading.Thread(target=start_server, daemon=True).start()

# 创建 PyWebView 独立窗口
window = webview.create_window(
    title='CSI 探测查看器',
    url=f'http://127.0.0.1:{PORT}/{VIEWER_FILE}',
    width=1400,
    height=900,
    resizable=True
)
webview.start()
