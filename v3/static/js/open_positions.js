function renderPositions(data) {
  const el = document.getElementById("open_positions_list");
  if (!el || !data.portfolio) return;

  const trades = data.portfolio.open_trades || [];

  if (trades.length === 0) {
    el.innerHTML = '<p class="muted">目前沒有持倉</p>';
    return;
  }

  el.innerHTML = trades.map(t => `
    <div class="row">
      <b>${t.symbol}</b>
      <span>${t.signal ?? "--"}</span>
      <span>${fmtUsdt(t.size_usdt)}</span>
      <span class="muted">entry ${t.entry_price ?? "--"}</span>
    </div>
  `).join("");
}
