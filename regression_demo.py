"""5 演示场景回放回归（对着运行中的边缘服务跑全链路）。

用法：python regression_demo.py [base_url]   （默认 http://127.0.0.1:8000）

断言线（对应计划第七节）：
- demo_absent      无人零断言零告警
- demo_active      有人·活动中，无告警
- demo_rest        有人·休息中，无告警
- demo_fall_moving 双证据→告警→requery→两轮无人应答→危机（黑区）
- demo_fall_still  双证据→告警→运动恢复→EVT_FALL_RECOVERED 自动解除归绿
"""
from __future__ import annotations

import json
import sys
import time
import urllib.request

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000").rstrip("/")

# Windows 控制台 GBK → UTF-8
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

SCENARIOS = {
    "demo_absent": {"dur": 80, "speed": 10},
    "demo_active": {"dur": 120, "speed": 10},
    "demo_rest": {"dur": 140, "speed": 10},
    "demo_fall_moving": {"dur": 213, "speed": 30},
    "demo_fall_still": {"dur": 118, "speed": 30},
}


def req(method: str, path: str, body: dict | None = None) -> dict | list:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    r = urllib.request.Request(BASE + path, data=data, method=method,
                               headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(r, timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))


def run_scenario(name: str) -> tuple[list[dict], dict]:
    """重置 → 播放场景 → 等待播完 → 返回（事件列表, 终态 status）。"""
    req("POST", "/api/reset")
    req("POST", "/api/source-mode/demo", {"enabled": True, "scenario": name})
    wait = SCENARIOS[name]["dur"] / SCENARIOS[name]["speed"] + 3
    time.sleep(wait)
    events = req("GET", "/api/events?limit=100")
    status = req("GET", "/api/status")
    return events, status


def types(events: list[dict]) -> list[str]:
    return [e.get("type", "") for e in events]


FAIL = 0


def check(cond: bool, msg: str) -> None:
    global FAIL
    print(f"  {'PASS' if cond else 'FAIL'}  {msg}")
    if not cond:
        FAIL += 1


def main() -> None:
    # ---- 1 无人：零断言零告警 ----
    print("\n▶ demo_absent 无人·环境安静")
    ev, st = run_scenario("demo_absent")
    alarm_types = {"fall_breathing_bad", "breathing_lost", "breathing_abnormal",
                   "voice_requery", "fall_recovered"}
    check(not (alarm_types & set(types(ev))), "全程无告警类事件")
    check(st.get("present") is False, "终态无人（present=False）")

    # ---- 2 有人·活动中 ----
    print("\n▶ demo_active 有人·活动中")
    ev, st = run_scenario("demo_active")
    check(not (alarm_types & set(types(ev))), "全程无告警类事件")
    check(st.get("present") is True, "终态有人")
    check(st.get("semantic_state") == "active", f"终态语义=活动中（实际 {st.get('semantic_state')}）")

    # ---- 3 有人·休息中 ----
    print("\n▶ demo_rest 有人·休息中")
    ev, st = run_scenario("demo_rest")
    check(not (alarm_types & set(types(ev))), "全程无告警类事件")
    check(st.get("present") is True, "终态有人")
    check(st.get("semantic_state") == "rest", f"终态语义=休息中（实际 {st.get('semantic_state')}）")

    # ---- 4 移动中跌倒：危机全链 ----
    print("\n▶ demo_fall_moving 疑似跌倒·移动中（危机全链）")
    ev, st = run_scenario("demo_fall_moving")
    ts = types(ev)
    check("fall_breathing_bad" in ts, "双证据线成立告警")
    check("voice_requery" in ts, "第一轮无回应 → 第二轮询问事件")
    crisis = [e for e in ev if e.get("type") == "fall_breathing_bad"
              and "两轮询问无人应答" in json.dumps(e.get("features", {}), ensure_ascii=False)]
    check(bool(crisis), "第二轮超时 → 危机事件（两轮询问无人应答）")
    check(st.get("guard_zone", 0) >= 4, f"终态守护等级=危机区（实际 {st.get('guard_zone')}）")
    logs = req("GET", "/api/system-logs")
    log_text = json.dumps(logs, ensure_ascii=False)
    check("第二轮" in log_text or "requery" in log_text.lower() or "确证" in log_text,
          "system-logs 有两轮确证记录")
    # ack 接口（响铃直到知晓）
    ack = req("POST", "/api/alert-ack")
    check(ack.get("ok") is True, "POST /api/alert-ack 已知晓接口可用")

    # ---- 5 静止中跌倒：恢复自解除 ----
    print("\n▶ demo_fall_still 疑似跌倒·静止中（恢复自解除）")
    ev, st = run_scenario("demo_fall_still")
    ts = types(ev)
    check("fall_breathing_bad" in ts, "双证据线成立告警")
    check("fall_recovered" in ts, "运动恢复均速且气息平稳 → 自动解除事件")
    check(st.get("guard_zone", 9) <= 1, f"终态守护等级归绿（实际 {st.get('guard_zone')}）")
    check(st.get("semantic_state") in ("active", "rest"),
          f"终态语义恢复正常（实际 {st.get('semantic_state')}）")

    print(f"\n{'='*40}\n回归结果：{'全部通过 ✔' if FAIL == 0 else f'{FAIL} 项失败 ✘'}")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
