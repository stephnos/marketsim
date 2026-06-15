"use strict";

const ACCOUNT = "default";
const state = {
  symbol: "AAPL",
  range: "1D",
  quote: null,
  history: null,
  watch: [],
  moversTab: "gainers",
  movers: null,
  tradeSide: "buy",
};

// ---- helpers --------------------------------------------------------------
const $ = (id) => document.getElementById(id);
const api = async (path, opts) => {
  const res = await fetch(path, opts);
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || res.statusText);
  }
  return res.json();
};
const fmtUSD = (n, dp = 2) =>
  "$" + Number(n).toLocaleString("en-US", { minimumFractionDigits: dp, maximumFractionDigits: dp });
const fmtNum = (n) => Number(n).toLocaleString("en-US");
const fmtPct = (n) => (n >= 0 ? "+" : "") + Number(n).toFixed(2) + "%";
const fmtSigned = (n) => (n >= 0 ? "+" : "") + fmtUSD(n);
const cls = (n) => (n >= 0 ? "up" : "down");
function fmtCap(n) {
  if (n >= 1e12) return "$" + (n / 1e12).toFixed(2) + "T";
  if (n >= 1e9) return "$" + (n / 1e9).toFixed(2) + "B";
  if (n >= 1e6) return "$" + (n / 1e6).toFixed(2) + "M";
  return fmtUSD(n, 0);
}
function fmtVol(n) {
  if (n >= 1e9) return (n / 1e9).toFixed(2) + "B";
  if (n >= 1e6) return (n / 1e6).toFixed(2) + "M";
  if (n >= 1e3) return (n / 1e3).toFixed(1) + "K";
  return fmtNum(n);
}

let toastTimer;
function toast(msg, isErr = false) {
  const el = $("toast");
  el.textContent = msg;
  el.className = "toast" + (isErr ? " err" : "");
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (el.hidden = true), 2600);
}

// ---- canvas area chart ----------------------------------------------------
class AreaChart {
  constructor(canvas, tip) {
    this.canvas = canvas;
    this.tip = tip;
    this.ctx = canvas.getContext("2d");
    this.points = [];
    this.up = true;
    this._bind();
    window.addEventListener("resize", () => this.draw());
  }
  setData(points, up) {
    this.points = points;
    this.up = up;
    this.draw();
  }
  _bind() {
    this.canvas.addEventListener("mousemove", (e) => this._hover(e));
    this.canvas.addEventListener("mouseleave", () => {
      this.tip.hidden = true;
      this.draw();
    });
  }
  _geom() {
    const dpr = window.devicePixelRatio || 1;
    const w = this.canvas.clientWidth;
    const h = this.canvas.clientHeight;
    if (this.canvas.width !== w * dpr || this.canvas.height !== h * dpr) {
      this.canvas.width = w * dpr;
      this.canvas.height = h * dpr;
    }
    this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    return { w, h, padT: 14, padB: 22 };
  }
  draw(hoverIdx = -1) {
    const ctx = this.ctx;
    const { w, h, padT, padB } = this._geom();
    ctx.clearRect(0, 0, w, h);
    const pts = this.points;
    if (pts.length < 2) return;

    const ys = pts.map((p) => p.c);
    let min = Math.min(...ys), max = Math.max(...ys);
    const pad = (max - min) * 0.12 || max * 0.02 || 1;
    min -= pad; max += pad;
    const plotH = h - padT - padB;
    const x = (i) => (i / (pts.length - 1)) * w;
    const y = (v) => padT + (1 - (v - min) / (max - min)) * plotH;

    const color = this.up ? "#0a8f54" : "#d23a40";
    const fill = this.up ? "rgba(10,143,84,.13)" : "rgba(210,58,64,.12)";

    // gridlines
    ctx.strokeStyle = "#eef0f3";
    ctx.lineWidth = 1;
    ctx.fillStyle = "#aab0ba";
    ctx.font = "11px Inter, sans-serif";
    ctx.textAlign = "right";
    for (let g = 0; g <= 3; g++) {
      const gv = min + (g / 3) * (max - min);
      const gy = y(gv);
      ctx.beginPath();
      ctx.moveTo(0, gy);
      ctx.lineTo(w, gy);
      ctx.stroke();
      ctx.fillText(gv.toFixed(2), w - 4, gy - 3);
    }

    // baseline (prev close reference) — dashed
    const base = this.baseline;
    if (base != null && base > min && base < max) {
      ctx.save();
      ctx.strokeStyle = "#c2c8d1";
      ctx.setLineDash([4, 4]);
      ctx.beginPath();
      ctx.moveTo(0, y(base));
      ctx.lineTo(w, y(base));
      ctx.stroke();
      ctx.restore();
    }

    // area fill
    ctx.beginPath();
    ctx.moveTo(x(0), y(pts[0].c));
    for (let i = 1; i < pts.length; i++) ctx.lineTo(x(i), y(pts[i].c));
    ctx.lineTo(x(pts.length - 1), h - padB);
    ctx.lineTo(x(0), h - padB);
    ctx.closePath();
    const grad = ctx.createLinearGradient(0, padT, 0, h - padB);
    grad.addColorStop(0, fill);
    grad.addColorStop(1, "rgba(255,255,255,0)");
    ctx.fillStyle = grad;
    ctx.fill();

    // line
    ctx.beginPath();
    ctx.moveTo(x(0), y(pts[0].c));
    for (let i = 1; i < pts.length; i++) ctx.lineTo(x(i), y(pts[i].c));
    ctx.strokeStyle = color;
    ctx.lineWidth = 1.8;
    ctx.lineJoin = "round";
    ctx.stroke();

    // crosshair
    if (hoverIdx >= 0 && hoverIdx < pts.length) {
      const hx = x(hoverIdx), hy = y(pts[hoverIdx].c);
      ctx.strokeStyle = "#b6bcc6";
      ctx.setLineDash([3, 3]);
      ctx.beginPath();
      ctx.moveTo(hx, padT);
      ctx.lineTo(hx, h - padB);
      ctx.stroke();
      ctx.setLineDash([]);
      ctx.beginPath();
      ctx.arc(hx, hy, 4, 0, Math.PI * 2);
      ctx.fillStyle = color;
      ctx.fill();
      ctx.strokeStyle = "#fff";
      ctx.lineWidth = 2;
      ctx.stroke();
    }
  }
  _hover(e) {
    const pts = this.points;
    if (pts.length < 2) return;
    const rect = this.canvas.getBoundingClientRect();
    const px = e.clientX - rect.left;
    const idx = Math.round((px / rect.width) * (pts.length - 1));
    const i = Math.max(0, Math.min(pts.length - 1, idx));
    this.draw(i);
    const p = pts[i];
    const d = new Date(p.t * 1000);
    const isIntraday = state.range === "1D";
    const label = isIntraday
      ? d.toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" })
      : d.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
    this.tip.innerHTML = `<b>${fmtUSD(p.c)}</b> · ${label}`;
    this.tip.hidden = false;
    const tx = (i / (pts.length - 1)) * rect.width;
    this.tip.style.left = Math.max(40, Math.min(rect.width - 40, tx)) + "px";
    this.tip.style.top = "8px";
  }
}

let chart;

// ---- rendering ------------------------------------------------------------
const money = (v) => (typeof v === "number" ? fmtUSD(v) : "—");
const range2 = (lo, hi) =>
  typeof lo === "number" && typeof hi === "number" ? `${fmtUSD(lo)} – ${fmtUSD(hi)}` : "—";

function renderQuote() {
  const q = state.quote;
  if (!q) return;
  $("qSymbol").textContent = q.symbol;
  $("qName").textContent = q.name || "";
  $("qSector").textContent = q.sector || q.exchange || "";
  $("qPrice").textContent = fmtUSD(q.price);
  const c = $("qChange");
  c.className = "quote-change " + cls(q.change);
  c.textContent = `${fmtSigned(q.change)} (${fmtPct(q.changePercent)})`;

  const badge = badgeFor(q);
  $("qAsOf").innerHTML = badge;
  document.title = `${q.symbol} ${fmtUSD(q.price)} — MarketSim`;

  const stats = [
    ["Open", money(q.open)],
    ["Prev Close", money(q.prevClose)],
    ["Day Range", range2(q.dayLow, q.dayHigh)],
    ["52W Range", range2(q.yearLow, q.yearHigh)],
    ["Volume", q.volume ? fmtVol(q.volume) : "—"],
    ["Exchange", q.exchange || "—"],
    ["Currency", q.currency || "—"],
    ["Symbol", q.symbol],
  ];
  $("statsGrid").innerHTML = stats
    .map(([l, v]) => `<div class="stat"><div class="stat-label">${l}</div><div class="stat-value">${v}</div></div>`)
    .join("");

  const watching = state.watch.some((w) => w.symbol === q.symbol);
  const wb = $("watchBtn");
  wb.classList.toggle("watching", watching);
  wb.textContent = watching ? "✓ Watching" : "＋ Watchlist";
}

function badgeFor(q) {
  if (q.stale) return `<span class="mkt-badge stale">DELAYED</span>`;
  if (q.marketState === "OPEN") return `<span class="mkt-badge live">● LIVE</span>`;
  if (q.marketState === "CLOSED") return `<span class="mkt-badge closed">MARKET CLOSED</span>`;
  return `<span class="mkt-badge">Yahoo Finance</span>`;
}

function renderChart() {
  const h = state.history;
  if (!h || !chart) return;
  // On 1D, colour and baseline track the day's move vs previous close (Apple
  // Stocks behaviour); other ranges track move across the whole window.
  const intraday = state.range === "1D" && state.quote;
  chart.baseline = intraday ? state.quote.prevClose : h.points[0]?.c;
  const up = intraday ? state.quote.change >= 0 : h.changePercent >= 0;
  chart.setData(h.points, up);
}

function renderWatchlist() {
  const el = $("watchlist");
  if (!state.watch.length) {
    el.innerHTML = `<div class="empty">No symbols yet.</div>`;
    return;
  }
  el.innerHTML = state.watch
    .map(
      (q) => `
    <div class="wl-item" data-sym="${q.symbol}">
      <div class="wl-left">
        <div class="wl-sym">${q.symbol}</div>
        <div class="wl-name">${q.name}</div>
      </div>
      <div class="wl-right">
        <div class="wl-px">${fmtUSD(q.price)}</div>
        <div class="wl-pct ${cls(q.changePercent)}">${fmtPct(q.changePercent)}</div>
      </div>
    </div>`
    )
    .join("");
  el.querySelectorAll(".wl-item").forEach((n) =>
    n.addEventListener("click", () => selectSymbol(n.dataset.sym))
  );
}

function renderMovers() {
  const list = state.movers ? state.movers[state.moversTab] : [];
  const el = $("moversList");
  if (!list || !list.length) {
    el.innerHTML = `<div class="empty">—</div>`;
    return;
  }
  el.innerHTML = list
    .map(
      (q) => `
    <div class="mv-item" data-sym="${q.symbol}">
      <div class="mv-sym">${q.symbol}</div>
      <div class="mv-px">${fmtUSD(q.price)}</div>
      <div class="mv-pct ${cls(q.changePercent)}">${fmtPct(q.changePercent)}</div>
    </div>`
    )
    .join("");
  el.querySelectorAll(".mv-item").forEach((n) =>
    n.addEventListener("click", () => selectSymbol(n.dataset.sym))
  );
}

function renderPortfolio(p) {
  $("navCash").textContent = fmtUSD(p.cash);
  const items = [
    ["Total Equity", fmtUSD(p.equity), ""],
    ["Cash", fmtUSD(p.cash), ""],
    ["Holdings", fmtUSD(p.holdingsValue), ""],
    ["Unrealized P/L", fmtSigned(p.totalUnrealizedPL) + ` (${fmtPct(p.totalUnrealizedPLPercent)})`, cls(p.totalUnrealizedPL)],
  ];
  $("portSummary").innerHTML = items
    .map(([l, v, c]) => `<div class="ps-item"><div class="ps-label">${l}</div><div class="ps-value ${c}">${v}</div></div>`)
    .join("");

  const body = $("positionsBody");
  if (!p.positions.length) {
    body.innerHTML = `<tr><td colspan="6" class="empty">No open positions. Buy something to get started.</td></tr>`;
    return;
  }
  body.innerHTML = p.positions
    .map(
      (pos) => `
    <tr class="clickable" data-sym="${pos.symbol}">
      <td class="sym-cell">${pos.symbol}</td>
      <td class="num">${pos.qty}</td>
      <td class="num">${fmtUSD(pos.avgCost)}</td>
      <td class="num">${fmtUSD(pos.price)}</td>
      <td class="num">${fmtUSD(pos.marketValue)}</td>
      <td class="num ${cls(pos.unrealizedPL)}">${fmtSigned(pos.unrealizedPL)} (${fmtPct(pos.unrealizedPLPercent)})</td>
    </tr>`
    )
    .join("");
  body.querySelectorAll("tr.clickable").forEach((n) =>
    n.addEventListener("click", () => selectSymbol(n.dataset.sym))
  );
}

function renderOrders(orders) {
  const body = $("ordersBody");
  if (!orders.length) {
    body.innerHTML = `<tr><td colspan="6" class="empty">No orders yet.</td></tr>`;
    return;
  }
  body.innerHTML = orders
    .map((o) => {
      const t = new Date(o.ts * 1000).toLocaleString("en-US", {
        month: "short", day: "numeric", hour: "numeric", minute: "2-digit",
      });
      return `<tr>
        <td>${t}</td>
        <td><span class="tag ${o.side}">${o.side}</span></td>
        <td class="sym-cell">${o.symbol}</td>
        <td class="num">${o.qty}</td>
        <td class="num">${fmtUSD(o.price)}</td>
        <td class="num">${fmtUSD(o.notional)}</td>
      </tr>`;
    })
    .join("");
}

function renderTape() {
  const items = state.watch.length ? state.watch : (state.movers?.actives || []);
  if (!items.length) return;
  const html = items
    .map(
      (q) => `<span class="tape-item" data-sym="${q.symbol}">
        <span class="t-sym">${q.symbol}</span>
        <span class="t-px">${fmtUSD(q.price)}</span>
        <span class="t-pct ${cls(q.changePercent)}">${fmtPct(q.changePercent)}</span>
      </span>`
    )
    .join("");
  const track = $("tapeTrack");
  track.innerHTML = html + html; // duplicate for seamless loop
  track.querySelectorAll(".tape-item").forEach((n) =>
    n.addEventListener("click", () => selectSymbol(n.dataset.sym))
  );
}

// ---- connection banner ----------------------------------------------------
// We never blank the UI on a transient data hiccup: keep the last good render
// and surface a small banner instead.
function setConnection(stateName, detail) {
  const el = $("netBanner");
  if (!el) return;
  if (stateName === "live") {
    el.hidden = true;
    return;
  }
  el.hidden = false;
  el.className = "net-banner " + stateName;
  el.textContent = detail;
}

async function loadStatus() {
  try {
    const s = await api(`/api/status`);
    if (s.rateLimited) {
      setConnection("delayed",
        `Live data is rate-limited — showing last known prices. Retrying in ~${s.cooldownSeconds || 0}s.`);
    } else if (state.quote && state.quote.stale) {
      setConnection("delayed", "Live data delayed — showing last known prices.");
    } else {
      setConnection("live");
    }
  } catch (_) {
    setConnection("offline", "Can't reach MarketSim server. Reconnecting…");
  }
}

// ---- data loading (each resilient; failures keep the last good render) -----
async function selectSymbol(symbol) {
  state.symbol = symbol;
  closeSearch();
  await Promise.allSettled([loadQuote(), loadHistory()]);
  window.scrollTo({ top: 0, behavior: "smooth" });
}
async function loadQuote() {
  try {
    const q = await api(`/api/quote/${state.symbol}`);
    state.quote = q;
    renderQuote();
    if (q.stale) setConnection("delayed", "Live data delayed — showing last known prices.");
  } catch (e) {
    // Keep whatever is on screen; only show a placeholder if we have nothing.
    if (!state.quote || state.quote.symbol !== state.symbol) showQuotePlaceholder(state.symbol);
    setConnection("delayed", "Live data unavailable for this symbol — retrying…");
    throw e;
  }
}
async function loadHistory() {
  try {
    state.history = await api(`/api/history/${state.symbol}?range=${state.range}`);
    renderChart();
  } catch (e) {
    // Leave the previous chart in place rather than clearing it.
    throw e;
  }
}
async function loadWatchlist() {
  state.watch = await api(`/api/watchlist?account=${ACCOUNT}`);
  renderWatchlist();
  renderTape();
  if (state.quote) renderQuote();
}
async function loadMovers() {
  state.movers = await api(`/api/movers?limit=6`);
  renderMovers();
  if (!state.watch.length) renderTape();
}
async function loadPortfolio() {
  const p = await api(`/api/portfolio?account=${ACCOUNT}`);
  renderPortfolio(p);
}
async function loadOrders() {
  const o = await api(`/api/orders?account=${ACCOUNT}&limit=25`);
  renderOrders(o);
}

function showQuotePlaceholder(symbol) {
  $("qSymbol").textContent = symbol || "—";
  $("qName").textContent = "Waiting for live data…";
  $("qSector").textContent = "";
  $("qPrice").textContent = "—";
  $("qChange").textContent = "";
  $("qAsOf").innerHTML = `<span class="mkt-badge stale">CONNECTING</span>`;
}

// ---- search ---------------------------------------------------------------
let searchTimer, searchActiveIdx = -1, searchItems = [];
function openSearchResults(results) {
  searchItems = results;
  searchActiveIdx = -1;
  const box = $("searchResults");
  if (!results.length) {
    box.hidden = true;
    return;
  }
  box.innerHTML = results
    .map(
      (r, i) => `<div class="sr-item" data-i="${i}" data-sym="${r.symbol}">
        <span class="sr-sym">${r.symbol}</span>
        <span class="sr-name">${r.name || ""}</span>
        <span class="sr-exch">${r.exchange || ""}</span>
        ${r.tracked ? `<span class="sr-tag">tracked</span>` : ""}
      </div>`
    )
    .join("");
  box.hidden = false;
  box.querySelectorAll(".sr-item").forEach((n) =>
    n.addEventListener("click", () => {
      selectSymbol(n.dataset.sym);
      $("searchInput").value = "";
    })
  );
}
function closeSearch() {
  $("searchResults").hidden = true;
}
async function doSearch(q) {
  if (!q.trim()) return closeSearch();
  const results = await api(`/api/search?q=${encodeURIComponent(q)}`);
  openSearchResults(results);
}

// ---- trade modal ----------------------------------------------------------
function openTrade(side) {
  if (!state.quote) return;
  state.tradeSide = side;
  $("tradeTitle").textContent = side === "buy" ? "Buy" : "Sell";
  $("tradeSymbol").textContent = `${state.quote.symbol} · ${state.quote.name}`;
  $("tradePrice").textContent = fmtUSD(state.quote.price);
  $("tradeQty").value = 1;
  $("tradeError").hidden = true;
  const submit = $("tradeSubmit");
  submit.textContent = side === "buy" ? "Buy" : "Sell";
  submit.className = "btn btn-block" + (side === "sell" ? " sell" : "");
  updateTradeTotal();
  $("tradeModal").hidden = false;
  $("tradeQty").focus();
}
function closeTrade() {
  $("tradeModal").hidden = true;
}
function updateTradeTotal() {
  const qty = parseFloat($("tradeQty").value) || 0;
  const px = state.quote ? state.quote.price : 0;
  $("tradeTotal").textContent = fmtUSD(qty * px);
}
async function submitTrade() {
  const qty = parseFloat($("tradeQty").value);
  if (!qty || qty <= 0) {
    showTradeError("Enter a positive share quantity.");
    return;
  }
  try {
    await api("/api/orders", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ symbol: state.quote.symbol, side: state.tradeSide, qty, account: ACCOUNT }),
    });
    closeTrade();
    toast(`${state.tradeSide === "buy" ? "Bought" : "Sold"} ${qty} ${state.quote.symbol}`);
    await Promise.all([loadPortfolio(), loadOrders()]);
  } catch (e) {
    showTradeError(e.message);
  }
}
function showTradeError(msg) {
  const el = $("tradeError");
  el.textContent = msg;
  el.hidden = false;
}

// ---- events ---------------------------------------------------------------
function bindEvents() {
  $("searchInput").addEventListener("input", (e) => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => doSearch(e.target.value), 130);
  });
  $("searchInput").addEventListener("keydown", (e) => {
    const box = $("searchResults");
    if (box.hidden) return;
    const items = box.querySelectorAll(".sr-item");
    if (e.key === "ArrowDown") {
      e.preventDefault();
      searchActiveIdx = Math.min(items.length - 1, searchActiveIdx + 1);
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      searchActiveIdx = Math.max(0, searchActiveIdx - 1);
    } else if (e.key === "Enter") {
      const pick = searchActiveIdx >= 0 ? searchItems[searchActiveIdx] : searchItems[0];
      if (pick) {
        selectSymbol(pick.symbol);
        e.target.value = "";
      }
      return;
    } else if (e.key === "Escape") {
      closeSearch();
      return;
    }
    items.forEach((n, i) => n.classList.toggle("active", i === searchActiveIdx));
  });
  document.addEventListener("click", (e) => {
    if (!$("search").contains(e.target)) closeSearch();
  });

  $("rangeTabs").addEventListener("click", (e) => {
    const btn = e.target.closest("button");
    if (!btn) return;
    state.range = btn.dataset.range;
    $("rangeTabs").querySelectorAll("button").forEach((b) => b.classList.toggle("active", b === btn));
    loadHistory();
  });

  $("moversTabs").addEventListener("click", (e) => {
    const btn = e.target.closest("button");
    if (!btn) return;
    state.moversTab = btn.dataset.mv;
    $("moversTabs").querySelectorAll("button").forEach((b) => b.classList.toggle("active", b === btn));
    renderMovers();
  });

  $("buyBtn").addEventListener("click", () => openTrade("buy"));
  $("sellBtn").addEventListener("click", () => openTrade("sell"));
  $("tradeClose").addEventListener("click", closeTrade);
  $("tradeModal").addEventListener("click", (e) => {
    if (e.target === $("tradeModal")) closeTrade();
  });
  $("tradeQty").addEventListener("input", updateTradeTotal);
  $("tradeSubmit").addEventListener("click", submitTrade);

  $("watchBtn").addEventListener("click", async () => {
    if (!state.quote) return;
    const watching = state.watch.some((w) => w.symbol === state.quote.symbol);
    try {
      if (watching) {
        await api(`/api/watchlist/${state.quote.symbol}?account=${ACCOUNT}`, { method: "DELETE" });
        toast(`Removed ${state.quote.symbol} from watchlist`);
      } else {
        await api("/api/watchlist", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ symbol: state.quote.symbol, account: ACCOUNT }),
        });
        toast(`Added ${state.quote.symbol} to watchlist`);
      }
      await loadWatchlist();
    } catch (e) {
      toast(e.message, true);
    }
  });

  $("resetBtn").addEventListener("click", async () => {
    if (!confirm("Reset paper account to $100,000 and clear all positions?")) return;
    await api(`/api/reset?account=${ACCOUNT}`, { method: "POST" });
    toast("Account reset");
    await Promise.all([loadPortfolio(), loadOrders(), loadWatchlist()]);
  });
}

// ---- live refresh ---------------------------------------------------------
// Gentle cadence so we don't trip the data source's rate limits. Each task is
// independent (allSettled) so one failure never blanks the page. Movers and
// history refresh less often than the headline quote.
const POLL_MS = 12000;
let pollTick = 0;
function startPolling() {
  setInterval(async () => {
    pollTick++;
    const tasks = [loadQuote(), loadWatchlist(), loadPortfolio(), loadStatus()];
    if (state.range === "1D") tasks.push(loadHistory());
    if (pollTick % 5 === 0) tasks.push(loadMovers()); // ~once a minute
    await Promise.allSettled(tasks);
  }, POLL_MS);
}

// ---- boot -----------------------------------------------------------------
async function init() {
  chart = new AreaChart($("chart"), $("chartTip"));
  bindEvents();
  await Promise.allSettled([loadWatchlist(), loadMovers(), loadPortfolio(), loadOrders(), loadStatus()]);
  await selectSymbol(state.symbol);
  startPolling();
}

init();
