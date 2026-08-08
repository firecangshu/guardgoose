"""一次性归档：把过往真机测试数据统一收进 test_data_db/legacy。

收录对象：
  ① 原始 CSI 帧 CSV（测试数据/ 三个 + waveguard/test_data_realtime.csv）：
     用与桥接器同款的 signal_proc 管线离线重算逐秒指标（强度/呼吸/锁频/底噪），
     落 per-second CSV + summary.json + meta.json（原始文件只引用不复制）。
  ② experiment_test.py 的实验 JSON（test_results/*.json）：
     展平为逐秒 CSV（带 phase 标签）+ summary.json + meta.json。

用法：cd waveguard; python tools/import_legacy_tests.py
幂等：已归档过的（meta.json 存在）自动跳过。
"""
from __future__ import annotations

import csv
import json
import statistics
import sys
from datetime import datetime
from pathlib import Path

# Windows 控制台 GBK：✓/⚠ 等符号会编码崩溃，统一 UTF-8
for _stream in (sys.stdout, sys.stderr):
    if _stream is not None:
        try:
            _stream.reconfigure(encoding="utf-8")
        except (ValueError, OSError):
            pass

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from hw.csi_parser import parse_csi_line                      # noqa: E402
from hw.signal_proc import (                                   # noqa: E402
    ActivityEstimator, BreathDetector, NoiseFloorEstimator,
    classify_breathing, ACTIVE_MIN, BR_WINDOW_S,
)

LEGACY_DIR = ROOT / "test_data_db" / "legacy"
LOCK_GATE = 13   # 共识门：32 条子载波 ≥40%

# (原始路径, 归档目录名, 备注)
RAW_SOURCES = [
    (ROOT.parent / "测试数据" / "csi_data.csv",
     "20260731_第一次测试", "首轮真机采集（站立/走动/跌倒混场）"),
    (ROOT.parent / "测试数据" / "第二次测试csi_data.csv",
     "20260731_第二次测试", "第二轮真机采集"),
    (ROOT.parent / "测试数据" / "第三次测试.csv",
     "20260731_第三次测试", "第三轮真机采集"),
    (ROOT / "test_data_realtime.csv",
     "20260806_实时联调", "状态机 v2 联调期实时采集"),
]


def _summarize(rows: list[dict]) -> dict:
    def _f(v, default=0.0):
        try:
            return float(v)
        except (ValueError, TypeError):
            return default

    locked = [_f(r.get("br_locked")) for r in rows]
    intens = [_f(r.get("intensity")) for r in rows]
    motion = [_f(r.get("br_motion_ratio")) for r in rows]
    fps_l = [_f(r.get("fps")) for r in rows]
    rates = [_f(r.get("br_rate")) for r in rows]
    n = max(1, len(rows))
    return {
        "duration_s": len(rows),
        "fps_avg": round(statistics.mean(fps_l), 1) if fps_l else 0.0,
        "noise_floor_last": _f(rows[-1].get("noise_floor")),
        "br_lock_rate": round(sum(1 for v in locked if v >= LOCK_GATE) / n, 3),
        "br_rate_avg": round(statistics.mean(rates), 1) if rates else 0.0,
        "intensity_p90": round(sorted(intens)[int(len(intens) * 0.9) - 1], 4)
        if intens else 0.0,
        "motion_ratio_avg": round(statistics.mean(motion), 3) if motion else 0.0,
        "note": "",
    }


def import_raw_csi(src: Path, name: str, note: str) -> None:
    dst = LEGACY_DIR / name
    if (dst / "meta.json").exists():
        print(f"  跳过 {name}（已归档）")
        return
    dst.mkdir(parents=True, exist_ok=True)

    activity, breath, noise = ActivityEstimator(), BreathDetector(), NoiseFloorEstimator()
    noise_floor_track = noise
    fps_ema = breath.fs
    rows: list[dict] = []
    win_frames = 0
    sim_t = 0
    last_rssi = 0
    intensity_hist: list[float] = []

    with open(src, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line.startswith("CSI_DATA"):
                continue
            frame = parse_csi_line(line)
            if frame is None:
                continue
            last_rssi = frame.rssi
            win_frames += 1
            activity.add_frame(frame.amp)
            breath.add_frame(frame.amp)
            # 按固件标称 72fps 切 1 秒窗（原始帧无时间戳，回放节奏同 bridge）
            if win_frames < 72:
                continue
            fps = float(win_frames)
            win_frames = 0
            sim_t += 1
            fps_ema = 0.7 * fps_ema + 0.3 * fps
            breath.fs = max(20.0, min(100.0, fps_ema))

            intensity = activity.flush()
            if intensity is not None:
                intensity_hist.append(intensity)
                intensity_hist = intensity_hist[-int(BR_WINDOW_S):]
                noise.feed(intensity)
            rate, _consensus, locked = breath.detect(
                significance_min=noise.significance_min)
            motion_ratio = (sum(1 for v in intensity_hist if v >= ACTIVE_MIN)
                            / len(intensity_hist)) if intensity_hist else 0.0
            if ((intensity is not None and intensity >= ACTIVE_MIN)
                    or motion_ratio > 0.20):
                rate, locked = None, 0.0
            state = classify_breathing(rate)
            rows.append({
                "ts": "", "sim_t": sim_t,
                "intensity": f"{intensity:.4f}" if intensity is not None else "",
                "br_rate": int(rate) if rate is not None else 0,
                "br_state": state or "",
                "br_locked": f"{locked or 0:.0f}",
                "br_motion_ratio": f"{motion_ratio:.3f}",
                "noise_floor": f"{noise_floor_track.floor:.4f}",
                "rssi": last_rssi, "fps": f"{fps:.1f}",
            })

    if not rows:
        print(f"  ⚠️ {src.name} 解析不出 CSI 帧，仅建索引")
    csv_path = dst / f"{name}_persecond.csv"
    with open(csv_path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["ts", "sim_t", "intensity", "br_rate",
                                          "br_state", "br_locked", "br_motion_ratio",
                                          "noise_floor", "rssi", "fps"])
        w.writeheader()
        w.writerows(rows)
    summary = _summarize(rows)
    (dst / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    meta = {
        "name": name, "kind": "raw_csi",
        "source": str(src), "source_mtime":
            datetime.fromtimestamp(src.stat().st_mtime).isoformat(timespec="seconds"),
        "note": note,
        "imported_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "persecond_csv": csv_path.name,
    }
    (dst / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  ✓ {name}: {len(rows)}s 逐秒指标 | 锁频率 "
          f"{summary['br_lock_rate']:.0%} | fps {summary['fps_avg']}")


def import_experiment_json(src: Path) -> None:
    data = json.loads(src.read_text(encoding="utf-8"))
    stamp = data.get("timestamp", src.stem)
    name = f"{stamp}_控制变量实验"
    dst = LEGACY_DIR / name
    if (dst / "meta.json").exists():
        print(f"  跳过 {name}（已归档）")
        return
    dst.mkdir(parents=True, exist_ok=True)

    rows: list[dict] = []
    round_stats: dict[str, dict] = {}
    for exp_key, phases in (data.get("results") or {}).items():
        for phase_name, samples in phases.items():
            for s in samples:
                rows.append({
                    "ts": "", "sim_t": s.get("t", ""),
                    "phase": f"{exp_key}/{phase_name}",
                    "intensity": s.get("intensity", ""),
                    "br_rate": s.get("breath_rate") or 0,
                    "br_state": s.get("breath_state") or "",
                    "br_locked": f"{s.get('breath_locked') or 0:.0f}",
                    "br_motion_ratio": "",
                    "noise_floor": "",
                    "rssi": s.get("rssi", ""), "fps": s.get("fps", ""),
                })
        # 每轮汇总（供找规律：哪类实验锁频/强度表现如何）
        flat = [s for ph in phases.values() for s in ph]
        locked_l = [s.get("breath_locked") or 0 for s in flat]
        intens_l = [s.get("intensity") or 0 for s in flat]
        round_stats[exp_key] = {
            "n_s": len(flat),
            "fps_avg": round(statistics.mean([s.get("fps") or 0 for s in flat]), 1)
            if flat else 0.0,
            "br_lock_rate": round(sum(1 for v in locked_l if v >= LOCK_GATE)
                                  / max(1, len(flat)), 3),
            "intensity_p90": round(sorted(intens_l)[max(0, int(len(intens_l) * 0.9) - 1)], 4)
            if intens_l else 0.0,
        }

    csv_path = dst / f"{name}_persecond.csv"
    with open(csv_path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["ts", "sim_t", "phase", "intensity",
                                          "br_rate", "br_state", "br_locked",
                                          "br_motion_ratio", "noise_floor",
                                          "rssi", "fps"])
        w.writeheader()
        w.writerows(rows)
    summary = _summarize(rows)
    summary["rounds"] = round_stats
    (dst / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    meta = {
        "name": name, "kind": "experiment",
        "source": str(src),
        "note": "F1-F4 跌倒 / B1-B4 呼吸控制变量实验（experiment_test.py）",
        "imported_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "persecond_csv": csv_path.name,
    }
    (dst / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  ✓ {name}: {len(rows)} 样本 | {len(round_stats)} 轮实验")


def main() -> None:
    LEGACY_DIR.mkdir(parents=True, exist_ok=True)
    print("[归档] 原始 CSI 帧 CSV → 离线重算逐秒指标")
    for src, name, note in RAW_SOURCES:
        if not src.exists():
            print(f"  ⚠️ 缺失 {src}")
            continue
        import_raw_csi(src, name, note)
    print("[归档] 实验 JSON → 展平入库")
    for src in sorted((ROOT / "test_results").glob("experiment_*.json")):
        import_experiment_json(src)
    print(f"[完成] 测试数据库 → {ROOT / 'test_data_db'}")


if __name__ == "__main__":
    main()
