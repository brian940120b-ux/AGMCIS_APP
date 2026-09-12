"""
第六十四節:新表要有東西寫進去,不然它們只是空的 schema。

migration 009 建了 news / system_events / strategies / backtests。
建表跟「有資料」是兩件事 —— 這一組測試盯的是**寫入端真的接上了**。
"""
import logging

import pytest

from agmcis.data import news_archive
from agmcis.strategy import mirror


# ---------------- 新聞歸檔 ----------------

def _feed(*items):
    return lambda: list(items)


def test_collect_skips_the_fetch_error_row():
    """
    news_center 抓失敗時會把錯誤訊息包成一則新聞回傳。
    那不是新聞 —— 存進去以後看起來像那天真的有一則叫
    「RSS 讀取失敗」的頭條。
    """
    rows = news_archive.collect(fetch=_feed(
        {"title": "BTC breaks 100k", "source": "CoinDesk", "url": "u1"},
        {"title": "RSS 讀取失敗:timeout", "source": "SYSTEM", "url": "#"},
    ))

    assert [r["title"] for r in rows] == ["BTC breaks 100k"]


def test_collect_skips_empty_titles():
    rows = news_archive.collect(fetch=_feed(
        {"title": "   ", "source": "CoinDesk"},
        {"title": "Real one", "source": "CoinDesk"},
    ))
    assert [r["title"] for r in rows] == ["Real one"]


def test_collect_marks_shock_headlines():
    """關鍵字掃描的結果要跟著新聞一起存 —— 事後才知道當時判斷成什麼。"""
    rows = news_archive.collect(fetch=_feed(
        {"title": "Major exchange hacked, funds drained", "source": "X"},
        {"title": "Weekly market wrap", "source": "X"},
    ))

    by_title = {r["title"]: r for r in rows}
    assert by_title["Major exchange hacked, funds drained"]["impact"] == "SHOCK"
    assert by_title["Weekly market wrap"]["impact"] is None


def test_collect_leaves_sentiment_empty():
    """
    情緒分數刻意不存:它會隨模型改版而變,跟原始標題混在同一列
    以後就分不出哪個是事實。
    """
    rows = news_archive.collect(fetch=_feed({"title": "T", "source": "X"}))
    assert rows[0]["sentiment"] is None
    assert rows[0]["score"] is None


def test_archive_reports_write_failure_instead_of_swallowing_it(monkeypatch, caplog):
    """表還沒 migrate 不該讓排程器看起來正常。"""
    monkeypatch.setattr(
        news_archive, "collect",
        lambda fetch=None: [{"title": "T", "source": "X", "impact": None}],
    )

    import database_service

    def boom(items):
        raise RuntimeError("relation \"news\" does not exist")

    monkeypatch.setattr(database_service, "insert_news", boom)

    with caplog.at_level(logging.WARNING):
        result = news_archive.archive_news()

    assert result["status"] == "WRITE_FAILED"
    assert result["inserted"] == 0
    assert "news" in caplog.text


def test_archive_reports_fetch_failure(monkeypatch):
    def boom():
        raise RuntimeError("network down")

    result = news_archive.archive_news(fetch=boom)
    assert result["status"] == "FETCH_FAILED"
    assert result["fetched"] == 0


def test_archive_counts_inserted(monkeypatch):
    monkeypatch.setattr(
        news_archive, "collect",
        lambda fetch=None: [{"title": "A", "impact": "SHOCK"}, {"title": "B", "impact": None}],
    )

    import database_service
    monkeypatch.setattr(database_service, "insert_news", lambda items: len(items))

    result = news_archive.archive_news()
    assert result == {"status": "OK", "fetched": 2, "inserted": 2, "shocks": 1}


# ---------------- 策略鏡像 ----------------

def test_snapshot_covers_every_registered_strategy():
    from agmcis.strategy.registry import get_registry

    rows = mirror.snapshot()
    assert {r["name"] for r in rows} == set(get_registry().names)


def test_snapshot_carries_the_data_requirements():
    """
    needs_candles / needs_order_book 是「這個策略拿不到資料就要 WAIT」
    的宣告。鏡像沒帶著它,查表的人會看不出為什麼某個策略總是棄權。
    """
    rows = {r["name"]: r for r in mirror.snapshot()}
    assert any(r["needs_candles"] for r in rows.values())
    assert all(isinstance(r["needs_order_book"], bool) for r in rows.values())


def test_snapshot_reads_status_from_the_file_store():
    """權威來源是檔案。鏡像讀的必須是同一個地方。"""
    class FakeStore:
        def get(self, name):
            return "live" if name == "trend_following" else "paper"

    rows = {r["name"]: r for r in mirror.snapshot(store=FakeStore())}
    assert rows["trend_following"]["status"] == "live"
    assert all(r["status"] in ("live", "paper") for r in rows.values())


def test_mirror_keeps_going_when_one_row_fails(monkeypatch):
    """一個策略寫不進去不該讓其他的也不寫。"""
    monkeypatch.setattr(mirror, "snapshot", lambda **kw: [
        {"name": "a", "status": "paper", "description": None,
         "suitable_regimes": [], "needs_candles": False, "needs_order_book": False},
        {"name": "b", "status": "paper", "description": None,
         "suitable_regimes": [], "needs_candles": False, "needs_order_book": False},
    ])

    import database_service
    calls = []

    def flaky(name, status, **kwargs):
        calls.append(name)
        if name == "a":
            raise RuntimeError("nope")
        return 1

    monkeypatch.setattr(database_service, "upsert_strategy", flaky)

    result = mirror.mirror_strategies()
    assert calls == ["a", "b"]
    assert result["status"] == "PARTIAL"
    assert result["failed"] == ["a"]
    assert result["mirrored"] == 1


def test_registry_strategies_property_returns_a_copy():
    from agmcis.strategy.registry import get_registry

    registry = get_registry()
    got = registry.strategies
    got.clear()
    assert registry.strategies, "外面清掉自己的清單不該影響註冊表"


# ---------------- 系統事件 ----------------

def _runner(job):
    from agmcis.scheduling.runner import SchedulerRunner

    return SchedulerRunner(
        [job], status_file="/tmp/agmcis-test-scheduler.json",
        logger=logging.getLogger("test.scheduler"),
    )


def _job(name, fn):
    from agmcis.scheduling.jobs import Job
    return Job(name=name, run=fn, interval_seconds=0)


def test_only_the_first_failure_writes_an_event(monkeypatch):
    """
    每分鐘失敗一次的任務會在一天內寫一千四百列,而那一千四百列
    講的是同一件事。值得記的是它什麼時候開始壞。
    """
    events = []
    import database_service
    monkeypatch.setattr(
        database_service, "insert_system_event",
        lambda event_type, **kw: events.append((event_type, kw.get("source"))),
    )

    def boom():
        raise RuntimeError("still broken")

    runner = _runner(_job("flaky", boom))
    runner.tick(now=1.0)
    runner.tick(now=2.0)
    runner.tick(now=3.0)

    assert events == [("SCHEDULER_JOB_FAILED", "flaky")]


def test_recovery_writes_an_event(monkeypatch):
    events = []
    import database_service
    monkeypatch.setattr(
        database_service, "insert_system_event",
        lambda event_type, **kw: events.append(event_type),
    )

    state = {"fail": True}

    def sometimes():
        if state["fail"]:
            raise RuntimeError("down")
        return {"status": "OK"}

    runner = _runner(_job("flaky", sometimes))
    runner.tick(now=1.0)
    state["fail"] = False
    runner.tick(now=2.0)

    assert events == ["SCHEDULER_JOB_FAILED", "SCHEDULER_JOB_RECOVERED"]


def test_a_healthy_job_writes_no_events(monkeypatch):
    events = []
    import database_service
    monkeypatch.setattr(
        database_service, "insert_system_event",
        lambda event_type, **kw: events.append(event_type),
    )

    runner = _runner(_job("fine", lambda: {"status": "OK"}))
    runner.tick(now=1.0)
    runner.tick(now=2.0)

    assert events == []


def test_event_write_failure_does_not_break_the_tick(monkeypatch, caplog):
    """
    寫事件的時機正好是「有東西壞了」—— 那時候壞掉的很可能就是資料庫。
    一個因為記不了「排程壞了」而讓排程器掛掉的機制,比沒有更糟。
    """
    import database_service

    def boom(*a, **kw):
        raise RuntimeError("db is down")

    monkeypatch.setattr(database_service, "insert_system_event", boom)

    runner = _runner(_job("flaky", lambda: (_ for _ in ()).throw(RuntimeError("x"))))

    with caplog.at_level(logging.WARNING):
        ran = runner.tick(now=1.0)

    assert "error" in ran["flaky"]
    assert "EVENT_WRITE_FAILED" in caplog.text


# ---------------- 回測持久化 ----------------

def test_backtest_rows_are_stored_one_per_strategy_and_symbol(monkeypatch):
    from agmcis.lab import jobs

    stored = []
    import database_service
    monkeypatch.setattr(
        database_service, "insert_backtest",
        lambda run_id, metrics, **kw: stored.append((run_id, kw.get("strategy"), kw.get("symbol"))),
    )

    rows = [
        {"strategy": "live_pipeline", "symbol": "BTC/USDT", "verdict": "PASS",
         "is_live_pipeline": True},
        {"strategy": "live_pipeline", "symbol": "ETH/USDT", "verdict": "REJECT",
         "is_live_pipeline": True},
    ]

    result = jobs._persist_backtest("RUN1", rows, "1h", 1500, None, False)

    assert result["stored"] == 2
    assert [s[1:] for s in stored] == [
        ("live_pipeline", "BTC/USDT"), ("live_pipeline", "ETH/USDT"),
    ]
    assert len({s[0] for s in stored}) == 2, "run_id 必須逐列唯一"


def test_backtest_persistence_survives_a_row_failure(monkeypatch):
    from agmcis.lab import jobs

    import database_service

    def flaky(run_id, metrics, **kw):
        if kw.get("symbol") == "BTC/USDT":
            raise RuntimeError("constraint")
        return 1

    monkeypatch.setattr(database_service, "insert_backtest", flaky)

    result = jobs._persist_backtest("RUN2", [
        {"strategy": "s", "symbol": "BTC/USDT", "verdict": "PASS"},
        {"strategy": "s", "symbol": "ETH/USDT", "verdict": "PASS"},
    ], "1h", 100, None, False)

    assert result["stored"] == 1


def test_error_rows_still_get_a_unique_run_id(monkeypatch):
    """strategy 是 None 的那些列(整檔驗證失敗)也要存得下去。"""
    from agmcis.lab import jobs

    ids = []
    import database_service
    monkeypatch.setattr(
        database_service, "insert_backtest",
        lambda run_id, metrics, **kw: ids.append(run_id),
    )

    jobs._persist_backtest("RUN3", [
        {"strategy": None, "symbol": "BTC/USDT", "verdict": "ERROR"},
        {"strategy": None, "symbol": "ETH/USDT", "verdict": "ERROR"},
    ], "1h", 100, None, False)

    assert len(set(ids)) == 2


def test_synthetic_flag_is_carried_through(monkeypatch):
    """
    合成 K 棒跑出來的 PASS 只證明管線接得起來。它必須標記,
    否則會混進「這個策略通過過幾次」的統計。
    """
    from agmcis.lab import jobs

    seen = []
    import database_service
    monkeypatch.setattr(
        database_service, "insert_backtest",
        lambda run_id, metrics, **kw: seen.append(kw.get("synthetic")),
    )

    jobs._persist_backtest("RUN4", [{"strategy": "s", "symbol": "X", "verdict": "PASS"}],
                           "1h", 100, None, True)

    assert seen == [True]


# ---------------- 排程器有註冊這兩個工作 ----------------

def test_new_jobs_are_in_the_full_job_set():
    from agmcis.scheduling.runner import JOB_SET_ALL, build_jobs

    names = [j.name for j in build_jobs(JOB_SET_ALL)]
    assert "news_archive" in names
    assert "strategy_mirror" in names


# ---------------- 唯讀端點 ----------------

def test_endpoints_exist_and_are_all_get():
    """
    第六十四節的表要查得到才有意義。而它們全部是查詢 ——
    不是動作,所以全部是 GET。
    """
    from api import transparency

    paths = {}
    for route in transparency.router.routes:
        paths[route.path] = set(route.methods) - {"HEAD", "OPTIONS"}

    for path in ("/api/system_events", "/api/backtests",
                 "/api/news", "/api/strategies"):
        assert path in paths, f"{path} 不存在"
        assert paths[path] == {"GET"}, f"{path} 不該有 GET 以外的方法"


def test_backtests_defaults_to_excluding_synthetic(monkeypatch):
    from api import transparency

    captured = {}

    import database_service

    def fake(limit=20, strategy=None, include_synthetic=False):
        captured.update(locals())
        return []

    monkeypatch.setattr(database_service, "get_backtests", fake)

    result = transparency._backtests()
    assert captured["include_synthetic"] is False
    assert result["include_synthetic"] is False


def test_backtests_counts_live_pipeline_rows_separately(monkeypatch):
    """一個舊策略的 PASS 讀起來會跟 live 管線的 PASS 一樣,除非分開數。"""
    from api import transparency

    import database_service
    monkeypatch.setattr(database_service, "get_backtests", lambda **kw: [
        {"strategy": "live_pipeline", "config": {"is_live_pipeline": True}},
        {"strategy": "legacy", "config": {"is_live_pipeline": False}},
        {"strategy": "old", "config": None},
    ])

    assert transparency._backtests()["live_pipeline_count"] == 1


def test_strategies_endpoint_shows_effective_state_even_when_mirror_fails(monkeypatch):
    """
    鏡像讀不到不影響「現在生效的是什麼」—— 那一份是從檔案來的。
    """
    from api import transparency

    import database_service

    def boom():
        raise RuntimeError("relation \"strategies\" does not exist")

    monkeypatch.setattr(database_service, "get_strategies", boom)

    result = transparency._strategies()
    assert result["effective"], "檔案那一份必須還在"
    assert "strategies" in (result["mirror_error"] or "")


def test_strategies_endpoint_reports_a_stale_mirror(monkeypatch):
    """鏡像落後要看得見,不能只顯示其中一邊。"""
    from api import transparency

    monkeypatch.setattr(
        "agmcis.strategy.mirror.snapshot",
        lambda **kw: [
            {"name": "a", "status": "live"},
            {"name": "b", "status": "paused"},
            {"name": "c", "status": "paper"},
        ],
    )

    import database_service
    monkeypatch.setattr(database_service, "get_strategies", lambda: [
        {"name": "a", "status": "live"},
        {"name": "b", "status": "paper"},   # 落後:檔案已經 paused 了
    ])

    result = transparency._strategies()
    assert result["stale_in_mirror"] == ["b"]
    assert result["missing_from_mirror"] == ["c"]


def test_system_events_counts_errors(monkeypatch):
    from api import transparency

    import database_service
    monkeypatch.setattr(database_service, "get_system_events", lambda limit=50: [
        {"event_type": "SCHEDULER_JOB_FAILED", "severity": "ERROR"},
        {"event_type": "SCHEDULER_START", "severity": "INFO"},
        {"event_type": "X", "severity": "CRITICAL"},
    ])

    assert transparency._system_events()["error_count"] == 2


def test_news_endpoint_counts_shocks(monkeypatch):
    from api import transparency

    import database_service
    monkeypatch.setattr(database_service, "get_news", lambda limit=30: [
        {"title": "a", "impact": "SHOCK"},
        {"title": "b", "impact": None},
    ])

    assert transparency._news()["shock_count"] == 1


def test_endpoints_degrade_instead_of_raising(monkeypatch):
    """表還沒 migrate 的環境不該讓整個 API 掛掉。"""
    from api import transparency

    import database_service

    def boom(*a, **kw):
        raise RuntimeError("no such table")

    for name in ("get_system_events", "get_backtests", "get_news"):
        monkeypatch.setattr(database_service, name, boom)

    assert transparency._system_events()["events"] == []
    assert transparency._backtests()["backtests"] == []
    assert transparency._news()["news"] == []
