"""Build a self-contained interactive price chart (one HTML file).

Usage:
    python .agents/skills/price-chart/scripts/build_chart.py BARS --out TASK/chart.html \
        [--title "AAPL 5m"] [--overlays vwap,ema:200] [--panes rsi:14,macd] \
        [--profile] [--profile-bins 60] [--levels levels.json] \
        [--trades trades.json] [--equity equity.json] [--stats stats.json] \
        [--last N]

BARS: JSON or CSV of {date, open, high, low, close, volume}, oldest first.
--overlays: comma list drawn on the price chart: ema:N, sma:N, vwap, bb:N:K.
    Default: vwap,ema:200 on intraday bars; ema:50,ema:200 on daily bars.
--panes: comma list drawn under it: rsi:N, macd:F:S:G, atr:N.
--profile: volume profile on the right edge with POC and value area lines.
--levels: JSON list of {"price": 123.4, "label": "PDH", "color": "#hex"}.
--trades / --equity / --stats: a backtest's trades.json, equity.json and
    stats.json (strategy-backtest writes them) for markers, curve and summary.
--last N: keep only the last N bars (default 5000) so the file stays light.

The page has drawing tools (horizontal level, trendline, measure, clear) that
the viewer's browser remembers per chart title. Prices and times are exchange
local, as in the bars.
"""

from __future__ import annotations

import argparse
import html
import json
import math
import sys
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1] / "volume-profile" / "scripts"))
from indicators import compute_overlay, compute_pane, is_intraday, load_bars  # noqa: E402

LIB = "https://cdn.jsdelivr.net/npm/lightweight-charts@4.2.0/dist/lightweight-charts.standalone.production.js"
PALETTE = ["#f5a623", "#4aa3ff", "#c86bfa", "#2ec4b6", "#ff6b9a", "#9ccc65", "#ffd54f"]


def _ts(t: pd.Timestamp) -> int:
    """Exchange-local wall time as a UTC timestamp, so axis labels read local."""
    return int(pd.Timestamp(t).tz_localize("UTC").timestamp()) if pd.Timestamp(t).tzinfo is None \
        else int(pd.Timestamp(t).timestamp())


def _series(s: pd.Series) -> list:
    return [{"time": _ts(t), "value": round(float(v), 6)}
            for t, v in s.items() if v is not None and not (isinstance(v, float) and math.isnan(v))]


def build(df: pd.DataFrame, title: str, overlays: list[str], panes: list[str], profile: bool,
          profile_bins: int, levels: list, trades: list | None, equity: list | None,
          stats: dict | None) -> dict:
    data = {
        "title": title,
        "intraday": is_intraday(df),
        "candles": [{"time": _ts(t), "open": r.open, "high": r.high, "low": r.low,
                     "close": r.close} for t, r in df.iterrows()],
        "volume": [{"time": _ts(t), "value": r.volume,
                    "color": "rgba(38,166,154,0.45)" if r.close >= r.open else "rgba(239,83,80,0.45)"}
                   for t, r in df.iterrows()],
        "overlays": [], "panes": [], "levels": levels or [], "markers": [],
        "profile": None, "equity": None, "stats": stats,
    }
    ci = 0
    for spec in overlays:
        for label, s in compute_overlay(df, spec).items():
            color = PALETTE[ci % len(PALETTE)]
            if label.startswith("BB"):
                color = "rgba(74,163,255,0.8)" if "mid" not in label else "rgba(74,163,255,0.45)"
            elif label == "VWAP":
                color = "#e0e0e0"
            else:
                ci += 1
            if label == "VWAP" and data["intraday"]:
                # Session VWAP restarts each day: one segment per session, so
                # the line breaks between days instead of jumping.
                segs = [_series(g) for _, g in s.groupby(s.index.normalize())]
                segs = [x for x in segs if x]
            else:
                segs = [_series(s)]
            data["overlays"].append({"label": label, "color": color, "segments": segs})
    for spec in panes:
        p = compute_pane(df, spec)
        data["panes"].append({
            "title": p["title"], "bands": p.get("bands"),
            "lines": [{"label": k, "color": PALETTE[i % len(PALETTE)], "data": _series(v)}
                      for i, (k, v) in enumerate(p["lines"].items())],
            "hist": _series(p["hist"]) if "hist" in p else None,
        })
    if profile:
        from volume_profile import profile as vp
        p = vp(df, bins=profile_bins)
        data["profile"] = {k: p[k] for k in ("edges", "volume", "buy_volume", "poc", "vah", "val")}
    if trades:
        times = {c["time"] for c in data["candles"]}
        first, last = data["candles"][0]["time"], data["candles"][-1]["time"]
        for t in trades:
            et, xt = _ts(pd.Timestamp(t["entry_time"])), _ts(pd.Timestamp(t["exit_time"]))
            long_ = t["side"] == "long"
            if first <= et <= last and et in times:
                data["markers"].append({
                    "time": et, "position": "belowBar" if long_ else "aboveBar",
                    "color": "#26a69a" if long_ else "#ef5350",
                    "shape": "arrowUp" if long_ else "arrowDown",
                    "text": "L" if long_ else "S"})
            if first <= xt <= last and xt in times:
                win = (t.get("pnl") or 0) > 0
                r = t.get("r")
                data["markers"].append({
                    "time": xt, "position": "aboveBar" if long_ else "belowBar",
                    "color": "#26a69a" if win else "#ef5350", "shape": "circle",
                    "text": f"{r:+.1f}R" if r is not None else t.get("reason", "exit")})
        data["markers"].sort(key=lambda m: m["time"])
    if equity:
        data["equity"] = [{"time": _ts(pd.Timestamp(e["time"])), "value": e["equity"]}
                          for e in equity if e.get("equity") is not None]
    return data


def render(data: dict) -> str:
    payload = json.dumps(data, separators=(",", ":")).replace("</", "<\\/")
    return (TEMPLATE.replace("__TITLE__", html.escape(data["title"]))
            .replace("__LIB__", LIB).replace("__DATA__", payload))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Build an interactive price chart HTML file.")
    ap.add_argument("bars")
    ap.add_argument("--out", required=True)
    ap.add_argument("--title")
    ap.add_argument("--overlays")
    ap.add_argument("--panes", default="")
    ap.add_argument("--profile", action="store_true")
    ap.add_argument("--profile-bins", type=int, default=60)
    ap.add_argument("--levels")
    ap.add_argument("--trades")
    ap.add_argument("--equity")
    ap.add_argument("--stats")
    ap.add_argument("--last", type=int, default=5000)
    a = ap.parse_args(argv)

    df = load_bars(a.bars)
    full = df
    if a.last and len(df) > a.last:
        df = df.iloc[-a.last:]
    if a.overlays is None:
        a.overlays = "vwap,ema:200" if is_intraday(df) else "ema:50,ema:200"
    overlays = [s for s in a.overlays.split(",") if s.strip()]
    panes = [s for s in a.panes.split(",") if s.strip()]
    # Indicators need warm-up history, so compute on the full set and trim after.
    if len(full) > len(df):
        data = build(full, a.title or Path(a.bars).stem, overlays, panes, False, a.profile_bins,
                     [], None, None, None)
        start = df.index[0]
        cut = _ts(start)
        for o in data["overlays"]:
            segs = [[p for p in seg if p["time"] >= cut] for seg in o["segments"]]
            o["segments"] = [seg for seg in segs if seg]
        for p in data["panes"]:
            for ln in p["lines"]:
                ln["data"] = [x for x in ln["data"] if x["time"] >= cut]
            if p["hist"]:
                p["hist"] = [x for x in p["hist"] if x["time"] >= cut]
        data["candles"] = [c for c in data["candles"] if c["time"] >= cut]
        data["volume"] = [v for v in data["volume"] if v["time"] >= cut]
        extra = build(df, data["title"], [], [], a.profile, a.profile_bins,
                      json.loads(Path(a.levels).read_text()) if a.levels else [],
                      json.loads(Path(a.trades).read_text()) if a.trades else None,
                      json.loads(Path(a.equity).read_text()) if a.equity else None,
                      json.loads(Path(a.stats).read_text()) if a.stats else None)
        for k in ("profile", "levels", "markers", "equity", "stats"):
            data[k] = extra[k]
    else:
        data = build(df, a.title or Path(a.bars).stem, overlays, panes, a.profile, a.profile_bins,
                     json.loads(Path(a.levels).read_text()) if a.levels else [],
                     json.loads(Path(a.trades).read_text()) if a.trades else None,
                     json.loads(Path(a.equity).read_text()) if a.equity else None,
                     json.loads(Path(a.stats).read_text()) if a.stats else None)
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(data))
    print(json.dumps({"file": str(out), "bars": len(data["candles"]),
                      "overlays": [o["label"] for o in data["overlays"]],
                      "panes": [p["title"] for p in data["panes"]],
                      "profile": bool(data["profile"]), "markers": len(data["markers"]),
                      "equity": bool(data["equity"])}))
    return 0


TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>
:root{--bg:#131722;--panel:#1b2030;--text:#d1d4dc;--muted:#8b93a7;--grid:#232838;--border:#2a3042;--accent:#4aa3ff;}
@media (prefers-color-scheme: light){:root:not([data-theme="dark"]){--bg:#ffffff;--panel:#f4f6fa;--text:#1f2430;--muted:#5d6577;--grid:#eef0f4;--border:#d9dde6;--accent:#1f6fd1;}}
*{box-sizing:border-box}
html,body{margin:0;background:var(--bg);color:var(--text);font:13px/1.4 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;}
header{display:flex;flex-wrap:wrap;align-items:center;gap:8px 16px;padding:10px 14px;border-bottom:1px solid var(--border);}
header h1{font-size:15px;margin:0;font-weight:600}
#legend{color:var(--muted);font-variant-numeric:tabular-nums}
.tools{display:flex;gap:4px;margin-left:auto;flex-wrap:wrap}
.tools button{background:var(--panel);color:var(--text);border:1px solid var(--border);border-radius:6px;padding:5px 10px;font:inherit;cursor:pointer}
.tools button.on{border-color:var(--accent);color:var(--accent)}
#hint{color:var(--muted);font-size:12px;padding:4px 14px;min-height:22px}
.wrap{position:relative}
#profile{position:absolute;top:0;left:0;pointer-events:none;z-index:5}
.pane-title{color:var(--muted);font-size:12px;padding:6px 14px 0}
#stats{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:8px;padding:12px 14px;border-top:1px solid var(--border)}
#stats div{background:var(--panel);border:1px solid var(--border);border-radius:8px;padding:8px 10px}
#stats b{display:block;font-size:16px;font-variant-numeric:tabular-nums}
#stats span{color:var(--muted);font-size:11px;text-transform:uppercase;letter-spacing:.04em}
#warn{color:#f5a623;padding:0 14px 10px}
footer{color:var(--muted);font-size:11px;padding:8px 14px 14px}
</style>
</head>
<body>
<header>
  <h1 id="title"></h1>
  <div id="legend"></div>
  <div class="tools">
    <button data-tool="cursor" class="on">Cursor</button>
    <button data-tool="hline">Level</button>
    <button data-tool="trend">Trendline</button>
    <button data-tool="measure">Measure</button>
    <button data-tool="clear">Clear drawings</button>
    <button data-tool="profile" id="profileBtn">Volume profile</button>
    <button data-tool="fit">Fit</button>
  </div>
</header>
<div id="hint"></div>
<div class="wrap" id="mainWrap"><div id="main"></div><canvas id="profile"></canvas></div>
<div id="panes"></div>
<div id="stats" hidden></div>
<div id="warn"></div>
<footer>Charts: TradingView Lightweight Charts (Apache-2.0). Volume profile and delta are estimated from bars, not trade data. Not investment advice.</footer>
<script type="application/json" id="data">__DATA__</script>
<script src="__LIB__"></script>
<script>
(function () {
  "use strict";
  var D = JSON.parse(document.getElementById("data").textContent);
  var css = getComputedStyle(document.documentElement);
  function v(name, fb) { var x = css.getPropertyValue(name).trim(); return x || fb; }
  var C = { bg: v("--bg", "#131722"), text: v("--text", "#d1d4dc"), grid: v("--grid", "#232838"),
            border: v("--border", "#2a3042"), accent: v("--accent", "#4aa3ff") };
  document.getElementById("title").textContent = D.title;
  if (typeof LightweightCharts === "undefined") {
    document.getElementById("hint").textContent = "The chart library could not load (cdn.jsdelivr.net).";
    return;
  }
  var LC = LightweightCharts;
  var charts = [];
  function base(height, el) {
    return LC.createChart(el, {
      height: height, autoSize: false, width: el.clientWidth || window.innerWidth,
      layout: { background: { type: "solid", color: C.bg }, textColor: C.text },
      grid: { vertLines: { color: C.grid }, horzLines: { color: C.grid } },
      rightPriceScale: { borderColor: C.border },
      timeScale: { borderColor: C.border, timeVisible: D.intraday, secondsVisible: false, rightOffset: 6 },
      crosshair: { mode: LC.CrosshairMode.Normal }
    });
  }
  var mainEl = document.getElementById("main");
  var mainH = Math.max(360, Math.round(window.innerHeight * 0.6));
  var chart = base(mainH, mainEl);
  charts.push(chart);
  var candles = chart.addCandlestickSeries({ upColor: "#26a69a", downColor: "#ef5350", borderVisible: false,
    wickUpColor: "#26a69a", wickDownColor: "#ef5350" });
  candles.setData(D.candles);
  var vol = chart.addHistogramSeries({ priceScaleId: "vol", priceFormat: { type: "volume" }, lastValueVisible: false, priceLineVisible: false });
  chart.priceScale("vol").applyOptions({ scaleMargins: { top: 0.8, bottom: 0 } });
  vol.setData(D.volume);
  var overlaySeries = D.overlays.map(function (o) {
    var list = o.segments.map(function (seg) {
      var s = chart.addLineSeries({ color: o.color, lineWidth: o.label.indexOf("BB") === 0 ? 1 : 2,
        priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false });
      s.setData(seg);
      return s;
    });
    return { label: o.label, color: o.color, series: list };
  });
  if (D.markers.length) candles.setMarkers(D.markers);
  D.levels.forEach(function (l) {
    candles.createPriceLine({ price: l.price, color: l.color || C.accent, lineWidth: 1,
      lineStyle: LC.LineStyle.Dashed, axisLabelVisible: true, title: l.label || "" });
  });

  // Lower panes, time-synced with the main chart.
  var panesEl = document.getElementById("panes");
  function addPane(title, h) {
    var t = document.createElement("div"); t.className = "pane-title"; t.textContent = title;
    var el = document.createElement("div");
    panesEl.appendChild(t); panesEl.appendChild(el);
    var c = base(h, el); charts.push(c);
    // One blank point per candle keeps bar N at the same position in every
    // pane, so panes stay aligned although indicators skip warm-up bars.
    c.addLineSeries({ visible: false, priceLineVisible: false, lastValueVisible: false })
      .setData(D.candles.map(function (k) { return { time: k.time }; }));
    return c;
  }
  D.panes.forEach(function (p) {
    var c = addPane(p.title, 150);
    if (p.hist) {
      var hs = c.addHistogramSeries({ priceLineVisible: false, lastValueVisible: false });
      hs.setData(p.hist.map(function (x) { return { time: x.time, value: x.value,
        color: x.value >= 0 ? "rgba(38,166,154,0.6)" : "rgba(239,83,80,0.6)" }; }));
    }
    p.lines.forEach(function (ln, i) {
      var s = c.addLineSeries({ color: ln.color, lineWidth: 2, priceLineVisible: false });
      s.setData(ln.data);
      if (i === 0 && p.bands) p.bands.forEach(function (b) {
        s.createPriceLine({ price: b, color: C.border, lineWidth: 1, lineStyle: LC.LineStyle.Dotted, axisLabelVisible: false });
      });
    });
  });
  if (D.equity) {
    var ec = addPane("Equity", 170);
    var es = ec.addAreaSeries({ lineColor: C.accent, topColor: "rgba(74,163,255,0.3)", bottomColor: "rgba(74,163,255,0.02)", lineWidth: 2 });
    es.setData(D.equity);
  }
  var syncing = false;
  charts.forEach(function (c) {
    c.timeScale().subscribeVisibleLogicalRangeChange(function (r) {
      if (syncing || !r) return; syncing = true;
      charts.forEach(function (o) { if (o !== c) o.timeScale().setVisibleLogicalRange(r); });
      syncing = false; drawProfile();
    });
  });

  // Legend.
  var legend = document.getElementById("legend");
  function fmt(x) { return x == null ? "-" : (Math.abs(x) >= 1000 ? x.toFixed(2) : x.toPrecision(6).replace(/\.?0+$/, "")); }
  function showLegend(param) {
    var bar = param && param.seriesData ? param.seriesData.get(candles) : null;
    if (!bar) bar = D.candles[D.candles.length - 1];
    var chg = bar.close - bar.open;
    var txt = "O " + fmt(bar.open) + "  H " + fmt(bar.high) + "  L " + fmt(bar.low) + "  C " + fmt(bar.close) +
      "  " + (chg >= 0 ? "+" : "") + fmt(chg);
    overlaySeries.forEach(function (o) {
      if (!param || !param.seriesData) return;
      for (var i = 0; i < o.series.length; i++) {
        var p = param.seriesData.get(o.series[i]);
        if (p && p.value != null) { txt += "   " + o.label + " " + fmt(p.value); break; }
      }
    });
    legend.textContent = txt;
  }
  chart.subscribeCrosshairMove(showLegend); showLegend(null);

  // Volume profile drawn on a canvas over the right side of the price chart.
  var canvas = document.getElementById("profile");
  var profileOn = !!D.profile, profileLines = [];
  var pBtn = document.getElementById("profileBtn");
  if (!D.profile) pBtn.hidden = true; else pBtn.classList.toggle("on", profileOn);
  function setProfileLines(on) {
    profileLines.forEach(function (l) { candles.removePriceLine(l); }); profileLines = [];
    if (!on || !D.profile) return;
    [["POC", D.profile.poc, "#f5a623"], ["VAH", D.profile.vah, "#8b93a7"], ["VAL", D.profile.val, "#8b93a7"]].forEach(function (x) {
      profileLines.push(candles.createPriceLine({ price: x[1], color: x[2], lineWidth: 1,
        lineStyle: x[0] === "POC" ? LC.LineStyle.Solid : LC.LineStyle.Dashed, axisLabelVisible: true, title: x[0] }));
    });
  }
  function drawProfile() {
    var w = mainEl.clientWidth, h = mainH, dpr = window.devicePixelRatio || 1;
    canvas.width = w * dpr; canvas.height = h * dpr; canvas.style.width = w + "px"; canvas.style.height = h + "px";
    var ctx = canvas.getContext("2d"); ctx.setTransform(dpr, 0, 0, dpr, 0, 0); ctx.clearRect(0, 0, w, h);
    if (!profileOn || !D.profile) return;
    var P = D.profile, maxV = Math.max.apply(null, P.volume) || 1;
    var scaleW = chart.priceScale("right").width() || 60;
    var right = w - scaleW - 4, maxLen = Math.min(220, (w - scaleW) * 0.28);
    for (var i = 0; i < P.volume.length; i++) {
      var y1 = candles.priceToCoordinate(P.edges[i + 1]), y0 = candles.priceToCoordinate(P.edges[i]);
      if (y1 == null || y0 == null) continue;
      var top = Math.min(y0, y1), hh = Math.max(1, Math.abs(y0 - y1) - 1);
      var len = P.volume[i] / maxV * maxLen, buyLen = P.volume[i] ? len * P.buy_volume[i] / P.volume[i] : 0;
      var inVA = P.edges[i] >= P.val - 1e-9 && P.edges[i + 1] <= P.vah + 1e-9;
      ctx.globalAlpha = inVA ? 0.55 : 0.28;
      ctx.fillStyle = "#26a69a"; ctx.fillRect(right - len, top, buyLen, hh);
      ctx.fillStyle = "#ef5350"; ctx.fillRect(right - len + buyLen, top, len - buyLen, hh);
    }
    ctx.globalAlpha = 1;
  }
  setProfileLines(profileOn);

  // Drawing tools. Kept in this browser per chart title.
  var key = "tdchart:" + D.title, tool = "cursor", pending = null, drawings = [], drawn = [];
  var hint = document.getElementById("hint");
  var hints = { cursor: "", hline: "Click a price to add a level.", trend: "Click two points to draw a trendline.",
    measure: "Click two points to measure the move." };
  try { drawings = JSON.parse(localStorage.getItem(key) || "[]"); } catch (e) { drawings = []; }
  function save() { try { localStorage.setItem(key, JSON.stringify(drawings)); } catch (e) {} }
  function paint(d) {
    if (d.type === "hline") {
      drawn.push({ kind: "price", obj: candles.createPriceLine({ price: d.price, color: C.accent, lineWidth: 1,
        lineStyle: LC.LineStyle.Solid, axisLabelVisible: true, title: "" }) });
    } else if (d.type === "trend" && d.a.time !== d.b.time) {
      var s = chart.addLineSeries({ color: C.accent, lineWidth: 2, priceLineVisible: false, lastValueVisible: false,
        crosshairMarkerVisible: false });
      var pts = [d.a, d.b].sort(function (x, y) { return x.time - y.time; });
      s.setData(pts.map(function (p) { return { time: p.time, value: p.price }; }));
      drawn.push({ kind: "series", obj: s });
    }
  }
  drawings.forEach(paint);
  function clearAll() {
    drawn.forEach(function (d) { if (d.kind === "price") candles.removePriceLine(d.obj); else chart.removeSeries(d.obj); });
    drawn = []; drawings = []; save();
  }
  function setTool(t) {
    tool = t; pending = null; hint.textContent = hints[t] || "";
    document.querySelectorAll(".tools button[data-tool]").forEach(function (b) {
      if (["cursor", "hline", "trend", "measure"].indexOf(b.dataset.tool) >= 0) b.classList.toggle("on", b.dataset.tool === t);
    });
  }
  document.querySelectorAll(".tools button").forEach(function (b) {
    b.addEventListener("click", function () {
      var t = b.dataset.tool;
      if (t === "clear") { clearAll(); return; }
      if (t === "fit") { chart.timeScale().fitContent(); return; }
      if (t === "profile") { profileOn = !profileOn; b.classList.toggle("on", profileOn); setProfileLines(profileOn); drawProfile(); return; }
      setTool(t);
    });
  });
  chart.subscribeClick(function (param) {
    if (tool === "cursor" || !param.point || param.time == null) return;
    var price = candles.coordinateToPrice(param.point.y);
    if (price == null) return;
    var pt = { time: param.time, price: price };
    if (tool === "hline") { var d = { type: "hline", price: price }; drawings.push(d); paint(d); save(); return; }
    if (!pending) { pending = pt; hint.textContent = "Now click the second point."; return; }
    if (tool === "trend") { var tr = { type: "trend", a: pending, b: pt }; drawings.push(tr); paint(tr); save(); hint.textContent = hints.trend; }
    if (tool === "measure") {
      var diff = pt.price - pending.price, pct = diff / pending.price * 100;
      var bars = Math.abs(chart.timeScale().timeToCoordinate(pt.time) - chart.timeScale().timeToCoordinate(pending.time));
      var a = D.candles.findIndex(function (c) { return c.time === pending.time; });
      var z = D.candles.findIndex(function (c) { return c.time === pt.time; });
      hint.textContent = "Move " + (diff >= 0 ? "+" : "") + fmt(diff) + " (" + (pct >= 0 ? "+" : "") + pct.toFixed(2) + "%) over " +
        (a >= 0 && z >= 0 ? Math.abs(z - a) + " bars" : Math.round(bars) + " px") + ".";
    }
    pending = null;
  });

  // Backtest summary.
  if (D.stats) {
    var S = D.stats, box = document.getElementById("stats"); box.hidden = false;
    var items = [["Trades", S.trades], ["Win rate", S.win_rate_pct != null ? S.win_rate_pct + "%" : "-"],
      ["Expectancy", S.expectancy_r != null ? S.expectancy_r + "R" : "-"], ["Profit factor", S.profit_factor != null ? S.profit_factor : "-"],
      ["Total return", S.total_return_pct + "%"], ["Buy & hold", S.buy_hold_return_pct + "%"],
      ["Max drawdown", S.max_drawdown_pct + "%"], ["Sharpe", S.sharpe != null ? S.sharpe : "-"],
      ["Losing streak", S.longest_losing_streak != null ? S.longest_losing_streak : "-"]];
    items.forEach(function (it) {
      var d = document.createElement("div"), s = document.createElement("span"), b = document.createElement("b");
      s.textContent = it[0]; b.textContent = it[1]; d.appendChild(s); d.appendChild(b); box.appendChild(d);
    });
    if (S.warning) document.getElementById("warn").textContent = S.warning;
  }

  function resize() {
    charts.forEach(function (c) { c.applyOptions({ width: mainEl.clientWidth }); });
    drawProfile();
  }
  window.addEventListener("resize", resize);
  // Dragging or wheeling the price axis moves bars without a time-range event.
  var queued = false;
  function redrawSoon() { if (queued) return; queued = true; requestAnimationFrame(function () { queued = false; drawProfile(); }); }
  ["wheel", "pointermove", "pointerup", "dblclick"].forEach(function (ev) { mainEl.addEventListener(ev, redrawSoon, { passive: true }); });
  chart.timeScale().fitContent();
  // The price scale has no coordinates until the first paint, so the profile
  // is redrawn once the chart has laid out, and again on crosshair moves.
  requestAnimationFrame(function () { resize(); });
  setTimeout(resize, 250); setTimeout(resize, 1000);
  chart.subscribeCrosshairMove(redrawSoon);
})();
</script>
</body>
</html>
"""

if __name__ == "__main__":
    sys.exit(main())
