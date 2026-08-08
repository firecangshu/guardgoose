"""4 完整剧本回放回归（对着运行中的边缘服务跑全链路）。

用法：python regression_demo.py [base_url]   （默认 http://127.0.0.1:8000）

断言线：
- demo_rest        休憩中：全程无告警，终态休憩
- demo_active      活动中：全程无告警，终态活动
- demo_fall_still  确认无碍·解除警报：双证据告警→剧本自动回应 ok→解除归绿
- demo_fall_moving 风险增加·救护链：双证据告警（呼吸紊乱每轮随机：
                   急促/节律紊乱/减弱骤减）→剧本自动回应 help→红区及以上

注意：剧本循环播放，回归在首轮播完后关闭演示停表再断言终态；
呼唤确证为急救导向 30s/15s，剧本内老人 8s 即回应，全部剧本一分半内走完。
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
    "demo_rest": {"dur": 60, "speed": 1},
    "demo_active": {"dur": 60, "speed": 1},
    "demo_fall_still": {"dur": 60, "speed": 1},
    "demo_fall_moving": {"dur": 53, "speed": 1},
}


def req(method: str, path: str, body: dict | None = None) -> dict | list:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    r = urllib.request.Request(BASE + path, data=data, method=method,
                               headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(r, timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))


def run_scenario(name: str) -> tuple[list[dict], dict]:
    """重置 → 播放场景 → 在首轮末段快照（剧本循环播放，跨过首轮边界状态机会归零，
    终态不可断言）→ 关演示停表 → 返回（事件列表, 终态 status）。"""
    req("POST", "/api/reset")
    req("POST", "/api/source-mode/demo", {"enabled": True, "scenario": name})
    wait = SCENARIOS[name]["dur"] - 5  # 首轮倒数第5秒快照：结局已落定且未进第二轮
    time.sleep(wait)
    events = req("GET", "/api/events?limit=100")
    req("POST", "/api/source-mode/demo", {"enabled": False, "scenario": name})
    time.sleep(1)
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
    alarm_types = {"fall_breathing_bad", "breathing_lost", "breathing_abnormal",
                   "voice_requery", "fall_recovered"}

    # ---- 1 休憩中 ----
    print("\n▶ demo_rest 休憩中")
    ev, st = run_scenario("demo_rest")
    check(not (alarm_types & set(types(ev))), "全程无告警类事件")
    check(st.get("present") is True, "终态有人")
    check(st.get("semantic_state") == "rest", f"终态语义=休息中（实际 {st.get('semantic_state')}）")

    # ---- 2 活动中 ----
    print("\n▶ demo_active 活动中")
    ev, st = run_scenario("demo_active")
    check(not (alarm_types & set(types(ev))), "全程无告警类事件")
    check(st.get("present") is True, "终态有人")
    check(st.get("semantic_state") == "active", f"终态语义=活动中（实际 {st.get('semantic_state')}）")

    # ---- 3 确认无碍·解除警报：剧本自动回应 ok ----
    print("\n▶ demo_fall_still 疑似跌倒·确认无碍·解除警报")
    ev, st = run_scenario("demo_fall_still")
    ts = types(ev)
    check("fall_breathing_bad" in ts, "剧烈形变+呼吸急促 → 双证据线成立告警")
    check(st.get("guard_zone", 9) <= 1, f"老人回应没事 → 警报解除归绿（实际 {st.get('guard_zone')}）")

    # ---- 4 风险增加·救护链：随机呼吸紊乱 + 剧本自动回应 help ----
    print("\n▶ demo_fall_moving 疑似跌倒·风险增加·进入救护链（呼吸紊乱随机）")
    ev, st = run_scenario("demo_fall_moving")
    ts = types(ev)
    check("fall_breathing_bad" in ts, "剧烈形变+呼吸紊乱（本轮随机抽中）→ 双证据线成立告警")
    check(st.get("guard_zone", 0) >= 3, f"侦测到呼救 → 风险增加终态红区及以上（实际 {st.get('guard_zone')}）")
    logs = req("GET", "/api/system-logs")
    log_text = json.dumps(logs, ensure_ascii=False)
    check("随机演示" in log_text, "system-logs 有本轮随机呼吸紊乱记录")
    # ack 接口（响铃直到知晓）
    ack = req("POST", "/api/alert-ack")
    check(ack.get("ok") is True, "POST /api/alert-ack 已知晓接口可用")

    print(f"\n{'='*40}\n回归结果：{'全部通过 ✔' if FAIL == 0 else f'{FAIL} 项失败 ✘'}")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
