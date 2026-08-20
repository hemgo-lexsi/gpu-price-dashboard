# GPU Price Dashboard · India (₹/hr)

A tiny, **zero-cost, self-updating** dashboard that shows the cheapest GPU-hour
across live cloud marketplaces and Indian GPU clouds, **converted to INR at the
live exchange rate**. Built to answer one question quickly:

> *"What's the cheapest / best GPU I can rent **right now** for my workload, in ₹?"*

It's a static site (no server, no database). A GitHub Action refreshes the price
data every ~30 minutes and commits it back to the repo; GitHub Pages serves it for
free. You can also just **open `index.html` on any PC** — it works fully offline
with the last-saved data.

## Live data sources (all keyless, fetched automatically)

| Source | What it gives | Type |
|---|---|---|
| **Vast.ai** marketplace API | Real-time on-demand **and spot** offers per GPU, with region + host reliability | 🟢 live |
| **RunPod** GraphQL API | Secure-cloud and community-cloud prices for ~48 GPU types | 🟢 live |
| **Frankfurter / ECB** | Live USD→INR (and EUR→INR) exchange rate | 🟢 live |
| `data/curated.json` | Published list prices for **E2E Networks, JarvisLabs**, and AWS/Azure/GCP reference rates | 🟡 list |

Every price is normalised to **one GPU for one hour** and converted to INR.
"List" prices are clearly labelled and dated — treat them as reference and confirm
on the provider's console before renting.

## Features

- 💱 Live INR conversion with the FX rate shown in the header
- 🏷️ KPI cards: cheapest H100 / A100 / H200 / L40S / L4 / RTX 4090 right now
- 🔎 Rich filtering: GPU model, provider, provider type, billing (spot/on-demand/secure/community), min VRAM, max ₹/hr (log slider), free-text search, "live only"
- ↕️ Sort by any column; **cheapest option per model is highlighted**
- 📈 Price-trend chart per model (fills in as snapshots accumulate) with optional USD→INR overlay
- 🧾 Per-hour / per-day / per-month view toggle
- ⭳ CSV export of the current filtered view
- 🌗 Automatic light / dark theme, responsive, no external dependencies (works offline)

## Repository layout

```
index.html            # the dashboard (self-contained UI)
app.js                # front-end logic (vanilla JS, no frameworks)
scripts/fetch_prices.py   # data pipeline (Python stdlib only — no pip installs)
data/curated.json     # editable list prices (add/remove providers here)
data/prices.json/.js  # generated current snapshot  (committed)
data/history.json/.js # generated trend history      (committed)
.github/workflows/update.yml   # runs the pipeline every ~30 min
```

## Run / refresh locally

```bash
python scripts/fetch_prices.py          # rebuild data/prices.* and data/history.*
python -m http.server 8000              # then open http://localhost:8000
```

(Opening `index.html` directly by double-click also works — the data is loaded via
`<script>` tags, so no local server is strictly required.)

## Adding or editing a provider

Live sources are handled in `scripts/fetch_prices.py`. For any provider without a
price API, just edit **`data/curated.json`** — copy a block, set the GPU model, VRAM,
`price_per_hr`, `currency`, and bump `as_of` to the date you verified it. The
dashboard and the KPI cards pick it up on the next run.

## How updating works

The scheduled Action runs `fetch_prices.py`, which is resilient: if one source is
unreachable it keeps that source's previous rows (marked **stale**) instead of
dropping them, so the dashboard degrades gracefully. Commits made by the bot don't
retrigger the workflow, but they do trigger a GitHub Pages rebuild.

---

*Not affiliated with any provider. Prices exclude taxes/egress and change constantly —
always verify on the provider's console before you rent.*
