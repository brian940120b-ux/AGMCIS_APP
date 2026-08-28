function renderAI(data) {
  const el = document.getElementById("ai_decision");
  if (!el) return;

  const rows = data.ai_decisions || [];

  if (rows.length === 0) {
    el.innerHTML = '<p class="muted">尚無 AI 決策</p>';
    return;
  }

  el.innerHTML = rows.map(x => `
    <div class="row">
      <b>${x.symbol}</b>
      <span>${x.trade_signal || "--"}</span>
      <span>${x.confidence ?? "--"}%</span>
    </div>
  `).join("");
}
