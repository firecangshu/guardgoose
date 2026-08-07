# -*- coding: utf-8 -*-
"""后端健壮性临时压测：快速开关演示 / 暴力切换场景 / 并发 GET / 真实接入空载。
用法：python stress_backend.py [base_url]，默认 http://127.0.0.1:8000"""
import json
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
PASS, FAIL = 0, 0


def check(name, ok):
    global PASS, FAIL
    PASS, FAIL = PASS + (1 if ok else 0), FAIL + (0 if ok else 1)
    print(("  PASS  " if ok else "  FAIL  ") + name)


def get(path):
    with urllib.request.urlopen(BASE + path, timeout=8) as r:
        return json.loads(r.read())


def post(path, payload=None):
    data = json.dumps(payload or {}).encode()
    req = urllib.request.Request(BASE + path, method="POST", data=data,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=8) as r:
        return json.loads(r.read())


print("== 1. 快速开关演示 ×8（每次间隔 0.3s，模拟手抖连点） ==")
ok = True
for i in range(8):
    try:
        post("/api/source-mode/demo", {"enabled": True, "scenario": "demo_fall_moving"})
        time.sleep(0.3)
        post("/api/source-mode/demo", {"enabled": False, "scenario": ""})
        time.sleep(0.3)
    except Exception as e:
        ok = False
        print("    exception:", e)
        break
m = get("/api/source-mode")
check("8 轮快速开关全部 200 且终态 demo 已关", ok and not m["demo_enabled"])

print("== 2. 播放中暴力切换场景 ×6（每 0.4s 换一个，打断式） ==")
try:
    post("/api/source-mode/demo", {"enabled": True, "scenario": "demo_rest"})
    ok = True
    for s in ["demo_active", "demo_fall_moving", "demo_absent",
              "demo_fall_still", "demo_rest", "demo_active"]:
        time.sleep(0.4)
        post("/api/source-mode/demo", {"enabled": True, "scenario": s})
    time.sleep(1.5)
    m = get("/api/source-mode")
    check("暴力切换后仍在播放最后一个场景", m["demo_enabled"] and m["demo_scenario"] == "demo_active")
    post("/api/source-mode/demo", {"enabled": False, "scenario": ""})
except Exception as e:
    check(f"暴力切换抛异常: {e}", False)

print("== 3. 并发 GET ×40（4 接口混打） ==")
paths = ["/api/status", "/api/events?limit=20", "/api/device/status", "/api/source-mode"]


def _safe_get(path):
    try:
        get(path)
        return True
    except Exception:
        return False


with ThreadPoolExecutor(max_workers=10) as ex:
    results = list(ex.map(_safe_get, [paths[i % 4] for i in range(40)]))
check("40 并发请求全部 200", all(results))

print("== 4. 真实接入空载（无硬件：开启即归零新鲜度，应立刻如实报断开，不崩） ==")
try:
    post("/api/source-mode/real", {"enabled": True})
    time.sleep(1)
    s = get("/api/device/status")
    check("开启真实接入后立刻如实报告 disconnected（实际 %s）" % s["state"],
          s["state"] == "disconnected")
    post("/api/source-mode/real", {"enabled": False})
    check("真实接入可正常关闭", True)
except Exception as e:
    check(f"真实接入空载抛异常: {e}", False)

print("== 5. 压测后健康检查：完整播放 demo_rest ==")
post("/api/reset")
post("/api/source-mode/demo", {"enabled": True, "scenario": "demo_rest"})
time.sleep(3)
s = get("/api/device/status")
check("播放中设备状态仍常驻 connected（实际 %s）" % s["state"], s["state"] == "connected")
time.sleep(13)  # demo_rest 墙钟约 14s
m = get("/api/source-mode")
check("播完自动关闭（source_mode 同步生效）", not m["demo_enabled"])
st = get("/api/status")
check("终态语义为休息中（实际 %s）" % st["semantic_state"], st["semantic_state"] == "rest")

print("\n========================================")
print(f"压测结果：{PASS} 通过 / {FAIL} 失败")
sys.exit(1 if FAIL else 0)
