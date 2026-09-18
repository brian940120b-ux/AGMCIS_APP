"""
AGMCIS — 每日作業 · 2026-09-08

取代已刪除的 autopilot。舊的那支同時做八件事(法庭開庭、新聞、
15m 紙上引擎、名冊淘汰、脈動、交易池、週報、日報),其中六件服務的
系統已經不存在。

現在只做四件,而且順序就是依賴順序:
  ① 更新交易池與日線快取
  ② 組合記帳(產訂單 → 執行 → 記帳 → 更新監控)
  ③ 更新基準線與實盤資格契約
  ④ 推播日報(帳戶、今日訂單、監控示警)

每天一次,由 agmcis-portfolio.timer 觸發。
沒有常駐迴圈 —— 常駐進程會讓「程式改了但沒生效」變成無聲的錯誤,
2026-09-07 就是這樣讓四天的改動一行都沒跑。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import interpreter

# 用錯直譯器的時候講人話,而不是丟一個 ModuleNotFoundError 讓人猜。
interpreter.require()

from core.config import load_env
from core.logging import get_logger
from notify import telegram

log = get_logger("daily")

BASE = Path(__file__).resolve().parents[1]
DATA = BASE / "data"

# ── 2026-09-09 修:每日作業從沒呼叫過 load_env() ─────────────
# systemd 用裸環境跑這支腳本,.env 裡的 TELEGRAM_BOT_TOKEN 從沒被讀進去。
# 記帳、更新契約全部照常成功、腳本乾淨退出 0,只有 telegram.send()
# 靜靜印一行「Telegram 未設定,略過通知」——**日報從這支腳本存在以來
# 就沒有真的送出去過**,而系統其餘每個環節看起來都正常。
# 這正是這座城邦一路在防的那種錯:功能「看起來」在運作,承諾的行為
# 卻從來沒有發生。今天才在準備通知改版時順手發現。
load_env()


def _refresh_daily_bars() -> None:
    """更新那七個幣的日線快取。

    ⚠️ **這裡不會換交易池。** 名字原本叫 `_refresh_universe`,
    而它從來沒有 refresh 過任何 universe —— 2026-09-18 執政官問
    「交易池是會掃描更換嗎」,查下去才發現這個名字騙了所有人,
    包括我。

    交易池是 `portfolio/paper.SYMBOLS` 裡**寫死的七個幣**。
    `portfolio/universe.screen()` 有全市場掃描的能力(流動性、
    歷史長度、交易所狀態),但**主系統沒有任何地方呼叫它**。

    要改成動態交易池是**策略層的決定**(§6),不是換個函式名就好:
    交易池一換,回測的 Calmar 1.33 就不是這個池子的數字,要重跑。
    """
    from market_data.history import load_or_download
    from portfolio.paper import LOOKBACK_DAYS, SYMBOLS
    for s in SYMBOLS:
        try:
            load_or_download(s, LOOKBACK_DAYS, interval="1d")
        except Exception as e:
            log.warning(f"{s} 日線更新失敗:{e}")


def _report() -> str:
    """日報。2026-09-09 對齊面板改版:琥珀 = 模擬金(不是裝飾,
    每次看到都在提醒這不是真錢)、綠/紅維持漲跌不變。用 Telegram
    的 HTML 解析模式(<b>/<code>),數字一律用 <code> 排版,
    跟面板的等寬對齊數字是同一個視覺語言。"""
    from notify.telegram import esc

    a = json.loads((DATA / "portfolio_account.json").read_text("utf-8"))
    rows = []
    try:
        with (DATA / "portfolio_equity.jsonl").open(encoding="utf-8") as fh:
            for ln in fh:
                ln = ln.strip()
                if ln:
                    rows.append(json.loads(ln))
    except OSError:
        pass
    last = rows[-1] if rows else {}
    eq = float(a.get("equity", 0) or 0)
    ret = float(a.get("return_pct", 0) or 0)
    bm = last.get("benchmark_pct")
    dd = float(a.get("drawdown_pct", 0) or 0)
    hold = sorted(a.get("positions") or {})

    up = ret > 0
    trend = "📈" if up else ("📉" if ret < 0 else "➖")
    lines = [
        "🟡 <b>AGMCIS</b> · 紙上交易台",
        f"{trend} <b>{eq:,.2f}</b> USDT  <code>{ret:+.2f}%</code>",
    ]
    if bm is not None:
        lines.append(f"基準 <code>{bm:+.2f}%</code> · 超額 "
                     f"<code>{ret - bm:+.2f}pp</code>")
    lines.append(f"{'✅' if dd <= 15 else '⚠️'} 回撤 <code>{dd:.2f}%</code>"
                 f"(契約上限 15%)")
    lines.append(f"已實現 <code>{float(a.get('realized_pnl', 0)):+,.2f}</code>"
                 f" · 未實現 <code>{float(a.get('unrealized_pnl', 0)):+,.2f}"
                 f"</code> USDT")
    lines.append(f"曝險 <code>{float(a.get('exposure', 0)):.0%}</code> · 持有 "
                 f"{len(hold)} 檔"
                 + (f":{esc(', '.join(h.replace('-USDT', '') for h in hold))}"
                    if hold else ":空手"))

    try:
        from portfolio.orders import format_orders
        from portfolio.paper import plan
        pl = plan()
        if "error" not in pl and pl.get("orders"):
            lines.append(f"\n📋 <b>今日訂單</b>({pl['exec_day'][:10]} 開盤)")
            lines.append(format_orders(pl["orders"], html=True))
    except Exception as e:
        log.warning(f"訂單預覽失敗:{e}")

    # ── 強平必須顯眼地報出來(2026-09-10 加)──────────────────
    # 2026-09-10 00:30 UNI-USDT 真的被強平了,而日報照樣寫「✅ 回撤 1.38%」,
    # 完全沒提這件事 —— 強平只寫進 log,沒人會看到。
    # 憲法第十條:「一個只報自己好消息的面板,是在幫操作者自我欺騙。」
    try:
        rows_liq = []
        with (DATA / "portfolio_equity.jsonl").open(encoding="utf-8") as fh:
            for ln in fh:
                ln = ln.strip()
                if ln:
                    rows_liq.append(json.loads(ln))
        liq = (rows_liq[-1].get("liquidated") if rows_liq else None) or []
        if liq:
            # 這一段的 f-string 刻意不在 {} 裡換行:那是 Python 3.12
            # 才合法的寫法(PEP 701),而 3.11 會在**載入時**就
            # SyntaxError —— 整支腳本一行都跑不到。記帳與日報都在這裡。
            names = ", ".join(x.replace("-USDT", "") for x in liq)
            lines.insert(1, f"\n💥 <b>強平發生</b>:{esc(names)}"
                            f"\n該倉已於強平價強制平倉,"
                            f"損失止於該倉保證金(逐倉)。")
    except OSError:
        pass

    try:
        m = json.loads((DATA / "portfolio_monitor.json").read_text("utf-8"))
        for al in (m.get("alerts") or []):
            if al.get("level") == "HIGH":
                lines.append(f"\n🚨 <b>{esc(al.get('kind'))}</b>"
                             f"\n{esc(al.get('msg'))}")
    except Exception:
        pass

    try:
        c = json.loads((DATA / "portfolio_contract.json").read_text("utf-8"))
        lines.append(f"\n🎓 實盤資格 <b>{c.get('passed')}/{c.get('total')}"
                     "</b> · PAPER 模擬金")
    except Exception:
        pass
    return "\n".join(lines)


def funding_symbols() -> list[str]:
    """
    今天需要哪些幣的資金費歷史。

    ═══ 這個函式為什麼存在(2026-09-13)═══
    原本這裡寫的是 `refresh_funding(SYMBOLS)` —— 只有固定那 7 個幣。
    當時還有一個跑動態交易池的測試組,它挑進 1000PEPE-USDT,
    而沒有人抓過那個幣的資金費歷史:

        SpecMissing: 沒有 1000PEPE-USDT 的資金費率歷史 —— 不猜一個數字

    那個例外是對的(用不完整的資料記帳會靜靜少收資金費、美化績效),
    錯的是沒有人去抓那個幣的歷史。測試組整個 tick 死掉,73.7 小時
    沒有記帳,而巡檢報了三天。

    測試組已於 2026-09-13 退役,但**這個函式要留著**:

    ═══ 為什麼交易池固定成 7 幣之後還需要它 ═══
    「帳上握著的」不一定等於「交易池裡的」。一個幣被交易所下架、
    或被移出交易池之後,倉不會瞬間消失 —— 它還要收資金費,
    直到真的被平掉為止。

    只抓交易池會漏掉每一個**正在出場**的倉,而那正是最不該漏的時候:
    出場那筆的成本算錯,直接錯在已實現損益上。
    """
    from portfolio.account import Account
    from portfolio.paper import MAIN, SYMBOLS

    wanted = set(SYMBOLS)

    try:
        wanted.update(Account.load(MAIN.state_path).positions)
    except Exception as e:
        log.warning(f"讀不到帳本,資金費清單可能不全:{e}")

    return sorted(wanted)


def main() -> int:
    print("① 更新日線快取")
    _refresh_daily_bars()

    # 交易所合約規格(數量/價格精度、最小量、費率)。放在記帳之前:
    # 訂單層要靠它把數量調到交易所接受的精度,規格過期會產生會被拒的單。
    try:
        from portfolio.specs import refresh as refresh_specs
        from portfolio.specs import refresh_funding
        print(f"   交易所規格 {refresh_specs()} 個合約")
        # 資金費率:記帳要收的是交易所**實際結算過**的費率,不是估計值。
        # 抓不到會讓記帳沿用過期資料,所以跟規格一起放在記帳之前。
        wanted = funding_symbols()
        print(f"   資金費結算 {refresh_funding(wanted)} 筆"
              f"({len(wanted)} 個標的)")
    except Exception as e:
        # 抓不到就沿用上次的快取(specs 會在超過七天時自己警告),
        # 但這裡必須把錯誤講出來,不能靜靜跳過。
        log.warning(f"交易所規格更新失敗,沿用既有快取:{e}")
        print(f"   ⚠ 交易所規格更新失敗:{e}")

    print("② 組合記帳")
    from portfolio.paper import tick
    # ── 2026-09-09 修:tick() 原本沒有包 try/except ─────────────
    # 同日新增的資金費快取守門會在快取不完整時拋 SpecMissing(那是對的:
    # 用不完整的資料記帳會靜靜少收資金費、美化績效)。但那個例外沒有
    # 被接住 —— 只要上一步的快取更新失敗(網路一抖就夠),整支腳本
    # 就會崩掉:不記帳、不更新契約,**連失敗通知都送不出去**
    # (原本的通知只在 "error" in r 時觸發,接不到例外)。
    # 一個「正確地拒絕壞資料」的守門,不該讓整個作業無聲消失。
    try:
        r = tick()
    except Exception as e:
        msg = f"{type(e).__name__}: {e}"
        log.error(f"記帳拋出例外:{msg}")
        print(f"   記帳例外:{msg}")
        telegram.send(f"🚨 AGMCIS 記帳中斷(例外)\n{msg}")
        return 1
    if "error" in r:
        print(f"   記帳失敗:{r['error']}")
        telegram.send(f"⚠️ AGMCIS 記帳失敗:{r['error']}")
        return 1
    print(f"   {r.get('skipped') or f'''權益 {r['equity']:,.2f}'''}")

    print("③ 更新契約")
    try:
        from portfolio.contract import evaluate
        c = evaluate()
        print(f"   實盤資格 {c['passed']}/{c['total']}")
    except Exception as e:
        log.warning(f"契約更新失敗:{e}")

    print("④ 研究迴路")
    # 執政官要的是「**不斷地**經過多次的模擬交易之後發現該怎麼調整」。
    # 所以它跟記帳一起每天跑,而不是等人想到才跑一次。
    #
    # ⚠️ 兩件事刻意如此:
    #   · 它**只產生提案,不改任何設定** —— 生效要執政官裁決(§102)
    #   · 它失敗**不得拖垮記帳**。記帳是這個系統的本業,
    #     研究是加值;一個加值功能把本業弄掛掉,是最蠢的一種當機。
    try:
        from scripts.research import main as research_main
        research_main([])
    except Exception as e:                       # noqa: BLE001
        log.warning(f"研究迴路失敗:{e}")
        print(f"   ⚠ 研究迴路失敗(不影響記帳):{type(e).__name__}: {e}")

    print("⑤ 推播日報")
    msg = _report()
    print(msg)
    telegram.send(msg, html=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
