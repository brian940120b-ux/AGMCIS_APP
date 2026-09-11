/*
 * 透明度面板。
 *
 * 每一塊各自載入,一塊失敗不會讓整頁空白 —— 但失敗會顯示出來,
 * 不會安靜地留著「載入中…」讓人以為還在跑。
 */
"use strict";

const VOTE_CLASS = {
    "做多": "vote-long",
    "做空": "vote-short",
    "觀望": "vote-wait",
    "棄權": "vote-abstain",
};

function esc(value) {
    if (value === null || value === undefined) return "-";
    return String(value)
        .replace(/&/g, "&amp;").replace(/</g, "&lt;")
        .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

function num(value, digits = 2) {
    if (value === null || value === undefined || value === "") return "-";
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed.toFixed(digits) : esc(value);
}

async function load(path) {
    const response = await fetch(path, { credentials: "same-origin" });
    if (!response.ok) {
        throw new Error(`${path} 回應 ${response.status}`);
    }
    return response.json();
}

function render(id, html) {
    const element = document.getElementById(id);
    if (element) element.innerHTML = html;
}

function fail(id, error) {
    render(id, `<span class="neg">載入失敗:${esc(error.message || error)}</span>`);
}

/* ---------------- 頂端狀態列 ---------------- */

async function loadAlerts() {
    const data = await load("/api/transparency_summary");
    const alerts = data.alerts || [];

    if (!alerts.length) {
        render("alerts",
            '<div class="alert alert-ok">沒有需要注意的事項。' +
            '(這本身就是有意義的資訊 —— 不是「還沒檢查」。)</div>');
        return;
    }

    render("alerts", alerts.map(alert =>
        `<div class="alert alert-${esc(alert.level)}">${esc(alert.message)}</div>`
    ).join(""));
}

/* ---------------- Agent 投票 ---------------- */

function voteBadges(votes) {
    return Object.entries(votes || {}).map(([agent, vote]) =>
        `<span class="vote ${VOTE_CLASS[vote] || "vote-abstain"}">` +
        `${esc(agent)}:${esc(vote)}</span>`
    ).join("");
}

async function loadAgentVotes() {
    const data = await load("/api/agent_votes");
    if (data.error) throw new Error(data.error);

    const rows = (data.symbols || []).map(row => {
        const verdict = row.has_intent
            ? `<b class="pos">${esc(row.direction)} (${num(row.confidence)})</b>`
            : `<span class="muted">${esc(row.blocked_reason || row.direction)}</span>`;

        const errors = (row.errors || []).length
            ? `<div class="neg">Agent 錯誤:${esc(row.errors.join("; "))}</div>`
            : "";

        return `<tr>
            <td class="nowrap">${esc(row.symbol)}</td>
            <td>${verdict}${errors}</td>
            <td>${voteBadges(row.votes)}</td>
        </tr>`;
    }).join("");

    render("agent_votes", rows
        ? `<table class="tp"><tr><th>標的</th><th>結論</th><th>各 Agent 投票</th></tr>${rows}</table>`
        : '<span class="muted">沒有資料。</span>');
}

/* ---------------- 訂單 ---------------- */

function orderRows(orders) {
    return (orders || []).map(order => {
        const flags = [];
        if (order.needs_reconciliation) flags.push('<span class="neg">需對帳</span>');
        if (order.is_naked) flags.push('<span class="neg">裸倉</span>');

        return `<tr>
            <td class="nowrap">${esc(order.symbol)}</td>
            <td class="nowrap">${esc(order.side)}</td>
            <td class="nowrap">${esc(order.state)}</td>
            <td>${num(order.filled_quantity, 6)} / ${num(order.quantity, 6)}</td>
            <td>${num(order.average_fill_price, 6)}</td>
            <td>${flags.join(" ") || "-"}</td>
            <td>${esc(order.reject_reason || "-")}</td>
        </tr>`;
    }).join("");
}

async function loadOrders() {
    const data = await load("/api/orders");
    if (data.error) throw new Error(data.error);

    render("unresolved_count", esc(data.unresolved_count ?? 0));
    render("open_orders_count", esc(data.open_count ?? 0));

    const naked = data.naked_count ?? 0;
    render("naked_count",
        naked ? `<span class="neg">${esc(naked)}</span>` : "0");

    const all = [...(data.unresolved || []), ...(data.open || [])];
    const rows = orderRows(all);

    render("orders", rows
        ? `<table class="tp"><tr><th>標的</th><th>方向</th><th>狀態</th>
           <th>成交/總量</th><th>成交均價</th><th>標記</th><th>原因</th></tr>${rows}</table>`
        : '<span class="muted">目前沒有未結束的訂單。</span>');
}

/* ---------------- 對帳 ---------------- */

async function loadReconciliation() {
    const data = await load("/api/reconciliation");
    if (data.error) throw new Error(data.error);

    const errors = (data.errors || []).length
        ? `<div class="neg">對帳過程出錯:${esc(data.errors.join("; "))}</div>`
        : "";

    const discrepancies = data.discrepancies || [];

    if (!discrepancies.length) {
        render("reconciliation", errors +
            `<span class="muted">檢查了 ${esc(data.checked_orders ?? 0)} 張單、` +
            `${esc(data.checked_positions ?? 0)} 個部位,沒有差異。</span>`);
        return;
    }

    const rows = discrepancies.map(item => `<tr>
        <td class="nowrap">${item.critical ? '<span class="neg">●</span> ' : ""}${esc(item.kind)}</td>
        <td class="nowrap">${esc(item.symbol || "-")}</td>
        <td>${esc(item.detail)}</td>
    </tr>`).join("");

    render("reconciliation", errors +
        `<table class="tp"><tr><th>類型</th><th>標的</th><th>說明</th></tr>${rows}</table>`);
}

/* ---------------- 成本 ---------------- */

async function loadCosts() {
    const data = await load("/api/costs");
    if (data.error) throw new Error(data.error);

    const costs = data.costs || {};
    const model = data.model || {};

    const legacy = costs.legacy_trades_without_costs
        ? `<p class="muted">另有 ${esc(costs.legacy_trades_without_costs)} 筆
           Phase 10 之前的交易不含成本,未計入上表。</p>`
        : "";

    render("costs", `
        <table class="tp">
          <tr><th>含成本交易筆數</th><td>${esc(costs.trades_with_costs ?? 0)}</td></tr>
          <tr><th>毛損益</th><td>${num(costs.gross_pnl)} USDT</td></tr>
          <tr><th>淨損益</th><td>${num(costs.net_pnl)} USDT</td></tr>
          <tr><th>手續費合計</th><td>${num(costs.total_fees, 4)} USDT</td></tr>
          <tr><th>資金費用合計</th><td>${num(costs.total_funding, 4)} USDT</td></tr>
          <tr><th>成本拖累</th><td>${num(costs.cost_drag, 4)} USDT</td></tr>
          <tr><th>強制平倉次數</th><td>${esc(costs.liquidations ?? 0)}</td></tr>
          <tr><th>單趟往返成本</th><td>${num(model.round_trip_cost_pct * 100, 4)}%</td></tr>
        </table>${legacy}`);
}

/* ---------------- 校準 ---------------- */

async function loadCalibration() {
    const data = await load("/api/calibration");
    if (data.error) throw new Error(data.error);

    const warnings = (data.warnings || []).map(w =>
        `<div class="alert alert-warning">${esc(w)}</div>`).join("");

    const symbols = data.symbols || {};
    const rows = Object.entries(symbols).map(([symbol, fields]) => {
        const cells = Object.entries(fields).map(([name, info]) => {
            const fromExchange = info.source === "EXCHANGE";
            return `<div class="${fromExchange ? "tag-exchange" : "tag-guess"}">
                ${fromExchange ? "✓" : "?"} ${esc(name)} = ${esc(info.value)}</div>`;
        }).join("");
        return `<tr><td class="nowrap">${esc(symbol)}</td><td>${cells}</td></tr>`;
    }).join("");

    render("calibration", warnings + (rows
        ? `<table class="tp"><tr><th>標的</th><th>數值與來源</th></tr>${rows}</table>`
        : '<span class="muted">沒有校準資料。</span>'));
}

/* ---------------- 啟動 ---------------- */

const SECTIONS = [
    ["alerts", loadAlerts],
    ["agent_votes", loadAgentVotes],
    ["orders", loadOrders],
    ["reconciliation", loadReconciliation],
    ["costs", loadCosts],
    ["calibration", loadCalibration],
];

async function refresh() {
    await Promise.all(SECTIONS.map(async ([id, loader]) => {
        try {
            await loader();
        } catch (error) {
            fail(id, error);
        }
    }));
}

refresh();
setInterval(refresh, 60000);
