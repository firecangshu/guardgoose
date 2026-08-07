# -*- coding: utf-8 -*-
"""/ingest/sample 真实接入闸门验证：未开真实接入拒收 403，开启后放行。"""
import json
import sys
import time
import urllib.error
import urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
P, F = 0, 0

SAMPLE = {"ts": "2026-08-07T15:00:00", "sim_t": 0.0, "intensity": 0.05,
          "zone": "living", "breathing_rate": 15,
          "breathing_state": "normal", "noise_floor": 0.01}


def ck(name, ok):
    global P, F
    P += 1 if ok else 0
    F += 0 if ok else 1
    print(("  PASS  " if ok else "  FAIL  ") + name)


def post(p, payload=None):
    req = urllib.request.Request(BASE + p, method="POST",
                                 data=json.dumps(payload or {}).encode(),
                                 headers={"Content-Type": "application/json"})
    return urllib.request.urlopen(req, timeout=8)


def get(p):
    with urllib.request.urlopen(BASE + p, timeout=8) as r:
        return json.loads(r.read())


def try_sample():
    """推一个样本，返回 HTTP 状态码。"""
    req = urllib.request.Request(BASE + "/ingest/sample", method="POST",
                                 data=json.dumps(SAMPLE).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code


print("== 真实接入闸门验证 ==")
post("/api/reset").read()
post("/api/source-mode/real", {"enabled": False}).read()
post("/api/source-mode/demo", {"enabled": False}).read()
time.sleep(0.3)

code = try_sample()
ck("待机状态推样本被拒收（403，实际 %s）" % code, code == 403)

# 演示开启也不放行（演示轨走进程内直注，不吃外部样本）
post("/api/source-mode/demo", {"enabled": True, "scenario": "demo_rest"}).read()
time.sleep(0.5)
code = try_sample()
ck("仅开演示接入时推样本仍被拒收（403，实际 %s）" % code, code == 403)
post("/api/source-mode/demo", {"enabled": False}).read()
time.sleep(0.3)

post("/api/source-mode/real", {"enabled": True}).read()
time.sleep(0.3)
code = try_sample()
ck("开启真实接入后样本放行（200，实际 %s）" % code, code == 200)
ds = get("/api/device/status")
ck("样本到达后设备状态转接通（实际 %s）" % ds["state"], ds["state"] == "connected")

post("/api/source-mode/real", {"enabled": False}).read()
time.sleep(0.3)
code = try_sample()
ck("关闭真实接入后样本再次被拒收（403，实际 %s）" % code, code == 403)

post("/api/reset").read()
print(f"\n闸门验证结果：{P} PASS / {F} FAIL")
sys.exit(1 if F else 0)
