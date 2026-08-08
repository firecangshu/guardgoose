"""测试数据库规律分析：扫描 sessions/ 与 legacy/ 全部会话，生成 report.md。

横向对比维度：锁频率 vs 帧率/底噪/运动比/RSSI，强度分布，呼吸率命中。
用法：cd waveguard; python tools/analyze_test_db.py
"""
from __future__ import annotations

import csv
import json
import sys
from datetime import datetime
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    if _stream is not None:
        try:
            _stream.reconfigure(encoding="utf-8")
        except (ValueError, OSError):
            pass

DB = Path(__file__).resolve().parent.parent / "test_data_db"
LOCK_GATE = 13


def _f(v, default=0.0):
    try:
        return float(v)
    except (ValueError, TypeError):
        return default


def scan_dir(d: Path) -> list[dict]:
    """每个会话目录 → 一行统计。优先读 summary.json，缺失则现算。"""
    out = []
    if not d.exists():
        return out
    for sub in sorted(d.iterdir()) if d.name == "legacy" else [d]:
        if d.name == "sessions":
            # sessions 下是散文件，按 summary.json 聚合
            break
        if not sub.is_dir():
            continue
        meta_p, sum_p = sub / "meta.json", sub / "summary.json"
        meta = json.loads(meta_p.read_text(encoding="utf-8")) if meta_p.exists() else {}
        s = json.loads(sum_p.read_text(encoding="utf-8")) if sum_p.exists() else {}
        out.append({
            "name": sub.name, "kind": meta.get("kind", "legacy"),
            "duration_s": s.get("duration_s", 0),
            "fps_avg": s.get("fps_avg", 0),
            "noise_floor": s.get("noise_floor_last", 0),
            "br_lock_rate": s.get("br_lock_rate", 0),
            "br_rate_avg": s.get("br_rate_avg", 0),
            "intensity_p90": s.get("intensity_p90", 0),
            "motion_ratio_avg": s.get("motion_ratio_avg", 0),
            "note": s.get("note", ""),
            "rounds": s.get("rounds"),
        })
    return out


def scan_sessions() -> list[dict]:
    out = []
    sd = DB / "sessions"
    if not sd.exists():
        return out
    for p in sorted(sd.glob("*.summary.json")):
        s = json.loads(p.read_text(encoding="utf-8"))
        out.append({
            "name": s.get("label", p.stem), "kind": "session",
            "duration_s": s.get("duration_s", 0),
            "fps_avg": s.get("fps_avg", 0),
            "noise_floor": s.get("noise_floor_last", 0),
            "br_lock_rate": s.get("br_lock_rate", 0),
            "br_rate_avg": s.get("br_rate_avg", 0),
            "intensity_p90": s.get("intensity_p90", 0),
            "motion_ratio_avg": s.get("motion_ratio_avg", 0),
            "note": s.get("note", ""),
            "rounds": None,
        })
    return out


def main() -> None:
    rows = scan_dir(DB / "legacy") + scan_sessions()
    if not rows:
        print("库为空，无报告可生成")
        return

    lines = ["# 真机测试数据库 · 规律分析报告",
             "",
             f"生成时间：{datetime.now().astimezone().isoformat(timespec='seconds')} ｜ "
             f"会话数：{len(rows)}",
             "",
             "## 全库一览（按时间）",
             "",
             "| 会话 | 时长s | 帧率 | 底噪 | 锁频率 | 呼吸率 | 强度P90 | 运动比 | 备注 |",
             "|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        lines.append(
            f"| {r['name']} | {r['duration_s']} | {r['fps_avg']:.0f} | "
            f"{r['noise_floor']:.3f} | {r['br_lock_rate']:.0%} | "
            f"{r['br_rate_avg']:.0f} | {r['intensity_p90']:.3f} | "
            f"{r['motion_ratio_avg']:.2f} | {r['note'] or '--'} |")

    # 规律提炼：按锁频率分档看各指标
    locked_hi = [r for r in rows if r["br_lock_rate"] >= 0.5]
    locked_lo = [r for r in rows if r["br_lock_rate"] < 0.5]

    def avg(lst, key):
        vs = [r[key] for r in lst]
        return sum(vs) / len(vs) if vs else 0.0

    lines += ["", "## 规律观察（锁频率 ≥50% vs <50% 对照）", ""]
    if locked_hi and locked_lo:
        lines += [
            "| 指标 | 锁频好（≥50%） | 锁频差（<50%） | 差异提示 |",
            "|---|---|---|---|",
            f"| 会话数 | {len(locked_hi)} | {len(locked_lo)} | -- |",
            f"| 平均帧率 | {avg(locked_hi,'fps_avg'):.0f} | {avg(locked_lo,'fps_avg'):.0f} | "
            f"{'帧率影响锁频' if abs(avg(locked_hi,'fps_avg')-avg(locked_lo,'fps_avg'))>10 else '帧率非主因'} |",
            f"| 平均底噪 | {avg(locked_hi,'noise_floor'):.3f} | {avg(locked_lo,'noise_floor'):.3f} | "
            f"{'底噪影响锁频' if avg(locked_lo,'noise_floor')>avg(locked_hi,'noise_floor')*1.5 else '底噪非主因'} |",
            f"| 平均运动比 | {avg(locked_hi,'motion_ratio_avg'):.2f} | {avg(locked_lo,'motion_ratio_avg'):.2f} | "
            f"{'运动污染显著' if avg(locked_lo,'motion_ratio_avg')>avg(locked_hi,'motion_ratio_avg')*1.5 else '运动比非主因'} |",
        ]
    else:
        lines.append("（样本不足以分组对照——继续积累会话）")

    # 实验轮次明细（控制变量实验）
    exp_rows = [r for r in rows if r.get("rounds")]
    if exp_rows:
        lines += ["", "## 控制变量实验轮次明细", "",
                  "| 会话 | 轮次 | 样本s | 帧率 | 锁频率 | 强度P90 |",
                  "|---|---|---|---|---|---|"]
        for r in exp_rows:
            for k, v in r["rounds"].items():
                lines.append(f"| {r['name']} | {k} | {v.get('n_s',0)} | "
                             f"{v.get('fps_avg',0):.0f} | {v.get('br_lock_rate',0):.0%} | "
                             f"{v.get('intensity_p90',0):.3f} |")

    lines += ["", "---",
              "说明：锁频率 = 窗口内共识子载波 ≥13/32（40%门）的秒数占比。",
              "note 字段由各会话测试者手工补充（几何摆放/动作/结论），是找规律的关键上下文。"]
    report = DB / "report.md"
    report.write_text("\n".join(lines), encoding="utf-8")
    print(f"[分析完成] {report}")
    for r in rows:
        print(f"  {r['name']}: 锁频率 {r['br_lock_rate']:.0%} | 帧率 {r['fps_avg']:.0f}")


if __name__ == "__main__":
    main()
