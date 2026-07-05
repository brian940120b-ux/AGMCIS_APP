function renderScanner(data){
  const el=document.getElementById("scanner_top10");
  if(!el || !data.market_scan) return;
  el.innerHTML=data.market_scan.slice(0,10).map(x=>`
    <div style="padding:8px;border-bottom:1px solid #1e293b">
      <b>${x.symbol}</b> ｜ ${x.trade_signal||x.signal||"N/A"} ｜ ${x.confidence??"--"}%
    </div>
  `).join("");
}
