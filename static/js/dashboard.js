async function refreshDashboard() {
    try {
        const [
            dashboard,
            portfolio,
            performance,
            stats,
            journal
        ] = await Promise.all([
            AGMCIS_API.dashboard(),
            AGMCIS_API.portfolio(),
            AGMCIS_API.performance(),
            AGMCIS_API.stats(),
            AGMCIS_API.journal()
        ]);

        console.log("Dashboard:", dashboard);
        console.log("Portfolio:", portfolio);
        console.log("Performance:", performance);
        console.log("Stats:", stats);
        console.log("Journal:", journal);

    } catch (err) {
        console.error("Dashboard refresh failed:", err);
    }
}

refreshDashboard();

// 每 5 秒更新一次
setInterval(refreshDashboard, 5000);

