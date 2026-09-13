"""
新系統 — 執行層 · 2026-09-08

═══ 為什麼執行要抽象成一層 ═══
「紙上」與「實盤」的差別只該在**這一個檔案**裡。
如果紙上帳戶和未來的實盤帳戶各自維護一份下單邏輯,那就是第二份實作 ——
舊系統兩天內犯了十一次「兩把尺」,每一次的根因都是同一件事:
同一個概念有兩份實作,而它們會漂移。

所以:訂單長什麼樣、部位怎麼記帳、成本怎麼扣,紙上與實盤共用。
差別只有一個:**訂單送到哪裡去。**

═══ 實盤閘門是常數不是設定 ═══
LIVE_ENABLED 寫死在原始碼裡,不讀環境變數、不讀設定檔、不接命令列。
理由:一個能被設定檔打開的實盤開關,遲早會被某次「先開起來試試」打開。
要開實盤必須改這一行、commit、部署 —— 三個看得見的動作。

而且就算改成 True,LiveExecutor 仍會先檢查畢業契約八條。
兩道鎖:一道防手滑,一道防「還沒準備好就上場」。
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.logging import get_logger
from portfolio.orders import Order

log = get_logger("portfolio.execution")

BASE = Path(__file__).resolve().parents[1]
ORDERS_LOG = BASE / "data" / "portfolio_orders.jsonl"

# ══ 實盤閘門:改這一行需要 commit + 部署,不是設定選項 ══════════
LIVE_ENABLED = False
# ═════════════════════════════════════════════════════════


def _append(path: Path, rec: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        f.flush()
        os.fsync(f.fileno())


class Executor:
    """執行器介面。子類只需實作 _place()。"""

    venue = "?"

    def submit(self, orders: list[Order], now: datetime | None = None) -> list[dict]:
        now = now or datetime.now(timezone.utc)
        out = []
        for o in orders:
            rec = o.to_dict()
            rec.update({"t": now.isoformat(), "venue": self.venue})
            try:
                rec.update(self._place(o) or {})
                rec.setdefault("status", "FILLED")
                # ── 實際成交量,不是下單量(2026-09-09 加)──────────
                # 帳本必須記**真的成交了多少**。交易所的市價單可能部分成交
                # (掛單簿不夠深、瞬間被別人吃掉),回傳 executedQty < origQty。
                # 原本帳本直接用下單量記帳,接上真交易所後只要發生一次部分
                # 成交,帳上就會多出根本沒買到的部位 —— 而且沒有任何地方
                # 會發現:訂單記錄顯示成功、帳本平衡、面板正常。
                # 紙上永遠全額成交,但這條管路現在就要是對的。
                rec.setdefault("filled_qty", rec.get("qty"))
                fq, oq = float(rec["filled_qty"]), float(rec["qty"])
                if fq <= 0:
                    rec["status"] = "REJECTED"
                    rec.setdefault("error", "成交量為零")
                elif fq + 1e-12 < oq:
                    rec["status"] = "PARTIAL"
                    log.warning(f"{self.venue} 部分成交 {o.symbol}:"
                                f"{fq}/{oq}({fq / oq:.1%})")
            except Exception as e:
                rec.update({"status": "REJECTED", "filled_qty": 0.0,
                            "error": f"{type(e).__name__}: {e}"})
                log.warning(f"{self.venue} 下單失敗 {o.symbol}:{e}")
            _append(ORDERS_LOG, rec)
            out.append(rec)
        return out

    def _place(self, order: Order) -> dict:
        raise NotImplementedError


class PaperExecutor(Executor):
    """紙上執行:只記帳,不連交易所。

    這個類別**沒有任何交易所 client 的 import** —— 結構性保證,
    不是靠我記得不要下單。tests/test_execution.py 會斷言這件事。
    """

    venue = "PAPER"

    def _place(self, order: Order) -> dict:
        """紙上成交:市價單,成交價 = 隔日開盤價 ± 滑點。

        ═══ 2026-09-09 修正:滑點不是手續費 ═══
        原本把滑點跟手續費加在一起,整包當成 fee 從餘額扣掉。
        交易所不是這樣運作的:
          · 手續費 —— 真的收一筆錢,獨立入帳
          · 滑點   —— 不收錢,是你的**成交價比預期差**
        兩者總金額也許接近,但記在不同地方,而且後果不同:
        滑點會改變 avg_price,而 avg_price 決定強平價、未實現盈虧、
        ROI 三個數字。把滑點記成手續費,等於開倉均價從一開始就是錯的,
        接上真交易所之後每一個風險數字都會對不上。

        方向永遠對自己不利(買貴、賣便宜)—— 這是市價單的本質,
        也是唯一誠實的假設。滑點值取自 costs.py 這個唯一來源。

        ═══ 為什麼紙上一律全額成交 ═══
        2026-09-09 實測掛單簿:我們的單(每檔約 700 USDT)只佔前 20 檔
        深度的 0.009%~0.057%(BTC 0.012% / ETH 0.009% / SOL 0.057%)。
        這個規模下部分成交實務上不會發生,假設全額成交是誠實的。
        但 filled_qty 這條管路仍然照走 —— 真正的風險不是部分成交本身,
        是帳本記的是「下單量」而不是「成交量」:接上真交易所後只要
        發生一次,帳上就會多出沒買到的部位,而且沒有地方會發現。
        """
        from portfolio import specs
        from portfolio.costs import SLIP_FLOOR_PCT
        slip = SLIP_FLOOR_PCT / 100.0
        px = order.price * (1 + slip) if order.side == "BUY" \
            else order.price * (1 - slip)
        px = specs.round_price(order.symbol, px)
        return {"status": "FILLED", "price": px,
                "filled_qty": abs(order.qty),      # 紙上:全額成交
                "notional": abs(order.qty) * px,
                "ref_price": order.price,
                "slippage_pct": SLIP_FLOOR_PCT,
                "note": "紙上成交(隔日開盤價 ± 滑點,市價單,全額)"}


class LiveExecutor(Executor):
    """實盤執行。目前**永遠拒絕** —— 兩道鎖都必須先開。

    鎖一:LIVE_ENABLED 是原始碼常數(改它要 commit + 部署)
    鎖二:實盤資格契約八條必須全過(portfolio/contract.py,執政官預先登記)

    第三道鎖在憲法裡且不在程式碼中:實盤閘門人工簽署,永不自動化。
    """

    venue = "LIVE"

    def _place(self, order: Order) -> dict:
        if not LIVE_ENABLED:
            raise PermissionError(
                "實盤未啟用(LIVE_ENABLED=False)。這是原始碼常數,"
                "不是設定選項 —— 要開必須改碼、commit、部署。")
        ok, detail = graduation_ok()
        if not ok:
            raise PermissionError(f"實盤資格契約未通過:{detail}")
        raise PermissionError(
            "實盤下單路徑尚未實作 —— 契約通過後仍需人工簽署(憲法第十條)。")

    # ══════════════════════════════════════════════════════
    # 實作實盤路徑時,這個 _place() 必須回傳的欄位(2026-09-09 訂):
    #
    #   price       **實際成交均價**(交易所的 avgPrice),不是下單價。
    #               市價單的成交價由簿子決定,滑點是結果不是假設。
    #   filled_qty  **實際成交量**(交易所的 executedQty),可能 < 下單量。
    #               submit() 會據此把狀態改成 PARTIAL,帳本只記這個數字。
    #   status      交易所回的狀態要映射到 FILLED / PARTIAL / REJECTED。
    #
    # 絕對不可以用下單量或下單價回填這兩個欄位 —— 那會讓帳本記進
    # 根本沒成交的部位,而訂單記錄顯示成功、帳本平衡、面板正常,
    # 沒有任何一條檢查會發現。這正是本系統一路在防的那種錯。
    #
    # 另外三件實盤才會遇到、紙上不存在的事,實作時必須一起處理:
    #   · 下單前要先設該幣的槓桿(交易所是逐幣設定的)
    #   · 要處理下單後未立即成交的掛單狀態(以及要不要撤單)
    #   · 交易所的錯誤碼要分辨「可重試」與「不可重試」
    # ══════════════════════════════════════════════════════


def graduation_ok() -> tuple[bool, str]:
    """實盤資格契約八條全過了嗎?回傳 (是否, 說明)。"""
    try:
        from portfolio.contract import evaluate
        g = evaluate()
        passed, total = g.get("passed", 0), g.get("total", 8)
        if passed >= total:
            return True, f"{passed}/{total} 全過"
        fail = [c["name"] for c in g.get("criteria", []) if not c.get("passed")]
        return False, f"{passed}/{total};未過:{', '.join(fail)}"
    except Exception as e:
        return False, f"契約無法評估({type(e).__name__}),一律視為未過"


def get_executor() -> Executor:
    """現役執行器。永遠回傳紙上,除非兩道鎖都開。"""
    if LIVE_ENABLED and graduation_ok()[0]:
        return LiveExecutor()
    return PaperExecutor()


def load_orders(limit: int = 200) -> list[dict]:
    out: list[dict] = []
    try:
        with ORDERS_LOG.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    try:
                        out.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
    except OSError:
        return []
    return out[-limit:] if limit else out
