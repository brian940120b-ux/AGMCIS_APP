"""
第九十一節(四種模式要非常清楚)與第九十二節(LIVE 七項確認)。

最重要的一條:**這個網頁流程不會讓系統開始下實單。**
它產生的確認檔只是 LIVE SAFETY GATE 其中一項檢查,而且
LiveBroker 不存在 —— 最壞的情況是磁碟上多了一個檔案。
其餘的測試都在盯那個「最壞情況」真的就是最壞。
"""
import json
from unittest.mock import patch

import pytest

import api.live_confirmation as api_confirm
from agmcis.config import modes
from agmcis.safety import live_confirm
from agmcis.safety import live_gate as gate_module


class FakeSettings:
    TRADING_MODE = "paper"
    AUTO_TRADING_ENABLED = True
    EXCHANGE_USE_TESTNET = False

    def __init__(self, **overrides):
        for key, value in overrides.items():
            setattr(self, key, value)


# ---------------- 第九十一節:四種模式 ----------------

@pytest.mark.parametrize("flags,expected", [
    ({"TRADING_MODE": "paper", "AUTO_TRADING_ENABLED": False}, modes.MANUAL),
    ({"TRADING_MODE": "paper", "AUTO_TRADING_ENABLED": True}, modes.PAPER),
    ({"EXCHANGE_USE_TESTNET": True}, modes.TEST),
    ({"TRADING_MODE": "live"}, modes.LIVE),
])
def test_each_mode_is_derivable(flags, expected):
    assert modes.current(FakeSettings(**flags)) == expected


def test_test_beats_live():
    """
    TRADING_MODE=live 加上測試環境是 TEST,不是 LIVE ——
    **沒有真錢在動。**

    反過來判定的話,測試網上的系統會一直顯示紅色警告,而使用者會
    學會忽略那個警告 —— 然後在真的 LIVE 的那一天,它看起來一模一樣。
    """
    fake = FakeSettings(TRADING_MODE="live", EXCHANGE_USE_TESTNET=True)

    assert modes.current(fake) == modes.TEST
    assert modes.is_real_money(fake) is False


def test_only_live_is_real_money():
    for name in modes.ORDER:
        expected = name == modes.LIVE
        assert (name in modes.REAL_MONEY) is expected


def test_every_mode_says_what_happens_automatically():
    """模式的名字本身沒有資訊。使用者要知道的是什麼會自動發生。"""
    for name in modes.ORDER:
        assert modes.DESCRIPTIONS[name].strip()


def test_the_snapshot_lists_all_four_and_marks_the_current_one():
    """
    只顯示現在這一個的話,使用者看不出「我以為在 PAPER 但其實在 MANUAL」。
    """
    payload = modes.snapshot(FakeSettings(AUTO_TRADING_ENABLED=False))

    assert [m["mode"] for m in payload["all_modes"]] == list(modes.ORDER)
    assert [m["mode"] for m in payload["all_modes"] if m["current"]] == [modes.MANUAL]


def test_the_snapshot_shows_what_it_derived_from():
    """
    「為什麼是這個模式」比「是哪個模式」更常是使用者真正的問題。
    """
    payload = modes.snapshot(FakeSettings())

    assert set(payload["derived_from"]) == {
        "TRADING_MODE", "AUTO_TRADING", "EXCHANGE_USE_TESTNET",
    }


def test_mode_is_not_a_stored_setting():
    """
    再加一個 MODE= 設定會變成第四個真相來源,而四個之中只要有一個
    不一致,畫面上顯示的模式就會是錯的。
    """
    from agmcis.config import settings

    assert not hasattr(settings, "MODE")
    assert not hasattr(settings, "TRADING_MODE_UI")


# ---------------- 第九十二節:七項確認 ----------------

def _payload(**overrides):
    body = {
        "confirmations": {name: True for name in live_confirm.REQUIRED_ITEMS},
        "approved_notional_usdt": 20,
        "phrase": gate_module.REQUIRED_PHRASE,
        "fingerprint": live_confirm.fingerprint(),
    }
    body.update(overrides)
    return body


def _no_live_code():
    """
    假裝系統裡沒有實單程式碼。

    網頁精靈遇到實單程式碼會拒絕(見下面 TestTheWebWizardCannotSignLiveCode)。
    那個拒絕是對的,但它會蓋掉這一組要驗的其他事 —— 七項確認、金額、
    確認句、寫檔格式 —— 所以這裡把它拿掉,讓每一組只驗一件事。
    """
    return patch.object(api_confirm, "_live_path_blocked", lambda: (True, ""))


def test_a_complete_submission_writes_the_file(tmp_path):
    target = tmp_path / "conf.json"

    with _no_live_code():
        ok, message = api_confirm.submit(_payload(), path=str(target))

    assert ok is True, message
    data = json.loads(target.read_text(encoding="utf-8"))

    assert data["phrase"] == gate_module.REQUIRED_PHRASE
    assert data["approved_notional_usdt"] == 20.0
    assert set(data["confirmations"]) == set(live_confirm.REQUIRED_ITEMS)
    assert data["settings_fingerprint"] == live_confirm.fingerprint()


def test_the_web_file_passes_the_same_gate_check_as_the_cli(tmp_path):
    """
    網頁與 CLI 走同一個 live_confirm —— 四道保護一個都沒少。
    兩條路徑產生不同格式的檔案,遲早會有一條沒被驗到。
    """
    target = tmp_path / "conf.json"
    with _no_live_code():
        api_confirm.submit(_payload(), path=str(target))

    data = json.loads(target.read_text(encoding="utf-8"))
    ok, detail = live_confirm.verify(data)

    assert ok is True, detail


def test_it_records_that_it_came_from_the_web(tmp_path):
    """
    事後檢討時「是誰在什麼情境下批准的」很重要,
    而網頁與 CLI 的情境不一樣。
    """
    target = tmp_path / "conf.json"
    with _no_live_code():
        api_confirm.submit(_payload(), path=str(target))

    assert json.loads(target.read_text(encoding="utf-8"))["signed_via"] == "web"


class TestTheWebWizardCannotSignLiveCode:
    """
    第七十八節要的是「有人讀過那段會送真實訂單的程式碼」。
    一個網頁按鈕表達不了那件事 —— 它只證明有人點過一個按鈕。
    """

    def test_it_refuses_while_live_code_exists(self, tmp_path):
        target = tmp_path / "conf.json"
        found = [("agmcis/execution/live_broker.py", "LiveBroker", "類別名稱含 live")]

        with patch.object(
            gate_module.LiveGate, "_scan_live_broker_sources", lambda self: found,
        ):
            ok, message = api_confirm.submit(_payload(), path=str(target))

        assert ok is False
        assert "live_confirm.py" in message
        assert not target.exists()

    def test_a_scan_it_cannot_run_also_refuses(self, tmp_path):
        """掃不動不等於沒有。"""
        target = tmp_path / "conf.json"

        def boom(self):
            raise OSError("掃不動")

        with patch.object(
            gate_module.LiveGate, "_scan_live_broker_sources", boom,
        ):
            ok, message = api_confirm.submit(_payload(), path=str(target))

        assert ok is False
        assert "掃不動" in message
        assert not target.exists()

    def test_the_real_system_state_decides(self, tmp_path):
        """
        沒有 patch 的時候,結果要跟這個專案當下的實際狀態一致 ——
        有實單程式碼就拒絕,沒有就放行。
        """
        target = tmp_path / "conf.json"
        has_live_code = bool(gate_module.LiveGate()._scan_live_broker_sources())

        ok, _ = api_confirm.submit(_payload(), path=str(target))

        assert ok is not has_live_code


@pytest.mark.parametrize("overrides,fragment", [
    ({"confirmations": {}}, "沒有確認"),
    ({"confirmations": None}, "七項"),
    ({"approved_notional_usdt": 0}, "大於 0"),
    ({"approved_notional_usdt": "abc"}, "不是數字"),
    ({"phrase": "i understand live trading risk"}, "確認句不符"),
    ({"phrase": "I UNDERSTAND LIVE TRADING RISK "}, "確認句不符"),
])
def test_every_rejection_writes_nothing(tmp_path, overrides, fragment):
    """一份寫到一半的確認檔比沒有確認檔危險。"""
    target = tmp_path / "conf.json"

    ok, message = api_confirm.submit(_payload(**overrides), path=str(target))

    assert ok is False
    assert fragment in message
    assert not target.exists()


def test_one_missing_item_rejects_the_whole_thing(tmp_path):
    target = tmp_path / "conf.json"

    confirmations = {n: True for n in live_confirm.REQUIRED_ITEMS}
    confirmations[live_confirm.ITEM_API] = False

    ok, message = api_confirm.submit(
        _payload(confirmations=confirmations), path=str(target))

    assert ok is False
    assert "API" in message
    assert not target.exists()


def test_an_amount_over_the_first_live_cap_is_refused(tmp_path):
    target = tmp_path / "conf.json"
    over = gate_module.MAX_INITIAL_NOTIONAL_USDT + 1

    ok, message = api_confirm.submit(
        _payload(approved_notional_usdt=over), path=str(target))

    assert ok is False
    assert "上限" in message
    assert not target.exists()


def test_settings_changed_while_the_user_was_confirming_is_refused(tmp_path):
    """
    使用者在看那個畫面的期間設定被改過 —— 他確認的是舊的那一組值。
    這一條擋的是一個很窄但很真實的競態。
    """
    target = tmp_path / "conf.json"

    ok, message = api_confirm.submit(
        _payload(fingerprint="不是現在這一個"), path=str(target))

    assert ok is False
    assert "被改過" in message
    assert not target.exists()


def test_the_phrase_is_compared_exactly(tmp_path):
    """
    一個接受近似輸入的確認句等於沒有確認句。
    """
    target = tmp_path / "conf.json"

    for wrong in (gate_module.REQUIRED_PHRASE.lower(),
                  gate_module.REQUIRED_PHRASE + ".",
                  " " + gate_module.REQUIRED_PHRASE):
        ok, _ = api_confirm.submit(_payload(phrase=wrong), path=str(target))
        assert ok is False, f"{wrong!r} 不該被接受"

    assert not target.exists()


# ---------------- 它不會讓系統下實單 ----------------

def test_submitting_does_not_touch_the_trading_mode(tmp_path):
    """
    這個端點寫確認檔,**不切換模式**。切換模式是另一件事,
    而且需要 LIVE SAFETY GATE 的其他檢查都通過。
    """
    from agmcis.config import settings

    before = settings.TRADING_MODE
    api_confirm.submit(_payload(), path=str(tmp_path / "conf.json"))

    assert settings.TRADING_MODE == before


def test_submitting_never_reaches_the_execution_engine(tmp_path):
    with patch("agmcis.execution.engine.get_engine") as engine:
        api_confirm.submit(_payload(), path=str(tmp_path / "conf.json"))

    engine.assert_not_called()


def test_the_module_cannot_send_orders():
    """
    靜態保證。這一頁是整個系統裡最靠近「開始用真錢」的地方,
    所以它的原始碼裡不該有任何下單的痕跡。
    """
    from pathlib import Path

    source = Path("api/live_confirmation.py").read_text(encoding="utf-8")

    for forbidden in ("create_order", "execute(", "get_engine",
                      "TRADING_MODE =", "setattr("):
        assert forbidden not in source, f"確認端點不該碰 {forbidden}"


def test_there_is_still_no_live_broker():
    """
    確認檔簽了也送不出單。這一條在別的測試檔也有,放這裡是因為
    **這一頁的安全論述整個建立在它上面**。
    """
    import agmcis.execution.broker as broker

    names = [n for n in dir(broker) if n.endswith("Broker")]
    assert "LiveBroker" not in names


# ---------------- 端點 ----------------

def test_the_endpoints_are_registered_with_the_right_methods():
    import main

    spec = main.app.openapi()

    assert sorted(spec["paths"]["/api/live_confirmation"]) == ["get", "post"]
    assert sorted(spec["paths"]["/api/trading_mode"]) == ["get"]


def test_the_modes_page_exists():
    import main

    assert "/modes" in {getattr(r, "path", None) for r in main.app.routes}


def test_the_get_endpoint_never_leaks_the_secret():
    from agmcis.config import settings

    payload = api_confirm.api_live_confirmation()
    blob = json.dumps(payload, ensure_ascii=False, default=str)

    secret = settings.EXCHANGE_CREDENTIALS.get("secret")
    if secret:
        assert secret not in blob

    key = settings.EXCHANGE_CREDENTIALS.get("apiKey")
    if key and len(key) > 4:
        assert key not in blob


def test_an_unreadable_existing_file_is_not_reported_as_valid(tmp_path):
    """讀不到就回 None 或錯誤,**不回「有效」** —— 那個方向是錯的。"""
    broken = tmp_path / "conf.json"
    broken.write_text("{ not json", encoding="utf-8")

    with patch.object(gate_module, "CONFIRMATION_FILE", str(broken)):
        existing = api_confirm._existing()

    assert existing["readable"] is False


# ---------------- 首頁與模式頁不能各自判斷 ----------------

def test_the_landing_page_and_the_modes_page_agree():
    """
    首頁原本自己寫 `TRADING_MODE == "live"`,只認得 PAPER 與 LIVE。
    一個 AUTO_TRADING=false 的系統會在首頁顯示 PAPER、在模式頁顯示
    MANUAL —— 而首頁是第一百零五節說的「第一眼」。

    同一件事在兩個畫面上不一樣,使用者會不知道該相信哪一個。
    """
    from unittest.mock import patch

    from api import overview
    from agmcis.config import settings

    with patch.object(settings, "AUTO_TRADING_ENABLED", False), \
         patch("api.health.build_health",
               return_value={"status": "healthy", "components": {}}):
        status = overview._status()
        # 兩邊要在**同一組設定下**比較 —— 拉到 with 外面比的話,
        # modes.current() 看到的是還原後的設定。
        from_modes_page = modes.current()

    assert status["mode"] == modes.MANUAL
    assert status["mode"] == from_modes_page


def test_the_landing_page_does_not_judge_the_mode_itself():
    """靜態保證:模式的判斷只有一份。"""
    from pathlib import Path

    source = Path("api/overview.py").read_text(encoding="utf-8")

    assert "modes.snapshot()" in source
    # 不再自己比對 TRADING_MODE
    assert 'mode == "live"' not in source
    assert 'TRADING_MODE", "paper")).lower()' not in source


def test_test_mode_is_not_shown_as_real_money_on_the_landing_page():
    """
    測試網送的是真訂單但不是真錢。首頁的紅色警告留給真的 LIVE ——
    看習慣了就沒有用了。
    """
    from unittest.mock import patch

    from api import overview
    from agmcis.config import settings

    with patch.object(settings, "EXCHANGE_USE_TESTNET", True), \
         patch.object(settings, "TRADING_MODE", "live"), \
         patch("api.health.build_health",
               return_value={"status": "healthy", "components": {}}):
        status = overview._status()

    assert status["mode"] == modes.TEST
    assert status["is_live"] is False


def test_the_landing_page_carries_the_mode_description():
    """模式的名字本身沒有資訊。使用者要知道什麼會自動發生。"""
    from unittest.mock import patch

    from api import overview

    with patch("api.health.build_health",
               return_value={"status": "healthy", "components": {}}):
        status = overview._status()

    assert status["mode_description"]
    assert len(status["all_modes"]) == 4
