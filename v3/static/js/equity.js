let equityChart = null;

function renderEquity(data) {
  if (!data.portfolio || !data.portfolio.equity_curve) return;

  const ctx = document.getElementById("equityChart");
  if (!ctx) return;

  // Chart.js 走外部 CDN，載不到時給提示，不要留一塊空白
  if (typeof Chart === "undefined") {
    ctx.insertAdjacentHTML("afterend", '<p class="muted">圖表元件載入失敗（無法連線 Chart.js）</p>');
    ctx.remove();
    return;
  }

  const curve = data.portfolio.equity_curve;
  const labels = curve.map(x => x.index);
  const values = curve.map(x => x.balance);

  // 只更新資料，不每次重建圖表（重建會讓畫面每 5 秒閃一次）
  if (equityChart) {
    equityChart.data.labels = labels;
    equityChart.data.datasets[0].data = values;
    equityChart.update("none");
    return;
  }

  equityChart = new Chart(ctx, {
    type: "line",
    data: {
      labels: labels,
      datasets: [{
        label: "Balance",
        data: values,
        borderColor: "#22c55e",
        backgroundColor: "rgba(34,197,94,.12)",
        fill: true,
        tension: 0.25,
        pointRadius: 0
      }]
    },
    options: {
      responsive: true,
      animation: false,
      plugins: { legend: { display: true } },
      scales: {
        x: { ticks: { color: "#94a3b8" }, grid: { color: "#1e293b" } },
        y: { ticks: { color: "#94a3b8" }, grid: { color: "#1e293b" } }
      }
    }
  });
}
