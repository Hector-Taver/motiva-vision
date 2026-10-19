import argparse
import csv
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "vision"))
from detector import process  # noqa: E402

ACCIDENTS = {"TRASEIRA", "LATERAL", "CAPOTAMENTO"}


def predicted_from(events):
    """Final label of the FIRST accident (updates keep the same event_id)."""
    if not events:
        return "SEM_ACIDENTE", None, 0
    first_id = events[0]["event_id"]
    last = [e for e in events if e["event_id"] == first_id][-1]
    n_ids = len({e["event_id"] for e in events})
    return last["tipo"], last, n_ids


def outcome(expected, pred):
    if expected == "SEM_ACIDENTE":
        return {"SEM_ACIDENTE": "TN"}.get(pred, "INDETERMINADO" if pred == "INDETERMINADO" else "FP")
    if expected in ACCIDENTS:
        if pred == expected:
            return "TP"
        if pred == "SEM_ACIDENTE":
            return "FN"
        if pred == "INDETERMINADO":
            return "INDETERMINADO"
        return "TIPO_ERRADO"
    if expected == "INDETERMINADO":  # ambiguous clips: honest uncertainty is the goal
        if pred == "INDETERMINADO":
            return "OK_INCERTEZA"
        return "SEM_ACIDENTE" if pred == "SEM_ACIDENTE" else "SUPERCONFIANTE"
    return "?"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", choices=["dev", "eval", "all"], default="dev")
    ap.add_argument("--publish", action="store_true", help="also send events over MQTT")
    args = ap.parse_args()

    rows = list(csv.DictReader(open(ROOT / "videos" / "videos.csv", encoding="utf-8")))
    rows = [r for r in rows if args.split == "all" or r["split"] == args.split]
    results = []
    for r in rows:
        path = ROOT / "videos" / r["file"]
        print(f"\n=== {r['file']} (expected {r['expected']}) ===")
        t0 = time.time()
        events = process(str(path), show=False, publish=args.publish)
        pred, ev, n_ids = predicted_from(events)
        results.append({
            "file": r["file"], "split": r["split"], "expected": r["expected"],
            "predicted": pred, "outcome": outcome(r["expected"], pred),
            "confianca": ev["confianca"] if ev else "",
            "segundo": ev["evidencia"]["segundo"] if ev else "",
            "event_id": ev["event_id"] if ev else "",
            "frame": ev["evidencia"]["frame"] if ev else "",
            "n_event_ids": n_ids,  # should be 1 for one accident (duplicate control)
            "proc_seconds": round(time.time() - t0, 1),
        })

    out = ROOT / "tests" / f"results_{args.split}.csv"
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(results[0]))
        w.writeheader()
        w.writerows(results)
    print(f"\nWrote {out}")
    print(Counter(r["outcome"] for r in results))


if __name__ == "__main__":
    main()
