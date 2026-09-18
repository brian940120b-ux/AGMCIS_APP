"""
成績單 —— 守的是「能不能說出還不知道」· 2026-09-18

執政官問「現在這個的績效到底好不好」。最容易做出來的壞答案是一張
綠色的 +3% ——**那個數字沒有錯,但它不是這個問題的答案。**

這一組守四件事:

一、模擬停了要說停了,不准繼續報最後一天的數字當現況
二、進出沒走完幾次,一律「還不知道」,不給裁決
三、贏不過買入持有就是不好 —— 絕對報酬不算數(判準二、三)
四、回撤超過契約,賺錢也不叫好
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from portfolio.scorecard import (BAD, BLOCKED, GOOD, MIN_ROUND_TRIPS, UNKNOWN,
                                 latest_backtest, round_trips, score)

NOW = datetime(2026, 9, 18, 12, 0, tzinfo=timezone.utc)


def curve(tmp_path, rows) -> Path:
    p = tmp_path / "curve.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    return p


# 欄位名一律用曲線檔真正的 key。第一版這裡寫成 ret= / bench= /
# dd= / eq=,而 `rows[-1].update(ret=25.0)` 是**新增一個沒有人讀的
# key**,不是改 return_pct —— 這種寫法在別的測試裡會靜靜地變成綠燈。
def day(n, *, held, return_pct=0.0, benchmark_pct=0.0,
        drawdown_pct=0.0, equity=10000.0, ago=None):
    t = NOW - timedelta(days=ago if ago is not None else 0)
    return {"t": t.isoformat(), "signal_day": f"2026-09-{n:02d}",
            "equity": equity, "return_pct": return_pct,
            "benchmark_pct": benchmark_pct,
            "drawdown_pct": drawdown_pct, "holdings": list(held)}


def trips(n):
    """造出 n 次走完的進出 —— 一檔進、一檔出,重複 n 次。"""
    rows, d = [], 1
    for k in range(n):
        rows.append(day(d, held=[f"S{k}"], ago=n - k + 1))
        d += 1
        rows.append(day(d, held=[], ago=n - k + 1))
        d += 1
    return rows


# ══════════════════════════════════════════════════════════
# 一、走完的進出怎麼數
# ══════════════════════════════════════════════════════════
def test_a_position_still_open_is_not_a_finished_trade():
    """**帳面浮盈不是成績。** 還抱著的倉明天就可能變號 ——
    把它算進成績的系統,會在每一次上漲時說自己很行。"""
    assert round_trips([{"holdings": ["BTC"]},
                        {"holdings": ["BTC"]},
                        {"holdings": ["BTC"]}]) == 0


def test_a_position_that_opened_and_closed_counts_once():
    assert round_trips([{"holdings": []}, {"holdings": ["BTC"]},
                        {"holdings": []}]) == 1


def test_several_symbols_closing_on_the_same_day_all_count():
    assert round_trips([{"holdings": ["A", "B", "C"]},
                        {"holdings": []}]) == 3


# ══════════════════════════════════════════════════════════
# 二、沒資格回答的時候
# ══════════════════════════════════════════════════════════
def test_an_empty_ledger_says_so(tmp_path):
    s = score(curve(tmp_path, []), now=NOW)
    assert s.verdict == UNKNOWN
    assert "還沒記過" in " ".join(s.because)


def test_a_stopped_simulation_is_the_first_thing_reported(tmp_path):
    """**停掉的模擬會安安靜靜地一直報最後一天的數字。**

    而那個數字看起來跟一個活著的系統完全一樣 —— 這一關所以排最前面,
    排在「進出夠不夠」之前:一個停了三個月的模擬,進出次數可能很夠。
    """
    rows = trips(MIN_ROUND_TRIPS + 3)
    for r in rows:                                   # 全部往前推 9 天
        t = datetime.fromisoformat(r["t"]) - timedelta(days=9)
        r["t"] = t.isoformat()
    s = score(curve(tmp_path, rows), now=NOW)
    assert s.verdict == UNKNOWN
    assert not s.running
    assert "模擬停了" in " ".join(s.because)
    assert s.round_trips >= MIN_ROUND_TRIPS, "進出夠了也不該給裁決"


def test_too_few_round_trips_refuses_to_judge_even_when_winning(tmp_path):
    """**賺錢也不給裁決。** 這條是整組的重點:一個跑十天賺 3% 的模擬,
    最誘人的做法就是印一個綠色的 +3%,而那不是答案。"""
    rows = trips(MIN_ROUND_TRIPS - 1)
    rows[-1].update(return_pct=25.0, benchmark_pct=1.0, equity=12500.0)
    s = score(curve(tmp_path, rows), now=NOW)
    assert s.verdict == UNKNOWN
    text = " ".join(s.because)
    assert "還不能回答" in text
    assert "帳面" in text
    assert "+25.00%" in text and "還不是成績" in text


def test_the_threshold_says_it_is_a_floor_not_a_finish_line(tmp_path):
    """5 次不是「到了就可信」,是「不到就別講」。混淆這兩件事,
    第 5 次進出之後這張卡就會開始騙人。"""
    s = score(curve(tmp_path, trips(MIN_ROUND_TRIPS - 1)), now=NOW)
    assert "不是「到了就可信」" in " ".join(s.because)


def test_no_benchmark_means_no_verdict(tmp_path):
    """絕對報酬不是答案(判準二、三)—— 七個幣自己漲的不算功勞。"""
    rows = trips(MIN_ROUND_TRIPS + 1)
    for r in rows:
        r["benchmark_pct"] = None
    rows[-1]["return_pct"] = 40.0
    s = score(curve(tmp_path, rows), now=NOW)
    assert s.verdict == UNKNOWN
    assert "絕對報酬不是答案" in " ".join(s.because)


# ══════════════════════════════════════════════════════════
# 三、有資格回答的時候
# ══════════════════════════════════════════════════════════
def test_beating_the_benchmark_within_the_drawdown_contract_is_good(tmp_path):
    rows = trips(MIN_ROUND_TRIPS + 1)
    rows[-1].update(return_pct=20.0, benchmark_pct=8.0, drawdown_pct=9.0)
    s = score(curve(tmp_path, rows), now=NOW)
    assert s.verdict == GOOD
    assert s.excess_pct == 12.0


def test_losing_to_buy_and_hold_is_bad_however_much_it_made(tmp_path):
    """**賺 30% 而基準賺 40%,是不好。**

    判準三:只要輸給基準,這套規則就沒有存在的理由 ——
    不交易比較省事,而且不會有手續費。
    """
    rows = trips(MIN_ROUND_TRIPS + 1)
    rows[-1].update(return_pct=30.0, benchmark_pct=40.0, drawdown_pct=5.0)
    s = score(curve(tmp_path, rows), now=NOW)
    assert s.verdict == BAD
    assert "贏不過買入持有" in " ".join(s.because)


def test_winning_but_breaching_the_drawdown_contract_is_not_good(tmp_path):
    """回撤契約不因為賺錢而放寬。"""
    rows = trips(MIN_ROUND_TRIPS + 1)
    rows[-1].update(return_pct=50.0, benchmark_pct=10.0)
    rows[3]["drawdown_pct"] = 22.0
    s = score(curve(tmp_path, rows), now=NOW)
    assert s.verdict == BLOCKED
    assert s.max_dd_pct == 22.0
    assert "不可交易" in " ".join(s.because)


def test_the_drawdown_is_the_worst_ever_not_the_latest(tmp_path):
    """最後一天回撤 0 不代表沒回撤過 —— 要的是最深的那一次。"""
    rows = trips(MIN_ROUND_TRIPS + 1)
    rows[2]["drawdown_pct"] = 18.0
    rows[-1].update(return_pct=20.0, benchmark_pct=5.0, drawdown_pct=0.0)
    assert score(curve(tmp_path, rows), now=NOW).max_dd_pct == 18.0


# ══════════════════════════════════════════════════════════
# 四、回測證據從檔案讀,不寫死
# ══════════════════════════════════════════════════════════
def test_the_backtest_numbers_come_from_the_research_loop(tmp_path):
    """寫死的數字會在策略換掉之後繼續講上一個策略的成績 ——
    而那看起來完全正常。"""
    p = tmp_path / "proposals.json"
    p.write_text(json.dumps([
        {"proposal_id": "old", "created_utc": "2026-09-01T00:00:00+00:00",
         "incumbent_train": {"calmar": 9.99}, "incumbent_test": {"calmar": 9.99}},
        {"proposal_id": "new", "created_utc": "2026-09-17T00:00:00+00:00",
         "incumbent_train": {"calmar": 2.27},
         "incumbent_test": {"calmar": 0.48, "max_dd_pct": 17.4}},
    ]), encoding="utf-8")
    train, test, dd, as_of = latest_backtest(p)
    assert (train, test, dd) == (2.27, 0.48, 17.4), "拿到的不是最新那一份"
    assert as_of == "2026-09-17"


def test_no_proposals_file_means_no_backtest_not_a_made_up_one(tmp_path):
    assert latest_backtest(tmp_path / "nope.json") is None


def test_a_corrupt_line_does_not_take_the_whole_card_down(tmp_path):
    p = tmp_path / "curve.jsonl"
    good = trips(MIN_ROUND_TRIPS + 1)
    good[-1].update(return_pct=20.0, benchmark_pct=8.0)
    p.write_text("\n".join(json.dumps(r) for r in good[:-1])
                 + "\n{ 這行壞了\n" + json.dumps(good[-1]), encoding="utf-8")
    assert score(p, now=NOW).verdict == GOOD


# ══════════════════════════════════════════════════════════
# 五、帳面要拆成已實現 / 未實現
# ══════════════════════════════════════════════════════════
def test_the_paper_number_is_split_into_realised_and_unrealised(tmp_path):
    """**這個拆解就是「還不知道」那句話的證據。**

    「+0.42%,其中已實現 0.00、未實現 +41.85」—— 一行話就說完了
    為什麼十天的帳面不能當成績:那筆錢還沒有真的變成錢。
    """
    rows = trips(MIN_ROUND_TRIPS - 1)
    rows[-1].update(return_pct=0.42, benchmark_pct=0.30,
                    realized_pnl=0.0, unrealized_pnl=41.85)
    s = score(curve(tmp_path, rows), now=NOW)
    assert s.realized_pnl == 0.0 and s.unrealized_pnl == 41.85
    text = " ".join(s.because)
    assert "已實現 +0.00" in text and "未實現 +41.85" in text


def test_a_ledger_without_the_split_still_reports_the_rest(tmp_path):
    """舊的曲線檔沒有這兩欄 —— 不能因此整張卡都不見。"""
    rows = trips(MIN_ROUND_TRIPS - 1)
    rows[-1].update(return_pct=0.42)
    s = score(curve(tmp_path, rows), now=NOW)
    assert s.realized_pnl is None
    assert "+0.42%" in " ".join(s.because)
