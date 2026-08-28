function renderScanner(data) {
  const el = document.getElementById("scanner_top10");
  if (!el) return;

  const rows = data.market_scan || [];

  if (rows.length === 0) {
    el.innerHTML = '<p class="muted">掃描結果尚未產生</p>';
    return;
  }

  el.innerHTML = rows.map(x => `
    <div class="row">
      <b>${x.symbol}</b>
      <span>${x.trade_signal || x.signal || "N/A"}</span>
      <span>${x.confidence ?? "--"}%</span>
      <span class="muted">MTF ${x.mtf_score ?? "--"}</span>
    </div>
  `).join("");
}
