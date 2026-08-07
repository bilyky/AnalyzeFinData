/* AETHER Dashboard — frontend logic (vanilla JS, no build step). */

const $ = (id) => document.getElementById(id);
const fmt$ = (n) => (n == null ? "—" : "$" + Number(n).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }));
const fmtPct = (n) => (n == null ? "—" : (n >= 0 ? "+" : "") + Number(n).toFixed(2) + "%");
const cls = (n) => (n > 0 ? "pos" : n < 0 ? "neg" : "mut");
const esc = (s) => String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");

// ── central aether wiki database (loaded dynamically) ───────────────
let AETHER_WIKI = {};

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
const VALID_TABS = ["dashboard", "research", "accounts", "rotation", "history", "chat", "scorecard", "system", "about"];

document.querySelectorAll(".tab-btn").forEach((btn) => {
    btn.addEventListener("click", () => switchTab(btn.dataset.tab));
});

function switchTab(tab, updateHash = true) {
    if (!VALID_TABS.includes(tab)) tab = "dashboard";
    if (tab !== "system") _stopLogRefresh();   // stop log refresh when leaving system tab
    activeTab = tab;
    document.querySelectorAll(".tab-btn").forEach((b) =>
        b.classList.toggle("active", b.dataset.tab === tab));
    document.querySelectorAll(".tab-panel").forEach((p) =>
        p.classList.toggle("hidden", p.id !== `tab-${tab}`));
    loadTab(tab);
    
    if (updateHash) {
        window.location.hash = tab;
    }
}

// Support browser Back/Forward navigation and direct URL sharing!
window.addEventListener("hashchange", () => {
    const tab = window.location.hash.substring(1);
    if (VALID_TABS.includes(tab) && tab !== activeTab) {
        switchTab(tab, false);
    }
});

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
        if (health.server_needs_restart) {
            dot.className = "w-2.5 h-2.5 rounded-full bg-amber-400";
            txt.textContent = "Server outdated — restart needed";
        } else if (health.data_fresh) {
            dot.className = "w-2.5 h-2.5 rounded-full bg-green-500";
            txt.textContent = "Data fresh";
        } else {
            dot.className = "w-2.5 h-2.5 rounded-full bg-red-500";
            txt.textContent = "Data STALE";
        }
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
        return `<span class="text-purple-300 cursor-help hover:underline decoration-dotted" title="${esc(desc)}">${esc(p)}</span>`;
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
                <td class="font-semibold cursor-pointer hover:text-blue-400" data-open="${p.Symbol}">${p.Symbol}<div class="text-xs mut">${p.Industry || ""}</div></td>
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
                <td class="font-semibold cursor-pointer hover:text-blue-400" data-open="${p.symbol}">${p.symbol}${p.fractional ? ' <span class="text-[9px] px-1 py-0.5 rounded bg-emerald-950/80 text-emerald-300 border border-emerald-800/40 font-bold ml-1 uppercase" title="Fractional Share Order Entry Eligible (E*TRADE Production)">FRAC</span>' : ''}</td>
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

// Accounts tab sorting state
let accountsSort = { key: "symbol", dir: 1 };
const ACCTS_TEXT_COLS = ["symbol", "status", "buy_date", "streak"];
function accountsSortValue(h, key) {
    if (key === "symbol")   return h.symbol || "";
    if (key === "status")   return h.status || "";
    if (key === "buy_date") return h.buy_date || "";
    if (key === "streak")   return h.streak == null ? -9999 : Number(h.streak);
    if (key === "buy")      return h.buy == null ? -999999 : Number(h.buy);
    if (key === "current")  return h.current == null ? -999999 : Number(h.current);
    if (key === "stop")     return h.stop == null ? -999999 : Number(h.stop);
    if (key === "target")   return h.target == null ? -999999 : Number(h.target);
    return h[key] == null ? -999999 : Number(h[key]);
}

function _updateBrokerStatus(status) {
    const wrap = $("broker-status-wrap");
    const dot  = $("broker-dot");
    const txt  = $("broker-text");
    if (!wrap) return;
    if (status === "live") {
        wrap.classList.add("hidden");
        return;
    }
    wrap.classList.remove("hidden");
    if (status === "offline") {
        dot.className  = "w-2 h-2 rounded-full bg-amber-500";
        txt.className  = "text-xs text-amber-400";
        txt.textContent = "E*TRADE offline — showing last cached data";
    } else if (status === "reconnecting") {
        dot.className  = "w-2 h-2 rounded-full bg-blue-400 animate-pulse";
        txt.className  = "text-xs text-blue-300";
        txt.textContent = "E*TRADE reconnecting…";
    } else {
        wrap.classList.add("hidden");
    }
}

async function loadAccounts() {
    const data = await api("/api/accounts");
    const box = $("accounts-container");
    const accts = data.accounts || [];
    _updateBrokerStatus(data.broker_status || "live");
    acctHoldings = [];

    // Dynamically populate our global held symbols set for the Research tab badges
    heldSymbolsGlobal.clear();
    accts.forEach((a) => {
        if (a.holdings) {
            a.holdings.forEach((h) => {
                if (h.symbol) {
                    heldSymbolsGlobal.add(h.symbol.trim().toUpperCase());
                }
            });
        }
    });
    // Trigger a live re-render of the Research table if already loaded to overlay badges instantly!
    if (typeof researchRows !== "undefined" && researchRows.length > 0) {
        renderResearch();
    }

    // Sort holdings for each account dynamically before mapping
    const { key, dir } = accountsSort;
    accts.forEach((a) => {
        if (a.holdings) {
            a.holdings.sort((x, y) => {
                let xv = accountsSortValue(x, key);
                let yv = accountsSortValue(y, key);
                if (typeof xv === "string") {
                    return xv.localeCompare(yv) * dir;
                }
                return (xv - yv) * dir;
            });
        }
    });

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
            const entry = h.buy;
            const cur   = h.current;
            acctHoldings.push({ acctId: a.id, symbol: sym, buy: entry, qty: h.qty });
            const s10 = h.s10, l60 = h.l60, total = h.total;
            const buyDateShort = h.buy_date ? h.buy_date.slice(5) : "—";  // MM-DD
            const streakVal = h.streak;
            const streakCell = streakVal == null ? `<td class="text-xs mut text-center">—</td>`
                : streakVal > 0
                    ? `<td class="text-xs font-semibold text-green-400 text-center" title="${streakVal} consecutive green day${streakVal===1?'':'s'}">${streakVal}G</td>`
                    : `<td class="text-xs font-semibold text-red-400 text-center" title="${Math.abs(streakVal)} consecutive red day${Math.abs(streakVal)===1?'':'s'}">${Math.abs(streakVal)}R</td>`;
            const scoreCells = isGame ? "" :
                `<td class="${cls(s10)}">${s10 == null ? "—" : s10.toFixed(1)}</td>
                 <td class="${cls(l60)}">${l60 == null ? "—" : l60.toFixed(1)}</td>
                 <td class="font-bold ${cls(total)}">${total == null ? "—" : total.toFixed(1)}</td>`;
            const stopTitle = h.stop_source ? `stop source: ${esc(h.stop_source)}${h.buy_date ? " (as of " + esc(h.buy_date) + ")" : ""}` : "";
            const tgtTitle = h.target_source ? `target source: ${h.target_source}` : "";
            const badgeCls = _statusBadgeClass(h.status);
            return `<tr data-acct="${a.id}" data-sym="${sym}" data-buy="${entry ?? ""}" data-qty="${h.qty ?? ""}">
                <td class="font-semibold cursor-pointer hover:text-blue-400" data-open="${sym}">${sym}${h.fractional ? ' <span class="text-[9px] px-1 py-0.5 rounded bg-emerald-950/80 text-emerald-300 border border-emerald-800/40 font-bold ml-1 uppercase" title="Fractional Share Order Entry Eligible (E*TRADE Production)">FRAC</span>' : ''}${instrumentBadge(h.instrument)}</td>
                <td>${h.qty ?? "—"}</td>
                <td>${fmt$(entry)}</td>
                <td class="px-live">${fmt$(cur)}</td>
                <td class="pnl-$ ${cls(h.pnl)}">${fmt$(h.pnl)}</td>
                <td class="pnl-pct ${cls(h.pnl_pct)}">${fmtPct(h.pnl_pct)}</td>
                <td class="${weakStop(h, h.stop_source) ? "text-amber-400" : ""}" title="${stopTitle}">${fmt$(h.stop)}</td>
                ${isGame
                    ? `<td>${h.days_held ?? "—"} d</td><td class="text-xs mut" title="${esc(h.buy_date || "")}">${buyDateShort}</td>`
                    : `<td class="${weakStop(h, h.target_source) ? "text-amber-400" : ""}" title="${esc(tgtTitle)}">${fmt$(h.target)}</td><td class="text-xs mut" title="${esc(h.buy_date || "")}">${buyDateShort}</td>`}
                ${streakCell}
                ${scoreCells}
                <td><button class="rq-btn px-2 py-0.5 rounded text-xs font-semibold transition-colors max-w-[6rem] truncate ${badgeCls}" data-rq-sym="${sym}" data-rq-buy="${entry ?? ""}" title="Click to run live AI analysis">${esc(_statusToRec(h.status))}</button></td>
            </tr>
            <tr class="rq-result-row hidden" data-rq-for="${sym}"><td colspan="16" class="p-0 whitespace-normal"></td></tr>`;
        }).join("");

        const scoreHdr = isGame
            ? `<th data-sort="stop" class="cursor-pointer hover:text-blue-400">Stop</th><th data-sort="days_held" class="cursor-pointer hover:text-blue-400">Days</th><th data-sort="buy_date" class="cursor-pointer hover:text-blue-400">Bought</th><th data-sort="streak" class="cursor-pointer hover:text-blue-400 text-center" title="Daily Close Streak (consecutive green/red closing days), independent of total position PnL">G/R</th><th data-sort="status" class="cursor-pointer hover:text-blue-400 text-right">Status</th>`
            : `<th data-sort="stop" class="cursor-pointer hover:text-blue-400">Stop</th>
               <th data-sort="target" class="cursor-pointer hover:text-blue-400">Target</th>
               <th data-sort="buy_date" class="cursor-pointer hover:text-blue-400">Bought</th>
               <th data-sort="streak" class="cursor-pointer hover:text-blue-400 text-center" title="Daily Close Streak (consecutive green/red closing days), independent of total position PnL">G/R</th>
               <th data-sort="s10" class="cursor-pointer hover:text-blue-400">S10</th>
               <th data-sort="l60" class="cursor-pointer hover:text-blue-400">L60</th>
               <th data-sort="total" class="cursor-pointer hover:text-blue-400">Score</th>
               <th data-sort="status" class="cursor-pointer hover:text-blue-400 text-right">Status</th>`;

        return `
        <div>
            <div class="flex items-center gap-3 mb-2">
                <h2 class="section-title mb-0">${a.label}</h2>${badge}
                <div class="flex-1"></div>${summary}
            </div>
            <div class="overflow-x-auto">
                <table class="data-table">
                    <thead><tr>
                        <th data-sort="symbol" class="cursor-pointer hover:text-blue-400">Symbol</th>
                        <th data-sort="qty" class="cursor-pointer hover:text-blue-400">Qty</th>
                        <th data-sort="buy" class="cursor-pointer hover:text-blue-400">Entry</th>
                        <th data-sort="current" class="cursor-pointer hover:text-blue-400">Current</th>
                        <th data-sort="pnl" class="cursor-pointer hover:text-blue-400">P&amp;L $</th>
                        <th data-sort="pnl_pct" class="cursor-pointer hover:text-blue-400">P&amp;L %</th>
                        ${scoreHdr}
                    </tr></thead>
                    <tbody>${rows || `<tr><td colspan="12" class="text-center text-slate-500 py-4">No holdings.</td></tr>`}</tbody>
                </table>
            </div>
        </div>`;
    }).join("");

    refreshAccountPrices();
}

// Delegate table header clicks for accounts-container sorting
$("accounts-container").addEventListener("click", (e) => {
    const th = e.target.closest("th[data-sort]");
    if (!th) return;
    const key = th.dataset.sort;
    if (accountsSort.key === key) {
        accountsSort.dir *= -1;
    } else {
        accountsSort.dir = ACCTS_TEXT_COLS.includes(key) ? 1 : -1;
    }
    accountsSort.key = key;
    loadAccounts();
});

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
            <td class="neg font-semibold cursor-pointer hover:text-blue-400" data-open="${p.Sell}">${p.Sell}</td>
            <td class="${cls(p.Sell_Score)}">${p.Sell_Score?.toFixed?.(1) ?? p.Sell_Score}</td>
            <td class="text-xs">${p.Sell_Status || ""}</td>
            <td class="mut">→</td>
            <td class="pos font-semibold cursor-pointer hover:text-blue-400" data-open="${p.Buy}">${p.Buy}</td>
            <td class="${cls(p.Buy_Score)}">${p.Buy_Score?.toFixed?.(1) ?? p.Buy_Score}</td>
            <td>${p.Buy_PGR || "—"}</td>
        </tr>`).join("")
        : `<tr><td colspan="7" class="text-center text-slate-500 py-6">No rotation pairs.</td></tr>`;

    const resb = $("reserves-body");
    const rv = res.reserves || [];
    resb.innerHTML = rv.length ? rv.map((r) => `
        <tr>
            <td class="font-semibold cursor-pointer hover:text-blue-400" data-open="${r.Symbol}">${r.Symbol}</td>
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

// ── Log viewer ───────────────────────────────────────────────────────────────────
let _logSource = "pipeline";   // pipeline | txt | json
let _logRefreshTimer = null;
const LEVEL_COLORS = { DEBUG: "text-slate-500", INFO: "text-slate-300",
                       WARNING: "text-amber-400", ERROR: "text-red-400" };

function _logLevelClass(level) {
    return LEVEL_COLORS[(level || "").toUpperCase()] || "text-slate-300";
}

async function fetchAndRenderLog() {
    const lv = $("log-view"), st = $("log-status");
    const q = ($("log-search").value || "").toLowerCase();
    const lvlFilter = $("log-level-filter").value;
    try {
        if (_logSource === "pipeline") {
            const d = await api("/api/pipeline/logs?lines=300");
            let lines = d.lines || [];
            if (q) lines = lines.filter(l => l.toLowerCase().includes(q));
            lv.textContent = lines.join("\n");
            st.textContent = `${lines.length} lines · Pipeline Log`;
        } else if (_logSource === "txt") {
            const d = await api("/api/logs/aether?lines=300");
            let lines = d.lines || [];
            if (q) lines = lines.filter(l => l.toLowerCase().includes(q));
            lv.textContent = lines.join("\n");
            st.textContent = `${lines.length} lines · aether.log`;
        } else {
            const qs = lvlFilter ? `&level=${lvlFilter}` : "";
            const d = await api(`/api/logs/aether/json?lines=300${qs}`);
            let entries = d.entries || [];
            if (q) entries = entries.filter(e =>
                JSON.stringify(e).toLowerCase().includes(q));
            // Render as coloured lines
            lv.innerHTML = entries.map(e => {
                const lvl = (e.level || "").toUpperCase();
                const lcls = _logLevelClass(lvl);
                const ts = esc((e.ts || "").substring(11, 19));
                const mod = esc((e.module || "").replace("aether.", ""));
                const extra = e.extra ? " " + esc(JSON.stringify(e.extra)) : "";
                const exc = e.exc ? `\n  ${esc(e.exc.split("\n").slice(-2).join(" "))}` : "";
                return `<span class="${lcls}">[${ts}] ${lvl.padEnd(7)} ${mod}: ${esc(e.msg)}${extra}${exc}</span>`;
            }).join("\n") || "<span class='mut'>No entries.</span>";
            st.textContent = `${entries.length} entries · aether.jsonl${lvlFilter ? " (" + lvlFilter + ")" : ""}`;
        }
    } catch (err) {
        lv.textContent = "Error loading logs: " + err.message;
    }
    // Auto-scroll only if already at the bottom
    if (lv.scrollHeight - lv.scrollTop < lv.clientHeight + 40)
        lv.scrollTop = lv.scrollHeight;
}

function renderLogView() { fetchAndRenderLog(); }

function _startLogRefresh() {
    _stopLogRefresh();
    if ($("log-auto-refresh").checked)
        _logRefreshTimer = setInterval(fetchAndRenderLog, 5000);
}

function _stopLogRefresh() {
    if (_logRefreshTimer) { clearInterval(_logRefreshTimer); _logRefreshTimer = null; }
}

document.querySelectorAll(".log-src-btn").forEach(btn => {
    btn.addEventListener("click", () => {
        _logSource = btn.dataset.log;
        document.querySelectorAll(".log-src-btn").forEach(b => {
            b.classList.toggle("bg-slate-700", b === btn);
            b.classList.toggle("text-slate-200", b === btn);
            b.classList.toggle("bg-slate-900", b !== btn);
            b.classList.toggle("mut", b !== btn);
        });
        $("log-level-filter").classList.toggle("hidden", _logSource !== "json");
        fetchAndRenderLog();
    });
});
$("log-level-filter").addEventListener("change", fetchAndRenderLog);
$("log-search").addEventListener("input", fetchAndRenderLog);
$("log-refresh-btn").addEventListener("click", fetchAndRenderLog);
$("log-auto-refresh").addEventListener("change", () => {
    if ($("log-auto-refresh").checked) _startLogRefresh(); else _stopLogRefresh();
});

// ── System tab ─────────────────────────────────────────────────────────────────
// Category display names for the manual tasks grid
const TASK_CATEGORY_LABELS = {
    pipeline: "Pipeline", ai_game: "AI Game", data: "Data",
    research: "Research", monitoring: "Monitoring", system: "System",
};

// ── Task output panel ────────────────────────────────────────────────────────
let _pollTimer = null;

function _openOutputPanel(label) {
    $("top-task-label").textContent = label;
    $("top-task-status").textContent = "running…";
    $("top-task-status").className = "text-xs px-2 py-0.5 rounded bg-blue-900/60 text-blue-300";
    $("task-output-log").textContent = "";
    $("task-output-panel").classList.remove("hidden");
}

function _closeOutputPanel() {
    $("task-output-panel").classList.add("hidden");
    if (_pollTimer) { clearInterval(_pollTimer); _pollTimer = null; }
}

function _startPolling(runId) {
    if (_pollTimer) clearInterval(_pollTimer);
    let offset = 0;
    const log = $("task-output-log");
    _pollTimer = setInterval(async () => {
        try {
            const d = await api(`/api/tasks/output/${runId}?offset=${offset}`);
            if (d.lines) {
                log.textContent += d.lines;
                log.scrollTop = log.scrollHeight;
                offset = d.offset;
            }
            if (d.done) {
                clearInterval(_pollTimer); _pollTimer = null;
                const ok = d.exit_code === 0;
                $("top-task-status").textContent = ok ? `done (exit 0)` : `failed (exit ${d.exit_code})`;
                $("top-task-status").className = `text-xs px-2 py-0.5 rounded ${ok ? "bg-green-900/60 text-green-300" : "bg-red-900/60 text-red-400"}`;
            }
        } catch { /* network blip — keep polling */ }
    }, 800);
}

$("top-task-close").addEventListener("click", _closeOutputPanel);
$("top-task-clear").addEventListener("click", () => { $("task-output-log").textContent = ""; });

async function runManualTask(taskId, label, confirm_msg, adminOnly, inputEl) {
    const msg = $("run-msg");
    if (adminOnly && !isAdmin()) { $("login-btn").click(); return; }
    if (confirm_msg && !confirm(confirm_msg)) return;
    const inputValue = inputEl ? inputEl.value.trim() : "";
    msg.textContent = `Starting "${label}"${inputValue ? " (" + inputValue + ")" : ""}…`;
    msg.className = "text-xs mb-3 text-slate-400";
    try {
        const r = await fetch("/api/tasks/run", {
            method: "POST",
            headers: { "Content-Type": "application/json", ...authHeaders() },
            body: JSON.stringify({ task_id: taskId, input_value: inputValue }),
        });
        if (r.status === 401) { logout(); msg.textContent = "Session expired — log in again."; return; }
        const d = await r.json();
        if (d.status === "started") {
            msg.textContent = `"${label}" started (pid ${d.pid}).`;
            msg.className = "text-xs mb-3 pos";
            _openOutputPanel(`${d.label}${inputValue ? " · " + inputValue : ""}`);
            _startPolling(d.run_id);
        } else {
            msg.textContent = d.message || d.status;
            msg.className = "text-xs mb-3 neg";
        }
    } catch (e) {
        msg.textContent = "Error: " + e.message;
        msg.className = "text-xs mb-3 neg";
    }
}

// Global Hover Tooltip Modal for Scheduled Tasks
let _tooltipEl = null;

function showTooltip(event, text) {
    if (!_tooltipEl) {
        _tooltipEl = document.createElement("div");
        _tooltipEl.className = "fixed z-50 w-80 p-3 bg-slate-950 border border-slate-800 text-xs text-slate-200 rounded-lg shadow-2xl leading-relaxed transition-opacity duration-150 pointer-events-none";
        document.body.appendChild(_tooltipEl);
    }
    // Format WHAT:, WHY:, and OUTCOME: with spacing and color coding
    const formatted = text
        .replace(/WHAT:/g, "<b class='text-blue-400'>WHAT:</b>")
        .replace(/WHY:/g, "<br><b class='text-purple-400 mt-1.5 inline-block'>WHY:</b>")
        .replace(/OUTCOME:/g, "<br><b class='text-emerald-400 mt-1.5 inline-block'>OUTCOME:</b>");

    _tooltipEl.innerHTML = formatted;
    _tooltipEl.style.opacity = "0";
    _tooltipEl.style.display = "block";

    // Position tooltip relative to the hover target
    const rect = event.target.getBoundingClientRect();
    let left = rect.left;
    let top = rect.bottom + 8;

    // Shift left if extending off-screen right
    if (left + 320 > window.innerWidth) {
        left = window.innerWidth - 340;
    }
    // Shift above target if extending off-screen bottom
    if (top + _tooltipEl.offsetHeight > window.innerHeight) {
        top = rect.top - _tooltipEl.offsetHeight - 8;
    }

    _tooltipEl.style.left = left + "px";
    _tooltipEl.style.top = top + "px";
    _tooltipEl.style.opacity = "1";
}

function hideTooltip() {
    if (_tooltipEl) {
        _tooltipEl.style.display = "none";
    }
}

async function loadSystem() {
    const [health, tasks, logs, manual] = await Promise.all([
        api("/api/health"), api("/api/tasks"), api("/api/pipeline/logs?lines=100"),
        api("/api/tasks/manual"),
    ]);

    $("health-body").innerHTML = `
        <div>Data fresh: <b class="${health.data_fresh ? "pos" : "neg"}">${health.data_fresh ? "YES" : "NO"}</b></div>
        <div>Last refresh: <span class="mut">${health.last_refresh || "—"}</span></div>
        <div>Last pipeline: <span class="mut">${health.last_pipeline_run || "—"}</span></div>
        <div>Pipeline status: <b class="${health.pipeline_status === "OK" ? "pos" : "mut"}">${health.pipeline_status}</b></div>
        <div>Server time: <span class="mut">${health.server_time || "—"}</span></div>`;

    // Scheduled tasks — with a "Run Now" button that triggers the matching manual task
    const manualById = Object.fromEntries((manual.tasks || []).map((t) => [t.id, t]));
    const SCHED_TO_MANUAL = {
        "AETHER_Watchdog":           "watchdog",
        "AETHER_StopMonitor":        "intraday_monitor",
        "AETHER_DailyDriver":        "pipeline",
        "AETHER_PostMarketReporter": "ai_game_summary",
        "AETHER_PostMarketSync":     "pipeline",
        "AETHER_RD_Scientist":       "pattern_discovery",
    };
    const SCHED_DESCRIPTIONS = {
        "AETHER_Watchdog": "WHAT: Hourly pre-market/market diagnostics and 2-hourly off-market self-healing. WHY: Validates login cookie freshness, clears workbook locks, and renews E*TRADE sessions. OUTCOME: Generates watchdog_agent.log; auto-triggers headless Playwright browser re-auth.",
        "AETHER_StopMonitor": "WHAT: Intraday risk guard checking open positions against stops and targets every 30 mins (6:45 AM - 1:45 PM). WHY: Secures profits and shields capital during sudden dumps. OUTCOME: Generates intraday_monitor_agent.log; sends urgent breach emails and auto-exits virtual games.",
        "AETHER_DailyDriver": "WHAT: Core trading screener and portfolio rebalancer at 7:00 AM PST. WHY: Scrapes Chaikin, updates State, enforces Circuit Breakers, and executes virtual moves. OUTCOME: Generates daily_driver_agent.log and autonomous_pipeline logs; sends picks summary report.",
        "AETHER_PostMarketReporter": "WHAT: Portfolio closing audit and scheduler diagnostic run at 2:00 PM PST. WHY: Captures settled returns, checks for scheduler missed runs, and scans logs. OUTCOME: Generates post_market_reporter_agent.log and logs closing status report.",
        "AETHER_PostMarketSync": "WHAT: Nightly post-market data synchronization and workbook refresh at 1:30 PM PST. WHY: Finalizes daily PowerGauge scrapes, and refreshes price history caches via RapidAPI. OUTCOME: Generates post_market_sync_agent.log and autonomous_pipeline logs; updates State sheet.",
        "AETHER_RD_Scientist": "WHAT: Missed winner retro and statistical validation on Saturdays at 10:00 AM PST. WHY: Replays historical caches to isolate missed momentum setups and feeds rules to the exclusion guard. OUTCOME: Generates pattern_discovery_agent.log; writes failure-DNA rules to database.",
    };
    const tb = $("tasks-body");
    const ts = tasks.tasks || [];
    tb.innerHTML = ts.length ? ts.map((t) => {
        const manualId = SCHED_TO_MANUAL[t.name];
        const mt = manualId ? manualById[manualId] : null;
        const btn = mt
            ? `<button onclick="runManualTask('${mt.id}','${mt.label.replace(/'/g,"\\'")}',${mt.confirm ? `'${mt.confirm.replace(/'/g,"\\'")}'` : "null"},${mt.admin_only},null)"
                       class="btn text-xs ${mt.admin_only ? "admin-action" : ""}"
                       ${mt.admin_only && !isAdmin() ? "title='Admin required'" : ""}>
                 ▶ Run
               </button>`
            : "";
        const descEscaped = (SCHED_DESCRIPTIONS[t.name] || "").replace(/'/g, "\\'");
        return `<tr>
            <td>
                <span class="font-semibold text-sm cursor-help border-b border-dashed border-slate-500 hover:text-blue-400"
                      onmouseover="showTooltip(event, '${descEscaped}')"
                      onmouseout="hideTooltip()">
                    ${t.name}
                </span>
            </td>
            <td>${t.status || "—"}</td>
            <td class="text-xs mut">${t.last_run || "—"}</td>
            <td class="text-xs mut">${t.next_run || "—"}</td>
            <td class="text-right">${btn}</td>
        </tr>`;
    }).join("")
        : `<tr><td colspan="5" class="text-center text-slate-500 py-6">No tasks found.</td></tr>`;

    // Manual tasks grid — grouped by category
    const allTasks = manual.tasks || [];
    if (allTasks.length) {
        const byCategory = {};
        allTasks.forEach((t) => { (byCategory[t.category] = byCategory[t.category] || []).push(t); });
        let grid = "";
        for (const [cat, items] of Object.entries(byCategory)) {
            const catLabel = TASK_CATEGORY_LABELS[cat] || cat;
            grid += `<div class="col-span-full mt-3 mb-1">
                <span class="card-label">${catLabel}</span></div>`;
            grid += items.map((t) => {
                const needsAdmin = t.admin_only;
                const disabled = needsAdmin && !isAdmin();
                const title = disabled ? "Admin login required" : (t.confirm || "");
                const inputId = `task-input-${t.id}`;
                const inputHtml = t.input
                    ? `<input id="${inputId}" type="text" placeholder="${t.input.placeholder || ""}"
                               value="${t.input.default || ""}"
                               class="bg-slate-800 border border-slate-700 rounded px-2 py-1 text-xs w-full uppercase"
                               onkeydown="if(event.key==='Enter') document.getElementById('task-btn-${t.id}').click()" />`
                    : "";
                const onclickArgs = t.input
                    ? `'${t.id}','${t.label.replace(/'/g,"\\'")}',${t.confirm ? `'${t.confirm.replace(/'/g,"\\'")}'` : "null"},${t.admin_only},document.getElementById('${inputId}')`
                    : `'${t.id}','${t.label.replace(/'/g,"\\'")}',${t.confirm ? `'${t.confirm.replace(/'/g,"\\'")}'` : "null"},${t.admin_only},null`;
                return `<div class="card flex flex-col gap-2">
                    <div class="flex items-start justify-between gap-2">
                        <span class="font-semibold text-sm">${t.label}</span>
                        ${needsAdmin ? '<span class="text-[9px] px-1 rounded bg-slate-700 mut">ADMIN</span>' : ""}
                    </div>
                    <p class="text-xs mut flex-1">${t.description}</p>
                    ${inputHtml}
                    <button id="task-btn-${t.id}" title="${title}"
                        onclick="runManualTask(${onclickArgs})"
                        class="btn text-xs w-full ${needsAdmin ? "admin-action" : ""}"
                        ${disabled ? "disabled" : ""}>
                        ▶ Run
                    </button>
                </div>`;
            }).join("");
        }
        $("manual-tasks-grid").innerHTML = grid;
    } else {
        $("manual-tasks-grid").innerHTML = `<div class="col-span-3 text-center text-slate-500 py-4">No manual tasks configured.</div>`;
    }

    renderLogView();
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

// ── Chat tab ──────────────────────────────────────────────────────────────────
let _chatHistory = [];  // [{role, content}]

function _appendMessage(role, content) {
    const wrap = $("chat-messages");
    const isUser = role === "user";
    const div = document.createElement("div");
    div.className = `flex ${isUser ? "justify-end" : "justify-start"}`;
    // Render newlines and escape HTML in assistant output
    const safe = content.replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;")
                         .replace(/\n/g,"<br>");
    div.innerHTML = `<div class="max-w-[80%] px-4 py-2 rounded-xl text-sm leading-relaxed
        ${isUser ? "bg-blue-600 text-white" : "bg-slate-800 text-slate-200 border border-slate-700"}">${safe}</div>`;
    wrap.appendChild(div);
    wrap.scrollTop = wrap.scrollHeight;
    // Hide starters once conversation starts
    if (_chatHistory.length > 0) $("chat-starters").classList.add("hidden");
}

function _appendThinking() {
    const wrap = $("chat-messages");
    const div = document.createElement("div");
    div.id = "chat-thinking";
    div.className = "flex justify-start";
    div.innerHTML = `<div class="px-4 py-2 rounded-xl text-sm bg-slate-800 border border-slate-700 text-slate-400 italic">
        AETHER is thinking…</div>`;
    wrap.appendChild(div);
    wrap.scrollTop = wrap.scrollHeight;
}

function _removeThinking() {
    const el = $("chat-thinking");
    if (el) el.remove();
}

async function sendChatMessage(text) {
    text = (text || "").trim();
    if (!text) return;
    _chatHistory.push({ role: "user", content: text });
    _appendMessage("user", text);
    $("chat-input").value = "";
    $("chat-send-btn").disabled = true;
    _appendThinking();
    try {
        const r = await fetch("/api/chat", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(_chatHistory),
        });
        const d = await r.json();
        _removeThinking();
        const reply = d.reply || "(no response)";
        _chatHistory.push({ role: "assistant", content: reply });
        _appendMessage("assistant", reply);
        if (d.provider) $("chat-provider").textContent = d.provider;
    } catch (e) {
        _removeThinking();
        _appendMessage("assistant", "Error: " + e.message);
    } finally {
        $("chat-send-btn").disabled = false;
        $("chat-input").focus();
    }
}

$("chat-send-btn").addEventListener("click", () => sendChatMessage($("chat-input").value));
$("chat-input").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendChatMessage($("chat-input").value); }
});
$("chat-clear-btn").addEventListener("click", () => {
    _chatHistory = [];
    $("chat-messages").innerHTML = "";
    $("chat-starters").classList.remove("hidden");
});
document.querySelectorAll(".chat-starter").forEach((btn) => {
    btn.addEventListener("click", () => sendChatMessage(btn.textContent));
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
    $("misses-body").innerHTML = misses.length ? misses.map((m) => {
        const isAddressed = (m.symbol === "FANG" && m.date === "2026-07-14");
        const statusCell = isAddressed
            ? `<td class="text-center"><button class="px-2 py-0.5 rounded text-[10px] font-bold bg-emerald-500/10 text-emerald-400 border border-emerald-500/20 hover:bg-emerald-500/20 transition-all cursor-pointer" data-to-retro="true" title="Click to view Hardening & Resolution details">✅ Addressed</button></td>`
            : `<td class="text-center text-slate-500">—</td>`;
        return `
        <tr>
            <td class="font-semibold cursor-pointer hover:text-blue-400" data-open="${m.symbol}">${m.symbol}</td>
            <td class="text-xs mut">${m.date || "—"}</td>
            <td class="text-xs mut">${m.reason || "—"}</td>
            <td class="neg">+${m.fwd_return_pct}%</td>
            ${statusCell}
        </tr>`;
    }).join("")
    : `<tr><td colspan="5" class="text-center text-slate-500 py-6">None in the scored window.</td></tr>`;

    // Render Buy-Side Missed Winners
    const buySideMisses = sc.buy_side_missed_winners || [];
    const buySideDate = sc.buy_side_replay_date || "";

    const badge = $("buy-side-date-badge");
    if (badge) {
        badge.textContent = buySideDate ? `REPLAY DATE: ${buySideDate}` : "NO ACTIVE REPORT";
        badge.classList.toggle("hidden", !buySideDate);
    }

    $("buy-side-misses-body").innerHTML = buySideMisses.length ? buySideMisses.map((bm) => {
        const pgrCls = (bm.pgr || "").includes("Bu") ? "text-green-400 font-semibold" : (bm.pgr || "").includes("Be") ? "text-red-400" : "text-slate-300";
        const reasonsList = (bm.reasons || []).map((r) => `<span class="px-1.5 py-0.5 rounded bg-slate-800 text-slate-400 border border-slate-700/50 text-[10px] mr-1 inline-block">${esc(r)}</span>`).join("");
        const isAddressed = bm.status === "Addressed";
        const statusCell = isAddressed
            ? `<td class="text-center"><span class="px-2 py-0.5 rounded text-[10px] font-bold bg-emerald-500/10 text-emerald-400 border border-emerald-500/20" title="This missed buy-side opportunity has been officially addressed & resolved by our automated updates.">✅ Addressed</span></td>`
            : `<td class="text-center"><span class="px-2 py-0.5 rounded text-[10px] font-bold bg-amber-500/10 text-amber-400 border border-amber-500/20 animate-pulse" title="This missed buy-side opportunity is currently under investigation & review.">Reviewing</span></td>`;
        return `
        <tr>
            <td class="font-semibold cursor-pointer hover:text-blue-400" data-open="${bm.symbol}">${bm.symbol}</td>
            <td class="${pgrCls}">${bm.pgr || "—"}</td>
            <td class="${cls(bm.score)} font-semibold">${bm.score}</td>
            <td class="pos font-bold">+${bm.fwd_return_pct}%</td>
            <td>${reasonsList || "—"}</td>
            ${statusCell}
        </tr>`;
    }).join("")
    : `<tr><td colspan="6" class="text-center text-slate-500 py-6">No buy-side missed winners found.</td></tr>`;
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
// Stop sources that aren't a confirmed swing-low support -> shown amber (weaker).
const STOP_WEAK = new Set(["atr", "pct", "stale", "sheet"]);
// Leveraged/inverse/crypto ETFs intentionally use ATR levels, so don't amber them.
function weakStop(r, source) {
    return r.instrument === "normal" && STOP_WEAK.has(source);
}
function instrumentBadge(instrument) {
    if (instrument === "leveraged_inverse")
        return ' <span class="text-[9px] px-1 rounded bg-amber-900/60 text-amber-300" title="Leveraged/inverse ETF — excluded from new buys (temporary); ATR stop">LEV</span>';
    if (instrument === "crypto")
        return ' <span class="text-[9px] px-1 rounded bg-purple-900/60 text-purple-300" title="Crypto ETF — excluded from new buys (temporary); ATR stop">CRYPTO</span>';
    return "";
}

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

let heldSymbolsGlobal = new Set();
let researchRows = [];
let researchSort = { key: "combined", dir: -1 };
// Multi-stock comparison: symbols (uppercased) checked on the Research page. Held in a
// Set so selection survives search/sort re-renders; compareLastRun keeps the last compared
// set so the "Summarize with AI" button re-POSTs the same symbols.
let compareSelected = new Set();
let compareLastRun = [];
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
    const stale = $("research-stale");
    const msgs = [];
    if (s.stale_stops > 0)
        msgs.push(`⚠ OHLCV cache stale for ${s.stale_stops}/${s.total} symbols ` +
            `(oldest ${s.ohlcv_max_age_days}d) — their Stop is 8% off the live price, not a swing-low. ` +
            `Refresh Data/Symbol_full.`);
    if (s.support_misses > 0)
        msgs.push(`⚠ ${s.support_misses}/${s.total} symbols have fresh data but no confirmed ` +
            `swing-low support — Stop used an ATR/8% fallback.`);
    if (s.target_misses > 0)
        msgs.push(`⚠ ${s.target_misses}/${s.total} symbols have fresh data but no overhead ` +
            `resistance — Target used an ATR/8% projection.`);
    if (msgs.length) {
        stale.innerHTML = msgs.join("<br>");
        stale.classList.remove("hidden");
    } else {
        stale.classList.add("hidden");
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
            <td class="text-center"><input type="checkbox" class="cmp-check accent-blue-500" data-sym="${esc(r.symbol)}" ${compareSelected.has(String(r.symbol).toUpperCase()) ? "checked" : ""}></td>
            <td class="font-semibold cursor-pointer hover:text-blue-400" data-open="${r.symbol}">
                ${r.symbol}${r.fractional ? ' <span class="text-[9px] px-1 py-0.5 rounded bg-emerald-950/80 text-emerald-300 border border-emerald-800/40 font-bold ml-1 uppercase" title="Fractional Share Order Entry Eligible (E*TRADE Production)">FRAC</span>' : ''}${heldSymbolsGlobal.has(r.symbol.toUpperCase()) ? ' <span class="text-[9px] px-1.5 py-0.5 rounded bg-green-900/80 text-green-300 font-bold ml-1" title="Currently held in your accounts">HELD</span>' : ''}${instrumentBadge(r.instrument)}
            </td>
            <td><div class="truncate text-xs ${industryColor(r.industry_strength)}" style="max-width:70px"
                     title="${esc(r.industry || "")}${r.industry_strength ? " — " + esc(r.industry_strength) : ""}">${esc(r.industry || "—")}</div></td>
            <td class="text-xs whitespace-nowrap">${pgrCell(r.prev_pgr, r.pgr)}</td>
            <td class="text-right">${r.price == null ? "—" : fmt$(r.price)}</td>
            <td class="text-right ${weakStop(r, r.stop_source) ? "text-amber-400" : ""}" 
                title="${r.stop_source === "swing" ? "Stop Source: Swing-Low (from spreadsheet). Note: Autopilot will override this with a safe ATR-based stop floor on purchase." : (r.stop_source === "atr" ? "Stop Source: ATR-based (Volatility-buffered stop)." : "Stop Source: " + esc(r.stop_source || "?"))}">
                ${!r.stop ? "—" : fmt$(r.stop)}
            </td>
            <td class="text-right ${weakStop(r, r.target_source) ? "text-amber-400" : ""}" title="target source: ${esc(r.target_source || "?")}">${!r.target ? "—" : fmt$(r.target)}</td>
            <td class="text-right ${r.risk_ratio == null ? "" : (r.risk_ratio < 2.0 ? "text-slate-500" : (r.risk_ratio > 10.0 ? "text-amber-500 font-bold" : "text-green-400 font-semibold"))}"
                title="${r.risk_ratio == null ? "R:R not calculated" : (r.risk_ratio < 2.0 ? "Poor risk asymmetry. Autopilot will reject buying this candidate." : (r.risk_ratio > 10.0 ? "⚠️ Tight Stop Warning: This high ratio is a paper mirage. Price is extremely close to the swing-low. True ATR stop-loss is wider." : "Favorable R/R: Strong risk asymmetry."))}">
                ${r.risk_ratio == null ? "—" : (r.risk_ratio > 10.0 ? "⚠️ " : "") + num(r.risk_ratio, 2)}
            </td>
            <td class="text-right ${cls(r.s10)}">${num(r.s10, 1)}</td>
            <td class="text-right ${cls(r.l60)}">${num(r.l60, 1)}</td>
            <td class="text-right font-semibold ${cls(r.combined)}">${num(r.combined, 1)}</td>
            <td class="text-xs cursor-pointer hover:text-blue-400 font-semibold" data-to-scorecard="true" title="Click to open Scorecard & Retrospective History">${r.status || "—"}</td>
            <td>${r.setup ? '<span class="pos font-semibold">OK</span>' : '<span class="mut">—</span>'}</td>
            <td class="text-right">${r.win_pct == null ? "—" : r.win_pct + "%"}</td>
            <td class="text-right ${cls(r.buying_ratio)}">${num(r.buying_ratio, 1)}</td>
            <td class="text-right text-xs">${r.money_flow || "—"}</td>
            <td class="text-right text-xs">${r.obos || "—"}</td>
            <td class="text-right text-xs">${r.lt_trend || "—"}</td>
            <td>${renderPatternsHTML(r.patterns)}</td>
        </tr>`).join("")
        : `<tr><td colspan="19" class="text-center text-slate-500 py-6">No matching symbols.</td></tr>`;
    $("research-count").textContent = `${rows.length} of ${researchRows.length} symbols`;
}

// ── Multi-stock comparison (Research-page selection) ─────────────────────────
function updateCompareButton() {
    const n = compareSelected.size;
    const btn = $("compare-run");
    btn.textContent = `Compare selected (${n})`;
    btn.disabled = n < 2;
    $("compare-clear").classList.toggle("hidden", n === 0);
}

// Delegated: a checkbox toggle updates the Set + the action bar (no full re-render).
$("research-body").addEventListener("change", (e) => {
    const cb = e.target.closest(".cmp-check");
    if (!cb) return;
    const sym = (cb.dataset.sym || "").toUpperCase();
    if (!sym) return;
    if (cb.checked) compareSelected.add(sym);
    else compareSelected.delete(sym);
    updateCompareButton();
});

async function runCompare() {
    const syms = [...compareSelected];
    if (syms.length < 2) return;
    const panel = $("compare-panel"), body = $("compare-body");
    panel.classList.remove("hidden");
    body.innerHTML = `<tr><td colspan="14" class="text-center text-slate-500 py-4">Comparing…</td></tr>`;
    $("compare-summary").classList.add("hidden");
    $("compare-summary").innerHTML = "";
    $("compare-summarize").disabled = true;
    try {
        const data = await api("/api/compare", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ symbols: syms }),
        });
        if (data.error) { body.innerHTML = `<tr><td colspan="14" class="text-center neg py-4">${esc(data.error)}</td></tr>`; return; }
        compareLastRun = syms;
        renderComparePanel(data);
        $("compare-summarize").disabled = false;
    } catch (e) {
        body.innerHTML = `<tr><td colspan="14" class="text-center neg py-4">Error: ${esc(e.message)}</td></tr>`;
    }
}

function renderComparePanel(data) {
    const meta = data.meta || {};
    $("compare-regime").textContent = meta.market_regime ? "Regime: " + meta.market_regime : "";
    // Freshness banner
    const stale = $("compare-stale");
    if (meta.stale_warning) {
        stale.textContent = "⚠ " + meta.stale_warning;
        stale.classList.remove("hidden");
    } else {
        stale.classList.add("hidden");
    }
    // Ranking line
    const rank = (data.ranking || [])
        .map((x) => `${x.rank}. <span class="font-semibold">${esc(x.symbol)}</span> (${x.combined == null ? "—" : Number(x.combined).toFixed(1)})`)
        .join("  ›  ");
    $("compare-ranking").innerHTML = rank ? `<span class="mut">Ranking:</span> ${rank}` : "";
    // Missing symbols
    const missing = meta.missing || [];
    $("compare-missing").textContent = missing.length ? `Not covered (not on Research sheet): ${missing.join(", ")}` : "";
    // Table body — only found rows, in requested order
    const num = (v, d = 2, signed = false) =>
        (v == null ? "—" : (signed && v >= 0 ? "+" : "") + Number(v).toFixed(d));
    const rows = (data.rows || []).filter((r) => r.found);
    $("compare-body").innerHTML = rows.length ? rows.map((r) => `
        <tr>
            <td class="font-semibold cursor-pointer hover:text-blue-400" data-open="${esc(r.symbol)}">${esc(r.symbol)}</td>
            <td class="text-right">${r.price == null ? "—" : fmt$(r.price)}</td>
            <td class="text-right font-semibold ${cls(r.combined)}">${num(r.combined, 1, true)}</td>
            <td class="text-right ${cls(r.s10)}">${num(r.s10, 1, true)}</td>
            <td class="text-right ${cls(r.l60)}">${num(r.l60, 1, true)}</td>
            <td class="text-xs">${esc(r.pgr ?? "—")}</td>
            <td class="text-right text-xs">${esc(r.money_flow || "—")}</td>
            <td class="text-right text-xs">${esc(r.lt_trend || "—")}</td>
            <td>${r.setup ? '<span class="pos font-semibold">OK</span>' : '<span class="mut">—</span>'}</td>
            <td class="text-right ${r.stale ? "text-amber-400" : ""}" title="stop source: ${esc(r.stop_source || "?")}">${r.stop == null ? "—" : fmt$(r.stop)}</td>
            <td class="text-right ${r.stale ? "text-amber-400" : ""}" title="target source: ${esc(r.target_source || "?")}">${r.target == null ? "—" : fmt$(r.target)}</td>
            <td class="text-right">${num(r.risk_ratio, 2)}</td>
            <td>${renderPatternsHTML(r.patterns)}</td>
            <td class="text-xs">${esc(r.status || "—")}</td>
        </tr>`).join("")
        : `<tr><td colspan="14" class="text-center text-slate-500 py-4">No comparable symbols.</td></tr>`;
}

async function summarizeCompare() {
    if (compareLastRun.length < 2) return;
    const box = $("compare-summary");
    box.classList.remove("hidden");
    box.textContent = "Thinking…";
    $("compare-summarize").disabled = true;
    try {
        const data = await api("/api/compare", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ symbols: compareLastRun, summarize: true }),
        });
        const summary = data.summary;
        if (!summary) {
            const why = data.summary_error ? " — " + data.summary_error : "";
            box.textContent = "AI summary unavailable" + why + ".";
            return;
        }
        if (window.marked && typeof window.marked.parse === "function") {
            box.innerHTML = window.marked.parse(summary);
        } else {
            box.innerHTML = esc(summary).replace(/\n/g, "<br>");
        }
    } catch (e) {
        box.textContent = "AI summary unavailable — the request failed: " + e.message;
    } finally {
        $("compare-summarize").disabled = false;
    }
}

function clearCompare() {
    compareSelected.clear();
    compareLastRun = [];
    document.querySelectorAll("#research-body .cmp-check").forEach((cb) => { cb.checked = false; });
    $("compare-panel").classList.add("hidden");
    updateCompareButton();
}

$("compare-run").addEventListener("click", runCompare);
$("compare-clear").addEventListener("click", clearCompare);
$("compare-close").addEventListener("click", () => $("compare-panel").classList.add("hidden"));
$("compare-summarize").addEventListener("click", summarizeCompare);

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

async function runBacktest() {
    const sym = ($("bt-symbol").value || "").trim().toUpperCase();
    const out = $("bt-result");
    if (!sym) { out.textContent = "enter a symbol"; return; }
    out.textContent = "running…";
    try {
        const d = await api("/api/backtest?symbol=" + encodeURIComponent(sym));
        if (d.error) { out.textContent = `${sym}: ${d.error}`; return; }
        const sup = d.support, res = d.resistance, o = d.outcome;
        out.innerHTML = `<b class="text-slate-200">${sym}</b> · ${d.samples} predictions · ` +
            (sup ? `support held <b class="${sup.hold_rate >= 50 ? "pos" : "neg"}">${sup.hold_rate}%</b> (gap ${sup.median_gap_pct}%) · ` : "") +
            (res ? `target hit <b class="pos">${res.hit_rate}%</b> (gap ${res.median_gap_pct}%) · ` : "") +
            (o && o.win_rate != null ? `win-rate <b class="${o.win_rate >= 50 ? "pos" : "neg"}">${o.win_rate}%</b>` : "");
    } catch (e) { out.textContent = "error: " + e.message; }
}
$("bt-run").addEventListener("click", runBacktest);
$("bt-symbol").addEventListener("keydown", (e) => { if (e.key === "Enter") runBacktest(); });

// ── Per-tab loader ───────────────────────────────────────────────────────────
function loadTab(tab) {
    if (tab === "dashboard") loadDashboard();
    else if (tab === "research") loadResearch();
    else if (tab === "accounts") loadAccounts();
    else if (tab === "rotation") loadRotation();
    else if (tab === "history") loadHistory();
    else if (tab === "chat") setTimeout(() => $("chat-input").focus(), 50);
    else if (tab === "scorecard") loadScorecard();
    else if (tab === "system") { loadSystem(); _startLogRefresh(); }
    else if (tab === "about") loadRoadmap();
}

async function loadRoadmap() {
    const el = $("roadmap-dynamic-content");
    if (!el) return;
    el.innerHTML = '<span class="text-slate-500">Loading R&D roadmap from single source of truth…</span>';
    try {
        const d = await api("/api/roadmap");
        if (d.error) { el.textContent = "Error loading roadmap: " + d.error; return; }
        
        let parsedHTML = null;
        try {
            if (window.marked) {
                if (typeof window.marked.parse === "function") {
                    parsedHTML = window.marked.parse(d.markdown);
                } else if (typeof window.marked === "function") {
                    parsedHTML = window.marked(d.markdown);
                }
            }
        } catch (parseError) {
            console.error("Markdown parsing failed:", parseError);
        }
        
        if (parsedHTML) {
            el.className = "text-xs text-slate-300 leading-relaxed space-y-4 max-h-[60vh] overflow-y-auto pr-2 scrollbar-thin scrollbar-thumb-slate-800 scrollbar-track-transparent mt-2";
            el.innerHTML = parsedHTML;
        } else {
            // Fallback plaintext
            el.className = "text-[11px] text-slate-300 leading-relaxed font-mono whitespace-pre-wrap max-h-[60vh] overflow-y-auto pr-2 scrollbar-thin scrollbar-thumb-slate-800 scrollbar-track-transparent mt-2";
            el.textContent = d.markdown;
        }
    } catch (e) {
        el.textContent = "Error loading roadmap: " + e.message;
    }
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

// ── Symbol detail modal ──────────────────────────────────────────────────────
let _symChart = null;      // kept for closeSymbolModal cleanup (unused after candlestick)
let _smChartData = [];
let _smChartDays = 90;

function _set(id, html, klass) {
    const el = $(id); if (!el) return;
    el.innerHTML = html;
    if (klass !== undefined) el.className = klass;
}

// ── Chart fullscreen overlay ──────────────────────────────────────────────────
function openChartFullscreen(sym, bars, days) {
    const overlay = $("chart-fs-overlay");
    $("chart-fs-sym").textContent = sym;
    overlay.classList.add("active");

    const fsWrap    = $("chart-fs-svg");
    const fsTooltip = $("chart-fs-tooltip");

    const draw = (d) => renderCandlestick(bars, d, fsWrap, fsTooltip);
    draw(days);

    // Wire fullscreen range buttons
    overlay.querySelectorAll(".fs-range-btn").forEach(btn => {
        btn.onclick = () => draw(+btn.dataset.days);
    });

    // Redraw on window resize
    const onResize = () => draw(+overlay.querySelector(".fs-range-btn.bg-blue-700")?.dataset.days || days);
    window.addEventListener("resize", onResize);

    // Close
    const close = () => {
        overlay.classList.remove("active");
        fsWrap.innerHTML = "";
        window.removeEventListener("resize", onResize);
        $("chart-fs-close").onclick = null;
    };
    $("chart-fs-close").onclick = close;
    overlay._closeFs = close;
}

// ── Modal chart resize handle ─────────────────────────────────────────────────
function initChartResize() {
    const svgWrap = $("sm-chart-svg");
    if (!svgWrap || svgWrap._resizeInit) return;
    svgWrap._resizeInit = true;

    // Drag handle bar below the chart
    const handle = document.createElement("div");
    handle.style.cssText = "height:6px;cursor:ns-resize;background:transparent;border-top:2px solid #1e293b;margin-top:2px;border-radius:0 0 4px 4px;";
    handle.title = "Drag to resize chart";
    svgWrap.parentElement.insertBefore(handle, svgWrap.nextSibling);

    let startY = 0, startH = 0;
    handle.addEventListener("mousedown", e => {
        startY = e.clientY;
        startH = svgWrap.clientHeight;
        const onMove = mv => {
            const newH = Math.max(120, Math.min(600, startH + mv.clientY - startY));
            svgWrap.style.height = newH + "px";
            if (_smChartData.length) renderCandlestick(_smChartData, _smChartDays);
        };
        const onUp = () => {
            document.removeEventListener("mousemove", onMove);
            document.removeEventListener("mouseup", onUp);
        };
        document.addEventListener("mousemove", onMove);
        document.addEventListener("mouseup", onUp);
        e.preventDefault();
    });
}

// wrapEl / tooltipEl default to the modal chart; pass the fullscreen elements for fs mode.
function renderCandlestick(allBars, days, wrapEl, tooltipEl) {
    const bars = allBars.slice(-days);
    if (!bars.length) return;

    const wrap    = wrapEl    || $("sm-chart-svg");
    const tooltip = tooltipEl || $("sm-chart-tooltip");
    if (!wrap) return;

    // Highlight active range button — covers both modal (.sm-range-btn) and fullscreen (.fs-range-btn)
    const btnClass = wrapEl ? "fs-range-btn" : "sm-range-btn";
    document.querySelectorAll("." + btnClass).forEach(b => {
        const active = +b.dataset.days === days;
        b.className = `${btnClass} px-2 py-0.5 rounded text-xs ${active ? "bg-blue-700 text-white" : "bg-slate-800 text-slate-400 hover:text-white"}`;
    });

    // Ensure W and H have healthy safe minimums (cures collapsed modal clientWidth transition bugs)
    let W = wrap.clientWidth;
    if (!W || W < 300) W = wrapEl ? window.innerWidth : 600;
    let H = wrap.clientHeight;
    if (!H || H < 150) H = wrapEl ? window.innerHeight - 48 : 220;
    const volH = 36;           // volume panel height
    const padL = 52, padR = 8, padT = 10, padB = 20;
    const plotW = W - padL - padR;
    const priceH = H - padT - padB - volH - 6;  // 6px gap between panels

    const highs  = bars.map(b => b.high);
    const lows   = bars.map(b => b.low);
    const vols   = bars.map(b => b.volume);
    const yMax   = Math.max(...highs);
    const yMin   = Math.min(...lows);
    const yRange = yMax - yMin || 1;
    const vMax   = Math.max(...vols) || 1;

    const n = bars.length;
    const candleW = Math.max(1, Math.floor(plotW / n) - 1);
    const halfC   = Math.max(0.5, candleW / 2);

    function px(i) { return padL + (i + 0.5) * (plotW / n); }
    function py(v) { return padT + priceH - ((v - yMin) / yRange) * priceH; }
    function vy(v) { return H - padB - (v / vMax) * volH; }

    // Y-axis ticks (5 levels)
    const yTicks = 5;
    const yStep  = yRange / (yTicks - 1);
    let yAxis = "", yGrid = "";
    for (let i = 0; i < yTicks; i++) {
        const val = yMin + i * yStep;
        const y   = py(val);
        const lbl = val >= 1000 ? val.toFixed(0) : val >= 10 ? val.toFixed(1) : val.toFixed(2);
        yAxis += `<text x="${padL - 4}" y="${y + 4}" text-anchor="end" fill="#64748b" font-size="10">${lbl}</text>`;
        yGrid += `<line x1="${padL}" y1="${y}" x2="${W - padR}" y2="${y}" stroke="rgba(51,65,85,0.4)" stroke-width="1"/>`;
    }

    // X-axis labels (up to 8, spaced evenly, show MM-DD)
    const xLabelCount = Math.min(8, n);
    const xStep = Math.floor(n / xLabelCount);
    let xAxis = "";
    for (let i = 0; i < n; i += xStep) {
        const lbl = bars[i].date.slice(5);  // MM-DD
        xAxis += `<text x="${px(i)}" y="${H - padB + 13}" text-anchor="middle" fill="#64748b" font-size="10">${lbl}</text>`;
    }

    // Candles + wicks
    let candles = "";
    bars.forEach((b, i) => {
        const bull = b.close >= b.open;
        const col  = bull ? "#4ade80" : "#f87171";
        const x    = px(i);
        const bodyTop    = py(Math.max(b.open, b.close));
        const bodyBot    = py(Math.min(b.open, b.close));
        const bodyHeight = Math.max(1, bodyBot - bodyTop);
        const wickTop    = py(b.high);
        const wickBot    = py(b.low);
        candles +=
            `<line x1="${x}" y1="${wickTop}" x2="${x}" y2="${wickBot}" stroke="${col}" stroke-width="1"/>` +
            `<rect x="${x - halfC}" y="${bodyTop}" width="${candleW}" height="${bodyHeight}" fill="${col}" rx="1"/>`;
    });

    // Volume bars
    let volBars = "";
    bars.forEach((b, i) => {
        const bull = b.close >= b.open;
        const col  = bull ? "rgba(74,222,128,0.25)" : "rgba(248,113,113,0.25)";
        const x    = px(i);
        const top  = vy(b.volume);
        const bot  = H - padB;
        volBars += `<rect x="${x - halfC}" y="${top}" width="${candleW}" height="${bot - top}" fill="${col}" rx="1"/>`;
    });

    // Invisible hover hit targets
    let hits = "";
    bars.forEach((b, i) => {
        hits += `<rect class="cs-hit" x="${px(i) - (plotW / n) / 2}" y="${padT}" width="${plotW / n}" height="${H - padT - padB}" fill="transparent" data-i="${i}"/>`;
    });

    // Crosshair lines (hidden by default)
    const crosshair = `
        <line id="cs-vline" x1="0" y1="${padT}" x2="0" y2="${H - padB}" stroke="#475569" stroke-width="1" stroke-dasharray="3,3" display="none" pointer-events="none"/>
        <line id="cs-hline" x1="${padL}" y1="0" x2="${W - padR}" y2="0" stroke="#475569" stroke-width="1" stroke-dasharray="3,3" display="none" pointer-events="none"/>`;

    wrap.innerHTML = `<svg width="${W}" height="${H}" style="display:block">
        ${yGrid}
        <line x1="${padL}" y1="${H - padB - volH - 3}" x2="${W - padR}" y2="${H - padB - volH - 3}" stroke="rgba(51,65,85,0.3)" stroke-width="1"/>
        ${volBars}${candles}${crosshair}${hits}
        ${yAxis}${xAxis}
    </svg>`;

    const svg = wrap.querySelector("svg");
    const vline = wrap.querySelector("#cs-vline");
    const hline = wrap.querySelector("#cs-hline");

    wrap.querySelectorAll(".cs-hit").forEach(el => {
        el.addEventListener("mousemove", e => {
            const idx = +el.dataset.i;
            const b   = bars[idx];
            const x   = px(idx);
            const y   = py(b.close);

            vline.setAttribute("x1", x); vline.setAttribute("x2", x);
            hline.setAttribute("y1", y); hline.setAttribute("y2", y);
            vline.setAttribute("display", ""); hline.setAttribute("display", "");

            const bull = b.close >= b.open;
            const pctChg = ((b.close - b.open) / b.open * 100);
            const volFmt = b.volume >= 1e6 ? (b.volume / 1e6).toFixed(1) + "M" : (b.volume / 1e3).toFixed(0) + "K";
            tooltip.innerHTML = `
                <div class="font-semibold ${bull ? "text-green-400" : "text-red-400"} mb-1">${b.date} &nbsp;${bull ? "▲" : "▼"} ${pctChg >= 0 ? "+" : ""}${pctChg.toFixed(2)}%</div>
                <div class="grid grid-cols-2 gap-x-3 gap-y-0.5">
                    <span class="text-slate-400">Open</span><span>${fmt$(b.open)}</span>
                    <span class="text-slate-400">High</span><span class="text-green-400">${fmt$(b.high)}</span>
                    <span class="text-slate-400">Low</span><span class="text-red-400">${fmt$(b.low)}</span>
                    <span class="text-slate-400">Close</span><span class="font-semibold">${fmt$(b.close)}</span>
                    <span class="text-slate-400">Vol</span><span>${volFmt}</span>
                </div>`;
            // Position tooltip: right of cursor unless near right edge
            const rect = wrap.getBoundingClientRect();
            const cx = e.clientX - rect.left;
            const cy = e.clientY - rect.top;
            const tip = tooltip;
            tip.classList.remove("hidden");
            const tipW = 152;
            const left = cx + 12 + tipW > W ? cx - tipW - 8 : cx + 12;
            tip.style.left = left + "px";
            tip.style.top  = Math.max(0, cy - 20) + "px";
        });
        el.addEventListener("mouseleave", () => {
            vline.setAttribute("display", "none");
            hline.setAttribute("display", "none");
            tooltip.classList.add("hidden");
        });
    });
}

async function openSymbol(sym) {
    sym = (sym || "").trim().toUpperCase();
    if (!sym) return;
    const modal = $("sym-modal");
    modal.classList.remove("hidden");
    // Header (known before fetch)
    _set("sm-symbol", sym);
    _set("sm-badges", ""); _set("sm-pgr", ""); _set("sm-status", ""); _set("sm-industry", "");
    _set("sm-price", ""); _set("sm-holding-badge", ""); $("sm-holding-badge").classList.add("hidden");
    $("sm-loading").classList.remove("hidden");
    $("sm-body").classList.add("hidden");

    let d;
    try { d = await api("/api/symbol/" + encodeURIComponent(sym)); }
    catch (e) { _set("sm-loading", "Error: " + e.message); return; }

    $("sm-loading").classList.add("hidden");
    $("sm-body").classList.remove("hidden");

    const r = d.research || {};
    const bt = d.backtest || {};
    const h = d.holding;

    // Header
    _set("sm-symbol", sym);
    _set("sm-badges", instrumentBadge(r.instrument));
    const pgrCls = (r.pgr || "").includes("Bu") ? "text-green-400" : (r.pgr || "").includes("Be") ? "text-red-400" : "text-slate-300";
    _set("sm-pgr", pgrCell(r.prev_pgr, r.pgr));
    $("sm-pgr").className = `text-sm font-semibold px-2 py-0.5 rounded bg-slate-800 ${pgrCls}`;
    _set("sm-status", r.status || "—");
    _set("sm-industry", [r.industry, r.industry_strength ? `(${r.industry_strength})` : ""].filter(Boolean).join(" "));
    _set("sm-price", r.price != null ? fmt$(r.price) : "—");

    // Scores
    const n1 = (v, d=1) => v == null ? "—" : Number(v).toFixed(d);
    _set("sm-s10", n1(r.s10), `text-lg font-bold ${cls(r.s10)}`);
    _set("sm-l60", n1(r.l60), `text-lg font-bold ${cls(r.l60)}`);
    _set("sm-comb", n1(r.combined), `text-lg font-bold ${cls(r.combined)}`);
    _set("sm-win", r.win_pct != null ? r.win_pct + "%" : "—");
    _set("sm-br", n1(r.buying_ratio), `text-lg font-bold ${cls(r.buying_ratio)}`);
    _set("sm-seas", r.seasonality == null ? "—" : (r.seasonality >= 0 ? "+" : "") + r.seasonality.toFixed(1));

    // Levels
    const stopWk = weakStop(r, r.stop_source);
    _set("sm-stop", r.stop ? fmt$(r.stop) : "—", `font-semibold ${stopWk ? "text-amber-400" : ""}`);
    _set("sm-stop-src", r.stop_source || "");
    const tgtWk = weakStop(r, r.target_source);
    _set("sm-target", r.target ? fmt$(r.target) : "—", `font-semibold ${tgtWk ? "text-amber-400" : ""}`);
    _set("sm-tgt-src", r.target_source || "");
    _set("sm-rr", r.risk_ratio != null ? r.risk_ratio.toFixed(2) : "—");

    // Chaikin signals
    _set("sm-mf", r.money_flow || "—");
    _set("sm-obos", r.obos || "—");
    _set("sm-lt", r.lt_trend || "—");
    _set("sm-pat", r.patterns ? renderPatternsHTML(r.patterns) : "—");

    // Digit-sum numerology table
    const digitSec = $("sm-digit-section");
    const digitRows = d.digit_study || [];
    if (digitRows.length > 0) {
        digitSec.classList.remove("hidden");
        const byType = { OPEN: {}, CLOSE: {} };
        for (const row of digitRows) byType[row.type] && (byType[row.type][row.digit] = row);
        const digits = [1,2,3,4,5,6,7,8,9,0];
        const sig = (r) => r && Math.abs(r.z) >= 2.0;
        const hasSig = digits.some(dg => sig(byType.OPEN[dg]) || sig(byType.CLOSE[dg]));

        const _DIGIT_TOOLTIP = [
            "How to read this table:",
            "",
            "The digit-sum of a price collapses it to a single digit.",
            "Example: $247 → 2+4+7=13 → 1+3 = digit 4.",
            "",
            "Open→Day%: on days this stock opened at that digit,",
            "what % of those days closed up.",
            "",
            "Close→Next%: when the stock closed at that digit,",
            "what % of the NEXT day's sessions went up.",
            "",
            "Base: the stock's overall up-day rate (all digits combined).",
            "",
            "Bold = 95%+ confidence (|z|≥2.0, N≥50 days).",
            "Non-bold rows are within noise — ignore them.",
            "",
            "This is a weak tiebreaker factor (±1pt in S10),",
            "not a standalone buy/sell signal."
        ].join("\n");

        const buildTable = (showAll) => {
            let html = `<table class="w-full text-left border-collapse text-xs">
                <thead><tr class="text-slate-500">
                    <th class="pr-3 py-0.5">Digit</th>
                    <th class="pr-3">Open→Day% <span class="text-slate-600">(z)</span></th>
                    <th class="pr-3">Base</th>
                    <th class="pr-3 border-l border-slate-700 pl-3">Close→Next% <span class="text-slate-600">(z)</span></th>
                    <th class="pr-3">Base</th>
                    <th title="Temporal consistency across 2-year windows">Stability</th>
                </tr></thead><tbody>`;
            let shown = 0;
            for (const dg of digits) {
                const o = byType.OPEN[dg], c = byType.CLOSE[dg];
                if (!o && !c) continue;
                const isSig = sig(o) || sig(c);
                if (!showAll && !isSig) continue;
                shown++;
                const fmt = (r) => r
                    ? `<span class="${r.z > 0 ? 'text-green-400' : 'text-red-400'}${sig(r) ? ' font-bold' : ''}">${(r.up_pct*100).toFixed(1)}% (${r.z > 0 ? '+' : ''}${r.z.toFixed(1)})</span>`
                    : `<span class="mut">—</span>`;
                const baseO = o ? `${(o.base*100).toFixed(1)}%` : '—';
                const baseC = c ? `${(c.base*100).toFixed(1)}%` : '—';
                const _sigRow = (o && sig(o)) ? o : (c && sig(c)) ? c : null;
                let tqBadge;
                if (!_sigRow) {
                    tqBadge = '<span class="mut">—</span>';
                } else if (_sigRow.has_flip) {
                    tqBadge = '<span class="text-red-400" title="Direction flips between time periods — unreliable">flip</span>';
                } else if (_sigRow.is_sparse) {
                    tqBadge = '<span class="text-orange-400" title="Digit rarely appears at current price level">sparse</span>';
                } else if (_sigRow.temporal === 'consistent') {
                    tqBadge = '<span class="text-green-500" title="Fires in same direction across 4+ time periods">stable</span>';
                } else if (_sigRow.temporal === 'partial') {
                    tqBadge = '<span class="text-amber-500" title="Fires in same direction in 2-3 time periods">mixed</span>';
                } else {
                    tqBadge = '<span class="text-slate-500" title="Concentrated in historical data only">stale</span>';
                }
                html += `<tr class="border-t border-slate-800${isSig ? '' : ' opacity-50'}">
                    <td class="pr-3 py-0.5 text-slate-300">${dg}</td>
                    <td class="pr-3">${fmt(o)}</td>
                    <td class="pr-3 mut">${baseO}</td>
                    <td class="pr-3 border-l border-slate-700 pl-3">${fmt(c)}</td>
                    <td class="pr-3 mut">${baseC}</td>
                    <td>${tqBadge}</td>
                </tr>`;
            }
            if (shown === 0) {
                html += `<tr><td colspan="6" class="py-1 mut">No significant signals for this symbol.</td></tr>`;
            }
            html += `</tbody></table>`;
            return html;
        };

        // Hide the descriptive subtitle when there are no signals
        const subtitle = $("sm-digit-subtitle");
        if (subtitle) subtitle.classList.toggle("hidden", !hasSig);

        let _showAllDigits = false;
        const render = () => {
            if (!hasSig) {
                $("sm-digit-table").innerHTML = `<div class="mut text-xs py-1">No significant digit-sum signals for this symbol.</div>`;
                return;
            }
            const toggleLabel = _showAllDigits ? "Show significant only" : "Show all digits";
            $("sm-digit-table").innerHTML = buildTable(_showAllDigits) +
                `<div class="mt-1 flex items-center gap-3 text-slate-600">
                    <span>Bold = 95%+ confidence &middot; N≥50 per cell &middot; refresh monthly</span>
                    <button id="sm-digit-toggle" class="text-blue-500 hover:text-blue-300 underline underline-offset-2">${toggleLabel}</button>
                </div>`;
            const btn = $("sm-digit-toggle");
            if (btn) btn.onclick = () => { _showAllDigits = !_showAllDigits; render(); };
        };
        render();

        // Tooltip on section label
        const label = digitSec.querySelector(".card-label");
        if (label) label.title = _DIGIT_TOOLTIP;
    } else {
        digitSec.classList.add("hidden");
    }

    // Holding
    const holdSec = $("sm-holding-section");
    if (h) {
        $("sm-holding-badge").classList.remove("hidden");
        holdSec.classList.remove("hidden");
        _set("sm-acct", h.account_label || h.account_id);
        _set("sm-entry", fmt$(h.buy));
        _set("sm-qty", h.qty != null ? h.qty : "—");
        _set("sm-pnl", h.pnl_pct != null ? fmtPct(h.pnl_pct) : "—", `font-semibold ${cls(h.pnl_pct)}`);
        _set("sm-entry-stop", h.stop ? fmt$(h.stop) : "—", `font-semibold ${weakStop(h, h.stop_source) ? "text-amber-400" : ""}`);
        _set("sm-entry-stop-src", h.stop_source || "");
        _set("sm-entry-tgt", h.target ? fmt$(h.target) : "—");
        _set("sm-buy-date", h.buy_date || "—");
    } else {
        holdSec.classList.add("hidden");
        $("sm-holding-badge").classList.add("hidden");
    }

    // Requalify button + result panel (always shown on symbol modal)
    const smRqWrap = $("sm-rq-wrap");
    if (smRqWrap) {
        const _smInitLabel = esc(_statusToRec(r.status));
        smRqWrap.innerHTML = `
            <button id="sm-rq-btn" onclick="smRequalify()"
                class="px-3 py-1 rounded text-sm bg-slate-700 hover:bg-blue-700 text-slate-200 transition-colors max-w-[8rem] truncate"
                title="Click to run live AI analysis">
                ${_smInitLabel}
            </button>
            <div id="sm-rq-result" class="hidden mt-2"></div>`;
    }

    // Candlestick chart
    const chartWrap = $("sm-chart-wrap");
    const allBars = d.chart || [];
    if (allBars.length > 0) {
        chartWrap.classList.remove("hidden");
        _smChartData = allBars;
        _smChartDays = 90;
        renderCandlestick(allBars, 90);
        initChartResize();

        // Wire modal range buttons
        chartWrap.querySelectorAll(".sm-range-btn").forEach(btn => {
            btn.addEventListener("click", () => {
                _smChartDays = +btn.dataset.days;
                renderCandlestick(_smChartData, _smChartDays);
            });
        });

        // Chart fullscreen button
        const chartFsBtn = $("sm-chart-fs");
        if (chartFsBtn) {
            chartFsBtn.onclick = () => openChartFullscreen(sym, _smChartData, _smChartDays);
        }
    } else {
        chartWrap.classList.add("hidden");
    }

    // Modal fullscreen button (uses Browser Fullscreen API on the card element)
    const modalFsBtn = $("sm-fs");
    const modalCard  = $("sym-modal").querySelector(".card");
    if (modalFsBtn && modalCard) {
        modalFsBtn.onclick = () => {
            if (!document.fullscreenElement) {
                modalCard.requestFullscreen().catch(() => {});
            } else {
                document.exitFullscreen();
            }
        };
    }
    // Sync fullscreen button icon
    const syncFsIcon = () => {
        if (modalFsBtn) modalFsBtn.textContent = document.fullscreenElement ? "⊠" : "⛶";
    };
    document.removeEventListener("fullscreenchange", syncFsIcon);
    document.addEventListener("fullscreenchange", syncFsIcon);

    // Backtest
    const btWrap = $("sm-bt-wrap");
    const sup = bt.support, res = bt.resistance, o = bt.outcome;
    if (bt.samples > 0) {
        btWrap.classList.remove("hidden");
        _set("sm-bt-n", bt.samples);
        _set("sm-bt-sup", sup ? sup.hold_rate + "%" : "—", `font-semibold ${sup && sup.hold_rate >= 50 ? "pos" : "neg"}`);
        _set("sm-bt-tgt", res ? res.hit_rate + "%" : "—", `font-semibold ${res && res.hit_rate >= 50 ? "pos" : "neg"}`);
        _set("sm-bt-wr", o && o.win_rate != null ? o.win_rate + "%" : "—", `font-semibold ${o && o.win_rate >= 50 ? "pos" : "neg"}`);
    } else {
        btWrap.classList.add("hidden");
    }
}

function closeSymbolModal() {
    $("sym-modal").classList.add("hidden");
    _smChartData = [];
    const svg = $("sm-chart-svg");
    if (svg) svg.innerHTML = "";
    if (_symChart) { _symChart.destroy(); _symChart = null; }
}

$("sm-close").addEventListener("click", closeSymbolModal);
$("sym-modal").addEventListener("click", (e) => { if (e.target === $("sym-modal")) closeSymbolModal(); });
document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
        const fsOverlay = $("chart-fs-overlay");
        if (fsOverlay && fsOverlay.classList.contains("active")) {
            if (fsOverlay._closeFs) fsOverlay._closeFs();
        } else {
            closeSymbolModal();
        }
    }
});

// Wire every rendered data-sym row to open the symbol modal when the symbol
// name cell is clicked. Uses event delegation — works for any table re-rendered
// after page load. The symbol cell must carry data-open="sym" (added in each
// row template) so we don't accidentally open on price/P&L cell clicks.
document.addEventListener("click", (e) => {
    const cell = e.target.closest("[data-open]");
    if (cell) { openSymbol(cell.dataset.open); return; }

    // Status inline click: automatically open the Scorecard & Retrospective page
    const statusLink = e.target.closest("[data-to-scorecard]");
    if (statusLink) { switchTab("scorecard"); return; }

    // Accounts page status inline click: open AETHER AI Requalification modal
    const rqBtn = e.target.closest(".rq-btn");
    if (rqBtn) { requalify(rqBtn.dataset.rqSym, parseFloat(rqBtn.dataset.rqBuy) || null, rqBtn); return; }

    // Retro card click: smoothly scroll to and highlight our retrospective documentation card
    const retroLink = e.target.closest("[data-to-retro]");
    if (retroLink) {
        const retroCard = document.querySelector(".border.border-slate-800.bg-slate-900\\/30.rounded.p-5");
        if (retroCard) {
            retroCard.scrollIntoView({ behavior: "smooth", block: "center" });
            // Flashing highlight effect to draw focus!
            retroCard.classList.add("ring-2", "ring-emerald-500", "transition-all", "duration-500");
            setTimeout(() => retroCard.classList.remove("ring-2", "ring-emerald-500"), 2000);
        }
        return;
    }
});

// ── Requalify ─────────────────────────────────────────────────────────────────

// Maps sheet status strings to {label, cls} for the accounts/symbol button.
// One function; callers use .label or .cls as needed.
function _statusInfo(s) {
    const u = (s || "").toUpperCase().trim();
    if (u.startsWith("EXIT"))        return { label: "SELL",   cls: "bg-red-950/70 text-red-300 border border-red-700/60 hover:bg-red-900" };
    if (u.startsWith("REDUCE"))      return { label: "REDUCE", cls: "bg-orange-950/70 text-orange-300 border border-orange-700/60 hover:bg-orange-900" };
    if (u.startsWith("STRONG HOLD")) return { label: "HOLD",   cls: "bg-green-950/70 text-green-300 border border-green-700/60 hover:bg-green-900" };
    if (u.startsWith("HOLD"))        return { label: "HOLD",   cls: "bg-emerald-950/70 text-emerald-300 border border-emerald-700/60 hover:bg-emerald-900" };
    if (u.startsWith("WATCH") || u.startsWith("REVIEW")) return { label: "REVIEW", cls: "bg-amber-950/70 text-amber-300 border border-amber-700/60 hover:bg-amber-900" };
    if (u === "NEUTRAL")             return { label: "HOLD",   cls: "bg-emerald-950/70 text-emerald-300 border border-emerald-700/60 hover:bg-emerald-900" };
    if (u === "BUY MORE" || u === "SELL") return { label: u,  cls: u === "SELL" ? "bg-red-950/70 text-red-300 border border-red-700/60 hover:bg-red-900" : "bg-blue-950/70 text-blue-300 border border-blue-700/60 hover:bg-blue-900" };
    return { label: u || "⚡ AI",   cls: "bg-slate-800 text-slate-300 border border-slate-700/60 hover:bg-slate-700" };
}
const _statusToRec = (s) => _statusInfo(s).label;

const _RQ_POLL_INTERVAL_MS = 2000;
const _RQ_MAX_POLLS = 30;  // 30 × 2s = 60s max

const _RQ_BADGE = {
    "BUY MORE": "bg-green-800 text-green-200",
    "HOLD":     "bg-slate-700 text-slate-200",
    "REVIEW":   "bg-amber-800 text-amber-200",
    "REDUCE":   "bg-orange-800 text-orange-200",
    "SELL":     "bg-red-800 text-red-200",
};
const _VD_BADGE = {
    "AGREE":           "text-green-400",
    "FLAG-FOR-REVIEW": "text-amber-400",
    "NO-OPINION":      "text-slate-400",
};

const _statusBadgeClass = (s) => _statusInfo(s).cls;

function _rqBadge(rec) {
    const cls_ = _RQ_BADGE[rec] || "bg-slate-700 text-slate-300";
    return `<span class="px-2 py-0.5 rounded text-xs font-bold ${cls_}">${esc(rec || "—")}</span>`;
}

function _rqPanel(d, sym, newsStatus) {
    const f = d.factors || {};
    const vdCls = _VD_BADGE[d.verdict] || "text-slate-400";
    const confBadge = d.confidence
        ? `<span class="text-xs mut ml-1">(${esc(d.confidence)})</span>` : "";
    const verdictLine = d.verdict
        ? `<div class="mt-1 text-xs ${vdCls}">Engine: ${esc(d.verdict)}${d.verdict_note ? " — " + esc(d.verdict_note) : ""}</div>` : "";
    const factGrid = `
        <div class="grid grid-cols-4 gap-x-4 gap-y-0.5 text-xs mt-2 mut">
            <span>PGR: <b class="text-slate-200">${esc(f.pgr || "—")}</b></span>
            <span>S10: <b class="${cls(f.s10)}">${f.s10 != null ? Number(f.s10).toFixed(1) : "—"}</b></span>
            <span>L60: <b class="${cls(f.l60)}">${f.l60 != null ? Number(f.l60).toFixed(1) : "—"}</b></span>
            <span>Score: <b class="${cls(f.combined)}">${f.combined != null ? Number(f.combined).toFixed(1) : "—"}</b></span>
            <span>BR: <b class="${cls(f.buying_ratio)}">${f.buying_ratio != null ? Number(f.buying_ratio).toFixed(1) : "—"}</b></span>
            <span>MF: <b class="text-slate-200">${esc(f.money_flow || "—")}</b></span>
            <span>Stop: <b class="text-slate-200">${f.stop ? "$" + f.stop.toFixed(2) : "—"}</b></span>
            <span>Target: <b class="text-slate-200">${f.target ? "$" + f.target.toFixed(2) : "—"}</b></span>
        </div>
        ${f.patterns ? `<div class="text-xs mut mt-1">Patterns: <b class="text-slate-200">${esc(f.patterns)}</b></div>` : ""}`;
    const newsHtml = (d.news && d.news.length)
        ? `<ul class="mt-2 text-xs mut list-disc pl-4 space-y-0.5">${d.news.map(n => `<li>${esc(n)}</li>`).join("")}</ul>` : "";
    const errLine = d.error ? `<div class="text-xs text-amber-400 mt-1">⚠ ${esc(d.error)}</div>` : "";
    return `
        <div class="px-4 py-3 bg-slate-800/60 border-t border-slate-700 text-sm space-y-1 whitespace-normal break-words">
            <div class="flex items-start gap-2 flex-wrap">
                ${_rqBadge(d.recommendation)}${confBadge}
                <span class="text-slate-300 flex-1 break-words">${esc(d.rationale || "")}</span>
            </div>
            ${d.risk ? `<div class="text-xs text-slate-400 break-words">Risk: ${esc(d.risk)}</div>` : ""}
            ${verdictLine}${factGrid}${newsHtml}${errLine}
            <div class="text-xs mut mt-1" id="rq-news-status-${esc(sym)}">${newsStatus}</div>
        </div>`;
}

async function requalify(sym, cost, triggerBtn) {
    if (!sym) return;

    // Find or create the result row in the accounts table
    const resultRow = document.querySelector(`tr.rq-result-row[data-rq-for="${CSS.escape(sym)}"]`);

    // Update button state
    if (triggerBtn) {
        triggerBtn.disabled = true;
        triggerBtn.textContent = "⏳ Fetching...";
    }
    if (resultRow) {
        resultRow.classList.remove("hidden");
        resultRow.firstElementChild.innerHTML =
            `<div class="px-4 py-2 text-xs mut">Fetching live data for ${esc(sym)}…</div>`;
    }

    // Also update sym-modal requalify panel if open
    const smRq = $("sm-rq-result");
    if (smRq && $("sm-symbol") && $("sm-symbol").textContent === sym) {
        smRq.innerHTML = `<div class="text-xs mut py-2">Fetching live data…</div>`;
        smRq.classList.remove("hidden");
        const smBtn = $("sm-rq-btn");
        if (smBtn) { smBtn.disabled = true; smBtn.textContent = "⏳ Fetching..."; }
    }

    let d;
    try {
        d = await api("/api/requalify", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ symbol: sym, cost }),
        });
    } catch (err) {
        const errHtml = `<div class="px-4 py-2 text-xs text-red-400">Error: ${esc(err.message)}</div>`;
        if (resultRow) resultRow.firstElementChild.innerHTML = errHtml;
        if (smRq) smRq.innerHTML = errHtml;
        if (triggerBtn) { triggerBtn.disabled = false; }
        return;
    }

    const _rqLabel = (rec) => rec ? esc(rec) : "⚡ AI";
    const newsStatus = d.news_pending ? "🔍 Enriching with news…" : "";
    const panel = _rqPanel(d, sym, newsStatus);
    if (resultRow) resultRow.firstElementChild.innerHTML = panel;
    if (smRq && $("sm-symbol") && $("sm-symbol").textContent === sym) {
        smRq.innerHTML = panel;
        const smBtn = $("sm-rq-btn");
        if (smBtn) { smBtn.disabled = false; smBtn.textContent = _rqLabel(d.recommendation); }
    }
    if (triggerBtn) { triggerBtn.disabled = false; triggerBtn.textContent = _rqLabel(d.recommendation); }

    // Phase 2 — poll for news-enriched result (max 60s / 30 attempts)
    if (d.run_id && d.news_pending) {
        const runId = d.run_id;
        let attempts = 0;
        const poll = async () => {
            if (++attempts > _RQ_MAX_POLLS) {
                const nsEl = document.getElementById(`rq-news-status-${CSS.escape(sym)}`);
                if (nsEl) nsEl.textContent = "⚠ News search timed out.";
                return;
            }
            let p2;
            try { p2 = await api(`/api/requalify/${encodeURIComponent(runId)}`); }
            catch { return; }
            if (p2.status === "pending") { setTimeout(poll, _RQ_POLL_INTERVAL_MS); return; }
            const updated = { ...d, ...p2, news_pending: false };
            const panel2 = _rqPanel(updated, sym, "✅ Updated with news");
            const rr2 = document.querySelector(`tr.rq-result-row[data-rq-for="${CSS.escape(sym)}"]`);
            if (rr2) rr2.firstElementChild.innerHTML = panel2;
            if (smRq && $("sm-symbol") && $("sm-symbol").textContent === sym) smRq.innerHTML = panel2;
        };
        setTimeout(poll, _RQ_POLL_INTERVAL_MS);
    }
}

// Symbol modal Requalify button (injected into the modal HTML via openSymbol)
async function smRequalify() {
    const sym = $("sm-symbol") ? $("sm-symbol").textContent.trim() : "";
    const entryEl = $("sm-entry");
    const cost = entryEl ? parseFloat(entryEl.textContent.replace(/[^0-9.]/g, "")) || null : null;
    await requalify(sym, cost, $("sm-rq-btn"));
}

// Wiki Modal Interactive Logic
let AETHER_LIVE_RULES = null;
async function fetchLiveRules() {
    try {
        AETHER_LIVE_RULES = await api("/api/wiki/config");
        console.log("  [AETHER Wiki] Live rules hook connected successfully:", AETHER_LIVE_RULES);
    } catch (e) {
        console.log("  [AETHER Wiki] Live rules hook offline, falling back to static defaults:", e);
    }
}

async function fetchWiki() {
    try {
        AETHER_WIKI = await api('/api/wiki');
        console.log('  [AETHER Wiki] Wiki database loaded successfully:', Object.keys(AETHER_WIKI).length, 'entries');
    } catch (e) {
        console.error('  [AETHER Wiki] Failed to load wiki database:', e);
    }
}

async function initWiki() {
    await fetchWiki();
    fetchLiveRules();
    document.querySelectorAll("[data-wiki]").forEach((card) => {
        const key = card.getAttribute("data-wiki");
        // The roadmap card is not wiki-backed — its body is hydrated from
        // /api/roadmap (single-sourced already). Leave it entirely untouched.
        if (key === "aether_rd_roadmap") return;

        const entry = AETHER_WIKI[key];
        if (!entry) {
            // Never silently blank a card — surface the drift instead.
            console.warn(`[wiki] no wiki.json entry for data-wiki="${key}"`);
            return;
        }

        // Single source of truth: render the card face (title + summary) from
        // the wiki entry. summary is trusted HTML from our own wiki (same as body).
        card.innerHTML =
            `<h4 class="font-semibold text-slate-200 text-sm mb-1">${esc(entry.title)}</h4>` +
            `<p class="text-xs text-slate-400 leading-relaxed">${entry.summary}</p>`;

        card.classList.add("cursor-pointer", "transition", "duration-200", "hover:scale-[1.01]");
        card.addEventListener("click", () => {
            {
                $("wiki-title").textContent = entry.title;
                $("wiki-origin").innerHTML = "<b>Origin:</b> " + esc(entry.origin);
                $("wiki-body").innerHTML = entry.body;

                let configs = entry.config;
                // Hook dynamic configurations from the live Python backend if available!
                if (key === "strategy_profiles" && AETHER_LIVE_RULES) {
                    configs = [
                        `Defensive: Max ${AETHER_LIVE_RULES.DEFENSIVE.max_positions} positions, ${AETHER_LIVE_RULES.DEFENSIVE.max_allocation_pct * 100}% trade size, ${AETHER_LIVE_RULES.DEFENSIVE.cash_buffer_pct * 100}% cash buffer (Active when SPY L60 < -2)`,
                        `Balanced: Max ${AETHER_LIVE_RULES.BALANCED.max_positions} positions, ${AETHER_LIVE_RULES.BALANCED.max_allocation_pct * 100}% trade size, ${AETHER_LIVE_RULES.BALANCED.cash_buffer_pct * 100}% cash buffer (Active when -2 <= SPY L60 <= 2)`,
                        `Aggressive: Max ${AETHER_LIVE_RULES.AGGRESSIVE.max_positions} positions, ${AETHER_LIVE_RULES.AGGRESSIVE.max_allocation_pct * 100}% trade size, ${AETHER_LIVE_RULES.AGGRESSIVE.cash_buffer_pct * 100}% cash buffer (Active when SPY L60 > 2)`
                    ];
                } else if (key === "scarcity_core" && AETHER_LIVE_RULES) {
                    const base = AETHER_LIVE_RULES.BALANCED.scarcity_allocation_pct * 100;
                    const ceilPct = (r) => ((r.scarcity_cap_ceiling_pct ?? r.scarcity_allocation_pct) * 100);
                    configs = [
                        `Base Allocation: ${base}% of equity reserved for Scarcity plays (satellite ${100 - base}% for standard equities).`,
                        `Conviction Ramp: cap grows from the ${base}% base to a per-profile ceiling as Short10+Long60 rises — Aggressive ${ceilPct(AETHER_LIVE_RULES.AGGRESSIVE)}%, Balanced ${ceilPct(AETHER_LIVE_RULES.BALANCED)}%, Defensive ${ceilPct(AETHER_LIVE_RULES.DEFENSIVE)}% (no cliff, never fully suspended).`,
                        `Per-Position Ceiling: any single name capped at the profile's max trade size — Defensive ${AETHER_LIVE_RULES.DEFENSIVE.max_allocation_pct * 100}%, Balanced ${AETHER_LIVE_RULES.BALANCED.max_allocation_pct * 100}%, Aggressive ${AETHER_LIVE_RULES.AGGRESSIVE.max_allocation_pct * 100}%.`,
                        "Classifier: Dynamic LLM evaluation with local cache (Data/scarcity_cache.json).",
                        "Shrink-Ray Sizer: Dynamically downsizes the order quantity to fit the remaining bucket room, rather than rejecting the buy."
                    ];
                } else if (key === "flower_protection" && AETHER_LIVE_RULES) {
                    configs = [
                        `Hard Exit: Close price <= Stop-Loss floor (Enforced immediately, ${AETHER_LIVE_RULES.DEFENSIVE.atr_multiplier}x/${AETHER_LIVE_RULES.BALANCED.atr_multiplier}x/${AETHER_LIVE_RULES.AGGRESSIVE.atr_multiplier}x ATR by profile).`,
                        "Soft Exit: S10+L60 < 0 (Triggers sell unless protected).",
                        "Flower Protection: Bypasses soft exit if position is in profit AND trades above its 50 SMA (downgrades to REVIEW)."
                    ];
                }

                const configList = $("wiki-config");
                configList.innerHTML = "";
                configs.forEach((cfg) => {
                    const li = document.createElement("li");
                    li.className = "flex items-start gap-2 text-slate-300";
                    li.innerHTML = `<span class="text-purple-400 font-semibold">•</span> <span>${esc(cfg)}</span>`;
                    configList.appendChild(li);
                });

                setWikiExpanded(false);   // always open at the default width
                $("wiki-modal").classList.remove("hidden");
                document.body.style.overflow = "hidden";
            }
        });
    });
}

// Null-safe: if the wiki modal markup is ever removed again, degrade gracefully
// instead of throwing a TypeError at module load (which would abort the rest of
// this classic script — including startPolling() and initWiki() below).
const wikiModal = $("wiki-modal");
if (wikiModal) {
    const wikiCloseBtn = $("wiki-close-btn");
    if (wikiCloseBtn) wikiCloseBtn.addEventListener("click", closeWiki);
    const wikiExpandBtn = $("wiki-expand-btn");
    if (wikiExpandBtn) wikiExpandBtn.addEventListener("click", toggleWikiExpanded);
    wikiModal.addEventListener("click", (e) => {
        if (e.target === wikiModal) closeWiki();
    });
}

// Expand/restore the wiki panel between the default reading width and a wide view
// (handy for long, link-rich entries like the candlestick catalogue).
function setWikiExpanded(on) {
    const panel = $("wiki-panel");
    if (!panel) return;
    panel.classList.toggle("max-w-2xl", !on);
    panel.classList.toggle("max-w-6xl", on);
    const btn = $("wiki-expand-btn");
    if (btn) {
        btn.textContent = on ? "⤡ Restore" : "⤢ Expand";
        btn.title = on ? "Restore" : "Expand";
    }
}
function toggleWikiExpanded() {
    const panel = $("wiki-panel");
    if (panel) setWikiExpanded(!panel.classList.contains("max-w-6xl"));
}

function closeWiki() {
    const wm = $("wiki-modal");
    if (wm) wm.classList.add("hidden");
    document.body.style.overflow = "";
}
// ── Init ─────────────────────────────────────────────────────────────────────
setAdminUI(null);   // default to logged-out UI until whoami confirms
refreshAuth();

// Initialize the active tab from the URL hash if present, otherwise default to dashboard
const initialTab = window.location.hash.substring(1);
switchTab(VALID_TABS.includes(initialTab) ? initialTab : "dashboard", true);

startPolling();
initWiki();
