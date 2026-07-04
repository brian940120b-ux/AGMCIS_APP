const ws=new WebSocket((location.protocol==="https:"?"wss":"ws")+"://"+location.host+"/ws");ws.onmessage=e=>{const d=JSON.parse(e.data);if(d.type!=="dashboard_update")return;balance.innerText=d.summary.balance+" USDT";open_positions.innerText=d.summary.open_positions;risk.innerText=d.summary.risk;upnl.innerText=d.summary.upnl+" USDT";upnl.className="value "+(Number(d.summary.upnl)>=0?"green":"red"); if(typeof renderPositions==="function")renderPositions(d); if(typeof renderEquity==="function")renderEquity(d);};
function renderAI(d){
  const el=document.getElementById("ai_decision");
  if(!el || !d.ai_decisions)return;
  el.innerHTML=d.ai_decisions.slice(0,5).map(x=>`${x.symbol} ｜ ${x.trade_signal} ｜ ${x.confidence}%`).join("<br>");
}
const oldMsg=ws.onmessage;
ws.onmessage=e=>{oldMsg(e);const d=JSON.parse(e.data);renderAI(d);};
