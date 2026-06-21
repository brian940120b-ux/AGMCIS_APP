console.log("AGMCIS Dashboard JS Loaded");

async function loadDashboard() {
    try {
        const res = await fetch("/api/dashboard");
        const data = await res.json();

        document.getElementById("balance").innerText =
            data.balance + " USDT";

        document.getElementById("trades").innerText =
            data.trades;

        document.getElementById("open_count").innerText =
            data.open_count;
               document.getElementById("total_open_upnl").innerText =
            data.total_open_upnl + " USDT";

        document.getElementById("risk_level").innerText =
            data.risk_level;
        let winRate = 0;

        if (data.trades > 0) {
            winRate = (
                (data.wins / data.trades) * 100
            ).toFixed(2);
        }

        document.getElementById("win_rate").innerText =
            winRate + "%";
                const table = document.getElementById("open_positions_table");

        if (table) {
            let html = "<tr><th>幣種</th><th>方向</th><th>槓桿</th><th>進場</th><th>現價</th><th>停損</th><th>停利</th><th>ROI</th><th>UPNL</th></tr>";

            data.positions.forEach(function(p) {
                let c = Number(p.roi) >= 0 ? "pos" : "neg";

                html += "<tr>" +
                    "<td>" + p.symbol + "</td>" +
                    "<td>" + p.signal + "</td>" +
                    "<td>" + p.leverage + "x</td>" +
                    "<td>" + p.entry + "</td>" +
                    "<td>" + p.current + "</td>" +
                    "<td>" + p.stoploss + "</td>" +
                    "<td>" + p.takeprofit + "</td>" +
                    "<td class='" + c + "'>" + p.roi + "%</td>" +
                    "<td class='" + c + "'>" + p.upnl + " USDT</td>" +
                    "</tr>";
            });

            table.innerHTML = html;
        }
                const eqRes = await fetch("/api/equity_curve");
        const eq = await eqRes.json();

        let equityText = "";

        eq.points.slice(-10).forEach(function(p) {
            equityText += p.index + " : " + p.balance + " USDT\n";
        });

        const equityBox = document.getElementById("equity_curve");
        if (equityBox) {
            equityBox.innerText = equityText;
        }
        document.title =
            "AGMCIS Live " +
            new Date().toLocaleTimeString();

    } catch (e) {
        console.log("Dashboard Error:", e);
    }
}

setInterval(loadDashboard, 1000);

loadDashboard();

