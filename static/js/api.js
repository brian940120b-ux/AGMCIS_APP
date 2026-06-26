async function apiGet(path) {
  const res = await fetch(path);
  if (!res.ok) {
    throw new Error(path + " " + res.status);
  }
  return await res.json();
}

const AGMCIS_API = {
  dashboard: () => apiGet("/api/dashboard"),
  portfolio: () => apiGet("/api/portfolio"),
  performance: () => apiGet("/api/performance"),
  stats: () => apiGet("/api/stats"),
  journal: () => apiGet("/api/journal"),
  analytics: () => apiGet("/api/analytics_pro"),
  equity: () => apiGet("/api/equity_curve"),
  aiDecisions: () => apiGet("/api/ai_decisions")
};
