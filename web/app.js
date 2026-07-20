/* AETHER Dashboard — frontend logic (vanilla JS, no build step). */

const $ = (id) => document.getElementById(id);
const fmt$ = (n) => (n == null ? "—" : "$" + Number(n).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }));
const fmtPct = (n) => (n == null ? "—" : (n >= 0 ? "+" : "") + Number(n).toFixed(2) + "%");
const cls = (n) => (n > 0 ? "pos" : n < 0 ? "neg" : "mut");

// ── central aether wiki database ───────────────────────────────────────────
const AETHER_WIKI = {
    "buying_ratio": {
        title: "Marc Chaikin Buying Ratio (BR) Model",
        origin: "Marc Chaikin - Founder of Chaikin Analytics, creator of Chaikin Money Flow.",
        body: "Compiles relative volume, Money Flow, oscillators, and weekly seasonality into a unified momentum score in the range <code>[-10.0, +10.0]</code>. Sourced directly from Chaikin's PowerGauge rating parameters.",
        config: [
            "PGR Rating Weight: 1=Be- (-2) to 5=Bu+ (+2)",
            "Risk/Reward Weight: R/R < 0 (-1), >=1.0 (+1), >=3.0 (+2)",
            "LT Trend Weight: Weak/Recovery (+1), Strong/Extended (-1)",
            "Institutional Money Flow: Strong (+0.75), Weak (-0.75)",
            "Overbought/Oversold: Optimal (+1.0), Wait (-0.25)",
            "Industry Strength: Weak/Contrarian (+0.5), Strong (-0.5)"
        ]
    },
    "s10_score": {
        title: "S10 Short-Term Entry Score",
        origin: "Project AETHER Core Team (Calibration Backtest, 336k observations, 2023-2025).",
        body: "Standardizes short-term entry quality over a 10-day trading horizon. Aggregates volume spikes, momentum exhaustion oscillators, and contrarian overlays.",
        config: [
            "Relative Volume Weight: High (+2.5), Very High (+0.5), Low (-2.0)",
            "OB/OS Zone Weight: Optimal (+3.0), Early (+1.0), Wait (-2.0)",
            "Money Flow Weight: Strong (+3.0), Weak (-2.0)",
            "Contrarian Industry Strength: Weak (+2.0), Strong (-2.0)",
            "Contrarian LT Trend: Weak (+1.5), Strong (-1.5)",
            "Overlays: Seasonality (+-1.0) | Regime (+-1.0) | Fibonacci (+-1.0)"
        ]
    },
    "l60_score": {
        title: "L60 Long-Term Position Score",
        origin: "Project AETHER Core Team (Calibration Backtest, 336k observations, 2023-2025).",
        body: "Measures intermediate-to-long-term trend strength and durability over a rolling 60-day horizon, focusing on core moving average alignments and trend stability.",
        config: [
            "Contrarian LT Trend Weight: Weak (+4.0), Strong (-3.0)",
            "Relative Volume Weight: High (+2.0), Low (-1.0)",
            "Money Flow Weight: Strong (+2.5), Weak (-2.0)",
            "Contrarian Industry Strength: Weak (+2.0), Strong (-1.5)",
            "OB/OS Weight: Optimal (+1.5), Early (+0.5), Wait (-0.5)",
            "Overlays: Seasonality (+-0.5) | Regime (+-1.5) | Fibonacci (+-0.5)"
        ]
    },
    "seasonality_engine": {
        title: "Weekly Seasonality Detection Engine",
        origin: "Quantitative Research standard, analyzing 25 years of historical closes.",
        body: "Groups historical closing prices by month and week-of-month, calculating the historical 10-day forward return of that calendar week to apply weighted tailwinds or headwinds.",
        config: [
            "Strong Tailwind: +1.0 score boost",
            "Mild Tailwind: +0.5 score boost",
            "Neutral Week: 0.0 score impact",
            "Headwind: -0.5 to -1.0 score reduction",
            "Required History: Minimum 3 years of OHLCV daily data"
        ]
    },
    "fibonacci_rsi": {
        title: "Fibonacci Channels & RSI Divergences",
        origin: "J. Welles Wilder Jr. (RSI, 1978) & Classical Fibonacci Standards.",
        body: "Maps price relative to Fibonacci retracement levels (23.6%, 38.2%, 50.0%, 61.8%) computed from historical high-low channels and tracks price-RSI(14) momentum divergences to spot bottoms.",
        config: [
            "Fibonacci Channel: Based on 100-day High/Low range",
            "RSI Length: 14-period daily standard",
            "RSI Divergence: Bullish divergence (lower price low + higher RSI low) triggers +1.0 score boost."
        ]
    },
    "candlestick_engine": {
        title: "Japanese Candlestick Recognition Engine",
        origin: "Munehisa Homma (1700s, original Japanese rice-trading methodology).",
        body: "Aggregates 17 distinct candlestick patterns over a rolling 5-day lookback window. Based on traditional Japanese pattern configurations.",
        config: [
            "Patterns Scanned: Engulfing, Harami, Star, Doji, Piercing, Tasuki, Hikkake, Slingshot, Double Trouble, etc.",
            "Body Threshold: > 1% of 30-day average close to differentiate meaningful bodies from flat noise."
        ]
    },
    "chart_formations": {
        title: "Classical Chart Formations",
        origin: "Classical technical analysis standards.",
        body: "Identifies structural technical formations including Head & Shoulders (bearish), Inverse Head & Shoulders (bullish bottoming), Double Tops/Bottoms, Cup & Handle, and Bull/Bear Flags.",
        config: [
            "Inverse H&S (Bullish): Score boost of +1.5",
            "Double Bottom (Bullish): Score boost of +1.0",
            "Double Top / H&S (Bearish): Score reduction of -1.5"
        ]
    },
    "trend_crossovers": {
        title: "Trend Momentum Crossovers",
        origin: "Classical moving average crossover standards.",
        body: "Monitors moving average crossings (Golden/Death Crosses) and MACD crossings (signal line and trend crossovers) to lock in entry direction and confirm trend reversals.",
        config: [
            "Golden Cross (Bullish): 20 SMA crossing above 50 SMA.",
            "Death Cross (Bearish): 20 SMA crossing below 50 SMA.",
            "MACD Standard: 12-period EMA, 26-period EMA, and 9-period signal line."
        ]
    },
    "contrarian_calibration": {
        title: "Contrarian Calibration Override",
        origin: "AETHER Backtest Calibration Research (Phase A).",
        body: "Programmatically negates the combined pattern score (multiplying it by -1.0) because backtests prove overbought patterns act as contrarian indicators, prioritizing oversold, bottoming, and recovery plays.",
        config: [
            "Multiplier: -1.0 x raw pattern score",
            "Prioritization: Depreciates overbought momentum and prioritizes deeply oversold, coiled springs."
        ]
    },
    "strategy_profiles": {
        title: "Regime-Adaptive Strategy Profiles",
        origin: "Modern Portfolio Theory (MPT) & Adaptive Capital Sizing standards.",
        body: "Auto-scales risk parameters based on the broad market's intermediate trend score (SPY Long60): Defensive, Balanced, and Aggressive profiles.",
        config: [
            "Defensive: Max 3 positions, 10% trade size, 50% cash buffer (Active when SPY L60 < -2)",
            "Balanced: Max 5 positions, 15% trade size, 20% cash buffer (Active when -2 <= SPY L60 <= 2)",
            "Aggressive: Max 6 positions, 15% trade size, 0% cash buffer (Active when SPY L60 > 2)"
        ]
    },
    "vic_snipers": {
        title: "Trader Vic Reversal Bottom Snipers",
        origin: "Victor Sperandeo ('Trader Vic') - Principles of Professional Speculation.",
        body: "Mathematically scans trailing 80-bar price action to identify Trader Vic's legendary bottom-reconstruction setups, completely eliminating false breakout traps.",
        config: [
            "1-2-3 Reversal: Price breaks trendline, test of low establishes a HIGHER low, then price breaks above intermediate peak.",
            "2B Pattern (Bear Trap): Price breaks below a major previous low, then immediately closes back ABOVE the low (reclaims support) in 1-6 bars.",
            "Score Boost: +2.0 to overall Pattern Score"
        ]
    },
    "breadth_filter": {
        title: "S&P Market Breadth Filter",
        origin: "John Bollinger (Volatility & Index Breadth) & S&P Equal-Weight Delta standards.",
        body: "Monitors the score gap between Cap-Weighted SPY and Equal-Weighted RSP. If SPY is rising but RSP is weak, it flags a technology-concentrated, narrow market top and automatically downgrades the active risk profile.",
        config: [
            "Breadth Gap: SPY Long60 - RSP Long60",
            "Trigger Delta: > 4.0",
            "Action: Automatically downgrades the active strategy profile by 1 level (e.g. BALANCED -> DEFENSIVE) to preserve cash cushion."
        ]
    },
    "bubble_guard": {
        title: "2.5-Sigma Bubble Guard",
        origin: "Standard Deviation Volatility Band standards.",
        body: "Calculates standard deviation distance from the 500-day moving average. If a stock exceeds 2.5 standard deviations above its 500 SMA, it is blacklisted from new purchases, avoiding overextended parabolic peaks.",
        config: [
            "Length: 500-day Simple Moving Average",
            "Z-Score Threshold: > 2.5 (Super-Bubble Zone)",
            "Action: Automatic buy blacklist"
        ]
    },
    "scarcity_core": {
        title: "Dynamic Structural Scarcity Core (80/20)",
        origin: "Jeremy Grantham - Founder of Grantham, Mayo, & van Otterloo (GMO), 'Hard Asset Secular Supercycle'.",
        body: "Enforces a strict 20% portfolio allocation cap reserved strictly for metals, agriculture, and grid utility assets to insulate cash from tech-led market downturns. Employs a dynamic LLM-powered asset classifier.",
        config: [
            "Core Allocation: 20% of total portfolio equity strictly reserved for Scarcity plays.",
            "Satellite Allocation: 80% for standard equities (Tech, Consumer, Energy).",
            "Classifier: Dynamic LLM evaluation with local cache (Data/scarcity_cache.json).",
            "Shrink-Ray Sizer: Dynamically downsizes the order quantity to fit exactly under the remaining room in the 20% bucket, rather than rejecting the buy."
        ]
    },
    "flower_protection": {
        title: "Unified Exit Policy & Flower Protection",
        origin: "Peter Lynch - Famous manager of Fidelity Magellan Fund (14-year +29% CAGR), 'Weed Cutting'.",
        body: "Enforces Peter Lynch's core philosophy of 'watering your flowers and cutting your weeds'. Hard ATR stop-loss floors always override soft exit signals. Soft exits on profitable, above-50-DMA positions are downgraded to REVIEW to let winners run.",
        config: [
            "Hard Exit: Close price <= Stop-Loss floor (Enforced immediately, 1.5x/2.5x/3.5x ATR by profile).",
            "Soft Exit: S10+L60 < 0 (Triggers sell unless protected).",
            "Flower Protection: Bypasses soft exit if position is in profit AND trades above its 50 SMA (downgrades to REVIEW)."
        ]
    },
    "gap_guard": {
        title: "Catastrophic Gap Guard (CNXC Trap)",
        origin: "Project AETHER Loss Minimization Heuristic.",
        body: "Instantly rejects any buy order if today's price is more than 8% below yesterday's workbook close, protecting capital from waterfall crashes on earnings panics.",
        config: [
            "Trigger: Today's Price <= 0.92 x Yesterday's Close",
            "Action: Instant buy order rejection",
            "Bypass: If Trader Vic bottom sniper confirms a volume-backed, capitulation exhaustion gap."
        ]
    },
    "antifragile_gate": {
        title: "Antifragile Feedback Analyzer & Failure DNA Loop",
        origin: "Nassim Nicholas Taleb - Author of 'Fooled by Randomness', 'The Black Swan', 'Antifragile'.",
        body: "Employs zero-trust risk safety principles. Operates a closed-loop retrospective feedback analyzer that dynamically clusters true losses (bad habits) into active rejections to immunize the portfolio against repeating errors.",
        config: [
            "DNA Capture: Freezes PGR, scores, and Z-scores on purchase, logging completed trades to Data/trade_history_dna.json.",
            "Retrospective Loop: retrospective_analyzer.py runs every Saturday, automatically filtering out market-panic days and statistically clustering failures into failure_dna_rules.json.",
            "Action: Dynamic buy-rejection guard (check_failure_rules) blocks any new buy candidate matching our toxic clusters on autopilot."
        ]
    },
    "decision_eval": {
        title: "Systematic Sell & Decision Quality Evaluation",
        origin: "Decision Quality Theory & Backtracking Analytics standards.",
        body: "Evaluates exits with a hybrid qualitative rubric combining fundamental risks with technical charts. Automatically logs, scores, and audits decision quality for both closed trades and active reviews.",
        config: [
            "Logging: Data/decision_log.jsonl updated on every run.",
            "Scorecard: Runs decision_eval.py scoring all mature decisions after a 10-day forward window.",
            "Verification: Automatically emails the Retrospective Scorecard directly to your inbox daily."
        ]
    }
};

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
                <td class="font-semibold cursor-pointer hover:text-blue-400" data-open="${p.symbol}">${p.symbol}</td>
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
const ACCTS_TEXT_COLS = ["symbol", "status"];

function accountsSortValue(h, key) {
    if (key === "symbol") return h.symbol || "";
    if (key === "status") return h.status || "";
    if (key === "buy") return h.buy == null ? -999999 : Number(h.buy);
    if (key === "current") return h.current == null ? -999999 : Number(h.current);
    if (key === "stop") return h.stop == null ? -999999 : Number(h.stop);
    if (key === "target") return h.target == null ? -999999 : Number(h.target);
    return h[key] == null ? -999999 : Number(h[key]);
}

async function loadAccounts() {
    const data = await api("/api/accounts");
    const box = $("accounts-container");
    const accts = data.accounts || [];
    acctHoldings = [];

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
            const scoreCells = isGame ? "" :
                `<td class="${cls(s10)}">${s10 == null ? "—" : s10.toFixed(1)}</td>
                 <td class="${cls(l60)}">${l60 == null ? "—" : l60.toFixed(1)}</td>
                 <td class="font-bold ${cls(total)}">${total == null ? "—" : total.toFixed(1)}</td>
                 <td class="text-xs">${h.status || ""}</td>`;
            const stopTitle = h.stop_source ? `stop source: ${h.stop_source}${h.buy_date ? " (as of " + h.buy_date + ")" : ""}` : "";
            const tgtTitle = h.target_source ? `target source: ${h.target_source}` : "";
            return `<tr data-acct="${a.id}" data-sym="${sym}" data-buy="${entry ?? ""}" data-qty="${h.qty ?? ""}">
                <td class="font-semibold cursor-pointer hover:text-blue-400" data-open="${sym}">${sym}${instrumentBadge(h.instrument)}</td>
                <td>${h.qty ?? "—"}</td>
                <td>${fmt$(entry)}</td>
                <td class="px-live">${fmt$(cur)}</td>
                <td class="pnl-$ ${cls(h.pnl)}">${fmt$(h.pnl)}</td>
                <td class="pnl-pct ${cls(h.pnl_pct)}">${fmtPct(h.pnl_pct)}</td>
                <td class="${weakStop(h, h.stop_source) ? "text-amber-400" : ""}" title="${stopTitle}">${fmt$(h.stop)}</td>
                ${isGame ? `<td>${h.days_held ?? "—"} d</td>` : `<td class="${weakStop(h, h.target_source) ? "text-amber-400" : ""}" title="${tgtTitle}">${fmt$(h.target)}</td>`}
                ${scoreCells}
            </tr>`;
        }).join("");

        const scoreHdr = isGame
            ? `<th data-sort="stop" class="cursor-pointer hover:text-blue-400">Stop</th><th data-sort="days_held" class="cursor-pointer hover:text-blue-400">Days</th>`
            : `<th data-sort="stop" class="cursor-pointer hover:text-blue-400">Stop</th>
               <th data-sort="target" class="cursor-pointer hover:text-blue-400">Target</th>
               <th data-sort="s10" class="cursor-pointer hover:text-blue-400">S10</th>
               <th data-sort="l60" class="cursor-pointer hover:text-blue-400">L60</th>
               <th data-sort="total" class="cursor-pointer hover:text-blue-400">Score</th>
               <th data-sort="status" class="cursor-pointer hover:text-blue-400">Status</th>`;

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
                const cls = _logLevelClass(lvl);
                const ts = (e.ts || "").substring(11, 19);  // HH:MM:SS
                const mod = (e.module || "").replace("aether.", "");
                const extra = e.extra ? " " + JSON.stringify(e.extra) : "";
                const exc = e.exc ? `\n  ${e.exc.split("\n").slice(-2).join(" ")}` : "";
                return `<span class="${cls}">[${ts}] ${lvl.padEnd(7)} ${mod}: ${e.msg}${extra}${exc}</span>`;
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
    const inputValue = inputEl ? inputEl.value.trim().toUpperCase() : "";
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
        "AnalyzeFinData_Morning":    "pipeline",
        "AnalyzeFinData_AI_Game":    "ai_game_run",
        "AnalyzeFinData_AI_Summary": "ai_game_summary",
        "AnalyzeFinData_Evening":    null,
        "Project_AETHER_Watchdog":   "watchdog",
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
        return `<tr>
            <td class="font-semibold">${t.name}</td>
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
    $("misses-body").innerHTML = misses.length ? misses.map((m) => `
        <tr>
            <td class="font-semibold cursor-pointer hover:text-blue-400" data-open="${m.symbol}">${m.symbol}</td>
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
            <td class="font-semibold cursor-pointer hover:text-blue-400" data-open="${r.symbol}">${r.symbol}${instrumentBadge(r.instrument)}</td>
            <td><div class="truncate text-xs ${industryColor(r.industry_strength)}" style="max-width:70px"
                     title="${(r.industry || "")}${r.industry_strength ? " — " + r.industry_strength : ""}">${r.industry || "—"}</div></td>
            <td class="text-xs whitespace-nowrap">${pgrCell(r.prev_pgr, r.pgr)}</td>
            <td class="text-right">${r.price == null ? "—" : fmt$(r.price)}</td>
            <td class="text-right ${weakStop(r, r.stop_source) ? "text-amber-400" : ""}" title="stop source: ${r.stop_source || "?"}">${!r.stop ? "—" : fmt$(r.stop)}</td>
            <td class="text-right ${weakStop(r, r.target_source) ? "text-amber-400" : ""}" title="target source: ${r.target_source || "?"}">${!r.target ? "—" : fmt$(r.target)}</td>
            <td class="text-right">${num(r.risk_ratio, 2)}</td>
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
            <td>${renderPatternsHTML(r.patterns)}</td>
        </tr>`).join("")
        : `<tr><td colspan="18" class="text-center text-slate-500 py-6">No matching symbols.</td></tr>`;
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
    else if (tab === "about") {} // No API data to load for static about tab
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
let _symChart = null;

function _set(id, html, klass) {
    const el = $(id); if (!el) return;
    el.innerHTML = html;
    if (klass !== undefined) el.className = klass;
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

    // Price chart
    const chartWrap = $("sm-chart-wrap");
    const chartData = d.chart || [];
    if (chartData.length > 0) {
        chartWrap.classList.remove("hidden");
        const labels = chartData.map(p => p.date.slice(5));   // MM-DD
        const closes = chartData.map(p => p.close);
        const first = closes[0], last = closes[closes.length - 1];
        const up = last >= first;
        const ctx = $("sm-chart").getContext("2d");
        if (_symChart) { _symChart.destroy(); _symChart = null; }
        _symChart = new Chart(ctx, {
            type: "line",
            data: {
                labels,
                datasets: [{
                    data: closes,
                    borderColor: up ? "#4ade80" : "#f87171",
                    backgroundColor: up ? "rgba(74,222,128,0.07)" : "rgba(248,113,113,0.07)",
                    fill: true, tension: 0.3, pointRadius: 0, borderWidth: 1.5,
                }],
            },
            options: {
                plugins: { legend: { display: false }, tooltip: { mode: "index", intersect: false } },
                scales: {
                    x: { ticks: { color: "#64748b", maxTicksLimit: 8 }, grid: { display: false } },
                    y: { ticks: { color: "#64748b" }, grid: { color: "rgba(51,65,85,0.3)" } },
                },
                animation: false,
            },
        });
    } else {
        chartWrap.classList.add("hidden");
    }

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
    if (_symChart) { _symChart.destroy(); _symChart = null; }
}

$("sm-close").addEventListener("click", closeSymbolModal);
$("sym-modal").addEventListener("click", (e) => { if (e.target === $("sym-modal")) closeSymbolModal(); });
document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeSymbolModal(); });

// Wire every rendered data-sym row to open the symbol modal when the symbol
// name cell is clicked. Uses event delegation — works for any table re-rendered
// after page load. The symbol cell must carry data-open="sym" (added in each
// row template) so we don't accidentally open on price/P&L cell clicks.
document.addEventListener("click", (e) => {
    const cell = e.target.closest("[data-open]");
    if (cell) { openSymbol(cell.dataset.open); return; }
});

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

function initWiki() {
    fetchLiveRules(); // Fetch on startup
    document.querySelectorAll("[data-wiki]").forEach((card) => {
        card.classList.add("cursor-pointer", "transition", "duration-200", "hover:scale-[1.01]");
        card.addEventListener("click", () => {
            const key = card.getAttribute("data-wiki");
            const entry = AETHER_WIKI[key];
            if (entry) {
                $("wiki-title").textContent = entry.title;
                $("wiki-origin").innerHTML = "<b>Origin:</b> " + entry.origin;
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
                    configs = [
                        `Core Allocation: ${AETHER_LIVE_RULES.BALANCED.scarcity_allocation_pct * 100}% of total portfolio equity strictly reserved for Scarcity plays.`,
                        `Satellite Allocation: ${(1.0 - AETHER_LIVE_RULES.BALANCED.scarcity_allocation_pct) * 100}% for standard equities (Tech, Consumer, Energy).`,
                        "Classifier: Dynamic LLM evaluation with local cache (Data/scarcity_cache.json).",
                        "Shrink-Ray Sizer: Dynamically downsizes the order quantity to fit exactly under the remaining room in the scarcity bucket, rather than rejecting the buy."
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
                    li.innerHTML = `<span class="text-purple-400 font-semibold">•</span> <span>${cfg}</span>`;
                    configList.appendChild(li);
                });

                $("wiki-modal").classList.remove("hidden");
                document.body.style.overflow = "hidden";
            }
        });
    });
}

$("wiki-close-btn").addEventListener("click", closeWiki);
$("wiki-modal").addEventListener("click", (e) => {
    if (e.target === $("wiki-modal")) closeWiki();
});

function closeWiki() {
    $("wiki-modal").classList.add("hidden");
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
