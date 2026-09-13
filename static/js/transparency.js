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

    // 量不到的筆數要看得見。只在後端記錄而不顯示,等於沒有記錄 ——
    // 上面那些總和會少算,而少算多少沒有人知道。
    const unmeasured = costs.unmeasured || {};
    const gaps = Object.entries(unmeasured)
        .filter(([, count]) => count)
        .map(([field, count]) => `${esc(field)} ${esc(count)} 筆`);
    const missing = gaps.length
        ? `<p class="neg">⚠️ 有欄位量不到,<b>沒有</b>計入上表的總和:
           ${gaps.join("、")}。成本拖累因此顯示為 -。</p>`
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
        </table>${legacy}${missing}`);
}

/* ---------------- 自我檢討 ---------------- */

const VERDICT_CLASS = {
    HEALTHY: "alert-ok",
    FRAGILE: "alert-warning",
    LOSING: "alert-critical",
    NOT_ENOUGH_DATA: "alert-warning",
};

async function loadSelfReview() {
    const data = await load("/api/self_review");
    if (data.error) throw new Error(data.error);

    const verdict = data.verdict || "NOT_ENOUGH_DATA";
    const head = `<div class="alert ${VERDICT_CLASS[verdict] || "alert-warning"}">
        <b>${esc(verdict)}</b> — ${esc(data.headline)}</div>`;

    const findings = (data.findings || []).length
        ? `<p><b>發現</b></p><ul>${data.findings
            .map(f => `<li>${esc(f)}</li>`).join("")}</ul>`
        : "";

    /* 「答不出來的問題」跟「發現」一樣重要:看報告的人需要知道
       哪些結論還沒有依據,否則他會把沉默當成沒問題。 */
    const questions = (data.questions_we_cannot_answer || []).length
        ? `<p><b>目前答不出來的問題</b></p><ul>${data.questions_we_cannot_answer
            .map(q => `<li class="muted">${esc(q)}</li>`).join("")}</ul>`
        : "";

    const agents = (data.agents && data.agents.agents) || [];
    const agentRows = agents.map(agent => `<tr>
        <td class="nowrap">${esc(agent.agent)}</td>
        <td class="nowrap">${esc(agent.verdict)}</td>
        <td>${num(agent.edge, 4)}</td>
        <td>${agent.edge_sigmas === null || agent.edge_sigmas === undefined
            ? "-" : num(agent.edge_sigmas, 1) + " σ"}</td>
        <td>${esc(agent.agreed_trades)} / ${esc(agent.disagreed_trades)}</td>
        <td class="muted">${esc((agent.notes || [])[0] || "")}</td>
    </tr>`).join("");

    const agentTable = agentRows
        ? `<p><b>Agent 貢獻度</b></p>
           <table class="tp"><tr><th>Agent</th><th>判定</th><th>優勢</th>
           <th>標準誤</th><th>同意/反對筆數</th><th>備註</th></tr>${agentRows}</table>`
        : "";

    render("self_review", head + findings + questions + agentTable);
}

/* ---------------- LIVE SAFETY GATE ---------------- */

async function loadLiveGate() {
    const data = await load("/api/live_gate");
    if (data.error) throw new Error(data.error);

    const head = `<div class="alert ${data.open ? "alert-critical" : "alert-ok"}">
        <b>${data.open ? "⚠️ 閘門開啟" : "🔒 閘門關閉"}</b> —
        ${data.open
            ? "所有檢查都通過了,包含實單路徑那一項 —— 代表有人逐檔讀過實單程式碼並簽了原始碼雜湊。"
            : `${esc(data.failed_count ?? 0)} 項未通過`}
    </div>`;

    const rows = (data.checks || []).map(check => `<tr>
        <td class="nowrap">${check.passed ? "✓" : (check.blocking ? "⛔" : "⚠️")}</td>
        <td class="nowrap">${esc(check.name)}</td>
        <td class="muted">${esc(check.detail)}</td>
    </tr>`).join("");

    const how = data.how_to_open
        ? `<p class="muted"><b>怎麼開:</b>${esc(data.how_to_open)}</p>` : "";

    render("live_gate", head + (rows
        ? `<table class="tp"><tr><th></th><th>檢查</th><th>說明</th></tr>${rows}</table>`
        : '<span class="muted">拿不到檢查結果。</span>') + how);
}

/* ---------------- News Center ---------------- */

const IMPACT_CLASS = {
    HIGH: "alert-critical", MEDIUM: "alert-warning", LOW: "",
};

async function loadNewsCenter() {
    const data = await load("/api/news_center?limit=10");
    if (data.error) throw new Error(data.error);

    const rows = (data.news || []).map(item => `<tr>
        <td class="nowrap ${IMPACT_CLASS[item.impact] || ""}">${esc(item.impact)}</td>
        <td class="nowrap">${esc(item.direction)}</td>
        <td>${num(item.score, 0)}</td>
        <td class="nowrap">${(item.affected_symbols || []).map(esc).join("、") || "-"}</td>
        <td>${item.url
            ? `<a href="${esc(item.url)}" target="_blank" rel="noopener">${esc(item.title)}</a>`
            : esc(item.title)}</td>
    </tr>`).join("");

    render("news_center", rows
        ? `<table class="tp"><tr><th>影響</th><th>方向</th><th>強度</th>
           <th>相關標的</th><th>標題</th></tr>${rows}</table>`
        : '<span class="muted">目前沒有新聞。</span>');
}

/* ---------------- 決策紀錄 ---------------- */

const OUTCOME_CLASS = {
    OPENED: "alert-ok",
    WAIT: "",
    REJECTED_BY_RISK: "alert-warning",
    REJECTED_BY_RULES: "alert-warning",
    BLOCKED: "alert-warning",
    FAILED: "alert-critical",
};

async function loadDecisions() {
    const data = await load("/api/decisions?limit=30");
    if (data.error) throw new Error(data.error);

    const counts = Object.entries(data.by_outcome || {})
        .map(([outcome, n]) => `${esc(outcome)} ${esc(n)}`).join("、");

    const rows = (data.decisions || []).map(d => {
        /* 拒絕的原因放在這裡最有用 —— 「為什麼沒開」是這張表存在的理由。 */
        const why = d.reason
            || (d.risk_decision && d.risk_decision.reason)
            || "";

        return `<tr>
            <td class="nowrap">${esc(d.created_at || "-")}</td>
            <td class="nowrap">${esc(d.symbol)}</td>
            <td class="nowrap">${esc(d.direction || "-")}</td>
            <td class="nowrap ${OUTCOME_CLASS[d.outcome] || ""}">${esc(d.outcome)}</td>
            <td>${d.score === null ? "-" : num(d.score, 0)}</td>
            <td>${d.confidence === null ? "-" : num(d.confidence, 0)}</td>
            <td class="nowrap">${esc(d.market_regime || "-")}</td>
            <td class="muted">${esc(why)}</td>
            <td>${d.trade_id ? `<a href="/api/why/${esc(d.trade_id)}">為什麼</a>` : ""}</td>
        </tr>`;
    }).join("");

    const summary = counts
        ? `<p class="muted">最近 ${esc(data.count)} 筆:${counts}</p>` : "";

    render("decisions", summary + (rows
        ? `<table class="tp"><tr><th>時間</th><th>標的</th><th>方向</th>
           <th>結果</th><th>分數</th><th>信心</th><th>市況</th>
           <th>原因</th><th></th></tr>${rows}</table>`
        : '<span class="muted">還沒有決策紀錄。</span>'));
}

/* ---------------- 策略健康度 ---------------- */

const STRATEGY_VERDICT_CLASS = {
    HEALTHY: "alert-ok",
    WATCH: "alert-warning",
    STRATEGY_DRIFT: "alert-warning",
    SHOULD_PAUSE: "alert-critical",
    INSUFFICIENT_DATA: "alert-warning",
};

async function loadStrategyHealth() {
    const data = await load("/api/strategy_health");
    if (data.error) throw new Error(data.error);

    render("paused_count", esc(data.paused_count ?? 0));
    render("drifted_count", esc(data.drifted_count ?? 0));

    const rows = (data.strategies || []).map(s => {
        /* 漂移的欄位放的是「幾個標準誤」,不是「差多少錢」——
           差多少錢在樣本數不同時無法互相比較。 */
        const sigma = s.drift && s.drift.sigma_gap !== null
            && s.drift.sigma_gap !== undefined
            ? num(s.drift.sigma_gap, 1) + " σ" : "-";

        return `<tr>
            <td class="nowrap">${esc(s.name)}</td>
            <td class="nowrap">${esc(s.status)}</td>
            <td class="nowrap ${STRATEGY_VERDICT_CLASS[s.verdict] || ""}">${esc(s.verdict)}</td>
            <td>${esc(s.trades)}</td>
            <td>${num(s.win_rate, 1)}%</td>
            <td>${s.profit_factor === null ? "-" : num(s.profit_factor, 2)}</td>
            <td>${num(s.max_drawdown_pct, 1)}%</td>
            <td>${sigma}</td>
            <td class="muted">${esc((s.reasons || [])[0] || "")}</td>
        </tr>`;
    }).join("");

    const table = rows
        ? `<table class="tp"><tr><th>策略</th><th>狀態</th><th>判定</th>
           <th>筆數</th><th>勝率</th><th>PF</th><th>回撤</th>
           <th>近期 vs 歷史</th><th>說明</th></tr>${rows}</table>`
        : '<span class="muted">還沒有任何策略的已平倉交易。</span>';

    const disabled = (data.disabled || []).length
        ? `<p class="muted">這一輪不參與投票:${data.disabled.map(esc).join("、")}</p>`
        : "";

    const changes = (data.recent_changes || []).length
        ? `<p><b>最近的狀態變更</b></p><ul>${data.recent_changes.slice(-5).reverse()
            .map(c => `<li class="muted">${esc(c.at)} ${esc(c.strategy)}
                ${esc(c.from || "-")} → ${esc(c.to)}:${esc(c.reason)}</li>`)
            .join("")}</ul>`
        : "";

    render("strategy_health", table + disabled + changes);
}

/* ---------------- 設定變更 ---------------- */

async function loadConfigChanges() {
    const data = await load("/api/config_changes");
    if (data.error) throw new Error(data.error);

    const records = data.records || [];

    if (!records.length) {
        render("config_changes",
            '<span class="muted">沒有記錄到任何風控參數變更。</span>');
        return;
    }

    const rows = [];
    for (const record of records.slice().reverse()) {
        for (const change of record.changes || []) {
            const risky = change.kind === "RISK_INCREASED";
            rows.push(`<tr>
                <td class="nowrap">${esc(record.at)}</td>
                <td class="nowrap">${esc(change.key)}</td>
                <td>${esc(change.old)} → ${esc(change.new)}</td>
                <td class="${risky ? "neg" : "muted"}">${
                    risky ? "風險變大" : esc(change.kind)}</td>
            </tr>`);
        }
    }

    const banner = data.risk_increase_count
        ? `<div class="alert alert-warning">風控參數被放寬過
           ${esc(data.risk_increase_count)} 次。</div>`
        : "";

    render("config_changes", banner + (rows.length
        ? `<table class="tp"><tr><th>時間</th><th>設定</th><th>變更</th>
           <th>方向</th></tr>${rows.join("")}</table>`
        : '<span class="muted">沒有記錄到任何風控參數變更。</span>'));
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
    ["live_gate", loadLiveGate],
    ["news_center", loadNewsCenter],
    ["decisions", loadDecisions],
    ["strategy_health", loadStrategyHealth],
    ["self_review", loadSelfReview],
    ["config_changes", loadConfigChanges],
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
