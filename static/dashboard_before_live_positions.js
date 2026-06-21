console.log("AGMCIS Dashboard JS Loaded");

async function loadDashboard() {
    try {
        const res = await fetch("/api/dashboard");
        const data = await res.json();

        document.getElementById("balance").innerText = data.balance + " USDT";
        document.getElementById("trades").innerText = data.trades;
        document.getElementById("open_count").innerText = data.open_count;

        let winRate = 0;
        if (data.trades > 0) {
            winRate = ((data.wins / data.trades) * 100).toFixed(2);
        }
        document.getElementById("win_rate").innerText = winRate + "%";

        document.title = "AGMCIS Live " + new Date().toLocaleTimeString();

    } catch (e) {
        console.log("Dashboard Error:", e);
    }
}

setInterval(loadDashboard, 1000);
loadDashboard();
