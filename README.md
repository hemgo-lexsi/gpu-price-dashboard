# GPU Price Dashboard · India (₹/hr)

A tiny, **zero-cost, self-updating** dashboard that shows the cheapest GPU-hour
across live cloud marketplaces and Indian GPU clouds, **converted to INR at the
live exchange rate**. Built to answer one question quickly:

> *"What's the cheapest / best GPU I can rent **right now** for my workload, in ₹?"*

It's a static site (no server, no database). A GitHub Action refreshes the price
data every ~10 minutes and commits it back to the repo; GitHub Pages serves it for
free. You can also just **open `index.html` on any PC** — it works fully offline
with the last-saved data.

## Data sources

Every price is normalised to **one GPU for one hour** and converted to INR at the
live USD→INR rate (**Frankfurter / ECB**). "List" prices are clearly labelled and
dated — treat them as reference and confirm on the provider's console before renting.

| Source | What it gives | How |
|---|---|---|
| **Vast.ai** | On-demand **+ spot** marketplace offers, per region, host reliability | 🟢 live · keyless API |
| **RunPod** | Secure + community prices for ~48 GPU types | 🟢 live · keyless API |
| **Verda** (ex-DataCrunch) | On-demand **+ spot**, wide GPU range (GB300…V100) | 🟢 live · keyless API |
| **Akamai** (Linode) | RTX 6000 / RTX 4000 Ada instances | 🟢 live · keyless API |
| **E2E Networks** | Full India lineup, **native ₹** | 🟢 live · scrapes JSON-LD offers |
| **JarvisLabs** | India on-demand lineup | 🟢 live · scrapes pricing page (+fallback) |
| **Yotta Labs** | Global self-serve lineup | 🟢 live · scrapes pricing page (+fallback) |
| **Nebius** | H100/H200/B200… on-demand + spot | 🟡 list — prices render client-side, no keyless feed |
| **AWS / GCP** | Hyperscaler on-demand reference | 🟡 list by default — **can be made live** with a free credential (below) |
| **Azure** | Hyperscaler on-demand reference | 🟡 list |

### Why some vendors aren't keyless-live

- **Nebius** renders its prices in the browser (client-side JS), so they aren't in
  the page HTML — scraping them keyless isn't possible without a headless browser.
- **AWS** on-demand needs the **Price List Query API** (a free IAM key); spot needs
  EC2 credentials. The only keyless option is multi-GB bulk files — impractical to
  pull every few minutes.
- **GCP** needs the **Cloud Billing Catalog API** (a free API key).

## Enabling live AWS / GCP (optional)

The code paths are already built and stay dormant until you add credentials as
**GitHub Actions secrets** (repo → *Settings → Secrets and variables → Actions →
New repository secret*). **Never commit these or paste them anywhere public.**

**GCP** — one secret, `GCP_API_KEY`:
1. In the [Google Cloud console](https://console.cloud.google.com/) pick/create a project.
2. *APIs & Services → Library →* enable **Cloud Billing API**.
3. *APIs & Services → Credentials → Create credentials → API key*. Copy it.
4. (Recommended) *Restrict key* → API restrictions → allow only **Cloud Billing API**.
5. Add it as repo secret `GCP_API_KEY`.

**AWS** — two secrets, `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY`:
1. In the [AWS IAM console](https://console.aws.amazon.com/iam/) create a user (no console access).
2. Attach a minimal inline policy allowing `pricing:GetProducts` (and `pricing:DescribeServices`) on `*`.
3. Create an **access key** for that user; copy the key id and secret.
4. Add them as the two repo secrets above.

On the next run the dashboard shows an **AWS**/**GCP** live pill; if a key is wrong,
that source just shows as down and everything else keeps working.

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
scripts/fetch_prices.py   # data pipeline (Python stdlib; boto3 only if you enable live AWS)
data/curated.json     # editable list prices (add/remove providers here)
data/prices.json/.js  # generated current snapshot  (committed)
data/history.json/.js # generated trend history      (committed)
.github/workflows/update.yml   # runs the pipeline every ~10 min
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
