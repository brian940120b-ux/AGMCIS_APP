"""
第五十六節(出場參數必須由回測驗證)與第九十二節(LIVE 七項確認)。

兩個東西的共同點:它們都是**把一個判斷從「憑印象」變成「有依據」**
的工具,而兩個都不會自己套用結果。
"""
import importlib.util
import json
import logging
from pathlib import Path
from unittest.mock import patch

import pytest

from agmcis.lab import exit_tuning
from agmcis.safety import live_confirm
from agmcis.safety import live_gate as lg


# ---------------- 第五十六節:出場參數調校 ----------------

class FakeMetrics:
    def __init__(self, trades=50, expectancy_r=0.1, profit_factor=1.4,
                 win_rate=55.0, total_return_pct=12.0, max_drawdown_pct=8.0):
        self.total_trades = trades
        self.expectancy_r = expectancy_r
        self.profit_factor = profit_factor
        self.win_rate = win_rate
        self.total_return_pct = total_return_pct
        self.max_drawdown_pct = max_drawdown_pct


def _run(metrics_by_label, trailing_options=None, stage_options=None):
    """
    用一張「組合名稱 -> 指標」的表跑調校。

    回測本身被替換掉 —— 這一組測試驗的是**結論怎麼下**,
    不是回測算得對不對(那是 test_backtest 的事)。
    """
    trailing_options = trailing_options if trailing_options is not None else (
        None, {"mode": "percent", "gap_pct": 3.0},
    )
    stage_options = stage_options if stage_options is not None else (
        None, ((1.0, 0.5), (2.0, 0.5)),
    )

    calls = []

    def run_backtest(trailing, stages):
        calls.append((trailing, stages))
        return object()

    def compute(result):
        trailing, stages = calls[-1]
        return metrics_by_label(trailing, stages)

    with patch("agmcis.backtest.metrics.compute", side_effect=compute):
        return exit_tuning.run(
            run_backtest,
            trailing_options=trailing_options,
            stage_options=stage_options,
        )


def test_every_combination_is_tried():
    report = _run(lambda t, s: FakeMetrics())
    assert len(report.variants) == 4


def test_a_baseline_of_doing_nothing_is_always_reported():
    """
    沒有基準線就無法回答「加了移動停損到底有沒有比較好」。
    """
    report = _run(lambda t, s: FakeMetrics())

    assert report.baseline is not None
    assert report.baseline["trailing"] is None
    assert report.baseline["stages"] is None


def test_thin_samples_do_not_win():
    """
    在 40 筆交易上挑出最好的一組參數,挑到的是那 40 筆的噪音。
    """
    def metrics(trailing, stages):
        if trailing is None and stages is None:
            return FakeMetrics(trades=200, expectancy_r=0.05)
        # 樣本很少但看起來很好
        return FakeMetrics(trades=5, expectancy_r=2.0)

    report = _run(metrics)

    assert report.best["expectancy_r"] == 0.05
    assert report.best["trades"] == 200


def test_no_reliable_variant_means_no_conclusion():
    report = _run(lambda t, s: FakeMetrics(trades=3))

    assert report.verdict == exit_tuning.VERDICT_INSUFFICIENT
    assert report.best is None
    assert any("噪音" in w for w in report.warnings)


def test_a_rugged_parameter_surface_is_called_out():
    """
    最佳明顯優於中位數 = 過度擬合的訊號,不是「找到更好的參數」。
    """
    def metrics(trailing, stages):
        if trailing and stages:
            return FakeMetrics(expectancy_r=1.0)
        return FakeMetrics(expectancy_r=0.01)

    report = _run(metrics)

    assert report.verdict == exit_tuning.VERDICT_SENSITIVE
    assert report.spread > exit_tuning.FLAT_SPREAD
    assert any("過度擬合" in w for w in report.warnings)


def test_a_flat_parameter_surface_is_robust():
    report = _run(lambda t, s: FakeMetrics(expectancy_r=0.1))

    assert report.verdict == exit_tuning.VERDICT_ROBUST
    assert report.spread == pytest.approx(0.0)


def test_it_says_so_when_nothing_beats_doing_nothing():
    def metrics(trailing, stages):
        if trailing is None and stages is None:
            return FakeMetrics(expectancy_r=0.5)
        return FakeMetrics(expectancy_r=0.4)

    report = _run(metrics)

    assert any("基準線" in w for w in report.warnings)


def test_one_failing_combination_does_not_stop_the_others(caplog):
    """
    悄悄跳過失敗的組合,會讓「這一組沒出現」被讀成「這一組比較差」。
    """
    def run_backtest(trailing, stages):
        if trailing is not None and stages is not None:
            raise RuntimeError("boom")
        return object()

    with patch("agmcis.backtest.metrics.compute", return_value=FakeMetrics()), \
         caplog.at_level(logging.ERROR):
        report = exit_tuning.run(
            run_backtest,
            trailing_options=(None, {"mode": "percent", "gap_pct": 3.0}),
            stage_options=(None, ((1.0, 1.0),)),
        )

    assert len(report.variants) == 4
    failed = [v for v in report.variants if v.error]
    assert len(failed) == 1
    assert "boom" in failed[0].error
    assert any("回測失敗" in w for w in report.warnings)


def test_an_unmeasurable_expectancy_is_none_not_zero():
    """
    0 代表打平,None 代表沒有答案。前者可以排名,後者不行。
    """
    report = _run(lambda t, s: FakeMetrics(expectancy_r=None))

    assert all(v.expectancy_r is None for v in report.variants)
    assert all(not v.reliable for v in report.variants)
    assert report.verdict == exit_tuning.VERDICT_INSUFFICIENT


def test_the_min_trades_threshold_matches_attribution():
    """
    兩邊用不同的門檻,會讓同一批交易在兩份報告裡一份可信一份不可信。
    """
    from agmcis.review.attribution import MIN_SAMPLE

    assert exit_tuning.MIN_TRADES == MIN_SAMPLE


def test_the_tuning_script_does_not_apply_its_own_findings():
    """
    第七十八節:自己修改 → 自己測試 → 自己批准,這條鏈不成立。
    """
    source = Path("scripts/tune_exits.py").read_text(encoding="utf-8")

    for forbidden in ("DEFAULT_TARGETS =", "exit_plan.DEFAULT",
                      "settings.MAX_RISK", "write_text(\"DEFAULT"):
        assert forbidden not in source, f"調校腳本不該寫入 {forbidden}"

    assert "提案流程" in source


# ---------------- 第九十二節:七項確認 ----------------

def test_all_seven_items_from_section_92_are_present():
    items = live_confirm.build_items()
    names = [entry["item"] for entry in items]

    assert names == list(live_confirm.REQUIRED_ITEMS)
    assert len(names) == 7


def test_no_api_secret_anywhere_in_the_items():
    """
    第十 / 八十四節:金鑰不得出現在任何檔案、log 或紀錄裡。
    確認檔會被寫到磁碟上,所以這一條在這裡特別重要。
    """
    fake_key = "REALKEY1234567890"
    fake_secret = "REALSECRET0987654321"

    class FakeSettings:
        APP_ENV = "production"
        TRADING_MODE = "live"
        EXCHANGE = "bingx"
        MARKET_TYPE = "swap"
        EXCHANGE_USE_TESTNET = False
        ALLOW_PUBLIC_DATA_ONLY = False
        WATCHLIST_SYMBOLS = ["BTC/USDT"]
        EXCHANGE_CREDENTIALS = {"apiKey": fake_key, "secret": fake_secret}

        @staticmethod
        def has_exchange_credentials():
            return True

        @staticmethod
        def risk_limits_dict():
            from agmcis.config import settings
            return settings.risk_limits_dict()

    blob = json.dumps(live_confirm.build_items(FakeSettings), ensure_ascii=False)

    assert fake_secret not in blob
    assert fake_key not in blob
    # 後四碼可以有 —— 它不足以還原金鑰,但足以確認是哪一把。
    assert fake_key[-4:] in blob


def test_a_confirmation_missing_an_item_is_rejected():
    data = {
        "confirmations": {name: True for name in live_confirm.REQUIRED_ITEMS
                          if name != live_confirm.ITEM_DAILY_LOSS},
        "settings_fingerprint": live_confirm.fingerprint(),
    }
    ok, detail = live_confirm.verify(data)

    assert ok is False
    assert "Daily Loss" in detail


def test_an_old_style_confirmation_without_the_items_is_rejected():
    """
    只檢查那一句話,等於使用者可以在不知道自己批准了什麼設定的
    情況下簽名。舊格式失效的方向是對的。
    """
    ok, detail = live_confirm.verify({"phrase": lg.REQUIRED_PHRASE})

    assert ok is False
    assert "confirmations" in detail


def test_changing_a_risk_limit_after_signing_voids_the_confirmation():
    """
    12:00 確認「日虧損 20 USDT」→ 13:00 有人改成 500 →
    14:00 簽名還在有效期內。放行那一次是錯的。
    """
    from agmcis.config import settings

    signed = {
        "confirmations": {name: True for name in live_confirm.REQUIRED_ITEMS},
        "settings_fingerprint": live_confirm.fingerprint(),
    }
    assert live_confirm.verify(signed)[0] is True

    with patch.object(settings, "MAX_DAILY_LOSS_USDT", 500.0):
        ok, detail = live_confirm.verify(signed)

    assert ok is False
    assert "設定在簽署之後被改過" in detail


def test_switching_environment_after_signing_voids_the_confirmation():
    from agmcis.config import settings

    signed = {
        "confirmations": {name: True for name in live_confirm.REQUIRED_ITEMS},
        "settings_fingerprint": live_confirm.fingerprint(),
    }

    with patch.object(settings, "APP_ENV", settings.ENV_PRODUCTION):
        ok, _ = live_confirm.verify(signed)

    assert ok is False


def test_a_confirmation_without_a_fingerprint_is_rejected():
    data = {"confirmations": {n: True for n in live_confirm.REQUIRED_ITEMS}}
    ok, detail = live_confirm.verify(data)

    assert ok is False
    assert "fingerprint" in detail


def test_a_false_confirmation_is_not_a_confirmation():
    data = {
        "confirmations": dict(
            {name: True for name in live_confirm.REQUIRED_ITEMS},
            api=False,
        ),
        "settings_fingerprint": live_confirm.fingerprint(),
    }
    assert live_confirm.verify(data)[0] is False


# ---------------- 精靈 ----------------

def _wizard():
    spec = importlib.util.spec_from_file_location(
        "live_confirm_script", "scripts/live_confirm.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _answers(*values):
    stream = iter(values)
    return lambda prompt="": next(stream, "")


def test_the_wizard_writes_a_complete_confirmation(tmp_path):
    wizard = _wizard()
    target = tmp_path / "conf.json"

    ok, message = wizard.run(
        reader=_answers(*(["yes"] * 7 + ["20", lg.REQUIRED_PHRASE])),
        writer=lambda *a: None,
        path=str(target),
    )

    assert ok is True, message
    data = json.loads(target.read_text(encoding="utf-8"))

    assert data["phrase"] == lg.REQUIRED_PHRASE
    assert data["approved_notional_usdt"] == 20.0
    assert set(data["confirmations"]) == set(live_confirm.REQUIRED_ITEMS)
    assert data["settings_fingerprint"] == live_confirm.fingerprint()


def test_saying_no_to_any_item_writes_nothing(tmp_path):
    """
    一份寫到一半的確認檔比沒有確認檔危險。
    """
    wizard = _wizard()
    target = tmp_path / "conf.json"

    ok, message = wizard.run(
        reader=_answers("yes", "yes", "no", "yes"),
        writer=lambda *a: None,
        path=str(target),
    )

    assert ok is False
    assert "第 3 項" in message
    assert not target.exists()


def test_a_wrong_phrase_writes_nothing(tmp_path):
    wizard = _wizard()
    target = tmp_path / "conf.json"

    ok, message = wizard.run(
        reader=_answers(*(["yes"] * 7 + ["20", "i understand live trading risk"])),
        writer=lambda *a: None,
        path=str(target),
    )

    assert ok is False
    assert "確認句不符" in message
    assert not target.exists()


def test_an_amount_over_the_first_live_cap_is_refused(tmp_path):
    wizard = _wizard()
    target = tmp_path / "conf.json"

    over = lg.MAX_INITIAL_NOTIONAL_USDT + 1

    ok, message = wizard.run(
        reader=_answers(*(["yes"] * 7 + [str(over), lg.REQUIRED_PHRASE])),
        writer=lambda *a: None,
        path=str(target),
    )

    assert ok is False
    assert "上限" in message
    assert not target.exists()


def test_a_non_numeric_amount_is_refused(tmp_path):
    wizard = _wizard()
    target = tmp_path / "conf.json"

    ok, message = wizard.run(
        reader=_answers(*(["yes"] * 7 + ["很多", lg.REQUIRED_PHRASE])),
        writer=lambda *a: None,
        path=str(target),
    )

    assert ok is False
    assert not target.exists()


def test_an_empty_answer_is_not_a_yes(tmp_path):
    """
    直接按 Enter、EOF、Ctrl-C —— 全部都不是同意。
    """
    wizard = _wizard()
    target = tmp_path / "conf.json"

    ok, _ = wizard.run(
        reader=_answers(""), writer=lambda *a: None, path=str(target),
    )

    assert ok is False
    assert not target.exists()


def test_the_wizard_output_never_contains_the_secret(tmp_path):
    wizard = _wizard()
    target = tmp_path / "conf.json"

    printed = []
    wizard.run(
        reader=_answers(*(["yes"] * 7 + ["20", lg.REQUIRED_PHRASE])),
        writer=lambda *a: printed.append(" ".join(str(x) for x in a)),
        path=str(target),
    )

    from agmcis.config import settings

    secret = settings.EXCHANGE_CREDENTIALS.get("secret")
    if secret:
        assert secret not in "\n".join(printed)
        assert secret not in target.read_text(encoding="utf-8")


def test_the_wizard_does_not_open_the_gate(tmp_path):
    """
    簽名只是七項檢查裡的一項。模擬盤筆數、天數、上線前檢查、
    提款權限,那些不是人簽名就能通過的。
    """
    source = Path("scripts/live_confirm.py").read_text(encoding="utf-8")

    # 精靈只寫確認檔。它不碰交易模式、不改設定、不呼叫閘門。
    for forbidden in ("TRADING_MODE =", "os.environ[", "setattr("):
        assert forbidden not in source, f"精靈不該做 {forbidden}"

    assert "這**不代表閘門開了**" in source
