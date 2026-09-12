/*
 * 第一百零五節的首頁。
 *
 * 一個原則:**這一頁不隱藏壞消息。** 沒有機會就說沒有機會、
 * Agent 棄權就說它棄權、系統降級就把降級的元件列出來。
 * 一個永遠看起來很好的儀表板,在真的出事的時候看起來也一樣好。
 */

function esc(value) {
    return String(value === null || value === undefined ? "" : value)
        .replace(/&/g, "&amp;").replace(/</g, "&lt;")
        .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

function num(value, digits = 2) {
    return (value === null || value === undefined || Number.isNaN(Number(value)))
        ? "-" : Number(value).toFixed(digits);
}

function render(id, html) {
    const element = document.getElementById(id);
    if (element) element.innerHTML = html;
}

const HEALTH_ICON = { healthy: "🟢", degraded: "🟡", unhealthy: "🔴" };

function renderStatus(status) {
    const badge = document.getElementById("mode_badge");
    const mode = (status.mode || "UNKNOWN").toUpperCase();

    badge.textContent = mode === "LIVE" ? "🔴 LIVE MODE" : `🔵 ${mode} MODE`;
    badge.className = `mode-badge mode-${mode.toLowerCase()}`;

    render("system_status",
        `${HEALTH_ICON[status.health] || "⚪"} ${esc((status.health || "unknown").toUpperCase())}`);
    render("exchange", esc(status.exchange));
    render("markets", (status.markets || []).map(esc).join("<br>"));
    render("mode", esc(mode));
    render("auto_trading", status.auto_trading ? "🟢 ON" : "⚪ OFF");

    /* 降級時把壞掉的元件列出來。只顯示「DEGRADED」而不說哪裡壞了,
       等於要人去翻 log —— 那件事沒有人會做。 */
    const failed = Object.entries(status.components || {})
        .filter(([, state]) => state === "error")
        .map(([name]) => name);

    const warning = document.getElementById("live_warning");
    const notes = [];

    if (status.is_live) {
        const limits = (status.safe_live && status.safe_live.max_notional_usdt) || null;
        notes.push(
            `<b>實單模式。</b>每一筆都是真錢。` +
            (limits ? ` SAFE LIVE 單筆名目上限 ${esc(limits)} USDT。` : "")
        );
    }
    if (failed.length) {
        notes.push(`以下元件異常:${failed.map(esc).join("、")}`);
    }

    warning.hidden = notes.length === 0;
    warning.innerHTML = notes.join("<br>");
}

function renderAgents(agents, activeCount) {
    const roles = agents.roles || [];
    const registered = agents.registered || roles.length;

    /* 「12/12 ACTIVE」不寫死:active 是這一輪真的有意見的數量。
       一個永遠顯示滿格的儀表板,在三個 Agent 棄權時看起來一模一樣。 */
    const active = activeCount === null || activeCount === undefined
        ? "?" : activeCount;
    render("agent_count", `${esc(active)}/${esc(registered)} ACTIVE`);

    render("agent_grid", roles.length
        ? roles.map(role => `<div class="agent-card">
              <div class="agent-name">${esc(role.agent)}</div>
              <div class="agent-doing">${esc(role.doing)}</div>
           </div>`).join("")
        : '<span class="muted">拿不到 Agent 名冊。</span>');
}

function whyTable(entry) {
    const rows = (entry.why || []).map(w => `<tr>
        <td class="nowrap">${esc(w.agent)}</td>
        <td class="nowrap vote-${esc(w.vote)}">${esc(w.vote)}</td>
        <td>${num(w.confidence, 0)}</td>
        <td>${w.error
            ? `<span class="neg">${esc(w.error)}</span>`
            : esc((w.reasons || []).join("; "))}</td>
    </tr>`).join("");

    return rows
        ? `<table class="why-table">${rows}</table>`
        : '<span class="muted">沒有 Agent 意見。</span>';
}

function opportunityCard(entry) {
    return `<div class="opportunity">
        <h3>${esc(entry.symbol)} ${esc(entry.direction)}</h3>
        <div class="score">
            信心 ${num(entry.confidence, 0)}%
            ${entry.supervisor_vetoed ? '· <b>Supervisor 已否決</b>' : ""}
        </div>
        <details>
            <summary>WHY?</summary>
            ${whyTable(entry)}
        </details>
    </div>`;
}

function renderOpportunities(data) {
    if (data.no_high_quality_setup) {
        render("opportunities", `<div class="no-setup">
            <b>NO HIGH QUALITY SETUP</b><br>
            這一輪掃了 ${esc(data.scanned)} 檔,沒有任何一檔產生可交易的共識。<br>
            這不是系統壞掉 —— 不交易是合法的結論(第二十九節)。
        </div>`);
    } else {
        render("opportunities", (data.top || []).map(opportunityCard).join(""));
    }

    const rest = (data.considered || []).filter(
        entry => !(data.top || []).some(t => t.symbol === entry.symbol)
    );

    render("considered", rest.length
        ? `<table class="tp"><tr><th>標的</th><th>方向</th>
           <th>信心</th><th>沒入選的原因</th></tr>${rest.map(entry => `<tr>
              <td class="nowrap">${esc(entry.symbol)}</td>
              <td class="nowrap">${esc(entry.direction)}</td>
              <td>${num(entry.confidence, 0)}</td>
              <td class="muted">${esc(
                  entry.blocked_reason
                  || (entry.tradable ? "信心未達門檻" : "沒有可交易的意圖")
              )}</td>
           </tr>`).join("")}</table>`
        : '<span class="muted">沒有其他掃描結果。</span>');
}

async function refresh() {
    try {
        const response = await fetch("/api/overview", { credentials: "same-origin" });
        if (!response.ok) throw new Error(`/api/overview 回應 ${response.status}`);

        const data = await response.json();

        renderStatus(data.status || {});
        renderOpportunities(data.opportunities || {});
        renderAgents(
            data.agents || {},
            (data.opportunities || {}).active_agents,
        );
    } catch (error) {
        render("opportunities",
            `<span class="neg">載入失敗:${esc(error.message || error)}</span>`);
    }
}

refresh();
setInterval(refresh, 60000);
