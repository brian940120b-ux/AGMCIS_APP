function fmtUsdt(value) {
  const n = Number(value);
  if (!isFinite(n)) return "--";
  return n.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + " USDT";
}

function renderSummary(data) {
  const s = data.summary;
  if (!s) return;

  document.getElementById("balance").innerText = fmtUsdt(s.balance);
  document.getElementById("open_positions").innerText = s.open_positions ?? "--";
  document.getElementById("risk").innerText = s.risk ?? "--";

  const upnl = document.getElementById("upnl");
  upnl.innerText = fmtUsdt(s.upnl);
  upnl.className = "value " + (Number(s.upnl) >= 0 ? "green" : "red");
}
