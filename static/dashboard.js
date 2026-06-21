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

            if (totalOpenUpnl >= 0) {
            upnlBox.style.color = "#22c55e";
            } else {
                upnlBox.style.color = "#ef4444";
            }
        }

        const riskBox =
            document.getElementById("risk_level");

        if (riskBox) {

            riskBox.innerText = riskLevel;

            if (riskLevel === "LOW") {
            riskBox.style.color = "#22c55e";
            }
            else if (riskLevel === "MEDIUM") {
            riskBox.style.color = "#f59e0b";
            }
            else {
            riskBox.style.color = "#ef4444";
            }
       }
const statusBox =
    document.getElementById("system_status");

if (statusBox) {
    statusBox.innerText = data.system_status || "-";

    if (data.system_status === "ACTIVE") {
        statusBox.innerText = "🟢 ACTIVE";
        statusBox.style.color = "#22c55e";

    } else if (data.system_status === "LIMITED") {
        statusBox.innerText = "🟡 LIMITED";
        statusBox.style.color = "#f59e0b";

    } else if (data.system_status === "EMERGENCY_STOP") {
        statusBox.innerText = "🔴 EMERGENCY STOP";
        statusBox.style.color = "#ef4444";

    } else {
        statusBox.innerText = "⛔ PAUSED";
        statusBox.style.color = "#94a3b8";
    }
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
"<th>倉位價值</th>" +
"<th>進場</th>" +
"<th>現價</th>" +
"<th>出場</th>" +
"<th>停損</th>" +
"<th>停利</th>" +
"<th>距離停損%</th>" +
"<th>距離停利%</th>" +
"<th>ROI</th>" +
"<th>UPNL</th>" +
"<th>已實現</th>" +
"<th>狀態</th>" +
"<th>時間/原因</th>" +
"</tr>";

data.positions.forEach(function(p){

    const cls =
        Number(p.upnl) >= 0 ? "pos" : "neg";

    const value =
        1000 * Number(p.leverage || 3);

    html +=
    "<tr>" +
    "<td>" + p.symbol + "</td>" +
    "<td>" + p.signal + "</td>" +
    "<td>" + p.leverage + "x</td>" +
    "<td>" + value + " USDT</td>" +
    "<td>" + p.entry + "</td>" +
    "<td>" + p.current + "</td>" +
    "<td>-</td>" +
    "<td>" + p.stoploss + "</td>" +
    "<td>" + p.takeprofit + "</td>" +
    "<td class='" + (Number(p.distance_to_sl) <= 3 ? "neg" : "") + "'>" + (Number(p.distance_to_sl) <= 3 ? "🚨 " : "") + p.distance_to_sl + "%</td>" +
    "<td class='" + (Number(p.distance_to_tp) <= 3 ? "pos" : "") + "'>" + (Number(p.distance_to_tp) <= 3 ? "🎯 " : "") + p.distance_to_tp + "%</td>" +
    "<td class='" + cls + "'>" + p.roi + "%</td>" +
    "<td class='" + cls + "'>" + p.upnl + " USDT</td>" +
    "<td>-</td>" +
    "<td>OPEN</td>" +
    "<td>" + p.opened_at + "</td>" +
    "</tr>";

});

                table.innerHTML = html;    
        }

        
const analyticsRes =
    await fetch("/api/analytics_pro");

const analytics =
    await analyticsRes.json();

document.getElementById("total_realized").innerText =
    analytics.total_realized + " USDT";

document.getElementById("max_win_streak").innerText =
    analytics.max_win_streak;

document.getElementById("max_loss_streak").innerText =
    analytics.max_loss_streak;

document.getElementById("analytics_closed_trades").innerText =
    analytics.closed_trades;

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
const sortedPositions =
    [...data.positions].sort(
        (a,b) => Number(b.upnl) - Number(a.upnl)
    );

const bestBox =
    document.getElementById("best_positions");

if (bestBox) {

    let html = "";

    sortedPositions.slice(0,3).forEach(function(p){

        html +=
            p.symbol +
            " : " +
            p.upnl +
            " USDT<br>";

    });

    bestBox.innerHTML = html;
}

const worstBox =
    document.getElementById("worst_positions");

if (worstBox) {

    let html = "";

    [...sortedPositions]
        .reverse()
        .slice(0,3)
        .forEach(function(p){

            html +=
                p.symbol +
                " : " +
                p.upnl +
                " USDT<br>";

        });

    worstBox.innerHTML = html;
}

const nearestSLBox =
    document.getElementById("nearest_sl");

if (nearestSLBox) {
    let html = "";
    [...data.positions]
        .sort((a,b) => Number(a.distance_to_sl) - Number(b.distance_to_sl))
        .slice(0,3)
        .forEach(function(p){
            html += "🚨 " + p.symbol + " : " + p.distance_to_sl + "%<br>";
        });
    nearestSLBox.innerHTML = html;
}

const nearestTPBox =
    document.getElementById("nearest_tp");

if (nearestTPBox) {
    let html = "";
    [...data.positions]
        .sort((a,b) => Number(a.distance_to_tp) - Number(b.distance_to_tp))
        .slice(0,3)
        .forEach(function(p){
            html += "🎯 " + p.symbol + " : " + p.distance_to_tp + "%<br>";
        });
    nearestTPBox.innerHTML = html;
}

const profitCount =
    data.positions.filter(p => Number(p.upnl) > 0).length;

const lossCount =
    data.positions.filter(p => Number(p.upnl) < 0).length;

const flatCount =
    data.positions.filter(p => Number(p.upnl) === 0).length;

const profitBox =
    document.getElementById("profit_positions");

if (profitBox) {
    profitBox.innerText = profitCount;
}

const lossBox =
    document.getElementById("loss_positions");

if (lossBox) {
    lossBox.innerText = lossCount;
}

const flatBox =
    document.getElementById("flat_positions");

if (flatBox) {
    flatBox.innerText = flatCount;
}

const maxProfitBox =
    document.getElementById("max_profit_position");

if (maxProfitBox && sortedPositions.length > 0) {
    const p = sortedPositions[0];
    maxProfitBox.innerText =
        p.symbol + " " + p.upnl + " USDT";
}

const maxLossBox =
    document.getElementById("max_loss_position");

if (maxLossBox && sortedPositions.length > 0) {
    const p =
        [...sortedPositions].reverse()[0];

    maxLossBox.innerText =
        p.symbol + " " + p.upnl + " USDT";
}
        }

        

const uptimeBox =
    document.getElementById("uptime");

if (uptimeBox) {
    uptimeBox.innerText =
        formatUptime(data.uptime_seconds);
}

const lastUpdateBox =
    document.getElementById("last_update");

if (lastUpdateBox) {
    lastUpdateBox.innerText =
        new Date().toLocaleTimeString();
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

setInterval(loadDashboard, 5000);

loadDashboard();

function formatUptime(sec) {
    sec = Number(sec || 0);
    const d = Math.floor(sec / 86400);
    const h = Math.floor((sec % 86400) / 3600);
    const m = Math.floor((sec % 3600) / 60);
    if (d > 0) return d + "d " + h + "h";
    return h + "h " + m + "m";
}

