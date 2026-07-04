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

async function loadPortfolio(){try{const r=await fetch("/api/portfolio");const d=await r.json();document.getElementById("pf_balance").innerText="💰 Balance："+d.balance+" USDT";document.getElementById("pf_positions").innerText="📦 Open Positions："+d.open_positions;document.getElementById("pf_risk").innerText="⚠️ Risk："+d.risk_level;document.getElementById("pf_upnl").innerText="📈 UPNL："+d.total_open_upnl+" USDT";}catch(e){console.error(e)}}loadPortfolio();setInterval(loadPortfolio,10000);

async function loadMarketScan(){try{const r=await fetch("/api/market_scan");const d=await r.json();const el=document.getElementById("market_scan");if(!el)return;el.innerHTML="<table><tr><th>Symbol</th><th>Signal</th><th>Confidence</th><th>MTF</th><th>Action</th></tr>"+d.top.map(x=>`<tr><td>${x.symbol}</td><td>${x.trade_signal}</td><td>${x.confidence}