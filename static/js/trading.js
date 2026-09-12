/*
 * 交易面板(第八十七 / 六十三 / 六十一節)。
 *
 * 兩個原則貫穿整支檔案:
 *
 *   1. **拿不到的數字顯示「—」,不顯示 0。**
 *      0 在儀表板上讀起來像一個算出來的結論。
 *      「風險 0 USDT」與「算不出風險」在畫面上必須不一樣。
 *
 *   2. **被擋下來的東西不藏。**
 *      只顯示可以開的單,會讓連續三天被風控擋住看起來像沒有機會。
 */

function esc(value) {
    return String(value === null || value === undefined ? "" : value)
        .replace(/&/g, "&amp;").replace(/</g, "&lt;")
        .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

/* 有值就格式化,沒值就回 null —— 由呼叫端決定「沒有」長什麼樣子。 */
function fmt(value, digits = 2) {
    if (value === null || value === undefined) return null;
    const n = Number(value);
    return Number.isNaN(n) ? null : n.toFixed(digits);
}

function render(id, html) {
    const element = document.getElementById(id);
    if (element) element.innerHTML = html;
}

function set(id, value, suffix = "") {
    const element = document.getElementById(id);
    if (!element) return;

    if (value === null || value === undefined) {
        element.textContent = "—";
        element.classList.add("none");
        return;
    }
    element.textContent = `${value}${suffix}`;
    element.classList.remove("none");
}

async function getJSON(url) {
    const response = await fetch(url, { credentials: "same-origin" });
    if (!response.ok) throw new Error(`${url} 回應 ${response.status}`);
    return response.json();
}

/* ---------------- 第八十七節:開倉預覽 ---------------- */

const PLAN_CLASS = {
    APPROVED: "plan-approved",
    REJECTED_BY_RISK: "plan-blocked",
    NO_INTENT: "plan-none",
    ERROR: "plan-error",
};

const STAGE_TEXT = {
    APPROVED: "風控放行(這是預覽,單沒有送出)",
    REJECTED_BY_RISK: "Agent 有意圖,風控擋下",
    NO_INTENT: "Agent 沒有共識",
    ERROR: "計算失敗",
};

function field(key, value, extraClass = "") {
    const missing = value === null || value === undefined || value === "";
    return `<div class="plan-field">
        <span class="k">${esc(key)}</span>
        <span class="v ${missing ? "none" : extraClass}">${
            missing ? "—" : esc(value)}</span>
    </div>`;
}

function planCard(plan) {
    const cls = PLAN_CLASS[plan.stage] || "plan-none";
    const direction = plan.direction || null;

    const fields = [
        field("Symbol", plan.symbol),
        field("Market", plan.market),
        field("Direction", direction, direction ? `dir-${direction}` : ""),
        field("Entry", fmt(plan.entry, 6)),
        field("SL", fmt(plan.stop_loss, 6)),
        field("TP", fmt(plan.take_profit, 6)),
        field("Leverage", plan.leverage === null || plan.leverage === undefined
            ? null : `${plan.leverage}x`),
        field("Position Size", fmt(plan.position_size_usdt, 2)),
        field("Notional", fmt(plan.notional_usdt, 2)),
        field("Risk (USDT)", fmt(plan.risk_usdt, 2)),
        field("Risk (% 權益)", fmt(plan.risk_pct_of_equity, 3)),
        field("R:R", fmt(plan.risk_reward, 2)),
        field("Confidence", fmt(plan.confidence, 1)),
        /* Score 這一格在 Agent 管線上永遠是空的 —— 那條路徑不產生分數。
           留著這一格而不是拿掉,是為了讓「第八十七節列了但我們沒有」
           這件事在畫面上看得見。 */
        field("Score", fmt(plan.score, 1)),
    ].join("");

    const blockers = (plan.blockers || []).length
        ? `<div class="reason">擋下的原因:${
            (plan.blockers || []).map(esc).join("、")}</div>`
        : "";

    const warnings = (plan.warnings || []).length
        ? `<div class="muted">注意:${(plan.warnings || []).map(esc).join("；")}</div>`
        : "";

    return `<div class="plan ${cls}">
        <h3>${esc(plan.symbol)} ${direction ? esc(direction) : ""}</h3>
        <div class="stage">${esc(STAGE_TEXT[plan.stage] || plan.stage)}
            · 計算於 ${esc(plan.as_of)}</div>
        ${plan.reason ? `<div class="reason">${esc(plan.reason)}</div>` : ""}
        ${blockers}
        <div class="plan-fields">${fields}</div>
        ${warnings}
    </div>`;
}

async function loadPlans() {
    try {
        const data = await getJSON("/api/trade_plans?limit=3");
        const plans = data.plans || [];

        render("plans", plans.length
            ? plans.map(planCard).join("")
            : '<span class="muted">監控清單是空的。</span>');

        fillSymbols(plans.map(p => p.symbol));
    } catch (error) {
        render("plans", `<span class="neg">載入失敗:${esc(error.message)}</span>`);
    }
}

/* ---------------- 第六十三節:決策鏈 ---------------- */

function flowStage(stage) {
    return `<div class="flow-stage flow-${esc(stage.status)}">
        <span class="flow-badge">${esc(stage.status)}</span>
        <div class="label">${esc(stage.label)}</div>
        ${stage.detail ? `<div class="detail">${esc(stage.detail)}</div>` : ""}
        ${(stage.blockers || []).length
            ? `<div class="detail">${(stage.blockers).map(esc).join("、")}</div>` : ""}
        ${(stage.errored || []).length
            ? `<div class="detail">失敗的 Agent:${
                (stage.errored).map(esc).join("、")}</div>` : ""}
        ${(stage.health_warnings || []).length
            ? `<div class="detail">${
                (stage.health_warnings).map(esc).join("；")}</div>` : ""}
    </div>`;
}

async function loadFlow(symbol) {
    if (!symbol) return;

    render("flow", '<span class="muted">計算中…</span>');

    try {
        const data = await getJSON(`/api/agent_flow?symbol=${encodeURIComponent(symbol)}`);
        const stages = data.stages || [];

        render("flow", `<div class="flow">${
            stages.map(flowStage).join('<div class="flow-arrow">↓</div>')
        }</div><p class="muted">計算於 ${esc(data.as_of)}</p>`);
    } catch (error) {
        render("flow", `<span class="neg">載入失敗:${esc(error.message)}</span>`);
    }
}

function fillSymbols(symbols) {
    const select = document.getElementById("flow_symbol");
    if (!select || select.options.length) return;

    select.innerHTML = symbols.map(s =>
        `<option value="${esc(s)}">${esc(s)}</option>`).join("");
}

/* ---------------- 第六十一節:績效 ---------------- */

function loadAccount(account) {
    set("acc_equity", fmt(account.equity), " USDT");
    set("acc_available", fmt(account.available_balance), " USDT");
    set("acc_margin", fmt(account.margin_used), " USDT");
    set("acc_upnl", fmt(account.unrealized_pnl), " USDT");
    set("acc_r24", fmt(account.realized_pnl_24h), " USDT");
    set("acc_r7", fmt(account.realized_pnl_7d), " USDT");
}

function loadTrading(trading) {
    set("tr_win_rate", fmt(trading.win_rate), "%");
    /* PF 沒有虧損單時是 null,不是無限大。顯示「—」而不是「∞」。 */
    set("tr_pf", fmt(trading.profit_factor, 3));
    set("tr_expectancy", fmt(trading.expectancy_usdt, 4));
    set("tr_trades", trading.trades);
    set("tr_wins", trading.wins);
    set("tr_losses", trading.losses);
    set("tr_avg_win", fmt(trading.avg_win), " USDT");
    set("tr_avg_loss", fmt(trading.avg_loss), " USDT");

    /* Sharpe / Sortino 樣本不足時是 null。三筆算出來的 Sharpe 是一個
       數字但不是一個結論 —— 顯示「—」比顯示 2.4 誠實。 */
    set("tr_sharpe", fmt(trading.sharpe, 3));
    set("tr_sortino", fmt(trading.sortino, 3));
    set("tr_hold_avg", fmt(trading.avg_holding_hours, 1), " 小時");
    set("tr_hold_median", fmt(trading.median_holding_hours, 1), " 小時");

    set("tr_mfe", fmt(trading.avg_mfe_pct), "%");
    set("tr_mae", fmt(trading.avg_mae_pct), "%");
    set("tr_worst_mae", fmt(trading.worst_mae_pct), "%");
    /* 十筆裡只有兩筆有 MFE 的時候,那個平均值不代表這個系統。 */
    set("tr_measured", trading.measured === undefined ? null
        : `${trading.measured} / ${trading.total}`);

    render("tr_note", trading.reliable
        ? `模式 ${esc(trading.mode || "?")}。樣本足夠(至少 ${
            esc(trading.min_sample)} 筆)。`
        : `<span class="unreliable">模式 ${esc(trading.mode || "?")}。
           <b>樣本不足(少於 ${esc(trading.min_sample)} 筆),
           這幾排的數字不足以下結論。</b></span>`);
}

function loadRisk(risk) {
    set("rk_dd", fmt(risk.max_drawdown_pct), "%");
    set("rk_daily", fmt(risk.daily_loss_usdt), " USDT");
    set("rk_weekly", fmt(risk.weekly_loss_usdt), " USDT");
    set("rk_exposure", fmt(risk.exposure_pct), "%");
    set("rk_leverage", risk.max_leverage === null || risk.max_leverage === undefined
        ? null : `${risk.max_leverage}x`);
    set("rk_streak", risk.consecutive_losses);

    const blockers = risk.blockers || [];
    render("rk_blockers", blockers.length
        ? `<span class="unreliable">現在開不了新倉:${
            blockers.map(esc).join("、")}</span>`
        : "帳戶層閘門通過。");
}

function loadStrategy(strategy) {
    const best = strategy.best;
    const worst = strategy.worst;

    set("st_best", best ? `${best.key}(期望值 ${fmt(best.expectancy, 4)})` : null);
    set("st_worst", worst ? `${worst.key}(期望值 ${fmt(worst.expectancy, 4)})` : null);

    const rows = (strategy.strategies || []).map(s => `<tr>
        <td class="nowrap">${esc(s.key)}</td>
        <td>${esc(s.trades)}</td>
        <td>${fmt(s.win_rate) ?? "—"}%</td>
        <td>${fmt(s.profit_factor, 3) ?? "—"}</td>
        <td>${fmt(s.expectancy, 4) ?? "—"}</td>
        <td>${fmt(s.net_pnl, 2) ?? "—"}</td>
        <td class="${s.reliable ? "" : "unreliable"}">${
            s.reliable ? "足夠" : "不足"}</td>
    </tr>`).join("");

    render("st_table", rows
        ? `<table class="tp"><tr><th>策略</th><th>筆數</th><th>勝率</th>
           <th>PF</th><th>期望值</th><th>淨損益</th><th>樣本</th></tr>${rows}</table>`
        : '<span class="muted">還沒有帶策略名的已平倉交易。</span>');

    const notes = [];
    if ((strategy.insufficient_sample || []).length) {
        notes.push(`樣本不足(少於 ${esc(strategy.min_sample)} 筆)因此不參與
            最佳 / 最差排序:${strategy.insufficient_sample.map(esc).join("、")}`);
    }
    if (strategy.unclassified) {
        /* 沒有策略名的舊交易被排除了。不講的話,加總對不起來
           會讓人以為統計算錯。 */
        notes.push(`另有 ${esc(strategy.unclassified)} 筆沒有記錄策略名,
            已排除在分群統計外。`);
    }
    render("st_note", notes.join("<br>"));
}

async function loadPerformance() {
    try {
        const data = await getJSON("/api/performance_dashboard");
        loadAccount(data.account || {});
        loadTrading(data.trading || {});
        loadRisk(data.risk || {});
        loadStrategy(data.strategy || {});
    } catch (error) {
        render("st_note", `<span class="neg">績效載入失敗:${esc(error.message)}</span>`);
    }
}

/* ---------------- 啟動 ---------------- */

document.getElementById("flow_run").addEventListener("click", () => {
    loadFlow(document.getElementById("flow_symbol").value);
});

document.getElementById("flow_symbol").addEventListener("change", event => {
    loadFlow(event.target.value);
});

loadPlans();
loadPerformance();

/* 開倉預覽每分鐘重算 —— 價格會動,而一組過期的 Entry / SL
   比不顯示更危險:有人會照著它手動下單。
   決策鏈不自動重算(它要跑一整輪 Agent),由使用者按按鈕。 */
setInterval(loadPlans, 60000);
setInterval(loadPerformance, 60000);
