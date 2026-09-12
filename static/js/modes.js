/*
 * 交易模式與 LIVE 確認(第九十一 / 九十二節)。
 *
 * 送出按鈕的啟用條件是**全部七項都勾了、金額填了、確認句逐字對了**。
 * 這不是為了防惡意 —— 後端會再驗一次而且那才是真的把關。
 * 它是為了讓「還差什麼」看得見:一個按不下去而且說不出為什麼的
 * 按鈕,比一個會失敗的按鈕更讓人困惑。
 */

function esc(value) {
    return String(value === null || value === undefined ? "" : value)
        .replace(/&/g, "&amp;").replace(/</g, "&lt;")
        .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

function render(id, html) {
    const element = document.getElementById(id);
    if (element) element.innerHTML = html;
}

async function getJSON(url) {
    const response = await fetch(url, { credentials: "same-origin" });
    if (!response.ok) throw new Error(`${url} 回應 ${response.status}`);
    return response.json();
}

/* ---------------- 第九十一節:四種模式 ---------------- */

function renderModes(data) {
    const banner = document.getElementById("mode_banner");
    banner.className = `mode-banner is-${data.mode.toLowerCase()}`;
    banner.innerHTML =
        `<span class="big">${esc(data.mode)}</span>${esc(data.description)}` +
        (data.is_real_money
            ? "<br><b>真錢正在動。</b>" : "");

    render("mode_grid", (data.all_modes || []).map(m => `
        <div class="mode-card ${m.current ? "current" : ""} ${
            m.is_real_money ? "real-money" : ""}">
            <span class="badge">${m.current ? "現在" : ""}</span>
            <div class="name">${esc(m.mode)}</div>
            <div class="what">${esc(m.description)}</div>
        </div>`).join(""));

    const derived = data.derived_from || {};
    render("derived", `<tr><th>設定</th><th>值</th></tr>` +
        Object.entries(derived).map(([key, value]) => `<tr>
            <td class="nowrap"><code>${esc(key)}</code></td>
            <td>${esc(String(value))}</td>
        </tr>`).join(""));
}

/* ---------------- 第九十二節:七項確認 ---------------- */

let CONFIG = null;

function valueRow(key, value) {
    const missing = value === null || value === undefined || value === "";
    let shown;

    if (missing) {
        shown = "—";
    } else if (typeof value === "boolean") {
        shown = value ? "是" : "否";
    } else if (Array.isArray(value)) {
        shown = value.length ? value.join("、") : "(空)";
    } else {
        shown = String(value);
    }

    return `<div class="kv">
        <span class="k">${esc(key)}</span>
        <span class="v ${missing ? "none" : ""}">${esc(shown)}</span>
    </div>`;
}

function renderWizard(config) {
    CONFIG = config;

    const steps = (config.items || []).map((entry, index) => `
        <div class="step" data-item="${esc(entry.item)}">
            <h4>[${index + 1}/7] ${esc(entry.title)}</h4>
            <div class="values">${
                Object.keys(entry.values).sort()
                    .map(k => valueRow(k, entry.values[k])).join("")
            }</div>
            <label>
                <input type="checkbox" class="ack" data-item="${esc(entry.item)}">
                以上正確。
            </label>
        </div>`).join("");

    render("wizard", `
        ${steps}
        <div class="final">
            <label for="amount">批准的<b>單筆名目上限</b>(USDT,上限
                ${esc(config.max_notional_usdt)})</label>
            <input type="number" id="amount" min="1"
                   max="${esc(config.max_notional_usdt)}" step="1"
                   placeholder="第一次的規模應該小到虧光也不影響任何事">

            <label for="phrase">逐字輸入這一句(大小寫要一樣):</label>
            <code class="phrase">${esc(config.required_phrase)}</code>
            <input type="text" id="phrase" autocomplete="off" spellcheck="false"
                   placeholder="在這裡輸入上面那一句">

            <button type="button" class="danger" id="submit" disabled>
                簽署確認檔
            </button>
            <div id="result"></div>
        </div>`);

    document.querySelectorAll(".ack").forEach(box => {
        box.addEventListener("change", () => {
            box.closest(".step").classList.toggle("done", box.checked);
            refreshButton();
        });
    });

    document.getElementById("amount").addEventListener("input", refreshButton);
    document.getElementById("phrase").addEventListener("input", refreshButton);
    document.getElementById("submit").addEventListener("click", submit);

    renderExisting(config.existing);
}

function collect() {
    const confirmations = {};
    document.querySelectorAll(".ack").forEach(box => {
        confirmations[box.dataset.item] = box.checked;
    });
    return confirmations;
}

function whatIsMissing() {
    const confirmations = collect();
    const unchecked = Object.values(confirmations).filter(v => !v).length;

    if (unchecked) return `還有 ${unchecked} 項沒有確認`;

    const amount = Number(document.getElementById("amount").value);
    if (!amount || amount <= 0) return "還沒填批准金額";
    if (amount > CONFIG.max_notional_usdt) {
        return `金額超過首次上限 ${CONFIG.max_notional_usdt}`;
    }

    /* 逐字比對,不容錯。這一句是最後一道「你確定嗎」,
       而一個接受近似輸入的確認句等於沒有確認句。 */
    if (document.getElementById("phrase").value !== CONFIG.required_phrase) {
        return "確認句還沒有逐字對上";
    }

    return null;
}

function refreshButton() {
    const missing = whatIsMissing();
    const button = document.getElementById("submit");

    button.disabled = missing !== null;
    /* 說出還差什麼。一個按不下去而且說不出為什麼的按鈕最讓人困惑。 */
    render("result", missing ? `<span class="muted">${esc(missing)}</span>` : "");
    if (!missing) document.getElementById("result").className = "";
}

async function submit() {
    const button = document.getElementById("submit");
    button.disabled = true;
    render("result", "送出中…");

    try {
        const response = await fetch("/api/live_confirmation", {
            method: "POST",
            credentials: "same-origin",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                confirmations: collect(),
                approved_notional_usdt: Number(
                    document.getElementById("amount").value),
                phrase: document.getElementById("phrase").value,
                /* 前端拿到七項時一起拿到的指紋。後端會比對 ——
                   設定在你看畫面的期間被改過的話,你確認的是舊的那一組值。 */
                fingerprint: CONFIG.fingerprint,
            }),
        });

        const data = await response.json();
        const box = document.getElementById("result");

        box.className = data.ok ? "ok" : "bad";
        box.innerHTML = esc(data.message).replace(/\n/g, "<br>");

        if (data.ok) load();      // 重新載入,顯示新的有效期
    } catch (error) {
        const box = document.getElementById("result");
        box.className = "bad";
        box.textContent = `送出失敗:${error.message || error}`;
    } finally {
        refreshButton();
    }
}

function renderExisting(existing) {
    const box = document.getElementById("existing");

    if (!existing) {
        box.hidden = true;
        return;
    }

    box.hidden = false;

    if (!existing.readable) {
        box.innerHTML = `<h3>現有的確認檔</h3>
            <span class="neg">讀不出來:${esc(existing.error)}</span>`;
        return;
    }

    box.innerHTML = `<h3>現有的確認檔</h3>
        <table class="tp">
          <tr><td>簽署於</td><td>${esc(existing.signed_at)}</td></tr>
          <tr><td>批准金額</td><td>${
              esc(existing.approved_notional_usdt)} USDT</td></tr>
          <tr><td>七項與指紋</td><td>${
              existing.items_valid ? "✅ 通過" : "⛔ " + esc(existing.detail)
          }</td></tr>
        </table>
        <p class="muted">重新簽一次會覆蓋掉它。</p>`;
}

/* ---------------- 啟動 ---------------- */

async function load() {
    try {
        renderModes(await getJSON("/api/trading_mode"));
    } catch (error) {
        render("mode_banner", `載入失敗:${esc(error.message)}`);
    }

    try {
        renderWizard(await getJSON("/api/live_confirmation"));
    } catch (error) {
        render("wizard", `<span class="neg">載入失敗:${esc(error.message)}</span>`);
    }
}

load();
