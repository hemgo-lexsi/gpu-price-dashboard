#!/usr/bin/env python3
"""
GPU Price Dashboard - data pipeline.

Fetches (each source isolated; one failing never kills the run):
  * Live FX (USD->INR, EUR->INR) from Frankfurter / ECB
  * Vast.ai marketplace (on-demand + spot), RunPod, Verda/DataCrunch (on-demand +
    spot), Akamai/Linode — all keyless JSON APIs
  * E2E (JSON-LD, native INR), JarvisLabs, Yotta Labs — scraped public pricing pages
  * Optional live AWS (boto3 + IAM key) and GCP (Billing Catalog API key), only
    when their credentials are present in the environment
  * Curated list prices from data/curated.json (Nebius + hyperscaler reference)

Normalises everything to INR per SINGLE GPU per hour and writes:
  * data/prices.json / data/prices.js   (current snapshot for the dashboard)
  * data/history.json / data/history.js (throttled append-only trend series)

Dependencies: Python stdlib only, except boto3 which is imported lazily and only
needed if live AWS pricing is enabled.
"""

import json
import os
import re
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

HISTORY_CAP = 1500          # keep at most this many trend snapshots (~31 days at 30-min spacing)
HISTORY_MIN_GAP_MIN = 30    # don't append a trend point more often than this
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


BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


def http_get(url, headers=None, browser=False):
    ua = BROWSER_UA if browser else USER_AGENT
    req = urllib.request.Request(url, headers={"User-Agent": ua, **(headers or {})})
    with urllib.request.urlopen(req, timeout=TIMEOUT, context=SSL_CTX) as r:
        return r.read().decode("utf-8", "replace")


def html_to_text(html):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))


def scrape_token_prices(text, tokens, window=70, lo=0.05, hi=30):
    """Return {token: price} by finding each token's nearest following $price."""
    out = {}
    low = text.lower()
    for tok in tokens:
        start = 0
        while True:
            i = low.find(tok.lower(), start)
            if i == -1:
                break
            seg = text[i + len(tok): i + len(tok) + window]
            pm = re.search(r"\$?\s*([0-9]{1,2}\.[0-9]{2})", seg)
            if pm:
                p = float(pm.group(1))
                if lo <= p <= hi:
                    out[tok] = p
                    break
            start = i + len(tok)
    return out


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

    # Grace-Blackwell superchips (check before B200/B300 — "GB200" contains "B200")
    if "GB300" in r:
        return "GB300", "GB300 (Grace-Blackwell)", vram_gb or 288
    if "GB200" in r:
        return "GB200", "GB200 (Grace-Blackwell)", vram_gb or 186
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
# Verda (formerly DataCrunch) — public keyless API, on-demand + spot
# ---------------------------------------------------------------------------
def _mk(provider, ptype, url, source, gpu_raw, vram, billing, usd, source_kind="live"):
    fam, label, vg = normalize_gpu(gpu_raw, vram)
    return {
        "provider": provider, "provider_type": ptype, "gpu": fam, "gpu_label": label,
        "vram_gb": vram or vg, "billing": billing, "region": "Global (lowest)", "country": "Global",
        "currency": "USD", "usd_per_hr": round(float(usd), 4), "num_gpus": 1,
        "source": source, "source_kind": source_kind, "url": url,
    }


def fetch_verda():
    data = json.loads(http_get("https://api.datacrunch.io/v1/instance-types"))
    best = {}  # (fam, billing) -> record with min per-GPU price
    for t in data:
        g = t.get("gpu") or {}
        n = g.get("number_of_gpus") or 0
        if n < 1:
            continue
        vram = (t.get("gpu_memory") or {}).get("size_in_gigabytes")
        for field, billing in (("price_per_hour", "on-demand"), ("spot_price", "spot")):
            raw = t.get(field)
            try:
                per = float(raw) / n
            except (TypeError, ValueError):
                continue
            if per <= 0:
                continue
            rec = _mk("Verda (DataCrunch)", "neocloud", "https://verda.com/pricing", "verda",
                      g.get("description", ""), vram, billing, per)
            key = (rec["gpu"], billing)
            if key not in best or per < best[key]["usd_per_hr"]:
                best[key] = rec
    recs = list(best.values())
    log(f"Verda ok: {len(recs)} offers")
    return recs


# ---------------------------------------------------------------------------
# Akamai (Linode) — public keyless API
# ---------------------------------------------------------------------------
def fetch_akamai():
    data = json.loads(http_get("https://api.linode.com/v4/linode/types")).get("data", [])
    best = {}
    for t in data:
        n = t.get("gpus") or 0
        if n < 1:
            continue
        hourly = (t.get("price") or {}).get("hourly")
        if not hourly or hourly <= 0:
            continue
        label = t.get("label", "")
        if "RTX6000" in label.replace(" ", ""):
            gpu_raw, vram = "RTX 6000 (Turing)", 24
        elif "RTX4000" in label.replace(" ", ""):
            gpu_raw, vram = "RTX 4000 Ada", 20
        else:
            gpu_raw, vram = re.sub(r"(Dedicated.*?\+|GPU|x\d.*$)", "", label).strip() or label, None
        per = hourly / n
        rec = _mk("Akamai (Linode)", "neocloud", "https://www.linode.com/pricing/", "akamai",
                  gpu_raw, vram, "on-demand", per)
        key = rec["gpu"]
        if key not in best or per < best[key]["usd_per_hr"]:
            best[key] = rec
    recs = list(best.values())
    log(f"Akamai ok: {len(recs)} offers")
    return recs


# ---------------------------------------------------------------------------
# E2E Networks — scrape JSON-LD product offers (native INR)
# ---------------------------------------------------------------------------
E2E_FALLBACK = [  # (gpu, vram, inr) — used only if the live scrape yields too little
    ("B200", 192, 671), ("H200", 141, 436), ("H100", 80, 362), ("RTX 6000 Pro", 96, 182),
    ("A100", 80, 189), ("A40", 48, 96), ("L40S", 48, 102), ("A30", 24, 90), ("L4", 24, 49),
]


def fetch_e2e():
    html = http_get("https://www.e2enetworks.com/pricing", browser=True)
    offers = re.findall(
        r'"name":"NVIDIA ([^"]+?) GPU Cloud Instance".{0,160}?"priceCurrency":"([A-Z]+)","price":([0-9.]+)',
        html)
    recs = []
    for name, cur, price in offers:
        fam, label, vg = normalize_gpu(name, None)
        rec = {"provider": "E2E Networks", "provider_type": "india_cloud", "gpu": fam,
               "gpu_label": label, "vram_gb": vg, "billing": "on-demand",
               "region": "India (Delhi/Mumbai)", "country": "IN", "currency": cur,
               "num_gpus": 1, "source": "e2e", "source_kind": "live",
               "url": "https://www.e2enetworks.com/pricing"}
        if cur == "INR":
            rec["inr_per_hr"] = round(float(price), 2)
            rec["usd_per_hr"] = None
        else:
            rec["usd_per_hr"] = round(float(price), 4)
        recs.append(rec)
    if len(recs) < 5:
        # Page structure changed / blocked: fall back to last-known INR list prices.
        log(f"E2E scrape weak ({len(recs)} offers); using fallback list prices.")
        recs = []
        for gpu, vram, inr in E2E_FALLBACK:
            fam, label, vg = normalize_gpu(gpu, vram)
            recs.append({"provider": "E2E Networks", "provider_type": "india_cloud", "gpu": fam,
                         "gpu_label": label, "vram_gb": vram or vg, "billing": "on-demand",
                         "region": "India (Delhi/Mumbai)", "country": "IN", "currency": "INR",
                         "inr_per_hr": float(inr), "usd_per_hr": None, "num_gpus": 1,
                         "source": "e2e", "source_kind": "list", "as_of": "2026-08-21",
                         "note": "Fallback list price (live scrape unavailable).",
                         "url": "https://www.e2enetworks.com/pricing"})
        log(f"E2E ok: {len(recs)} offers (fallback list)")
        return recs
    # E2E JSON-LD names both A100 variants just "A100"; label by price (higher = 80GB).
    a100 = sorted([r for r in recs if r["gpu"] == "A100"], key=lambda r: -(r.get("inr_per_hr") or 0))
    for i, r in enumerate(a100):
        r["vram_gb"] = 80 if i == 0 else 40
        r["gpu_label"] = "A100 %dGB" % r["vram_gb"]
    log(f"E2E ok: {len(recs)} offers (live INR)")
    return recs


# ---------------------------------------------------------------------------
# JarvisLabs & Yotta Labs — scrape public pricing pages (USD), with fallback
# ---------------------------------------------------------------------------
# config: token used to locate the price -> (gpu name, vram, fallback USD)
JARVIS_CFG = {
    "H200": ("H200", 141, 3.99), "H100": ("H100", 80, 2.69),
    "RTX Pro 6000": ("RTX 6000 Pro", 96, 1.89), "RTX PRO 6000": ("RTX 6000 Pro", 96, 1.89),
    "A100 80GB": ("A100", 80, 1.49), "A100 40GB": ("A100", 40, 0.89),
    "A30": ("A30", 24, 0.41), "L4": ("L4", 24, 0.44),
}
YOTTA_CFG = {
    "H100": ("H100", 80, 2.56), "H200": ("H200", 141, 2.50), "B200": ("B200", 180, 5.37),
    "B300": ("B300", 268, 7.64), "A100 PCIe": ("A100", 80, 0.92), "A100 80G": ("A100", 80, 1.48),
    "RTX 5090": ("RTX 5090", 32, 0.70), "RTX 4090": ("RTX 4090", 24, 0.48),
    "RTX A6000": ("A6000", 48, 0.45), "RTX 6000 Ada": ("RTX 6000 Ada", 48, 0.97),
    "RTX PRO 6000": ("RTX 6000 Pro", 96, 1.35),
}


def _scrape_provider(provider, ptype, url, source, cfg):
    """Scrape a USD pricing page; use live price where found, fallback constant otherwise."""
    live = {}
    try:
        live = scrape_token_prices(html_to_text(http_get(url, browser=True)), list(cfg.keys()))
    except Exception as e:
        log(f"{provider} page fetch failed ({e}); using fallback prices.")
    recs, nlive = [], 0
    seen = set()
    for tok, (gpu, vram, fb) in cfg.items():
        key = (gpu, vram)  # de-dupe A100 80/40 vs alias tokens
        if key in seen:
            continue
        price = live.get(tok)
        kind = "live" if price is not None else "list"
        if price is None:
            price = fb
        else:
            nlive += 1
        seen.add(key)
        rec = _mk(provider, ptype, url, source, gpu, vram, "on-demand", price, source_kind=kind)
        if kind == "list":
            rec["as_of"] = "2026-08-21"
            rec["note"] = "Published list price (page auto-scrape missed this row)."
        recs.append(rec)
    log(f"{provider} ok: {len(recs)} offers ({nlive} live / {len(recs) - nlive} fallback)")
    return recs


def fetch_jarvislabs():
    return _scrape_provider("JarvisLabs", "india_cloud", "https://jarvislabs.ai/pricing", "jarvislabs", JARVIS_CFG)


def fetch_yotta():
    # yottalabs.ai — a distinct (global) service, NOT the Indian Yotta/Shakti Cloud.
    return _scrape_provider("YottaLabs.ai", "neocloud", "https://www.yottalabs.ai/pricing", "yotta", YOTTA_CFG)


# ---------------------------------------------------------------------------
# GCP — Cloud Billing Catalog API (needs a free API key in env GCP_API_KEY)
# ---------------------------------------------------------------------------
GCP_COMPUTE_SVC = "6F81-5844-456A"  # Compute Engine service id


def _gcp_model(desc):
    d = desc.upper()
    if "H200" in d:
        return "H200", 141
    if "H100" in d:
        return "H100", 80
    if "A100" in d:
        return "A100", (80 if "80GB" in d or "80 GB" in d else 40)
    if "L40S" in d:
        return "L40S", 48
    if "L4" in d and "L40" not in d:
        return "L4", 24
    if "T4" in d:
        return "T4", 16
    if "V100" in d:
        return "V100", 16
    if "P100" in d:
        return "P100", 16
    return None, None


def fetch_gcp():
    key = os.environ["GCP_API_KEY"]
    base = f"https://cloudbilling.googleapis.com/v1/services/{GCP_COMPUTE_SVC}/skus?pageSize=5000&key={key}"
    skus, page = [], None
    for _ in range(10):  # safety cap on pagination
        url = base + (f"&pageToken={page}" if page else "")
        d = json.loads(http_get(url))
        skus.extend(d.get("skus", []))
        page = d.get("nextPageToken")
        if not page:
            break
    best = {}  # model -> (price, region, vram)
    for s in skus:
        cat = s.get("category", {})
        if cat.get("resourceGroup") != "GPU" or cat.get("usageType") != "OnDemand":
            continue
        desc = s.get("description", "")
        model, vram = _gcp_model(desc)
        if not model:
            continue
        pes = (s.get("pricingInfo") or [{}])[0].get("pricingExpression", {})
        rates = pes.get("tieredRates") or []
        if not rates:
            continue
        up = rates[-1].get("unitPrice", {})
        price = (up.get("units") and float(up["units"]) or 0) + float(up.get("nanos", 0)) / 1e9
        if price <= 0:
            continue
        m = re.search(r"running in (.+)$", desc)
        region = (m.group(1).strip() if m else "Global").rstrip(".")
        if model not in best or price < best[model][0]:  # keep cheapest region per model
            best[model] = (price, region, vram)
    recs = [_mk("GCP", "hyperscaler", "https://cloud.google.com/compute/gpus-pricing", "gcp",
                model, vram, "on-demand", price) | {"region": region, "country": "Global"}
            for model, (price, region, vram) in best.items()]
    if not recs:
        raise RuntimeError("GCP catalog returned no GPU SKUs")
    log(f"GCP ok: {len(recs)} offers (live)")
    return recs


# ---------------------------------------------------------------------------
# AWS — Price List Query API via boto3 (needs AWS_ACCESS_KEY_ID/SECRET in env)
# ---------------------------------------------------------------------------
AWS_TARGETS = {  # instanceType -> (gpu, vram, num_gpus)
    "p5.48xlarge": ("H100", 80, 8),
    "p4de.24xlarge": ("A100", 80, 8),
    "p4d.24xlarge": ("A100", 40, 8),
    "g6.xlarge": ("L4", 24, 1),
    "g6e.xlarge": ("L40S", 48, 1),
    "g5.xlarge": ("A10G", 24, 1),
}
AWS_REGION_PRICING = "us-east-1"   # the Pricing API endpoint region
AWS_REGION_TARGET = "ap-south-1"   # Mumbai — the prices we want


def fetch_aws():
    import boto3  # only imported when AWS creds are configured
    cli = boto3.client("pricing", region_name=AWS_REGION_PRICING)
    recs = []
    for it, (gpu, vram, gpus) in AWS_TARGETS.items():
        try:
            resp = cli.get_products(ServiceCode="AmazonEC2", MaxResults=1, Filters=[
                {"Type": "TERM_MATCH", "Field": "instanceType", "Value": it},
                {"Type": "TERM_MATCH", "Field": "regionCode", "Value": AWS_REGION_TARGET},
                {"Type": "TERM_MATCH", "Field": "tenancy", "Value": "Shared"},
                {"Type": "TERM_MATCH", "Field": "operatingSystem", "Value": "Linux"},
                {"Type": "TERM_MATCH", "Field": "preInstalledSw", "Value": "NA"},
                {"Type": "TERM_MATCH", "Field": "capacitystatus", "Value": "Used"},
            ])
            for pl in resp.get("PriceList", []):
                d = json.loads(pl)
                od = d.get("terms", {}).get("OnDemand", {})
                for term in od.values():
                    for dim in term.get("priceDimensions", {}).values():
                        usd = dim.get("pricePerUnit", {}).get("USD")
                        if usd and float(usd) > 0:
                            recs.append(_mk("AWS (Mumbai ap-south)", "hyperscaler",
                                            "https://aws.amazon.com/ec2/instance-types/", "aws",
                                            gpu, vram, "on-demand", float(usd) / gpus)
                                        | {"region": "Asia Pacific (Mumbai)", "country": "IN"})
        except Exception as e:
            log(f"AWS {it} failed: {e}")
    if not recs:
        raise RuntimeError("AWS returned no priced GPU instances (check IAM perms / region)")
    log(f"AWS ok: {len(recs)} offers (live)")
    return recs


# ---------------------------------------------------------------------------
# Oracle OCI — keyless public price list API. Prices are a uniform global list
# (same in the Mumbai region). We target stable part numbers to avoid the
# software/VMware/Cloud@Customer noise, and read the PAY_AS_YOU_GO rate.
# ---------------------------------------------------------------------------
OCI_PARTS = {  # partNumber -> (gpu, vram)
    "B98415": ("H100", 80), "B110519": ("H200", 141), "B95907": ("A100", 80),
    "B109479": ("L40S", 48), "B109485": ("MI300X", 192), "B110978": ("B200", 180),
    "B110979": ("GB200", 186), "B112237": ("B300", 288), "B112140": ("GB300", 288),
    "B112613": ("RTX PRO 6000", 96), "B111758": ("MI355X", 288), "B95909": ("A10", 24),
}


def fetch_oci():
    d = json.loads(http_get("https://apexapps.oracle.com/pls/apex/cetools/api/v1/products/?currencyCode=USD"))
    by_part = {x.get("partNumber"): x for x in d.get("items", [])}
    recs = []
    for part, (gpu, vram) in OCI_PARTS.items():
        it = by_part.get(part)
        if not it:
            continue
        prices = ((it.get("currencyCodeLocalizations") or [{}])[0].get("prices") or [])
        payg = next((p for p in prices if p.get("model") == "PAY_AS_YOU_GO"), prices[0] if prices else None)
        if not payg or not payg.get("value"):
            continue
        recs.append(_mk("Oracle OCI", "hyperscaler", "https://www.oracle.com/cloud/compute/gpu/",
                        "oci", gpu, vram, "on-demand", float(payg["value"]))
                    | {"region": "Mumbai / global list", "country": "IN"})
    if not recs:
        raise RuntimeError("OCI price list returned no matching GPU parts")
    log(f"OCI ok: {len(recs)} offers (live)")
    return recs


# DigitalOcean is handled as a curated list vendor (data/curated.json): its page
# has several price tiers that defeat reliable keyless scraping, and its live API
# needs a token, so we keep verified on-demand rates instead.

# ---------------------------------------------------------------------------
# Nebius — keyless. Prices are baked into the server-rendered (SSG) HTML, so we
# scrape the pricing table directly. Each GPU row has a preemptible and an
# on-demand $/GPU-hour column.
# ---------------------------------------------------------------------------
NEBIUS_CFG = {  # token found in the page -> (gpu, vram, (fallback_preempt, fallback_ondemand))
    "HGX H200": ("H200", 141, (2.45, 4.50)),
    "HGX H100": ("H100", 80, (2.15, 3.85)),
    "HGX B300": ("B300", 268, (4.30, 7.85)),
    "HGX B200": ("B200", 180, (3.95, 7.15)),
    "RTX PRO 6000": ("RTX 6000 Pro", 96, (0.95, 1.80)),
    "L40S with AMD": ("L40S", 48, (0.74, 1.55)),
}


def fetch_nebius():
    txt = ""
    try:
        txt = html_to_text(http_get("https://nebius.com/prices", browser=True))
    except Exception as e:
        log(f"Nebius page fetch failed ({e}); using fallback prices.")
    recs, nlive = [], 0
    for tok, (gpu, vram, fb) in NEBIUS_CFG.items():
        preempt, ondemand, kind = fb[0], fb[1], "list"
        i = txt.find(tok)
        if i >= 0:
            prices = re.findall(r"\$([0-9]{1,2}\.[0-9]{2})", txt[i + len(tok): i + len(tok) + 90])
            if len(prices) >= 2 and float(prices[1]) > float(prices[0]) > 0:
                preempt, ondemand, kind, nlive = float(prices[0]), float(prices[1]), "live", nlive + 1
        for billing, price in (("on-demand", ondemand), ("spot", preempt)):
            rec = _mk("Nebius", "neocloud", "https://nebius.com/prices", "nebius",
                      gpu, vram, billing, price, source_kind=kind) | {"region": "EU / US", "country": "Global"}
            if kind == "list":
                rec["as_of"] = "2026-08-21"
                rec["note"] = "Fallback list price (live scrape unavailable)."
            recs.append(rec)
    log(f"Nebius ok: {len(recs)} offers ({nlive} live / {len(NEBIUS_CFG) - nlive} fallback rows)")
    return recs


# ---------------------------------------------------------------------------
# Azure — keyless public Retail Prices API. We price a few representative GPU
# SKUs and normalise to per-GPU, keeping the cheapest region per family.
# ---------------------------------------------------------------------------
AZURE_TARGETS = {  # armSkuName -> (gpu, vram, num_gpus)
    "Standard_NC40ads_H100_v5": ("H100", 94, 1),
    "Standard_ND96isr_H100_v5": ("H100", 80, 8),
    "Standard_ND96isr_H200_v5": ("H200", 141, 8),
    "Standard_NC24ads_A100_v4": ("A100", 80, 1),
    "Standard_ND96asr_A100_v4": ("A100", 80, 8),
}


def _azure_query(flt):
    items, url = [], "https://prices.azure.com/api/retail/prices?" + urllib.parse.urlencode({"$filter": flt})
    for _ in range(5):  # follow paging, capped
        d = json.loads(http_get(url))
        items.extend(d.get("Items", []))
        url = d.get("NextPageLink")
        if not url:
            break
    return items


def fetch_azure():
    best = {}  # gpu -> (per_gpu, region, vram)
    for sku, (gpu, vram, count) in AZURE_TARGETS.items():
        flt = ("serviceName eq 'Virtual Machines' and priceType eq 'Consumption' "
               f"and armSkuName eq '{sku}'")
        for x in _azure_query(flt):
            mn = x.get("meterName", "")
            if "Spot" in mn or "Low Priority" in mn or x.get("unitOfMeasure") != "1 Hour":
                continue
            price = x.get("retailPrice") or 0
            if price <= 0:
                continue
            per = price / count
            if gpu not in best or per < best[gpu][0]:
                best[gpu] = (per, x.get("armRegionName", "—"), vram)
    recs = [_mk("Azure", "hyperscaler", "https://azure.microsoft.com/en-us/pricing/details/virtual-machines/",
                "azure", gpu, vram, "on-demand", per) | {"region": region, "country": "Global"}
            for gpu, (per, region, vram) in best.items()]
    if not recs:
        raise RuntimeError("Azure Retail Prices returned no GPU SKUs")
    log(f"Azure ok: {len(recs)} offers (live)")
    return recs


# ---------------------------------------------------------------------------
# Curated list prices
# ---------------------------------------------------------------------------
def fetch_curated(skip_providers=()):
    with open(CURATED, "r", encoding="utf-8") as f:
        doc = json.load(f)
    records = []
    for prov in doc.get("providers", []):
        if any(prov["provider"].startswith(s) for s in skip_providers):
            continue
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
    # Backfill a USD figure for INR-native rows (E2E, Shakti, etc.) so the
    # dashboard's USD view and sorting work for every row.
    usd_rate = fx_rates.get("USD")
    if usd_rate:
        for r in records:
            if r.get("usd_per_hr") is None and r.get("inr_per_hr"):
                r["usd_per_hr"] = round(r["inr_per_hr"] / usd_rate, 4)
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

    def run_source(name, fn):
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

    # Optional true-live hyperscaler sources activate only when their (free)
    # credentials are present as environment variables / GitHub secrets.
    source_fns = [
        ("vast.ai", fetch_vast), ("runpod", fetch_runpod), ("verda", fetch_verda),
        ("akamai", fetch_akamai), ("e2e", fetch_e2e), ("jarvislabs", fetch_jarvislabs),
        ("yotta", fetch_yotta), ("nebius", fetch_nebius), ("azure", fetch_azure),
        ("oci", fetch_oci),
    ]
    if os.getenv("GCP_API_KEY"):
        source_fns.append(("gcp", fetch_gcp))
    if os.getenv("AWS_ACCESS_KEY_ID") and os.getenv("AWS_SECRET_ACCESS_KEY"):
        source_fns.append(("aws", fetch_aws))

    for name, fn in source_fns:
        run_source(name, fn)

    # Curated runs last; skip a provider's reference row only if its live
    # source actually produced data (so a broken key keeps the reference).
    skip = []
    if sources.get("gcp", {}).get("status") in ("ok", "stale"):
        skip.append("GCP")
    if sources.get("aws", {}).get("status") in ("ok", "stale"):
        skip.append("AWS")
    run_source("curated", lambda: fetch_curated(skip_providers=tuple(skip)))

    all_records = []
    for s in sources.values():
        all_records.extend(s["records"])

    all_records = apply_fx(all_records, fx_rates)
    all_records = [r for r in all_records if r.get("inr_per_hr")]

    # De-duplicate identical offers (e.g. RunPod lists two SKUs that map to the
    # same family/price). Keep the first occurrence.
    seen, deduped = set(), []
    for r in all_records:
        key = (r["provider"], r["gpu"], r.get("gpu_label"), r["billing"], r.get("region"),
               round(r["inr_per_hr"], 2))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(r)
    all_records = deduped

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
        "cheapest_by_gpu": {k: {"inr_per_hr": v["inr_per_hr"], "usd_per_hr": v.get("usd_per_hr"),
                                "provider": v["provider"], "billing": v["billing"], "region": v["region"]}
                            for k, v in cheapest.items()},
    }

    write_json(PRICES_JSON, payload)
    write_js(PRICES_JS, "__GPU_DATA__", payload)

    # --- History (append a compact snapshot, throttled so a fast refresh
    # cadence doesn't bloat the file or over-churn commits) ---
    history = load_history()
    if _history_gap_ok(history, fetched_at):
        history.append({
            "ts": fetched_at,
            "usd_inr": round(fx_rates.get("USD", 0), 3),
            "min_by_gpu": {k: v["inr_per_hr"] for k, v in cheapest.items()},
        })
        history = history[-HISTORY_CAP:]
        write_json(HISTORY_JSON, history)
        write_js(HISTORY_JS, "__GPU_HISTORY__", history)
    else:
        log("History: within min-gap window, not appending a trend point this run.")

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


def _history_gap_ok(history, now_ts):
    if not history:
        return True
    try:
        last = datetime.strptime(history[-1]["ts"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        now = datetime.strptime(now_ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        return (now - last).total_seconds() >= HISTORY_MIN_GAP_MIN * 60
    except Exception:
        return True


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
