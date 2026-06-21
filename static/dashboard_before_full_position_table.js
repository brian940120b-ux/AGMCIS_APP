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

        let winRate = 0;

        if (data.trades > 0) {
            winRate =
                ((data.wins / data.trades) * 100).toFixed(2);
        }

        document.getElementById("win_rate").innerText =
            winRate + "%";

        const totalOpenUpnl =
            data.total_open_upnl || 0;

        const riskLevel =
            data.risk_level || "LOW";

        const upnlBox =
            document.getElementById("total_open_upnl");

        if (upnlBox) {
            upnlBox.innerText =
                totalOpenUpnl.toFixed(2) + " USDT";
        }

        const riskBox =
            document.getElementById("risk_level");

        if (riskBox) {
            riskBox.innerText = riskLevel;
        }
        const statsRes =
    await fetch("/api/stats");

const stats =
    await statsRes.json();

const bestTrade =
    document.getElementById("best_trade");

if(bestTrade){
    bestTrade.innerText =
        stats.best_trade + " USDT";
}

const worstTrade =
    document.getElementById("worst_trade");

if(worstTrade){
    worstTrade.innerText =
        stats.worst_trade + " USDT";
}

const profitFactor =
    document.getElementById("profit_factor");

if(profitFactor){
    profitFactor.innerText =
        stats.profit_factor;
}

const totalClosed =
    document.getElementById("total_closed_trades");

if(totalClosed){
    totalClosed.innerText =
        stats.total_closed_trades;
}
        const table =
            document.getElementById("open_positions_table");

        if (table) {

            let html =
            "<tr>" +
            "<th>幣種</th>" +
            "<th>方向</th>" +
            "<th>槓桿</th>" +
            "<th>進場</th>" +
            "<th>現價</th>" +
            "<th>ROI</th>" +
            "<th>UPNL</th>" +
            "</tr>";

            data.positions.forEach(function(p){

                html +=
                "<tr>" +
                "<td>" + p.symbol + "</td>" +
                "<td>" + p.signal + "</td>" +
                "<td>" + p.leverage + "x</td>" +
                "<td>" + p.entry + "</td>" +
                "<td>" + p.current + "</td>" +
                "<td>" + p.roi + "%</td>" +
                "<td>" + p.upnl + " USDT</td>" +
                "</tr>";

            });

            table.innerHTML = html;
        }

        const eqRes =
            await fetch("/api/equity_curve");

        const eq =
            await eqRes.json();

        const equityBox =
            document.getElementById("equity_curve");

        if (equityBox) {

            const pts = eq.points;

            const values =
                pts.map(x => Number(x.balance));

            const min =
                Math.min(...values);

            const max =
                Math.max(...values);

            let svg =
            "<svg width='100%' height='220' viewBox='0 0 700 220'>";

            let polyline = "";

            pts.forEach(function(p, i){

                const x =
                    30 +
                    (i * 620 /
                    Math.max(pts.length - 1, 1));

                const y =
                    190 -
                    (
                        (Number(p.balance) - min)
                        /
                        Math.max(max - min, 1)
                    ) * 150;

                polyline +=
                    x + "," + y + " ";

            });

            svg +=
            "<polyline points='" +
            polyline +
            "' fill='none' stroke='#38bdf8' stroke-width='4' />";

            svg +=
            "<text x='20' y='20'>Equity Curve</text>";

            svg +=
            "<text x='20' y='210'>Current: " +
            eq.current_balance +
            " USDT</text>";

            svg += "</svg>";

            equityBox.innerHTML = svg;
            const lbRes = await fetch("/api/leaderboard");
const lb = await lbRes.json();

const winnersBox = document.getElementById("top_winners");

if (winnersBox) {
    let html = "";

    lb.winners.forEach(function(t) {
        html += t.symbol + " : +" + t.pnl + " USDT<br>";
    });

    winnersBox.innerHTML = html;
}

const losersBox = document.getElementById("top_losers");

if (losersBox) {
    let html = "";

    lb.losers.forEach(function(t) {
        html += t.symbol + " : " + t.pnl + " USDT<br>";
    });

    losersBox.innerHTML = html;
}
        }

        document.title =
            "AGMCIS Live " +
            new Date().toLocaleTimeString();

    }
    catch (e) {

        console.log(
            "Dashboard Error:",
            e
        );

    }
}

setInterval(loadDashboard, 1000);

loadDashboard();
