async function refreshDashboard() {
    try {
        const [
            dashboard,
            portfolio,
            performance,
            stats,
            journal
        ] = await Promise.all([
            AGMCIS_API.dashboard(),
            AGMCIS_API.portfolio(),
            AGMCIS_API.performance(),
            AGMCIS_API.stats(),
            AGMCIS_API.journal()
        ]);

        console.log("Dashboard:", dashboard);

document.getElementById("balance").innerText =
dashboard.balance + " USDT";
        console.log("Portfolio:", portfolio);

document.getElementById("win_rate").innerText =
dashboard.win_rate + "%";
        console.log("Performance:", performance);

document.getElementById("trades").innerText =
dashboard.trades;
        console.log("Stats:", stats);

document.getElementById("open_count").innerText =
dashboard.open_count;
        console.log("Journal:", journal);

updateOpenPositions(dashboard);
        updateJournal(journal);

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
