#!/usr/bin/env python3
"""
GPU Price Dashboard - data pipeline (stdlib only, no pip installs required).

Fetches:
  * Live FX (USD->INR, EUR->INR) from Frankfurter
  * Live Vast.ai marketplace offers (on-demand + spot) per GPU model
  * Live RunPod pricing (secure + community) for all GPU types
  * Curated list prices from data/curated.json (Indian clouds + hyperscalers)

Normalises everything to INR per SINGLE GPU per hour and writes:
  * data/prices.json / data/prices.js   (current snapshot for the dashboard)
  * data/history.json / data/history.js (append-only trend series)

Design goals: robust (one source failing never kills the run), honest
(everything labelled live vs list), and dependency-free (runs on a bare
Python 3.9+ on any GitHub Actions runner or a laptop).
"""

import json
import os
import ssl
import sys
import time
import urllib.request
import urllib.parse
from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA = os.path.join(ROOT, "data")
PRICES_JSON = os.path.join(DATA, "prices.json")
PRICES_JS = os.path.join(DATA, "prices.js")
HISTORY_JSON = os.path.join(DATA, "history.json")
HISTORY_JS = os.path.join(DATA, "history.js")
CURATED = os.path.join(DATA, "curated.json")

HISTORY_CAP = 3000          # keep at most this many trend snapshots
USER_AGENT = "gpu-price-dashboard/1.0 (+https://github.com/)"
TIMEOUT = 40

# One SSL context reused for all HTTPS calls.
try:
    SSL_CTX = ssl.create_default_context()
except Exception:
    SSL_CTX = None


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def log(msg):
    print(f"[{now_iso()}] {msg}", flush=True)


def http_get(url, headers=None):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})})
    with urllib.request.urlopen(req, timeout=TIMEOUT, context=SSL_CTX) as r:
        return r.read().decode("utf-8", "replace")


def http_post_json(url, payload, headers=None):
    data = json.dumps(payload).encode("utf-8")
    hdr = {"User-Agent": USER_AGENT, "Content-Type": "application/json", **(headers or {})}
    req = urllib.request.Request(url, data=data, headers=hdr, method="POST")
    with urllib.request.urlopen(req, timeout=TIMEOUT, context=SSL_CTX) as r:
        return r.read().decode("utf-8", "replace")


# ---------------------------------------------------------------------------
# GPU normalisation: map many raw names -> a clean family key + display label
# ---------------------------------------------------------------------------
# Family key is what the KPI cards / filters group on. VRAM helps disambiguate.
def normalize_gpu(raw, vram_gb=None):
    """Return (family_key, display_label, vram_gb). family_key groups the KPIs/filters."""
    r = (raw or "").upper().replace("-", " ").replace("_", " ")
    r = " ".join(r.split())

    # Datacenter / Hopper / Blackwell
    if "B200" in r:
        return "B200", "B200", vram_gb or 180
    if "B300" in r:
        return "B300", "B300", vram_gb or 288
    if "H200" in r:
        return "H200", ("H200 NVL" if "NVL" in r else "H200 SXM"), vram_gb or 141
    if "H100" in r:
        variant = ("H100 NVL" if "NVL" in r else "H100 PCIe" if "PCIE" in r or "PCI" in r
                   else "H100 SXM" if "SXM" in r else "H100")
        return "H100", variant, vram_gb or 80
    if "A100" in r:
        mem = vram_gb or (40 if "40" in r else 80)
        return "A100", f"A100 {int(mem)}GB", mem
    if "MI300X" in r:
        return "MI300X", "MI300X", vram_gb or 192
    if "A40" in r:
        return "A40", "A40", vram_gb or 48
    if "A30" in r:
        return "A30", "A30", vram_gb or 24
    if "A10G" in r:
        return "A10G", "A10G", vram_gb or 24
    if "L40S" in r:
        return "L40S", "L40S", vram_gb or 48
    if "L40" in r:
        return "L40", "L40", vram_gb or 48
    if "L4" in r and "L40" not in r:
        return "L4", "L4", vram_gb or 24
    if "V100" in r:
        return "V100", "V100", vram_gb or 16
    # Workstation "6000" cards: Blackwell Pro (96GB) vs Ada (48GB)
    if "6000" in r and ("PRO" in r or "BLACKWELL" in r):
        return "RTX 6000 Pro", "RTX 6000 Pro (Blackwell)", vram_gb or 96
    if "6000" in r and "ADA" in r:
        return "RTX 6000 Ada", "RTX 6000 Ada", vram_gb or 48
    if "A6000" in r:
        return "A6000", "RTX A6000", vram_gb or 48
    # Consumer flagships
    if "5090" in r:
        return "RTX 5090", "RTX 5090", vram_gb or 32
    if "4090" in r:
        return "RTX 4090", "RTX 4090", vram_gb or 24
    if "3090" in r:
        return "RTX 3090", "RTX 3090", vram_gb or 24
    if "A5000" in r:
        return "A5000", "RTX A5000", vram_gb or 24
    if "A4000" in r:
        return "A4000", "RTX A4000", vram_gb or 16
    # Fallback: strip vendor prefixes and keep as its own family
    fam = raw.strip() if raw else "Unknown"
    for token in ("NVIDIA", "GeForce", "AMD", "Instinct", "Tesla", "Quadro", "OAM"):
        fam = " ".join(w for w in fam.split() if w.upper() != token.upper())
    fam = fam.strip() or "Unknown"
    return fam, fam, vram_gb or 0


# ---------------------------------------------------------------------------
# FX
# ---------------------------------------------------------------------------
def fetch_fx():
    """Return dict of currency -> INR rate, plus metadata."""
    endpoints = [
        "https://api.frankfurter.dev/v1/latest?base=USD&symbols=INR,EUR",
        "https://api.frankfurter.app/latest?from=USD&to=INR,EUR",
    ]
    for url in endpoints:
        try:
            d = json.loads(http_get(url))
            rates = d.get("rates", {})
            usd_inr = float(rates["INR"])
            eur = float(rates.get("EUR", 0)) or None
            fx = {"USD": usd_inr, "INR": 1.0}
            if eur:
                fx["EUR"] = usd_inr / eur  # INR per 1 EUR
            log(f"FX ok: 1 USD = {usd_inr:.3f} INR (via {url.split('//')[1].split('/')[0]})")
            return {"rates": fx, "date": d.get("date"), "source": url, "fetched_at": now_iso()}
        except Exception as e:
            log(f"FX endpoint failed ({url}): {e}")
    return None


# ---------------------------------------------------------------------------
# Vast.ai live marketplace
# ---------------------------------------------------------------------------
VAST_GPUS = [
    "H100 SXM", "H100 NVL", "H100 PCIE", "H200", "H200 NVL",
    "A100 SXM4", "A100 PCIE", "L40S", "L40", "L4",
    "RTX 6000Ada", "RTX 4090", "RTX 5090", "A40", "RTX A6000", "B200",
]


def _vast_query(gpu_name, limit):
    q = {
        "rentable": {"eq": True},
        "num_gpus": {"eq": 1},
        "gpu_name": {"eq": gpu_name},
        "order": [["dph_total", "asc"]],
        "type": "on-demand",
        "limit": limit,
    }
    url = "https://console.vast.ai/api/v0/bundles/?q=" + urllib.parse.quote(json.dumps(q))
    return json.loads(http_get(url)).get("offers", [])


def _vast_record(o, billing, price):
    vram = round(o["gpu_ram"] / 1024) if o.get("gpu_ram") else None
    fam, label, vg = normalize_gpu(o.get("gpu_name", ""), vram)
    geo = (o.get("geolocation") or "").strip().lstrip(",").strip() or "—"
    return {
        "provider": "Vast.ai",
        "provider_type": "marketplace",
        "gpu": fam,
        "gpu_label": label,
        "vram_gb": vram or vg,
        "billing": billing,
        "region": geo,
        "country": geo.split(",")[-1].strip() if "," in geo else geo,
        "currency": "USD",
        "usd_per_hr": round(float(price), 4),
        "num_gpus": 1,
        "reliability": round(float(o.get("reliability2") or 0), 3),
        "source": "vast.ai",
        "source_kind": "live",
        "url": "https://cloud.vast.ai/",
    }


def fetch_vast():
    """One query per GPU. Each offer yields an on-demand record (dph_total) and,
    when available, a cheaper spot record (min_bid)."""
    records = []
    ok = 0
    for gpu in VAST_GPUS:
        try:
            offers = _vast_query(gpu, 2)
            for o in offers:
                dph = o.get("dph_total")
                if not dph or dph <= 0:
                    continue
                records.append(_vast_record(o, "on-demand", dph))
                bid = o.get("min_bid")
                if bid and 0 < bid < dph:
                    records.append(_vast_record(o, "spot", bid))
            ok += 1
        except Exception as e:
            log(f"Vast {gpu} failed: {e}")
        time.sleep(0.2)
    log(f"Vast.ai ok: {len(records)} offers ({ok}/{len(VAST_GPUS)} GPUs)")
    return records


# ---------------------------------------------------------------------------
# RunPod live pricing
# ---------------------------------------------------------------------------
def fetch_runpod():
    query = ("query { gpuTypes { id displayName memoryInGb secureCloud "
             "communityCloud securePrice communityPrice } }")
    raw = http_post_json("https://api.runpod.io/graphql", {"query": query})
    gts = json.loads(raw)["data"]["gpuTypes"]
    records = []
    for g in gts:
        name = g.get("displayName") or g.get("id") or ""
        if not name or name.lower() == "unknown":
            continue
        mem = g.get("memoryInGb") or None
        fam, label, vg = normalize_gpu(name, mem)
        for price_key, billing, avail_key in (
            ("securePrice", "secure", "secureCloud"),
            ("communityPrice", "community", "communityCloud"),
        ):
            price = g.get(price_key)
            if not price or price <= 0 or not g.get(avail_key):
                continue
            records.append({
                "provider": "RunPod",
                "provider_type": "neocloud",
                "gpu": fam,
                "gpu_label": label,
                "vram_gb": mem or vg,
                "billing": billing,
                "region": "Global (lowest)",
                "country": "Global",
                "currency": "USD",
                "usd_per_hr": round(float(price), 4),
                "num_gpus": 1,
                "source": "runpod",
                "source_kind": "live",
                "url": "https://www.runpod.io/pricing",
            })
    log(f"RunPod ok: {len(records)} offers")
    return records


# ---------------------------------------------------------------------------
# Curated list prices
# ---------------------------------------------------------------------------
def fetch_curated():
    with open(CURATED, "r", encoding="utf-8") as f:
        doc = json.load(f)
    records = []
    for prov in doc.get("providers", []):
        for off in prov.get("offers", []):
            fam, label, vg = normalize_gpu(off["gpu"], off.get("vram_gb"))
            cur = off.get("currency", "USD")
            rec = {
                "provider": prov["provider"],
                "provider_type": prov.get("provider_type", "india_cloud"),
                "gpu": fam,
                "gpu_label": off["gpu"] if off["gpu"] != fam else label,
                "vram_gb": off.get("vram_gb") or vg,
                "billing": off.get("billing", "on-demand"),
                "region": prov.get("region", "—"),
                "country": prov.get("country", "—"),
                "currency": cur,
                "num_gpus": 1,
                "source": "curated",
                "source_kind": "list",
                "as_of": prov.get("as_of"),
                "note": prov.get("note"),
                "url": prov.get("url", ""),
            }
            if cur == "INR":
                rec["inr_per_hr"] = round(float(off["price_per_hr"]), 2)
                rec["usd_per_hr"] = None
            else:
                rec["usd_per_hr"] = round(float(off["price_per_hr"]), 4)
            records.append(rec)
    log(f"Curated ok: {len(records)} offers")
    return records


# ---------------------------------------------------------------------------
# Assemble
# ---------------------------------------------------------------------------
def load_prev():
    try:
        with open(PRICES_JSON, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def apply_fx(records, fx_rates):
    for r in records:
        if r.get("inr_per_hr") is not None:
            continue
        usd = r.get("usd_per_hr")
        cur = r.get("currency", "USD")
        rate = fx_rates.get(cur)
        if usd is not None and rate:
            r["inr_per_hr"] = round(usd * rate, 2)
        elif usd is not None and cur == "USD" and fx_rates.get("USD"):
            r["inr_per_hr"] = round(usd * fx_rates["USD"], 2)
    return records


def main():
    prev = load_prev()
    fetched_at = now_iso()

    # --- FX (fall back to previous rates if the API is down) ---
    fx = fetch_fx()
    if not fx and prev:
        fx = prev.get("fx")
        if fx:
            fx = dict(fx)
            fx["stale"] = True
            log("FX failed; reusing previous rates (stale).")
    if not fx:
        log("FATAL: no FX rate available and no previous data. Aborting.")
        sys.exit(1)
    fx_rates = fx["rates"]

    # --- Sources (each isolated; on failure reuse previous records) ---
    sources = {}
    prev_by_source = {}
    if prev:
        for rec in prev.get("prices", []):
            prev_by_source.setdefault(rec.get("source"), []).append(rec)

    for name, fn in (("vast.ai", fetch_vast), ("runpod", fetch_runpod), ("curated", fetch_curated)):
        try:
            recs = fn()
            if recs:
                sources[name] = {"records": recs, "status": "ok"}
            else:
                raise RuntimeError("no records returned")
        except Exception as e:
            log(f"Source {name} FAILED: {e}")
            if prev_by_source.get(name):
                stale = [dict(r, stale=True) for r in prev_by_source[name]]
                sources[name] = {"records": stale, "status": "stale"}
                log(f"  -> reused {len(stale)} previous {name} records (stale).")
            else:
                sources[name] = {"records": [], "status": "down"}

    all_records = []
    for s in sources.values():
        all_records.extend(s["records"])

    all_records = apply_fx(all_records, fx_rates)
    all_records = [r for r in all_records if r.get("inr_per_hr")]

    # Stamp fetched_at on freshly fetched (non-stale) records
    for r in all_records:
        if not r.get("stale"):
            r["fetched_at"] = fetched_at

    # Sort cheapest first for a sensible default
    all_records.sort(key=lambda r: r["inr_per_hr"])

    # --- Cheapest per family (for KPI cards + history) ---
    cheapest = {}
    for r in all_records:
        fam = r["gpu"]
        if fam not in cheapest or r["inr_per_hr"] < cheapest[fam]["inr_per_hr"]:
            cheapest[fam] = r

    payload = {
        "generated_at": fetched_at,
        "fx": fx,
        "source_status": {k: v["status"] for k, v in sources.items()},
        "counts": {
            "total": len(all_records),
            "live": sum(1 for r in all_records if r.get("source_kind") == "live"),
            "list": sum(1 for r in all_records if r.get("source_kind") == "list"),
        },
        "prices": all_records,
        "cheapest_by_gpu": {k: {"inr_per_hr": v["inr_per_hr"], "provider": v["provider"],
                                "billing": v["billing"], "region": v["region"]}
                            for k, v in cheapest.items()},
    }

    write_json(PRICES_JSON, payload)
    write_js(PRICES_JS, "__GPU_DATA__", payload)

    # --- History (append compact snapshot) ---
    history = load_history()
    snap = {
        "ts": fetched_at,
        "usd_inr": round(fx_rates.get("USD", 0), 3),
        "min_by_gpu": {k: v["inr_per_hr"] for k, v in cheapest.items()},
    }
    history.append(snap)
    history = history[-HISTORY_CAP:]
    write_json(HISTORY_JSON, history)
    write_js(HISTORY_JS, "__GPU_HISTORY__", history)

    log(f"DONE: {payload['counts']['total']} records "
        f"({payload['counts']['live']} live / {payload['counts']['list']} list); "
        f"history points: {len(history)}")


def load_history():
    try:
        with open(HISTORY_JSON, "r", encoding="utf-8") as f:
            h = json.load(f)
            return h if isinstance(h, list) else []
    except Exception:
        return []


def write_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))


def write_js(path, varname, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"window.{varname} = ")
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))
        f.write(";\n")


if __name__ == "__main__":
    main()
