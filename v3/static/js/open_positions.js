function renderPositions(data){
  const el=document.getElementById("open_positions_list");
  if(!el || !data.portfolio || !data.portfolio.open_trades){
    return;
  }

  const trades=data.portfolio.open_trades;

  if(trades.length===0){
    el.innerHTML="<p>目前沒有持倉</p>";
    return;
  }

  el.innerHTML=trades.map(t=>`
    <div style="padding:10px;border-bottom:1px solid #1e293b">
      <b>${t.symbol}</b> ｜ ${t.signal} ｜ ${t.size_usdt} USDT
    </div>
  `).join("");
}
