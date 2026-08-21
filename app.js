/* GPU Price Dashboard — front-end logic (vanilla JS, no dependencies). */
(function () {
  "use strict";

  var DATA = window.__GPU_DATA__;
  var HISTORY = window.__GPU_HISTORY__ || [];

  if (!DATA || !DATA.prices) {
    document.querySelector(".wrap").innerHTML =
      '<div class="err"><h1>No data loaded</h1><p class="muted">Could not find <code>data/prices.js</code>. ' +
      'If you opened this file directly, keep <code>index.html</code> next to the <code>data/</code> folder, ' +
      'or view the hosted dashboard.</p></div>';
    return;
  }

  var PRICES = DATA.prices;

  // ---- display helpers -----------------------------------------------------
  var PTYPE_LABEL = { marketplace: "Marketplace", neocloud: "Neocloud", india_cloud: "India cloud", hyperscaler: "Hyperscaler" };
  var PTYPE_ORDER = ["india_cloud", "marketplace", "neocloud", "hyperscaler"];
  var BILLING_ORDER = ["spot", "community", "on-demand", "secure"];
  var POPULAR = ["H100", "A100", "H200", "L40S", "L4", "RTX 4090", "RTX 6000 Pro", "A6000", "B200", "MI300X"];

  function inrFmt(v) {
    if (v == null) return "—";
    var dec = v >= 1000 ? 0 : 2;
    return "₹" + Number(v).toLocaleString("en-IN", { minimumFractionDigits: dec, maximumFractionDigits: dec });
  }
  function usdFmt(v) {
    if (v == null) return "—";
    var dec = v >= 1000 ? 0 : v < 1 ? 3 : 2;
    return "$" + Number(v).toLocaleString("en-US", { minimumFractionDigits: dec, maximumFractionDigits: dec });
  }
  // Primary price cell in the currently selected display currency (× time unit).
  function priceFmt(inr, usd, unit) {
    unit = unit || 1;
    return state.currency === "USD" ? usdFmt((usd == null ? 0 : usd) * unit) : inrFmt(inr * unit);
  }
  // The "other" currency, always hourly, for the reference column.
  function altFmt(inr, usd) { return state.currency === "USD" ? inrFmt(inr) : usdFmt(usd); }
  function relTime(iso) {
    var s = (Date.now() - Date.parse(iso)) / 1000;
    if (isNaN(s)) return "";
    if (s < 90) return "just now";
    if (s < 3600) return Math.round(s / 60) + " min ago";
    if (s < 86400) return Math.round(s / 3600) + " hr ago";
    return Math.round(s / 86400) + " d ago";
  }
  function esc(s) { return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]; }); }
  function billingClass(b) { return "b-" + b.replace(/[^a-z]/g, ""); }

  // ---- unique dimensions ---------------------------------------------------
  function uniq(key) { var s = {}; PRICES.forEach(function (r) { if (r[key] != null) s[r[key]] = 1; }); return Object.keys(s); }
  var GPUS = uniq("gpu").sort(function (a, b) {
    var ia = POPULAR.indexOf(a), ib = POPULAR.indexOf(b);
    if (ia !== -1 || ib !== -1) { if (ia === -1) return 1; if (ib === -1) return -1; return ia - ib; }
    return a.localeCompare(b);
  });
  var PROVIDERS = uniq("provider").sort();
  var PTYPES = PTYPE_ORDER.filter(function (t) { return uniq("provider_type").indexOf(t) !== -1; });
  var BILLINGS = BILLING_ORDER.filter(function (b) { return uniq("billing").indexOf(b) !== -1; });

  var priceVals = PRICES.map(function (r) { return r.inr_per_hr; }).filter(function (v) { return v > 0; });
  var PMIN = Math.max(1, Math.floor(Math.min.apply(null, priceVals)));
  var PMAX = Math.ceil(Math.max.apply(null, priceVals));

  // ---- state ---------------------------------------------------------------
  var state = {
    q: "", gpus: new Set(), providers: new Set(), ptypes: new Set(), billings: new Set(),
    liveOnly: false, minVram: 0, maxInr: Infinity, unit: 1, currency: "INR",
    sort: "inr_per_hr", dir: 1
  };

  // Log-mapped max-price slider (fine control at the low end)
  function sliderToPrice(pos) {
    if (pos >= 100) return Infinity;
    return Math.round(PMIN * Math.pow(PMAX / PMIN, pos / 100));
  }

  // ========================================================================
  // Filtering
  // ========================================================================
  function filtered() {
    var q = state.q.trim().toLowerCase();
    return PRICES.filter(function (r) {
      if (state.liveOnly && r.source_kind !== "live") return false;
      if (state.gpus.size && !state.gpus.has(r.gpu)) return false;
      if (state.providers.size && !state.providers.has(r.provider)) return false;
      if (state.ptypes.size && !state.ptypes.has(r.provider_type)) return false;
      if (state.billings.size && !state.billings.has(r.billing)) return false;
      if (state.minVram && (r.vram_gb || 0) < state.minVram) return false;
      if (r.inr_per_hr > state.maxInr) return false;
      if (q) {
        var hay = (r.provider + " " + r.gpu + " " + r.gpu_label + " " + r.billing + " " +
                   r.region + " " + PTYPE_LABEL[r.provider_type]).toLowerCase();
        if (hay.indexOf(q) === -1) return false;
      }
      return true;
    });
  }

  function sortRows(rows) {
    var k = state.sort, d = state.dir;
    return rows.slice().sort(function (a, b) {
      var va = a[k], vb = b[k];
      if (typeof va === "string" || typeof vb === "string") {
        va = (va || "").toString().toLowerCase(); vb = (vb || "").toString().toLowerCase();
        return va < vb ? -d : va > vb ? d : 0;
      }
      // Missing values always sink to the bottom, regardless of sort direction.
      var an = va == null, bn = vb == null;
      if (an && bn) return 0;
      if (an) return 1;
      if (bn) return -1;
      return (va - vb) * d;
    });
  }

  // ========================================================================
  // Rendering
  // ========================================================================
  var COLS = [
    { k: "provider", t: "Provider" },
    { k: "provider_type", t: "Type" },
    { k: "gpu", t: "GPU" },
    { k: "billing", t: "Billing" },
    { k: "region", t: "Region" },
    { k: "reliability", t: "Rel.", num: true, title: "Vast.ai host reliability score (marketplace only)" },
    { k: "inr_per_hr", t: "₹ / hr", num: true },
    { k: "usd_per_hr", t: "USD/hr", num: true, title: "Original USD rate — always per hour, not affected by the day/month toggle" },
    { k: "source", t: "Source" }
  ];

  function renderHead() {
    document.getElementById("head").innerHTML = COLS.map(function (c) {
      var arrow = state.sort === c.k ? (state.dir === 1 ? " ▲" : " ▼") : "";
      return '<th data-k="' + c.k + '"' + (c.num ? ' class="num"' : "") +
        (c.title ? ' title="' + esc(c.title) + '"' : "") + ">" + esc(c.t) +
        '<span class="arrow">' + arrow + "</span></th>";
    }).join("");
    document.querySelectorAll("#head th").forEach(function (th) {
      th.onclick = function () {
        var k = th.getAttribute("data-k");
        if (state.sort === k) state.dir *= -1;
        else { state.sort = k; state.dir = (k === "inr_per_hr" || k === "usd_per_hr" || k === "reliability") ? 1 : 1; }
        render();
      };
    });
  }

  function unitLabel() {
    var sym = state.currency === "USD" ? "$" : "₹";
    return sym + (state.unit === 1 ? " / hr" : state.unit === 24 ? " / day" : " / month");
  }

  function render() {
    var rows = sortRows(filtered());

    // cheapest per family within the filtered set
    var cheapest = {};
    rows.forEach(function (r) {
      var f = r.gpu;
      if (!(f in cheapest) || r.inr_per_hr < cheapest[f]) cheapest[f] = r.inr_per_hr;
    });

    COLS[6].t = unitLabel();
    COLS[7].t = (state.currency === "USD" ? "₹/hr" : "USD/hr");   // reference column = the other currency
    renderHead();

    var body = rows.map(function (r) {
      var isBest = r.inr_per_hr === cheapest[r.gpu];
      var rel = r.reliability ? Math.round(r.reliability * 100) + "%" : "—";
      var srcCell = r.source_kind === "list"
        ? '<span class="tag b-list" title="' + esc(r.note || "Published list price") +
          (r.as_of ? " · as of " + esc(r.as_of) : "") + '">list' + (r.as_of ? " · " + esc(r.as_of) : "") + "</span>"
        : '<span class="tag b-live" title="Live fetch">' + esc(r.source) + "</span>";
      if (r.stale) srcCell += ' <span class="tag b-spot" title="Reused from previous run (source was unreachable)">stale</span>';
      return '<tr class="' + (isBest ? "cheapest" : "") + '">' +
        '<td><a class="provlink" href="' + esc(r.url) + '" target="_blank" rel="noopener" ' +
          'title="Open ' + esc(r.provider) + ' pricing in a new tab">' + esc(r.provider) +
          ' <span class="ext">↗</span></a></td>' +
        '<td><span class="ptype">' + esc(PTYPE_LABEL[r.provider_type] || r.provider_type) + "</span></td>" +
        '<td class="gpu-strong">' + esc(r.gpu_label || r.gpu) +
          (r.vram_gb && !/gb/i.test(r.gpu_label || "") ? ' <span class="muted">' + r.vram_gb + "GB</span>" : "") +
          (isBest ? '<span class="tag best">cheapest</span>' : "") + "</td>" +
        '<td><span class="tag ' + billingClass(r.billing) + '">' + esc(r.billing) + "</span></td>" +
        "<td>" + esc(r.region) + "</td>" +
        '<td class="num"><span class="rel">' + rel + "</span></td>" +
        '<td class="num inr">' + priceFmt(r.inr_per_hr, r.usd_per_hr, state.unit) + "</td>" +
        '<td class="num muted">' + altFmt(r.inr_per_hr, r.usd_per_hr) + "</td>" +
        "<td>" + srcCell + "</td>" +
      "</tr>";
    }).join("");

    document.getElementById("body").innerHTML = body ||
      '<tr><td colspan="9" class="chart-empty">No offers match these filters. Try <b>Reset filters</b>.</td></tr>';
    document.getElementById("count").textContent = rows.length + " offer" + (rows.length === 1 ? "" : "s");
    var live = rows.filter(function (r) { return r.source_kind === "live"; }).length;
    document.getElementById("cheapNote").textContent =
      rows.length ? "· " + live + " live · " + (rows.length - live) + " list · green = cheapest for its model" : "";

    window.__filteredRows = rows;
    syncControls();
  }

  // ---- KPI cards -----------------------------------------------------------
  function renderKpis() {
    var host = document.getElementById("kpis");
    var cards = POPULAR.filter(function (g) { return DATA.cheapest_by_gpu[g]; }).slice(0, 6);
    host.innerHTML = cards.map(function (g) {
      var c = DATA.cheapest_by_gpu[g];
      var active = state.gpus.has(g) ? " active" : "";
      return '<div class="kpi' + active + '" data-gpu="' + esc(g) + '">' +
        '<div class="g">' + esc(g) + " · cheapest</div>" +
        '<div class="p">' + priceFmt(c.inr_per_hr, c.usd_per_hr, 1) + '<small> /hr</small></div>' +
        '<div class="d">' + esc(c.provider) + " · " + esc(c.billing) + " · " + esc(c.region) + "</div>" +
      "</div>";
    }).join("");
    host.querySelectorAll(".kpi").forEach(function (el) {
      el.onclick = function () {
        var g = el.getAttribute("data-gpu");
        toggleSet(state.gpus, g); render();
        document.getElementById("gpuGroup").open = true;
      };
    });
  }

  // ---- chip builders -------------------------------------------------------
  function toggleSet(set, v) { if (set.has(v)) set.delete(v); else set.add(v); }
  function buildChips(hostId, items, set, labelFn) {
    var host = document.getElementById(hostId);
    host.innerHTML = items.map(function (it) {
      return '<span class="chip" data-v="' + esc(it) + '">' + esc(labelFn ? labelFn(it) : it) + "</span>";
    }).join("");
    host.querySelectorAll(".chip").forEach(function (el) {
      el.onclick = function () { toggleSet(set, el.getAttribute("data-v")); render(); };
    });
  }

  function syncControls() {
    document.querySelectorAll("#ptypeChips .chip").forEach(function (el) { el.classList.toggle("on", state.ptypes.has(el.getAttribute("data-v"))); });
    document.querySelectorAll("#billingChips .chip").forEach(function (el) { el.classList.toggle("on", state.billings.has(el.getAttribute("data-v"))); });
    document.querySelectorAll("#providerChips .chip").forEach(function (el) { el.classList.toggle("on", state.providers.has(el.getAttribute("data-v"))); });
    document.querySelectorAll("#gpuChips .chip").forEach(function (el) { el.classList.toggle("on", state.gpus.has(el.getAttribute("data-v"))); });
    document.getElementById("gpuSelCount").textContent = state.gpus.size ? "(" + state.gpus.size + " selected)" : "";
    document.querySelectorAll("#kpis .kpi").forEach(function (el) { el.classList.toggle("active", state.gpus.has(el.getAttribute("data-gpu"))); });
  }

  // ---- top meta + footer ---------------------------------------------------
  var SOURCE_LABEL = {
    "vast.ai": "Vast.ai", "runpod": "RunPod", "verda": "Verda", "akamai": "Akamai",
    "e2e": "E2E", "jarvislabs": "JarvisLabs", "yotta": "Yotta Labs",
    "nebius": "Nebius", "azure": "Azure", "gcp": "GCP", "aws": "AWS", "curated": "List prices"
  };

  function renderMeta() {
    var fx = DATA.fx || {}; var rate = (fx.rates || {}).USD;
    var st = DATA.source_status || {};
    function statusPill(name) {
      var s = st[name] || "down";
      var cls = s === "ok" ? "ok" : s === "stale" ? "stale" : "down";
      var title = "Source '" + name + "': " + s;
      return '<span class="meta-pill" title="' + title + '"><span class="dot ' + cls + '"></span>' +
        (SOURCE_LABEL[name] || name) + "</span>";
    }
    var pills = Object.keys(st).map(statusPill).join("");
    document.getElementById("topMeta").innerHTML =
      '<span class="meta-pill">💱 <b>1 USD = ₹' + (rate ? rate.toFixed(2) : "?") + "</b>" +
        (fx.stale ? " (stale)" : "") + "</span>" +
      '<span class="meta-pill">⏱ Updated <b>' + relTime(DATA.generated_at) + "</b></span>" + pills;

    var anyStale = Object.keys(st).some(function (k) { return st[k] === "stale" || st[k] === "down"; }) || (fx && fx.stale);
    var b = document.getElementById("staleBanner");
    if (anyStale) {
      b.style.display = "block";
      b.innerHTML = "⚠️ Some sources were unreachable on the last update; those rows may be showing the previous snapshot (marked <b>stale</b>).";
    }

    document.getElementById("footer").innerHTML =
      "<p><b>How to read this:</b> Every price is normalised to <b>one GPU for one hour</b> and converted to INR at the live " +
      "USD→INR rate shown above (source: Frankfurter / ECB). <span style='color:var(--live)'><b>Live</b></span> prices are fetched " +
      "automatically each run — from the <b>Vast.ai</b> &amp; <b>RunPod</b> APIs, the <b>Verda (DataCrunch)</b> and <b>Akamai (Linode)</b> " +
      "APIs, and by scraping the public pricing pages of <b>E2E</b> (native ₹), <b>JarvisLabs</b> and <b>Yotta Labs</b>. " +
      "<span style='color:var(--list)'><b>List</b></span> prices (Nebius, AWS, GCP, Azure) are published/reference rates verified on the " +
      "date shown — AWS &amp; GCP can be switched to true-live with a free credential (see the repo README). Always confirm on the " +
      "provider's console before renting.</p>" +
      "<p><b>Billing:</b> <span class='tag b-spot'>spot</span> = interruptible/bid (cheapest, can be reclaimed) · " +
      "<span class='tag b-community'>community</span> = RunPod community cloud · " +
      "<span class='tag b-ondemand'>on-demand</span> = standard · " +
      "<span class='tag b-secure'>secure</span> = RunPod secure datacenter. " +
      "<b>Rel.</b> is the Vast.ai host reliability score.</p>" +
      "<p class='muted'>Data generated " + esc(DATA.generated_at) + " · " + DATA.counts.total + " offers (" +
      DATA.counts.live + " live / " + DATA.counts.list + " list). Not affiliated with any provider. Prices exclude taxes/egress and change constantly.</p>";
  }

  // ========================================================================
  // Trend chart (hand-rolled SVG)
  // ========================================================================
  function renderTrendSelect() {
    var fams = {};
    HISTORY.forEach(function (h) { Object.keys(h.min_by_gpu || {}).forEach(function (f) { fams[f] = 1; }); });
    var list = Object.keys(fams).sort(function (a, b) {
      var ia = POPULAR.indexOf(a), ib = POPULAR.indexOf(b);
      if (ia !== -1 || ib !== -1) { if (ia === -1) return 1; if (ib === -1) return -1; return ia - ib; }
      return a.localeCompare(b);
    });
    var sel = document.getElementById("trendGpu");
    sel.innerHTML = list.map(function (f) { return '<option value="' + esc(f) + '">' + esc(f) + "</option>"; }).join("");
    sel.value = list.indexOf("H100") !== -1 ? "H100" : list[0];
    sel.onchange = drawTrend;
    document.getElementById("fxOverlay").onchange = drawTrend;
    drawTrend();
  }

  function drawTrend() {
    var box = document.getElementById("chartbox");
    var fam = document.getElementById("trendGpu").value;
    var showFx = document.getElementById("fxOverlay").checked;
    var pts = [];
    HISTORY.forEach(function (h) {
      var v = (h.min_by_gpu || {})[fam];
      if (v != null) pts.push({ t: Date.parse(h.ts), v: v, fx: h.usd_inr });
    });
    document.getElementById("legend").innerHTML = "";
    if (pts.length < 2) {
      box.innerHTML = '<div class="chart-empty">Not enough history yet for <b>' + esc(fam) + "</b>.<br>" +
        "The trend line fills in as the dashboard keeps updating (a fresh snapshot is saved on every refresh).</div>";
      return;
    }
    var W = box.clientWidth || 900, H = 300, P = { l: 52, r: showFx ? 52 : 16, t: 14, b: 28 };
    var iw = W - P.l - P.r, ih = H - P.t - P.b;
    var t0 = pts[0].t, t1 = pts[pts.length - 1].t, tr = (t1 - t0) || 1;
    var vs = pts.map(function (p) { return p.v; });
    var vmin = Math.min.apply(null, vs), vmax = Math.max.apply(null, vs);
    var pad = (vmax - vmin) * 0.15 || vmax * 0.1 || 1; vmin = Math.max(0, vmin - pad); vmax = vmax + pad;
    function X(t) { return P.l + (t - t0) / tr * iw; }
    function Y(v) { return P.t + (1 - (v - vmin) / ((vmax - vmin) || 1)) * ih; }

    var grid = "", ticks = 4;
    for (var i = 0; i <= ticks; i++) {
      var v = vmin + (vmax - vmin) * i / ticks, y = Y(v);
      grid += '<line x1="' + P.l + '" y1="' + y + '" x2="' + (W - P.r) + '" y2="' + y + '" stroke="var(--line)"/>' +
        '<text x="' + (P.l - 8) + '" y="' + (y + 4) + '" text-anchor="end" fill="var(--muted)" font-size="11">' + inrFmt(v) + "</text>";
    }
    var line = pts.map(function (p, i) { return (i ? "L" : "M") + X(p.t).toFixed(1) + " " + Y(p.v).toFixed(1); }).join(" ");
    var area = "M" + X(pts[0].t).toFixed(1) + " " + (P.t + ih) + " " + line.replace("M", "L") + " L" + X(t1).toFixed(1) + " " + (P.t + ih) + " Z";
    var dots = pts.map(function (p) {
      return '<circle cx="' + X(p.t).toFixed(1) + '" cy="' + Y(p.v).toFixed(1) + '" r="2.5" fill="var(--accent)"><title>' +
        new Date(p.t).toLocaleString() + " — " + inrFmt(p.v) + "/hr</title></circle>";
    }).join("");

    var fxPath = "", fxAxis = "";
    if (showFx) {
      var fxs = pts.map(function (p) { return p.fx; });
      var fmin = Math.min.apply(null, fxs), fmax = Math.max.apply(null, fxs);
      var fp = (fmax - fmin) * 0.3 || 1; fmin -= fp; fmax += fp;
      function FY(v) { return P.t + (1 - (v - fmin) / ((fmax - fmin) || 1)) * ih; }
      fxPath = '<path d="' + pts.map(function (p, i) { return (i ? "L" : "M") + X(p.t).toFixed(1) + " " + FY(p.fx).toFixed(1); }).join(" ") +
        '" fill="none" stroke="var(--spot)" stroke-width="1.5" stroke-dasharray="4 3"/>';
      fxAxis = '<text x="' + (W - P.r + 8) + '" y="' + (P.t + 6) + '" fill="var(--spot)" font-size="11">' + fmax.toFixed(1) + "</text>" +
               '<text x="' + (W - P.r + 8) + '" y="' + (P.t + ih) + '" fill="var(--spot)" font-size="11">' + fmin.toFixed(1) + "</text>";
    }

    var xlabels = '<text x="' + P.l + '" y="' + (H - 8) + '" fill="var(--muted)" font-size="11">' + new Date(t0).toLocaleDateString() + "</text>" +
      '<text x="' + (W - P.r) + '" y="' + (H - 8) + '" text-anchor="end" fill="var(--muted)" font-size="11">' + new Date(t1).toLocaleDateString() + "</text>";

    box.innerHTML = '<svg class="chart" viewBox="0 0 ' + W + " " + H + '" preserveAspectRatio="none">' +
      grid +
      '<path d="' + area + '" fill="color-mix(in srgb,var(--accent) 12%,transparent)" stroke="none"/>' +
      '<path d="' + line + '" fill="none" stroke="var(--accent)" stroke-width="2"/>' +
      fxPath + dots + fxAxis + xlabels + "</svg>";

    var last = pts[pts.length - 1].v, first = pts[0].v, chg = last - first;
    var chgPct = first ? (chg / first * 100) : 0;
    document.getElementById("legend").innerHTML =
      "<span><b style='color:var(--accent)'>━</b> " + esc(fam) + " cheapest ₹/hr</span>" +
      (showFx ? "<span><b style='color:var(--spot)'>┄</b> USD→INR</span>" : "") +
      "<span>Now: <b>" + inrFmt(last) + "</b></span>" +
      "<span>Range: <b>" + inrFmt(Math.min.apply(null, vs)) + " – " + inrFmt(Math.max.apply(null, vs)) + "</b></span>" +
      "<span>Change: <b style='color:" + (chg <= 0 ? "var(--good)" : "var(--spot)") + "'>" +
        (chg <= 0 ? "▼ " : "▲ ") + inrFmt(Math.abs(chg)) + " (" + chgPct.toFixed(1) + "%)</b></span>";
  }

  // ========================================================================
  // CSV export
  // ========================================================================
  function exportCsv() {
    var rows = window.__filteredRows || [];
    var head = ["provider", "provider_type", "gpu", "gpu_label", "vram_gb", "billing", "region", "reliability", "usd_per_hr", "inr_per_hr", "source_kind", "source", "as_of"];
    var lines = [head.join(",")];
    rows.forEach(function (r) {
      lines.push(head.map(function (k) {
        var v = r[k]; if (v == null) v = "";
        v = String(v);
        if (/^[=+\-@\t\r]/.test(v)) v = "'" + v;          // neutralise CSV formula injection
        return /[",\n]/.test(v) ? '"' + v.replace(/"/g, '""') + '"' : v;
      }).join(","));
    });
    var blob = new Blob([lines.join("\n")], { type: "text/csv" });
    var a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = "gpu-prices-inr-" + new Date().toISOString().slice(0, 16).replace("T", "_").replace(/:/g, "") + ".csv";
    document.body.appendChild(a); a.click(); a.remove();
  }

  // ========================================================================
  // Wire up
  // ========================================================================
  function bind() {
    document.getElementById("q").addEventListener("input", function (e) { state.q = e.target.value; render(); });
    document.getElementById("liveOnly").addEventListener("change", function (e) { state.liveOnly = e.target.checked; render(); });
    document.getElementById("unit").addEventListener("change", function (e) { state.unit = +e.target.value; render(); });
    document.getElementById("currency").addEventListener("change", function (e) { state.currency = e.target.value; renderKpis(); render(); });
    document.getElementById("minVram").addEventListener("change", function (e) { state.minVram = +e.target.value; render(); });
    var slider = document.getElementById("maxInr");
    slider.addEventListener("input", function (e) {
      state.maxInr = sliderToPrice(+e.target.value);
      document.getElementById("maxInrV").textContent = state.maxInr === Infinity ? "Any" : inrFmt(state.maxInr);
      render();
    });
    document.getElementById("csvBtn").addEventListener("click", exportCsv);
    document.getElementById("resetBtn").addEventListener("click", function () {
      state.q = ""; state.gpus.clear(); state.providers.clear(); state.ptypes.clear(); state.billings.clear();
      state.liveOnly = false; state.minVram = 0; state.maxInr = Infinity; state.unit = 1; state.currency = "INR";
      state.sort = "inr_per_hr", state.dir = 1;
      document.getElementById("q").value = ""; document.getElementById("liveOnly").checked = false;
      document.getElementById("minVram").value = "0"; document.getElementById("unit").value = "1";
      document.getElementById("currency").value = "INR";
      slider.value = 100; document.getElementById("maxInrV").textContent = "Any";
      renderKpis(); render();
    });
  }

  buildChips("ptypeChips", PTYPES, state.ptypes, function (t) { return PTYPE_LABEL[t] || t; });
  buildChips("billingChips", BILLINGS, state.billings);
  buildChips("providerChips", PROVIDERS, state.providers);
  buildChips("gpuChips", GPUS, state.gpus);
  renderKpis();
  renderMeta();
  bind();
  render();
  renderTrendSelect();
  window.addEventListener("resize", function () { clearTimeout(window.__rz); window.__rz = setTimeout(drawTrend, 150); });
})();
