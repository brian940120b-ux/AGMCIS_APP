"""
第八十二 / 八十三節:部署產物。

這一組測試盯的只有一件事:**staging 不能下實單。**

一個設定成 live 的 staging 不是測試環境,是第二個 production ——
而且是一個沒有人在看的 production。compose 檔是純設定,沒有測試的話
一次貼上錯誤就會安靜地生效。
"""
from pathlib import Path

import pytest
import yaml


def _load(name):
    return yaml.safe_load(Path(name).read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def staging():
    return _load("docker-compose.staging.yml")


@pytest.fixture(scope="module")
def base():
    return _load("docker-compose.yml")


# ---------------- staging 不能下實單 ----------------

@pytest.mark.parametrize("service", ["web", "scheduler"])
def test_staging_pins_paper_mode(staging, service):
    """
    不是「預設 paper」,是**寫死 paper**。預設值會被 .env 蓋掉,
    而 staging 與 production 共用一台機器時,那個 .env 是同一份。
    """
    env = staging["services"][service]["environment"]
    assert env["TRADING_MODE"] == "paper"


@pytest.mark.parametrize("service", ["web", "scheduler"])
def test_staging_turns_auto_trading_off(staging, service):
    env = staging["services"][service]["environment"]
    assert str(env["AUTO_TRADING"]).lower() == "false"


@pytest.mark.parametrize("service", ["web", "scheduler"])
def test_staging_declares_itself_as_staging(staging, service):
    """
    APP_ENV=staging 會走 IS_PROTECTED_ENV 分支,所以寬鬆的預設值
    在這裡就會被擋下來,而不是等到 production 才發現。
    """
    assert staging["services"][service]["environment"]["APP_ENV"] == "staging"


def test_staging_uses_its_own_database_volume(staging):
    """
    共用 production 資料庫的 staging 不是 staging,是第二個 production。
    """
    volumes = staging["services"]["db"]["volumes"]
    assert any("staging" in str(v) for v in volumes)
    assert "agmcis-staging-db" in staging["volumes"]


def test_staging_does_not_expose_the_database(staging):
    """開發用的 compose 也不對外開資料庫,staging 更不該。"""
    assert staging["services"]["db"]["ports"] == []


def test_staging_and_production_can_coexist_on_one_host(staging, base):
    """埠不同,所以在同一台機器上起 staging 不會撞到 production。"""
    staging_ports = staging["services"]["web"]["ports"]
    base_ports = base["services"]["web"]["ports"]

    assert staging_ports != base_ports
    # 而且一樣只綁 localhost —— 交易系統不對外開埠。
    assert all(str(p).startswith("127.0.0.1:") for p in staging_ports)


# ---------------- 金鑰 ----------------

def test_no_compose_file_contains_a_secret():
    """
    compose 檔會進 Git(第十 / 八十四節)。金鑰走 env_file。
    """
    for name in ("docker-compose.yml", "docker-compose.staging.yml"):
        text = Path(name).read_text(encoding="utf-8")
        for forbidden in ("BINGX_API_KEY:", "BINGX_API_SECRET:",
                          "DASHBOARD_KEY:", "TELEGRAM_BOT_TOKEN:"):
            assert forbidden not in text, f"{name} 不該出現 {forbidden}"


def test_the_database_password_has_no_default(base):
    """
    一個有預設密碼的資料庫遲早會帶著那個密碼上線。
    """
    password = base["services"]["db"]["environment"]["POSTGRES_PASSWORD"]
    assert ":?" in str(password), "DB_PASSWORD 必須是沒有預設值的必填變數"


def test_the_image_does_not_run_as_root():
    """
    一個以 root 跑的交易程式,任何遠端執行漏洞都會變成整台機器的問題。
    """
    dockerfile = Path("Dockerfile").read_text(encoding="utf-8")
    assert "USER agmcis" in dockerfile


# ---------------- 文件 ----------------

def test_the_operator_list_never_asks_for_the_api_key():
    """
    金鑰只該存在 VPS 的 .env 裡。一份要求使用者把金鑰貼出來的
    操作手冊,是這整套保護裡最容易被繞過的一環。
    """
    text = Path("docs/OPERATOR_ACTIONS.md").read_text(encoding="utf-8")

    assert "不要把 API 金鑰貼進對話" in text
    for forbidden in ("貼上你的 API", "提供金鑰", "把 secret 給我"):
        assert forbidden not in text


def test_the_operator_list_covers_every_thing_that_needs_authorisation():
    text = Path("docs/OPERATOR_ACTIONS.md").read_text(encoding="utf-8")

    for topic in ("Phase 18", "Phase 19", "Phase 20",
                  "I UNDERSTAND LIVE TRADING RISK",
                  "live_confirm.py", "live_gate.py",
                  "run_strategy_lab.py", "tune_exits.py",
                  "verify_bingx.py", "migrate.py"):
        assert topic in text, f"操作清單漏了 {topic}"


def test_the_deployment_doc_states_the_migration_ordering():
    """
    新程式碼配舊 schema 會炸;舊程式碼配新 schema 通常沒事。
    那個不對稱決定了順序,而順序寫錯的代價是一次線上故障。
    """
    text = Path("docs/DEPLOYMENT.md").read_text(encoding="utf-8")
    assert "先跑再重啟" in text


def test_the_deployment_doc_says_production_is_still_systemd():
    """
    第八十二節要 Docker,但同一節也說不要破壞正在運作的 Production。
    那是刻意偏離,不是還沒做完 —— 文件必須說出來。
    """
    text = Path("docs/DEPLOYMENT.md").read_text(encoding="utf-8")
    assert "systemd" in text
    assert "刻意偏離" in text
