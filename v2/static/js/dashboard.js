async function loadSummary() {
    const res = await fetch("/api/summary");
    const data = await res.json();

    document.getElementById("balance").innerText =
        "Balance： " + data.balance + " USDT";

    document.getElementById("open_positions").innerText =
        "Open Positions： " + data.open_positions;

    document.getElementById("win_rate").innerText =
        "Win Rate： " + data.win_rate + "%";

    document.getElementById("today_pnl").innerText =
        "Today PnL： " + data.today_pnl + " USDT";
}

loadSummary();
