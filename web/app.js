/* AETHER Dashboard — frontend logic (vanilla JS, no build step). */

const $ = (id) => document.getElementById(id);
const fmt$ = (n) => (n == null ? "—" : "$" + Number(n).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }));
const fmtPct = (n) => (n == null ? "—" : (n >= 0 ? "+" : "") + Number(n).toFixed(2) + "%");
const cls = (n) => (n > 0 ? "pos" : n < 0 ? "neg" : "mut");

async function api(path, opts) {
    const r = await fetch(path, opts);
    if (!r.ok) throw new Error(`${path} → ${r.status}`);
    return r.json();
}

// ── Auth state ─────────────────────────────────────────────────────────────
const TOKEN_KEY = "aether_token", USER_KEY = "aether_user";
function getToken() { return localStorage.getItem(TOKEN_KEY) || ""; }
function authHeaders() { const t = getToken(); return t ? { Authorization: "Bearer " + t } : {}; }
function isAdmin() { return !!getToken(); }

function setAdminUI(user) {
    const on = !!user;
    $("login-btn").classList.toggle("hidden", on);
    $("logout-btn").classList.toggle("hidden", !on);
    $("admin-label").classList.toggle("hidden", !on);
    if (on) $("admin-user").textContent = user;
    // Enable/disable admin action buttons + hint
    document.querySelectorAll(".admin-action").forEach((b) => (b.disabled = !on));
    const hint = $("admin-hint");
    if (hint) hint.classList.toggle("hidden", on);
}

async function refreshAuth() {
    if (!getToken()) { setAdminUI(null); return; }
    try {
        const r = await api("/api/whoami", { headers: authHeaders() });
        if (r.authenticated) setAdminUI(r.user);
        else logout();               // token expired/invalid — clear it
    } catch { setAdminUI(null); }
}

function logout() {
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(USER_KEY);
    setAdminUI(null);
}

// Login modal wiring
$("login-btn").addEventListener("click", () => {
    $("login-error").classList.add("hidden");
    $("login-modal").classList.remove("hidden");
    $("login-user").focus();
});
$("login-cancel").addEventListener("click", () => $("login-modal").classList.add("hidden"));
$("logout-btn").addEventListener("click", logout);
$("login-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const err = $("login-error");
    err.classList.add("hidden");
    try {
        const r = await fetch("/api/login", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ username: $("login-user").value, password: $("login-pass").value }),
        });
        if (!r.ok) {
            const d = await r.json().catch(() => ({}));
            err.textContent = d.detail || `Login failed (${r.status})`;
            err.classList.remove("hidden");
            return;
        }
        const data = await r.json();
        localStorage.setItem(TOKEN_KEY, data.token);
        localStorage.setItem(USER_KEY, data.user);
        $("login-pass").value = "";
        $("login-modal").classList.add("hidden");
        setAdminUI(data.user);
    } catch (ex) {
        err.textContent = "Network error: " + ex.message;
        err.classList.remove("hidden");
    }
});

// ── Tabs ──────────────────────────────────────────────────────────────────────
let activeTab = "dashboard";
document.querySelectorAll(".tab-btn").forEach((btn) => {
    btn.addEventListener("click", () => switchTab(btn.dataset.tab));
});
function switchTab(tab) {
    activeTab = tab;
    document.querySelectorAll(".tab-btn").forEach((b) =>
        b.classList.toggle("active", b.dataset.tab === tab));
    document.querySelectorAll(".tab-panel").forEach((p) =>
        p.classList.toggle("hidden", p.id !== `tab-${tab}`));
    loadTab(tab);
}

// ── Market-hours detection (ET, 9:30–16:00 weekdays) ────────────────────────────
function marketOpen() {
    const now = new Date();
    // Convert to US Eastern via locale trick
    const et = new Date(now.toLocaleString("en-US", { timeZone: "America/New_York" }));
    const day = et.getDay();
    if (day === 0 || day === 6) return false;
    const mins = et.getHours() * 60 + et.getMinutes();
    return mins >= 570 && mins <= 960; // 9:30 – 16:00
}

// ── Header + health ─────────────────────────────────────────────────────────────
let cashBalance = 0;

async function loadHeader() {
    try {
        const [pf, health] = await Promise.all([api("/api/portfolio"), api("/api/health")]);
        cashBalance = pf.balance;
        $("hdr-equity").textContent = fmt$(pf.equity);
        $("hdr-cash").textContent = fmt$(pf.balance);
        const ret = $("hdr-return");
        ret.textContent = fmtPct(pf.return_pct);
        ret.className = "font-bold text-base " + cls(pf.return_pct);
        $("profile-big").textContent = pf.profile || "—";
        $("positions-big").textContent = `${pf.open_positions} / ${pf.max_positions}`;

        const dot = $("fresh-dot"), txt = $("fresh-text");
        if (health.data_fresh) { dot.className = "w-2.5 h-2.5 rounded-full bg-green-500"; txt.textContent = "Data fresh"; }
        else { dot.className = "w-2.5 h-2.5 rounded-full bg-red-500"; txt.textContent = "Data STALE"; }
    } catch (e) { console.error(e); }
}

// ── Dashboard tab ───────────────────────────────────────────────────────────────
let dashPicks = [], dashPositions = [];

// Pattern Tooltip Helper: renders space-separated pattern abbreviations as plain inline text with native tooltips.
function renderPatternsHTML(patterns_str) {
    if (!patterns_str || patterns_str === "—") return "—";
    const desc_map = {
        "CS↑": "Bullish Candlestick Pattern (Oversold Recovery)",
        "CS↓": "Bearish Candlestick Pattern (Overbought Exhaustion)",
        "GoldX↑": "Golden Cross (20 SMA crossed above 50 SMA - Bullish Breakout)",
        "DeathX↓": "Death Cross (20 SMA crossed below 50 SMA - Bearish Breakdown)",
        "MACD+": "MACD Bullish Trend Crossover (Bullish Momentum)",
        "MACD-": "MACD Bearish Trend Crossover (Bearish Momentum)",
        "HS↓": "Head & Shoulders (Bearish Trend Reversal)",
        "InvHS↑": "Inverse Head & Shoulders (Bullish Trend Reversal / Spring)",
        "DoubleTop↓": "Double Top (Bearish Resistance Rejection)",
        "DoubleBottom↑": "Double Bottom (Bullish Support Bounce)",
        "CupHandle↑": "Cup & Handle (Bullish Continuation Pattern)",
        "BullFlag↑": "Bull Flag (Bullish Momentum Consolidation)",
        "BearFlag↓": "Bear Flag (Bearish Momentum Consolidation)"
    };
    return patterns_str.split(" ").map(p => {
        const desc = desc_map[p] || "Technical Price Action Pattern";
        return `<span class="text-purple-300 cursor-help hover:underline decoration-dotted" title="${desc}">${p}</span>`;
    }).join(" ");
}

async function loadDashboard() {
    const [picks, pf] = await Promise.all([api("/api/picks"), api("/api/portfolio")]);

    // Regime badges
    const regime = picks.market_regime || "Unknown";
    const color = picks.regime_color || "#64748b";
    ["regime-badge", "regime-big"].forEach((id) => {
        const el = $(id); el.textContent = regime; el.style.color = color;
    });

    // Picks
    dashPicks = picks.picks || [];
    const pb = $("picks-body");
    if (!dashPicks.length) {
        pb.innerHTML = `<tr><td colspan="11" class="text-center text-slate-500 py-6">No qualifying picks today.</td></tr>`;
    } else {
        pb.innerHTML = dashPicks.map((p, i) => `
            <tr data-sym="${p.Symbol}">
                <td>${i + 1}</td>
                <td class="font-semibold">${p.Symbol}<div class="text-xs mut">${p.Industry || ""}</div></td>
                <td>${p.PGR || "—"}</td>
                <td class="px-live">${fmt$(p.Price)}</td>
                <td>${fmt$(p.Stop)}</td>
                <td>${fmt$(p.Target)}</td>
                <td class="${cls(p.S10)}">${p.S10?.toFixed(1)}</td>
                <td class="${cls(p.L60)}">${p.L60?.toFixed(1)}</td>
                <td class="font-bold ${cls(p.Total)}">${p.Total?.toFixed(1)}</td>
                <td class="text-xs">${renderPatternsHTML(p.Patterns)}</td>
                <td class="text-xs">${p.Shares_ATR ?? "—"} / ${p.Shares_Stop ?? "—"}</td>
            </tr>`).join("");
    }

    // Positions
    dashPositions = pf.positions || [];
    const posb = $("positions-body");
    if (!dashPositions.length) {
        posb.innerHTML = `<tr><td colspan="8" class="text-center text-slate-500 py-6">No open positions.</td></tr>`;
    } else {
        posb.innerHTML = dashPositions.map((p) => `
            <tr data-sym="${p.symbol}">
                <td class="font-semibold">${p.symbol}</td>
                <td>${p.qty}</td>
                <td>${fmt$(p.cost)}</td>
                <td class="px-live">${fmt$(p.current_price)}</td>
                <td class="pnl-$ ${cls(p.pnl)}">${fmt$(p.pnl)}</td>
                <td class="pnl-pct ${cls(p.pnl_pct)}">${fmtPct(p.pnl_pct)}</td>
                <td>${fmt$(p.stop_loss)}</td>
                <td>${p.days_held}</td>
            </tr>`).join("");
    }

    refreshPrices();
}

// ── Live price refresh (updates current price + P&L in place) ──────────────────
async function refreshPrices() {
    const syms = new Set();
    dashPicks.forEach((p) => syms.add(p.Symbol));
    dashPositions.forEach((p) => syms.add(p.symbol));
    if (!syms.size) return;
    let prices;
    try { prices = await api("/api/prices?symbols=" + [...syms].join(",")); }
    catch { return; }

    // Update pick prices
    document.querySelectorAll("#picks-body tr[data-sym]").forEach((tr) => {
        const px = prices[tr.dataset.sym];
        if (px > 0) tr.querySelector(".px-live").textContent = fmt$(px);
    });

    let liveEquity = cashBalance;

    // Update position prices + P&L live
    dashPositions.forEach((p) => {
        const px = prices[p.symbol];
        if (!(px > 0)) {
            liveEquity += p.qty * p.cost;
            return;
        }
        liveEquity += p.qty * px;
        const tr = document.querySelector(`#positions-body tr[data-sym="${p.symbol}"]`);
        if (!tr) return;
        const pnl = (px - p.cost) * p.qty;
        const pnlPct = p.cost ? ((px - p.cost) / p.cost) * 100 : 0;
        tr.querySelector(".px-live").textContent = fmt$(px);
        const c$ = tr.querySelector(".pnl-\\$"), cP = tr.querySelector(".pnl-pct");
        c$.textContent = fmt$(pnl); c$.className = "pnl-$ " + cls(pnl);
        cP.textContent = fmtPct(pnlPct); cP.className = "pnl-pct " + cls(pnlPct);
    });

    // Dynamically update the header equity and return with live prices!
    $("hdr-equity").textContent = fmt$(liveEquity);
    const initialBalance = 10000.0;
    const returnPct = ((liveEquity - initialBalance) / initialBalance) * 100;
    const ret = $("hdr-return");
    ret.textContent = fmtPct(returnPct);
    ret.className = "font-bold text-base " + cls(returnPct);
}

// ── Accounts tab ─────────────────────────────────────────────────────────────
let acctHoldings = [];   // flat [{acctId, symbol, buy, qty}] for live-price refresh
let gameCashBalance = 0;

async function loadAccounts() {
    const data = await api("/api/accounts");
    const box = $("accounts-container");
    const accts = data.accounts || [];
    acctHoldings = [];

    box.innerHTML = accts.map((a) => {
        const isGame = a.type === "game";
        const badge = isGame
            ? `<span class="px-2 py-0.5 rounded text-xs font-semibold bg-purple-900/60 text-purple-300">AI GAME</span>`
            : `<span class="px-2 py-0.5 rounded text-xs font-semibold bg-blue-900/60 text-blue-300">REAL</span>`;
        if (isGame) {
            gameCashBalance = a.balance || 0;
        }
        const summary = isGame
            ? `<span class="text-sm mut">Equity <b id="game-live-equity" class="text-slate-200">${fmt$(a.equity)}</b> · Cash ${fmt$(a.balance)} · <span id="game-live-return" class="${cls(a.return_pct)}">${fmtPct(a.return_pct)}</span> · ${a.profile || ""}</span>`
            : `<span class="text-sm mut">${a.count} holdings</span>`;

        const rows = (a.holdings || []).map((h) => {
            const sym = h.symbol;
            const entry = isGame ? h.cost : h.buy;
            const cur   = isGame ? h.current_price : h.current;
            acctHoldings.push({ acctId: a.id, symbol: sym, buy: entry, qty: h.qty });
            const s10 = h.s10, l60 = h.l60, total = h.total;
            const scoreCells = isGame ? "" :
                `<td class="${cls(s10)}">${s10 == null ? "—" : s10.toFixed(1)}</td>
                 <td class="${cls(l60)}">${l60 == null ? "—" : l60.toFixed(1)}</td>
                 <td class="font-bold ${cls(total)}">${total == null ? "—" : total.toFixed(1)}</td>
                 <td class="text-xs">${h.status || ""}</td>`;
            return `<tr data-acct="${a.id}" data-sym="${sym}" data-buy="${entry ?? ""}" data-qty="${h.qty ?? ""}">
                <td class="font-semibold">${sym}</td>
                <td>${h.qty ?? "—"}</td>
                <td>${fmt$(entry)}</td>
                <td class="px-live">${fmt$(cur)}</td>
                <td class="pnl-$ ${cls(h.pnl)}">${fmt$(h.pnl)}</td>
                <td class="pnl-pct ${cls(h.pnl_pct)}">${fmtPct(h.pnl_pct)}</td>
                <td>${fmt$(h.stop ?? h.stop_loss)}</td>
                ${isGame ? `<td>${h.days_held ?? "—"} d</td>` : `<td>${fmt$(h.target)}</td>`}
                ${scoreCells}
            </tr>`;
        }).join("");

        const scoreHdr = isGame
            ? `<th>Stop</th><th>Days</th>`
            : `<th>Stop</th><th>Target</th><th>S10</th><th>L60</th><th>Score</th><th>Status</th>`;

        return `
        <div>
            <div class="flex items-center gap-3 mb-2">
                <h2 class="section-title mb-0">${a.label}</h2>${badge}
                <div class="flex-1"></div>${summary}
            </div>
            <div class="overflow-x-auto">
                <table class="data-table">
                    <thead><tr>
                        <th>Symbol</th><th>Qty</th><th>Entry</th><th>Current</th>
                        <th>P&amp;L $</th><th>P&amp;L %</th>${scoreHdr}
                    </tr></thead>
                    <tbody>${rows || `<tr><td colspan="10" class="text-center text-slate-500 py-4">No holdings.</td></tr>`}</tbody>
                </table>
            </div>
        </div>`;
    }).join("");

    refreshAccountPrices();
}

async function refreshAccountPrices() {
    if (!acctHoldings.length) return;
    const syms = [...new Set(acctHoldings.map((h) => h.symbol))];
    let prices;
    try { prices = await api("/api/prices?symbols=" + syms.join(",")); } catch { return; }

    let liveGameEquity = gameCashBalance;

    document.querySelectorAll("#accounts-container tr[data-sym]").forEach((tr) => {
        const px = prices[tr.dataset.sym];
        if (!(px > 0)) {
            if (tr.dataset.acct === "game") {
                const buy = parseFloat(tr.dataset.buy), qty = parseFloat(tr.dataset.qty);
                if (!isNaN(buy) && !isNaN(qty)) liveGameEquity += qty * buy;
            }
            return;
        }
        tr.querySelector(".px-live").textContent = fmt$(px);
        const buy = parseFloat(tr.dataset.buy), qty = parseFloat(tr.dataset.qty);
        if (!isNaN(buy) && !isNaN(qty) && buy) {
            const pnl = (px - buy) * qty, pnlPct = ((px - buy) / buy) * 100;
            const c$ = tr.querySelector(".pnl-\\$"), cP = tr.querySelector(".pnl-pct");
            c$.textContent = fmt$(pnl); c$.className = "pnl-$ " + cls(pnl);
            cP.textContent = fmtPct(pnlPct); cP.className = "pnl-pct " + cls(pnlPct);

            if (tr.dataset.acct === "game") {
                liveGameEquity += qty * px;
            }
        }
    });

    const eqEl = $("game-live-equity"), retEl = $("game-live-return");
    if (eqEl && retEl) {
        eqEl.textContent = fmt$(liveGameEquity);
        const initialBalance = 10000.0;
        const returnPct = ((liveGameEquity - initialBalance) / initialBalance) * 100;
        retEl.textContent = fmtPct(returnPct);
        retEl.className = cls(returnPct);
    }
}

// ── Rotation tab ─────────────────────────────────────────────────────────────
async function loadRotation() {
    const [rep, res] = await Promise.all([api("/api/replacements"), api("/api/reserves")]);
    const rb = $("rotation-body");
    const pairs = rep.pairs || [];
    rb.innerHTML = pairs.length ? pairs.map((p) => `
        <tr>
            <td class="neg font-semibold">${p.Sell}</td>
            <td class="${cls(p.Sell_Score)}">${p.Sell_Score?.toFixed?.(1) ?? p.Sell_Score}</td>
            <td class="text-xs">${p.Sell_Status || ""}</td>
            <td class="mut">→</td>
            <td class="pos font-semibold">${p.Buy}</td>
            <td class="${cls(p.Buy_Score)}">${p.Buy_Score?.toFixed?.(1) ?? p.Buy_Score}</td>
            <td>${p.Buy_PGR || "—"}</td>
        </tr>`).join("")
        : `<tr><td colspan="7" class="text-center text-slate-500 py-6">No rotation pairs.</td></tr>`;

    const resb = $("reserves-body");
    const rv = res.reserves || [];
    resb.innerHTML = rv.length ? rv.map((r) => `
        <tr>
            <td class="font-semibold">${r.Symbol}</td>
            <td class="text-xs">${r.Industry || ""}</td>
            <td>${r.PGR || "—"}</td>
            <td class="${cls(r.S10)}">${Number(r.S10).toFixed(1)}</td>
            <td class="${cls(r.L60)}">${Number(r.L60).toFixed(1)}</td>
            <td class="font-bold ${cls(r.Total)}">${Number(r.Total).toFixed(1)}</td>
        </tr>`).join("")
        : `<tr><td colspan="6" class="text-center text-slate-500 py-6">No reserves.</td></tr>`;
}

// ── History tab ────────────────────────────────────────────────────────────────
let histOffset = 0;
const HIST_LIMIT = 25;
let equityChart = null;

async function loadHistory() {
    const data = await api(`/api/history?limit=${HIST_LIMIT}&offset=${histOffset}`);
    $("hist-pnl").textContent = fmt$(data.total_pnl);
    $("hist-pnl").className = "text-2xl font-bold mt-1 " + cls(data.total_pnl);
    $("hist-winrate").textContent = (data.win_rate ?? 0) + "%";
    $("hist-count").textContent = data.total;

    const tb = $("history-body");
    const txns = data.transactions || [];
    tb.innerHTML = txns.length ? txns.map((t) => `
        <tr>
            <td class="text-xs">${(t.date || "").slice(0, 10)}</td>
            <td class="font-semibold ${t.type === "SELL" ? "neg" : t.type === "BUY" ? "pos" : "mut"}">${t.type}</td>
            <td>${t.symbol || "—"}</td>
            <td>${t.qty ?? "—"}</td>
            <td>${fmt$(t.price)}</td>
            <td>${fmt$((t.price || 0) * (t.qty || 0))}</td>
            <td class="${t.pnl == null ? "mut" : cls(t.pnl)}">${t.pnl == null ? "—" : fmt$(t.pnl)}</td>
        </tr>`).join("")
        : `<tr><td colspan="7" class="text-center text-slate-500 py-6">No transactions.</td></tr>`;

    const pages = Math.max(1, Math.ceil(data.total / HIST_LIMIT));
    $("hist-page").textContent = `Page ${histOffset / HIST_LIMIT + 1} / ${pages}`;
    $("hist-prev").disabled = histOffset === 0;
    $("hist-next").disabled = histOffset + HIST_LIMIT >= data.total;

    loadEquityCurve();
}

$("hist-prev").addEventListener("click", () => { if (histOffset > 0) { histOffset -= HIST_LIMIT; loadHistory(); } });
$("hist-next").addEventListener("click", () => { histOffset += HIST_LIMIT; loadHistory(); });

async function loadEquityCurve() {
    let pts;
    try { pts = await api("/api/history/equity-curve"); } catch { return; }
    if (!pts.length) return;
    const ctx = $("equity-chart");
    const cfg = {
        type: "line",
        data: {
            labels: pts.map((p) => p.date),
            datasets: [{
                label: "Balance", data: pts.map((p) => p.balance),
                borderColor: "#60a5fa", backgroundColor: "rgba(96,165,250,0.1)",
                fill: true, tension: 0.2, pointRadius: 0,
            }],
        },
        options: {
            plugins: { legend: { display: false } },
            scales: {
                x: { ticks: { color: "#64748b", maxTicksLimit: 8 }, grid: { color: "rgba(51,65,85,0.3)" } },
                y: { ticks: { color: "#64748b" }, grid: { color: "rgba(51,65,85,0.3)" } },
            },
        },
    };
    if (equityChart) { equityChart.data = cfg.data; equityChart.update(); }
    else equityChart = new Chart(ctx, cfg);
}

// ── System tab ─────────────────────────────────────────────────────────────────
async function loadSystem() {
    const [health, tasks, logs] = await Promise.all([
        api("/api/health"), api("/api/tasks"), api("/api/pipeline/logs?lines=100"),
    ]);

    $("health-body").innerHTML = `
        <div>Data fresh: <b class="${health.data_fresh ? "pos" : "neg"}">${health.data_fresh ? "YES" : "NO"}</b></div>
        <div>Last refresh: <span class="mut">${health.last_refresh || "—"}</span></div>
        <div>Last pipeline: <span class="mut">${health.last_pipeline_run || "—"}</span></div>
        <div>Pipeline status: <b class="${health.pipeline_status === "OK" ? "pos" : "mut"}">${health.pipeline_status}</b></div>
        <div>Server time: <span class="mut">${health.server_time || "—"}</span></div>`;

    const tb = $("tasks-body");
    const ts = tasks.tasks || [];
    tb.innerHTML = ts.length ? ts.map((t) => `
        <tr>
            <td class="font-semibold">${t.name}</td>
            <td>${t.status || "—"}</td>
            <td class="text-xs mut">${t.last_run || "—"}</td>
            <td class="text-xs mut">${t.next_run || "—"}</td>
        </tr>`).join("")
        : `<tr><td colspan="4" class="text-center text-slate-500 py-6">No tasks found.</td></tr>`;

    const lv = $("log-view");
    lv.textContent = (logs.lines || []).join("\n");
    lv.scrollTop = lv.scrollHeight;
}

$("run-pipeline-btn").addEventListener("click", async () => {
    if (!isAdmin()) { $("login-btn").click(); return; }
    if (!confirm("Run the full daily pipeline now? This fetches fresh data and may take several minutes.")) return;
    $("action-msg").textContent = "Starting pipeline…";
    try {
        const r = await fetch("/api/pipeline/run", { method: "POST", headers: authHeaders() });
        if (r.status === 401) { logout(); $("action-msg").textContent = "Session expired — log in again."; return; }
        const d = await r.json();
        $("action-msg").textContent = d.status === "started" ? `Pipeline started (pid ${d.pid}).`
            : d.status === "already_running" ? "Pipeline is already running." : (d.message || d.status);
    } catch (e) { $("action-msg").textContent = "Error: " + e.message; }
});

$("heal-tasks-btn").addEventListener("click", async () => {
    if (!isAdmin()) { $("login-btn").click(); return; }
    if (!confirm("Re-register all scheduled tasks?")) return;
    $("action-msg").textContent = "Healing tasks…";
    try {
        const r = await fetch("/api/tasks/heal", { method: "POST", headers: authHeaders() });
        if (r.status === 401) { logout(); $("action-msg").textContent = "Session expired — log in again."; return; }
        $("action-msg").textContent = "Tasks healed.";
        loadSystem();
    } catch (e) { $("action-msg").textContent = "Error: " + e.message; }
});

// ── Scorecard tab ──────────────────────────────────────────────────────────────
async function loadScorecard() {
    const sc = await api("/api/scorecard");
    const sel = sc.selectors || {};
    const names = Object.keys(sel);
    const empty = $("scorecard-empty");
    empty.classList.toggle("hidden", names.length > 0);

    // Values already arrive as percentages (e.g. 66.7); the module-global fmtPct
    // adds a +/- sign and 2 decimals, which we don't want here.
    const pct = (v) => (v == null ? "—" : v + "%");
    $("scorecard-body").innerHTML = names.length ? names.map((n) => {
        const s = sel[n];
        return `<tr>
            <td class="font-semibold">${n}</td>
            <td>${s.scored}</td>
            <td class="${s.hit_rate >= 50 ? "pos" : "neg"}">${pct(s.hit_rate)}</td>
            <td class="${s.winner_sell_miss ? "neg" : "mut"}">${s.winner_sell_miss}</td>
            <td class="mut">${pct(s.missed_upside_pct)}</td>
            <td class="pos">${pct(s.avoided_loss_pct)}</td>
        </tr>`;
    }).join("") : `<tr><td colspan="6" class="text-center text-slate-500 py-6">No scored decisions yet.</td></tr>`;

    const misses = sc.winner_selling_misses || [];
    $("misses-body").innerHTML = misses.length ? misses.map((m) => `
        <tr>
            <td class="font-semibold">${m.symbol}</td>
            <td class="text-xs mut">${m.date || "—"}</td>
            <td class="text-xs mut">${m.reason || "—"}</td>
            <td class="neg">+${m.fwd_return_pct}%</td>
        </tr>`).join("")
        : `<tr><td colspan="4" class="text-center text-slate-500 py-6">None in the scored window.</td></tr>`;
}

// ── Research tab ─────────────────────────────────────────────────────────────
// Industry text color encodes Industry Strength: Strong=green, Weak=red, NA=amber.
function industryColor(strength) {
    if (strength === "Strong") return "pos";
    if (strength === "Weak") return "neg";
    if (strength === "NA") return "text-amber-400";
    return "mut";
}

// PowerGauge ratings, worst -> best. Used to color the prev->current transition.
const PGR_RANK = { "Be-": 0, "Be": 1, "N/Be": 2, "N": 3, "N/": 3, "N/Bu": 4, "Bu": 5, "Bu+": 6 };

// One cell showing "prev > current"; green if the rating improved, red if it
// deteriorated, white when unchanged (or shown alone when there's no comparable
// prior rating).
function pgrCell(prev, cur) {
    const c = cur == null ? "" : String(cur);
    const p = prev == null ? "" : String(prev);
    if (!c) return '<span class="mut">—</span>';
    const rc = PGR_RANK[c], rp = PGR_RANK[p];
    if (rp == null || rc == null || p === c) return `<span>${c}</span>`;
    const klass = rc > rp ? "pos" : rc < rp ? "neg" : "";
    return `<span class="${klass}">${p} &gt; ${c}</span>`;
}

// Sort key: PGR sorts by rating rank (not alphabetically); Industry sorts by its
// strength (Strong > Weak > NA), matching the color it's shown in.
function researchSortValue(r, key) {
    if (key === "pgr") { const v = PGR_RANK[String(r.pgr)]; return v == null ? -1 : v; }
    if (key === "industry") {
        const s = r.industry_strength;
        return s === "Strong" ? 3 : s === "Weak" ? 2 : s === "NA" ? 1 : 0;
    }
    if (key === "industry_name") return r.industry || "";   // alphabetical (A–Z button)
    return r[key];
}

let researchRows = [];
let researchSort = { key: "combined", dir: -1 };
// Columns that sort as text (ascending default). PGR and Industry sort by numeric
// rank (see researchSortValue), so they default to descending = best/strongest first.
const RESEARCH_TEXT_COLS = ["symbol", "status", "patterns", "industry_name"];

async function loadResearch() {
    const data = await api("/api/research");
    researchRows = data.rows || [];
    const s = data.summary || {};
    $("rs-total").textContent = s.total ?? "—";
    $("rs-setups").textContent = s.setups ?? "—";
    $("rs-bullish").textContent = s.bullish ?? "—";
    $("rs-bearish").textContent = s.bearish ?? "—";
    $("rs-avg").textContent = s.avg_combined ?? "—";
    const rg = $("research-regime");
    if (s.market_regime) {
        rg.textContent = "Market Regime: " + s.market_regime;
        rg.style.color = s.regime_color || "#94a3b8";
    }
    if (data.error) $("research-count").textContent = "Error: " + data.error;
    renderResearch();
}

function renderResearch() {
    const q = ($("research-search").value || "").trim().toLowerCase();
    const setupsOnly = $("research-setups-only").checked;
    let rows = researchRows.filter((r) =>
        (!setupsOnly || r.setup) &&
        (!q || (r.symbol && r.symbol.toLowerCase().includes(q)) ||
               (r.industry && String(r.industry).toLowerCase().includes(q))));

    const { key, dir } = researchSort;
    rows = rows.slice().sort((a, b) => {
        let av = researchSortValue(a, key), bv = researchSortValue(b, key);
        if (av == null) av = -Infinity;
        if (bv == null) bv = -Infinity;
        if (typeof av === "string" || typeof bv === "string")
            return dir * String(av).localeCompare(String(bv));
        return dir * (av - bv);
    });

    const num = (v, d = 2) => (v == null ? "—" : Number(v).toFixed(d));
    $("research-body").innerHTML = rows.length ? rows.map((r) => `
        <tr>
            <td class="font-semibold">${r.symbol}</td>
            <td><div class="truncate text-xs ${industryColor(r.industry_strength)}" style="max-width:70px"
                     title="${(r.industry || "")}${r.industry_strength ? " — " + r.industry_strength : ""}">${r.industry || "—"}</div></td>
            <td class="text-xs whitespace-nowrap">${pgrCell(r.prev_pgr, r.pgr)}</td>
            <td class="text-right ${cls(r.s10)}">${num(r.s10, 1)}</td>
            <td class="text-right ${cls(r.l60)}">${num(r.l60, 1)}</td>
            <td class="text-right font-semibold ${cls(r.combined)}">${num(r.combined, 1)}</td>
            <td class="text-xs">${r.status || "—"}</td>
            <td>${r.setup ? '<span class="pos font-semibold">OK</span>' : '<span class="mut">—</span>'}</td>
            <td class="text-right">${r.win_pct == null ? "—" : r.win_pct + "%"}</td>
            <td class="text-right ${cls(r.buying_ratio)}">${num(r.buying_ratio, 1)}</td>
            <td class="text-right text-xs">${r.money_flow || "—"}</td>
            <td class="text-right text-xs">${r.obos || "—"}</td>
            <td class="text-right text-xs">${r.lt_trend || "—"}</td>
            <td class="text-right">${r.price == null ? "—" : fmt$(r.price)}</td>
            <td class="text-right">${r.stop == null ? "—" : fmt$(r.stop)}</td>
            <td class="text-right">${num(r.risk_ratio, 2)}</td>
            <td>${renderPatternsHTML(r.patterns)}</td>
        </tr>`).join("")
        : `<tr><td colspan="17" class="text-center text-slate-500 py-6">No matching symbols.</td></tr>`;
    $("research-count").textContent = `${rows.length} of ${researchRows.length} symbols`;
}

function setResearchSort(key) {
    if (researchSort.key === key) researchSort.dir *= -1;
    else researchSort = { key, dir: RESEARCH_TEXT_COLS.includes(key) ? 1 : -1 };
    renderResearch();
}

document.querySelectorAll('#research-table th[data-sort]').forEach((th) => {
    th.classList.add("cursor-pointer", "select-none");
    th.addEventListener("click", () => setResearchSort(th.dataset.sort));
});
// Industry supports two sorts: the header sorts by strength, the A–Z button sorts
// by name (stopPropagation so the header's strength-sort doesn't also fire).
$("sort-industry-az").addEventListener("click", (e) => {
    e.stopPropagation();
    setResearchSort("industry_name");
});
$("research-search").addEventListener("input", renderResearch);
$("research-setups-only").addEventListener("change", renderResearch);

// ── Per-tab loader ───────────────────────────────────────────────────────────
function loadTab(tab) {
    if (tab === "dashboard") loadDashboard();
    else if (tab === "research") loadResearch();
    else if (tab === "accounts") loadAccounts();
    else if (tab === "rotation") loadRotation();
    else if (tab === "history") loadHistory();
    else if (tab === "scorecard") loadScorecard();
    else if (tab === "system") loadSystem();
}

// ── Polling loops ──────────────────────────────────────────────────────────────
function startPolling() {
    loadHeader();
    setInterval(loadHeader, 30000);

    // Live prices: 30s during market hours, 5min otherwise
    setInterval(() => {
        if (activeTab === "dashboard") refreshPrices();
        else if (activeTab === "accounts") refreshAccountPrices();
    }, marketOpen() ? 30000 : 300000);

    // System log auto-refresh when on system tab
    setInterval(() => { if (activeTab === "system") loadSystem(); }, 15000);
}

// ── Init ─────────────────────────────────────────────────────────────────────
setAdminUI(null);   // default to logged-out UI until whoami confirms
refreshAuth();
switchTab("dashboard");
startPolling();
