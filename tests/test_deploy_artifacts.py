"""
第八十二節列了十項要考慮的東西。

這一組測試盯的是那十項**都有交代**,而且交代的內容與系統的實際
行為一致 —— 一份與程式碼不符的部署設定,比沒有設定更危險:
照著它做會得到一個看起來設定好了、實際上是壞的系統。
"""
from pathlib import Path

import pytest

DEPLOY = Path("deploy")
README = (DEPLOY / "README.md").read_text(encoding="utf-8")
NGINX = (DEPLOY / "nginx.conf").read_text(encoding="utf-8")


# 第八十二節列的十項。
SECTION_82 = [
    "Docker", "Nginx", "HTTPS", "Process Manager", "Database",
    "Redis", "Worker", "WebSocket", "Monitoring", "Backup",
]


@pytest.mark.parametrize("item", SECTION_82)
def test_every_item_is_accounted_for(item):
    """
    「刻意不做」也是一種交代,但**沒提到**不是。
    """
    assert item in README, f"第八十二節的 {item} 在 deploy/README.md 裡沒有交代"


def test_the_artifacts_the_readme_promises_exist():
    for name in ("nginx.conf", "HTTPS.md", "MONITORING.md",
                 "agmcis.service", "agmcis-scheduler.service",
                 "agmcis-backup.service", "agmcis-backup.timer"):
        assert (DEPLOY / name).exists(), f"README 提到的 {name} 不存在"


def test_docker_artifacts_exist():
    for name in ("Dockerfile", "docker-compose.yml",
                 "docker-compose.staging.yml"):
        assert Path(name).exists()


def test_redis_is_refused_with_a_reason():
    """
    第八十二節說 Redis「如果必要」。說不必要要說得出為什麼,
    而且要說出**什麼時候會變成必要**。
    """
    assert "刻意不用" in README
    assert "shared_rate_limit" in README
    assert "門檻" in README


# ---------------- nginx 與程式碼一致 ----------------

def test_nginx_points_at_the_port_uvicorn_actually_uses():
    """
    設定檔與程式碼不一致的話,照著做會得到一個 502。
    """
    service = (DEPLOY / "agmcis.service").read_text(encoding="utf-8")

    assert "--port 8000" in service
    assert "127.0.0.1:8000" in NGINX


def test_uvicorn_only_binds_localhost():
    """
    綁 0.0.0.0 的話,任何人知道 IP 就繞過 nginx 直接打 API,
    而限流與 HTTPS 就都沒有意義了。
    """
    service = (DEPLOY / "agmcis.service").read_text(encoding="utf-8")

    assert "--host 127.0.0.1" in service
    assert "--host 0.0.0.0" not in service


def test_health_is_not_rate_limited():
    """
    監控會固定頻率打它。把它算進限流的話,監控自己會被擋,
    然後你會收到一封「系統掛了」的通知,而系統是好的。
    """
    import re

    block = re.search(r"location = /health \{(.*?)\n    \}", NGINX, re.S)
    assert block is not None
    assert "limit_req" not in block.group(1)


def test_the_acme_challenge_path_is_not_redirected():
    """
    把它也導到 HTTPS 的話,憑證更新會永遠失敗 ——
    而失敗發生在三個月後,沒有人記得為什麼。
    """
    assert ".well-known/acme-challenge" in NGINX


def test_the_websocket_location_has_the_upgrade_headers():
    """
    少一個 header 就會變成一個永遠在重連的連線,
    而那看起來跟正常運作一樣。
    """
    import re

    block = re.search(r"location /ws \{(.*?)\n    \}", NGINX, re.S)
    assert block is not None

    body = block.group(1)
    assert "Upgrade $http_upgrade" in body
    assert 'Connection "upgrade"' in body
    # 行情連線會安靜很久,預設 60 秒會把它砍掉
    assert "proxy_read_timeout" in body


def test_login_is_rate_limited_harder_than_the_rest():
    """登入是唯一會驗金鑰的端點。"""
    assert "zone=agmcis_login" in NGINX
    assert "rate=1r/s" in NGINX


# ---------------- systemd ----------------

def test_no_service_file_contains_a_secret():
    """service 檔會進版控,.env 不會(第十 / 八十四節)。"""
    for path in DEPLOY.glob("*.service"):
        text = path.read_text(encoding="utf-8")

        assert "EnvironmentFile=" in text, f"{path.name} 應該用 EnvironmentFile"
        for forbidden in ("BINGX_API_KEY=", "BINGX_API_SECRET=",
                          "DB_PASSWORD=", "DASHBOARD_KEY="):
            assert forbidden not in text, f"{path.name} 有金鑰"


def test_no_service_runs_as_root():
    for path in DEPLOY.glob("*.service"):
        text = path.read_text(encoding="utf-8")
        assert "User=agmcis" in text, f"{path.name} 沒有指定非 root 使用者"


def test_services_stop_after_repeated_failures():
    """
    無限重啟會讓一個設定錯誤變成一份寫滿同一行的 log,
    而真正的原因在最上面那一行。
    """
    for name in ("agmcis.service", "agmcis-scheduler.service"):
        text = (DEPLOY / name).read_text(encoding="utf-8")
        assert "StartLimitBurst=" in text, f"{name} 會無限重啟"


def test_the_scheduler_is_a_single_instance():
    """
    排程器會開倉、會平倉。跑兩份的話同一筆停損會被檢查兩次,
    而唯一索引擋得住重複開倉,擋不住重複平倉。
    """
    text = (DEPLOY / "agmcis-scheduler.service").read_text(encoding="utf-8")

    assert "--workers" not in text
    assert "只能有一個" in text or "不要用 systemd 的 template" in text


def test_the_scheduler_command_matches_the_module():
    """
    ExecStart 指到一個不存在的模組,服務會起不來 ——
    而那要等到部署當下才會發現。
    """
    text = (DEPLOY / "agmcis-scheduler.service").read_text(encoding="utf-8")

    assert "-m agmcis.scheduling.runner" in text
    assert Path("agmcis/scheduling/runner.py").exists()

    import agmcis.scheduling.runner as runner
    assert hasattr(runner, "run_scheduler")


def test_the_backup_timer_catches_up_after_downtime():
    """機器關過機的話要補跑。少一天的備份是真的少了一天。"""
    text = (DEPLOY / "agmcis-backup.timer").read_text(encoding="utf-8")
    assert "Persistent=true" in text


def test_the_backup_service_runs_the_script_that_exists():
    text = (DEPLOY / "agmcis-backup.service").read_text(encoding="utf-8")

    assert "backup_system.py" in text
    assert Path("backup_system.py").exists()

    import backup_system
    assert hasattr(backup_system, "run_backup")


# ---------------- 監控文件與實際端點一致 ----------------

def test_the_monitoring_doc_names_endpoints_that_exist():
    import main

    text = (DEPLOY / "MONITORING.md").read_text(encoding="utf-8")
    paths = set(main.app.openapi()["paths"])

    for endpoint in ("/health", "/api/system_events", "/api/positions",
                     "/api/reconciliation", "/api/orders"):
        assert endpoint in text, f"監控文件沒有提到 {endpoint}"
        assert endpoint in paths, f"監控文件提到的 {endpoint} 不存在"


def test_the_monitoring_doc_explains_degraded():
    """
    degraded 不會讓狀態碼變。只盯狀態碼的監控會漏掉它。
    """
    text = (DEPLOY / "MONITORING.md").read_text(encoding="utf-8")

    assert "degraded" in text
    assert "503" in text
    assert "naked_count" in text


def test_the_readme_states_it_does_not_apply_itself():
    """
    第八十二節:不要破壞目前正在運作的 Production。
    """
    assert "不會自己套用" in README
