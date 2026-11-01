"""Ham çıktılardan Markdown tabloları (rapor yazımı için; rakamlar elle kopyalanmaz).
  python tools/reliability/report.py stress <çıktı-klasörü>
  python tools/reliability/report.py failures <çıktı-klasörü>"""
import json
import sys
from pathlib import Path


def f(v, nd=1):
    if v is None:
        return "–"
    if isinstance(v, float):
        return f"{v:.{nd}f}"
    return str(v)


def stress(d: Path):
    rows = json.loads((d / "matrix.json").read_text(encoding="utf-8"))
    print("| Kamera | Stride | Ölçüm (sn) | Kamera | Kaynak FPS | Gereken FPS | Etkin FPS p50 (p05) | Lag p50/p95/max (sn) | "
          "Kare ms p95 | Çıkarım ms p50/p95 (p99 max) | ByteTrack p95 | PPEMonitor p95 | Başlangıç max (sn) |")
    print("|" + "---|" * 13)
    for r in rows:
        if "error" in r:
            print(f"| {r['cameras']} | {r['vid_stride']} | HATA: {r['error'][:80]} |")
            continue
        for cid, c in r["summary"]["cameras"].items():
            src = c.get("source_fps")
            need = round(src / r["vid_stride"], 2) if src else None
            reused = next((s["reused_video"] for s in r["sources"] if s["camera_id"] == cid), False)
            print(f"| {r['cameras']} | {r['vid_stride']} | {r['measured_sec']} | {cid}{' (tekrar video)' if reused else ''} | "
                  f"{f(src)} | {f(need, 2)} | {f(c['proc_fps_p50'], 2)} ({f(c['proc_fps_p05'], 2)}) | "
                  f"{f(c['lag_sec_p50'], 2)}/{f(c['lag_sec_p95'], 2)}/{f(c['lag_sec_max'], 2)} | "
                  f"{f(c['frame_ms_p95_median'])} | {f(c['inf_ms_p50_median'])}/{f(c['inf_ms_p95_median'])} "
                  f"({f(c['inf_ms_p99_max'])}) | {f(c['bytetrack_ms_p95_median'], 2)} | "
                  f"{f(c['monitor_ms_p95_median'], 3)} | {f(c.get('startup_sec_max'))} |")
    print()
    print("| Kamera | Stride | GPU % p50/p95 | GPU bellek MB max | Dedektör CPU % p50 (çok çekirdek) | Dedektör RSS MB max | "
          "Sistem RAM MB max | /api/state ms p50/p95 | DB olay yazım ms p95 | WS kare (probe) | Outbox max bekleyen | "
          "Teslim (outbox→DB eksik) | Olay | Kapanış |")
    print("|" + "---|" * 14)
    for r in rows:
        if "error" in r:
            continue
        s = r["summary"]
        pend = max((c.get("outbox_pending_max") or 0 for c in s["cameras"].values()), default=0)
        print(f"| {r['cameras']} | {r['vid_stride']} | {f(s.get('gpu_util', {}).get('p50'))}/{f(s.get('gpu_util', {}).get('p95'))} | "
              f"{f(s.get('gpu_mem_used_mb', {}).get('max'))} | {f(s.get('det_cpu', {}).get('p50'))} | "
              f"{f(s.get('det_rss_mb', {}).get('max'))} | {f(s.get('sys_mem_used_mb', {}).get('max') if 'sys_mem_used_mb' in s else None)} | "
              f"{f(s.get('state_ms', {}).get('p50'))}/{f(s.get('state_ms', {}).get('p95'))} | "
              f"{f(s['dashboard'].get('db_events_p95_last'))} | {f((s.get('websocket_probe') or {}).get('frames_total'))} | "
              f"{pend} | {r['delivery']['missing_in_db']} | {r['db']['events']} | "
              f"{'zarif' if r['stop']['graceful'] else 'zorla'} {r['stop']['sec']} sn |")


def failures(d: Path):
    res = json.loads((d / "failures.json").read_text(encoding="utf-8"))
    for k, v in res.items():
        items = v if isinstance(v, list) else [v]
        for x in items:
            keep = {kk: vv for kk, vv in x.items() if kk not in ("timeline", "trace")}
            print(f"### {k} — {x.get('scenario')}")
            print("```json")
            print(json.dumps(keep, ensure_ascii=False, indent=1)[:3500])
            print("```")


if __name__ == "__main__":
    {"stress": stress, "failures": failures}[sys.argv[1]](Path(sys.argv[2]))
