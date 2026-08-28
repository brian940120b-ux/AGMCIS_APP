/**
 * Dashboard 資料入口
 *
 * 1. 先用 REST /api/dashboard 把畫面填滿（不必等第一次 WebSocket 廣播）
 * 2. 接著由 WebSocket 持續更新
 * 3. 斷線自動重連（指數退避，上限 30 秒）
 */
const RENDERERS = [
  renderSummary,
  renderAI,
  renderPositions,
  renderEquity,
  renderScanner
];

let retryDelay = 1000;

function setStatus(state, text) {
  const el = document.getElementById("conn_status");
  if (!el) return;
  el.dataset.state = state;
  document.getElementById("conn_text").innerText = text;
}

function showDataErrors(status) {
  const el = document.getElementById("data_error");
  if (!el) return;

  if (!status || status.state !== "degraded") {
    el.hidden = true;
    return;
  }

  el.hidden = false;
  el.innerText = "⚠️ 部分資料源異常：" + (status.errors || []).join(" ｜ ");
}

function render(data) {
  if (!data || data.type !== "dashboard_update") return;

  showDataErrors(data.status);

  for (const fn of RENDERERS) {
    try {
      if (typeof fn === "function") fn(data);
    } catch (err) {
      console.error("render error", err);
    }
  }
}

async function loadInitial() {
  try {
    const res = await fetch("/api/dashboard");
    render(await res.json());
  } catch (err) {
    console.error("initial load failed", err);
  }
}

function connect() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}/ws`);

  ws.onopen = () => {
    retryDelay = 1000;
    setStatus("online", "已連線");
  };

  ws.onmessage = e => {
    try {
      render(JSON.parse(e.data));
    } catch (err) {
      console.error("bad payload", err);
    }
  };

  ws.onclose = () => {
    setStatus("offline", `連線中斷，${Math.round(retryDelay / 1000)} 秒後重試`);
    setTimeout(connect, retryDelay);
    retryDelay = Math.min(retryDelay * 2, 30000);
  };

  ws.onerror = () => ws.close();
}

loadInitial();
connect();
