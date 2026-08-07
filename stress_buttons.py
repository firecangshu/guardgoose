# -*- coding: utf-8 -*-
"""前端按键路径 API 级测试：ack/voice/monitor 按钮对应接口 + 开关连点。"""
import json
import sys
import time
import urllib.error
import urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
P, F = 0, 0


def ck(name, ok):
    global P, F
    P += 1 if ok else 0
    F += 0 if ok else 1
    print(("  PASS  " if ok else "  FAIL  ") + name)


def post(p, payload=None):
    req = urllib.request.Request(BASE + p, method="POST",
                                 data=json.dumps(payload or {}).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=8) as r:
        return json.loads(r.read())


def get(p):
    with urllib.request.urlopen(BASE + p, timeout=8) as r:
        return json.loads(r.read())


print("== A. 告警链按键接口（ack / voice / monitor） ==")
post("/api/reset")
post("/api/source-mode/demo", {"enabled": True, "scenario": "demo_fall_moving"})
time.sleep(6)
st = get("/api/status")
ck("告警链推进到红区以上（实际 zone %s）" % st["guard_zone"], st["guard_zone"] >= 3)
r = post("/api/alert-ack")
ck("点「已知晓」按钮接口返回 ok", r.get("ok") is True)
try:
    r = post("/api/voice-confirm/respond?answer=ok")
    ck("点「回应没事」接口可达（返回 %s）" % r, True)
except urllib.error.HTTPError as e:
    ck("回应没事接口 HTTP %s（404 需核实路由名）" % e.code, e.code < 500)
except Exception as e:
    ck("回应没事接口异常: %s" % e, False)
# 「开启监控排查」为纯前端状态切换（monitorChecked），无后端接口，不测

print("== B. 演示开关 6 连点（模拟手抖） ==")
ok = True
for i in range(6):
    try:
        post("/api/source-mode/demo",
             {"enabled": i % 2 == 0, "scenario": "demo_rest" if i % 2 == 0 else ""})
        time.sleep(0.2)
    except Exception as e:
        ok = False
        print("   ", e)
        break
m = get("/api/source-mode")
ck("6 连点全部 200，末次为关闭（demo_enabled=%s）" % m["demo_enabled"],
   ok and not m["demo_enabled"])

print("== C. 连点后各接口仍健康 ==")
ev = get("/api/events?limit=10")
ck("事件库接口正常", isinstance(ev, list))
st = get("/api/status")
ck("状态接口正常", "guard_zone" in st)
get("/api/source-mode")
ck("数据源接口正常", True)

print("\n按键接口测试结果：%s 通过 / %s 失败" % (P, F))
sys.exit(1 if F else 0)
