async function refreshDashboard() {
    try {
        const [
            dashboard,
            portfolio,
            performance,
            stats,
            journal,
            analytics
        ] = await Promise.all([
            AGMCIS_API.dashboard(),
            AGMCIS_API.portfolio(),
            AGMCIS_API.performance(),
            AGMCIS_API.stats(),
            AGMCIS_API.journal(),
            AGMCIS_API.analytics()
        ]);

        console.log("Dashboard:", dashboard);

document.getElementById("balance").innerText =
dashboard.balance + " USDT";
        console.log("Portfolio:", portfolio);

document.getElementById("win_rate").innerText =
performance.win_rate + "%";

document.getElementById("risk_level").innerText =
dashboard.risk_level;

document.getElementById("system_status").innerText =
dashboard.system_status;
        console.log("Performance:", performance);

document.getElementById("trades").innerText =
dashboard.trades;
        console.log("Stats:", stats);

document.getElementById("open_count").innerText =
dashboard.open_count;

document.getElementById("total_closed_trades").innerText =
performance.closed_trades;

document.getElementById("analytics_closed_trades").innerText =
performance.closed_trades;

document.getElementById("total_realized").innerText =
performance.total_pnl.toFixed(2) + " USDT";
        console.log("Journal:", journal);
        console.log("Analytics:", analytics);

        document.getElementById("max_win_streak").innerText =
            analytics.max_win_streak;

        document.getElementById("max_loss_streak").innerText =
            analytics.max_loss_streak;

updateOpenPositions(dashboard);
        updateJournal(journal);
        updateEquityChart(await AGMCIS_API.equity());
        updateHealth(dashboard);

document.getElementById("profit_factor").innerText =
stats.profit_factor.toFixed(2);

if (performance.best) {
    document.getElementById("best_trade").innerText =
        performance.best.pnl_usdt.toFixed(2) + " USDT";
}

if (performance.worst) {
    document.getElementById("worst_trade").innerText =
        performance.worst.pnl_usdt.toFixed(2) + " USDT";
}

document.getElementById("total_open_upnl").innerText =
    dashboard.total_open_upnl.toFixed(2) + " USDT";

    } catch (err) {
        console.error("Dashboard refresh failed:", err);
    }
}

refreshDashboard();

// 每 5 秒更新一次
setInterval(refreshDashboard, 5000);


function updateOpenPositions(dashboard) {
  const table = document.getElementById("open_positions_table");
  if (!table) return;

  table.innerHTML =
    "<tr><th>幣種</th><th>方向</th><th>ROI</th><th>UPNL</th></tr>" +
    dashboard.positions.map(p =>
      `<tr><td>${p.symbol}</td><td>${p.signal}</td><td>${p.roi}%</td><td>${p.upnl} USDT</td></tr>`
    ).join("");
}

function updateJournal(journal) {
  const old = document.getElementById("journal_table");
  if (old) old.remove();

  const title = Array.from(document.querySelectorAll("h2"))
    .find(h => h.innerText.includes("最近平倉"));

  if (!title) return;

  const table = document.createElement("table");
  table.id = "journal_table";

  table.innerHTML =
    "<tr><th>時間</th><th>動作</th><th>幣種</th><th>價格</th></tr>" +
    journal.map(j =>
      `<tr><td>${j.created_at}</td><td>${j.action}</td><td>${j.symbol}</td><td>${j.price}</td></tr>`
    ).join("");

  title.insertAdjacentElement("beforebegin", table);
}

let equityChart = null;

function updateEquityChart(equity) {
  const canvas = document.getElementById("equityChart");
  if (!canvas || !equity || !equity.points) return;

  const labels = equity.points.map(p => p.index);
  const data = equity.points.map(p => p.balance);

  if (equityChart) {
    equityChart.data.labels = labels;
    equityChart.data.datasets[0].data = data;
    equityChart.update();
    return;
  }

  equityChart = new Chart(canvas, {
    type: "line",
    data: {
      labels: labels,
      datasets: [{
        label: "Equity",
        data: data,
        tension: 0.35
      }]
    }
  });
}

function updateHealth(dashboard) {
  const last = document.getElementById("last_update");
  if (last) last.innerText = new Date().toLocaleString();

  const uptime = document.getElementById("uptime");
  if (uptime) uptime.innerText =
    Math.floor((dashboard.uptime_seconds || 0) / 60) + " min";

  ["health_api","health_risk","health_report","health_optimizer"].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.innerText = "🟢 OK";
  });
}
