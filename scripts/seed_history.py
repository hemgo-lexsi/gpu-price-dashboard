#!/usr/bin/env python3
"""
One-off: seed ~1 month of *simulated* price-trend history so the dashboard's
trend chart is populated before enough real snapshots have accrued.

Every seeded point is marked  "seed": true  and the dashboard renders the
seeded stretch as a dashed/lighter line labelled "simulated", so it is never
mistaken for real observed data. Real snapshots (appended by fetch_prices.py
every ~30 min) take over from now on, and the seed rolls off as the capped
history window slides forward.

Values are a gentle mean-reverting random walk anchored to the *current* cheapest
price per GPU (from data/prices.json), so the seed connects smoothly to live data.
"""

import json
import os
import random
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA = os.path.join(ROOT, "data")
PRICES_JSON = os.path.join(DATA, "prices.json")
HISTORY_JSON = os.path.join(DATA, "history.json")
HISTORY_JS = os.path.join(DATA, "history.js")

DAYS = 30
STEP_HOURS = 2            # 2-hour spacing -> ~360 points/month (keeps the file light)
VOL = 0.022              # per-step volatility
REVERT = 0.02            # pull back toward the anchor each step


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def walk(anchor, n):
    """Mean-reverting random walk of length n, ending exactly at `anchor`."""
    x = anchor * (1 + random.uniform(-0.10, 0.10))
    out = []
    for _ in range(n):
        x += (anchor - x) * REVERT + random.gauss(0, anchor * VOL)
        x = max(anchor * 0.6, min(anchor * 1.7, x))
        out.append(round(x, 2))
    out[-1] = anchor  # connect smoothly to "now"
    return out


def main():
    with open(PRICES_JSON, "r", encoding="utf-8") as f:
        prices = json.load(f)
    anchors = {k: v["inr_per_hr"] for k, v in prices.get("cheapest_by_gpu", {}).items() if v.get("inr_per_hr")}
    fx_now = round((prices.get("fx", {}).get("rates", {}) or {}).get("USD", 95.7), 3)
    if not anchors:
        raise SystemExit("no cheapest_by_gpu anchors in prices.json — run fetch_prices.py first")

    # Existing real (non-seed) points to preserve as the live tail.
    try:
        with open(HISTORY_JSON, "r", encoding="utf-8") as f:
            existing = [p for p in json.load(f) if not p.get("seed")]
    except Exception:
        existing = []

    now = datetime.now(timezone.utc)
    # Seed ends just before the earliest real point (or now) to avoid overlap.
    end = now
    if existing:
        try:
            end = min(end, datetime.strptime(existing[0]["ts"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
                      - timedelta(hours=STEP_HOURS))
        except Exception:
            pass
    n = int(DAYS * 24 / STEP_HOURS)
    times = [end - timedelta(hours=STEP_HOURS * (n - 1 - i)) for i in range(n)]

    series = {g: walk(a, n) for g, a in anchors.items()}
    fx_series = walk(fx_now, n)

    seeded = []
    for i, t in enumerate(times):
        seeded.append({
            "ts": iso(t),
            "usd_inr": round(fx_series[i], 3),
            "min_by_gpu": {g: series[g][i] for g in anchors},
            "seed": True,
        })

    history = seeded + existing
    with open(HISTORY_JSON, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, separators=(",", ":"))
    with open(HISTORY_JS, "w", encoding="utf-8") as f:
        f.write("window.__GPU_HISTORY__ = ")
        json.dump(history, f, ensure_ascii=False, separators=(",", ":"))
        f.write(";\n")

    print(f"Seeded {len(seeded)} simulated points over {DAYS} days "
          f"({len(anchors)} GPUs), + {len(existing)} real points. Total {len(history)}.")


if __name__ == "__main__":
    main()
