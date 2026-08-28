// TradingView 走外部 CDN，被擋或離線時要優雅降級，不能讓整個 script 丟錯
(function () {
  const container = document.getElementById("tradingview_btc");
  if (!container) return;

  if (typeof TradingView === "undefined") {
    container.innerHTML = '<p class="muted">圖表元件載入失敗（無法連線 TradingView）</p>';
    return;
  }

  new TradingView.widget({
    "width": "100%",
    "height": 520,
    "symbol": "OKX:BTCUSDT",
    "interval": "15",
    "timezone": "Asia/Taipei",
    "theme": "dark",
    "style": "1",
    "locale": "zh_TW",
    "container_id": "tradingview_btc"
  });
})();
