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

async function loadSystemHealth(){try{const r=await fetch("/api/system_health");const d=await r.json();const s=(i,n,v)=>{const e=document.getElementById(i);if(e)e.innerText=(v==="active"?"🟢":"🔴")+" "+n+"："+String(v).toUpperCase();};s("health_api","API",d.api);s("health_telegram","Telegram",d.telegram);s("health_okx","OKX",d.okx);s("health_bingx","BingX",d.bingx);}catch(e){console.error(e)}}loadSystemHealth();setInterval(loadSystemHealth,10000);
