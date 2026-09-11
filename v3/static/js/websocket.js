const ws = new WebSocket(
  (location.protocol === "https:" ? "wss" : "ws") + "://" + location.host + "/ws"
);

function fmt(v, suffix) {
  if (v === null || v === undefined || v === "") return "--";
  return suffix ? v + " " + suffix : v;
}

function renderSummary(d) {
  const s = d.summary || {};
  balance.innerText = fmt(s.balance, "USDT");
  open_positions.innerText = fmt(s.open_positions);
  risk.innerText = fmt(s.risk);
  upnl.innerText = fmt(s.upnl, "USDT");
  upnl.className = "value " + (Number(s.upnl) >= 0 ? "green" : "red");
}

function renderAI(d) {
  const el = document.getElementById("ai_decision");
  if (!el || !d.ai_decisions) return;
  if (d.ai_decisions.length === 0) {
    el.innerHTML = "目前沒有 AI 決策";
    return;
  }
  el.innerHTML = d.ai_decisions
    .slice(0, 5)
    .map(x => `${x.symbol} ｜ ${x.trade_signal || x.signal || "N/A"} ｜ ${x.confidence ?? "--"}%`)
    .join("<br>");
}

ws.onmessage = e => {
  const d = JSON.parse(e.data);
  if (d.type !== "dashboard_update") return;

  renderSummary(d);
  renderAI(d);

  if (typeof renderPositions === "function") renderPositions(d);
  if (typeof renderEquity === "function") renderEquity(d);
  if (typeof renderScanner === "function") renderScanner(d);
};
