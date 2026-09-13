"""
BingX 標準合約 · 2026-09-13 全面改寫 —— **原本的結論一半是錯的**

═══ 2026-09-10 寫下的結論,與它為什麼錯 ═══
這個檔案本來整支都在說一句話:「標準合約無法實作,原因在交易所端。」
理由列了三點:只有三個私有端點、沒有公開行情、文件寫 internal testing。

那三點**對 U 本位標準合約是對的**,而它們被套用到了「標準合約」
這四個字上 —— 於是連帶把另一個產品也一起判死了,而那一個
**有完整的交易 API**。

錯誤的形狀跟本專案反覆出現的那一種一模一樣:
**一個聽起來很確定、但其實沒有被完整問過的答案。**

更精確地說,2026-09-10 的實測留下了這一行:

    /openApi/cswap/v1/market/contracts → code 104414

它被讀成「這個端點也不行」。但 104414 的意思是**參數不對**,
不是端點不存在。2026-09-13 帶上正確參數重問,交易所回了 20 檔合約。
一個錯誤的錯誤碼解讀,讓一整個市場被寫掉了三天。

═══ BingX App 裡的「標準合約」有兩種 ═══
執政官 2026-09-13 的截圖:分頁上寫著「U本位標準合約」與「幣本位標準合約」。
它們在 API 上是**兩個完全不同的東西**:

┌────────────────┬──────────────────┬─────────────────────────────┐
│                │ U 本位標準合約    │ 幣本位標準合約               │
├────────────────┼──────────────────┼─────────────────────────────┤
│ API 前綴        │ /openApi/contract│ /openApi/cswap/v1           │
│                │ /v1              │                             │
│ 代號格式        │ BTCUSDT(無槓)   │ BTC-USD(有槓)              │
│ 官方文件        │ BingX-Standard-  │ api-ai-skills/skills/       │
│                │ Contract-doc     │ cswap-trade                 │
│ 端點總數        │ **3 個,全是 GET**│ 17 個(含下單/撤單/平倉)     │
│ 下單 API        │ **沒有**         │ POST .../trade/order        │
│ 強平價          │ **回應裡沒有**   │ liquidationPrice 有         │
│ 結算            │ USDT(正向)      │ **以幣結算(反向)**         │
│ 資金費          │ 未查證           │ **有**(premiumIndex)       │
│ 到期日          │ 未查證           │ **沒有**(官方稱 perpetual) │
└────────────────┴──────────────────┴─────────────────────────────┘

所以「按照標準合約去做」這句話,在程式碼裡只有一個可能的落點:
**幣本位標準合約 /openApi/cswap/v1。** U 本位標準合約連一張單都送不出去。

═══ 「幣本位標準合約」其實是永續 ═══
官方文件第一行寫的是 "BingX Coin-M (CSwap) Trade"、
"Coin-M **perpetual** contracts"。它沒有到期日、會收資金費。
「標準合約」是 BingX 在 App 上的產品線名稱,不是合約性質的描述。

這件事已經回頭改了 exchange/types.py:MarketType 不准再拿來推論
有沒有資金費、有沒有到期日。

═══ 反向合約:為什麼還不能下單 ═══
幣本位是**反向合約** —— 面額以 USD 計、盈虧與保證金**以幣結算**:

    正向(現在的帳本):名目 = 數量 × 價格,盈虧 = 數量 × 價差(USDT)
    反向(幣本位)    :名目 = 張數 × 面額,盈虧 = 張數 × 面額 ×
                       (1/開倉價 − 1/平倉價)  ← 以**幣**計

第二行對價格是**非線性**的。做多的下檔損失有上限(價格歸零時虧掉
全部保證金),做空的上檔損失**無上限** —— 跟正向合約剛好相反。
portfolio/account.py 與 paper.py 目前每一條算式都是第一行。

所以這一支**開放行情、開放讀帳戶,但 submit() 仍然拋出**,
理由從「交易所不給」改成「**我方帳本還沒改寫**」。
這兩個理由的差別很重要:前者只能等,後者是我們自己的工作。

═══ 還有一個沒解決的數字:一張是多少 ═══
官方文件說 minTickSize 是「最小合約價值(USD)」,BTC-USD 給的是 10。
但拿 ticker 的成交量回推是 ~100:

    volume 390,724 張 × 面額 ÷ lastPrice 76,676.7 ≈ quoteVolume 506.41 BTC
    → 面額 ≈ 506.41 × 76,676.7 ÷ 390,724 ≈ 99.4

文件說 10,實測回推 ~100,差十倍。**下單量差十倍是會爆倉的差別。**
`measure_contract_size()` 把這個回推做成可執行的檢查,但它建立在
「quoteVolume 以幣計價」這個**未被文件證實的假設**上 ——
真正的定案要靠 Demo 開一張最小單,讀回 positionAmt 與 initialMargin。
在那之前,contracts() 會把兩個數字都放進 Contract,並且不假裝知道。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from exchange.base import ExchangeAdapter
from exchange.types import (Contract, MarketType, NotSupported, OrderRequest,
                            Unverified)

# ══════════════════════════════════════════════════════════
# 一、U 本位標準合約 —— 唯讀,交易所沒有給下單 API
# ══════════════════════════════════════════════════════════

#: 覆核日期。任何「不支援」的宣稱都要帶日期,否則無法判斷是否過時。
USDT_CHECKED_ON = "2026-09-13"

USDT_REASON = (
    "BingX **U 本位**標準合約(/openApi/contract/v1)沒有下單 API —— "
    "原因在交易所端。官方文件 BingX-API/BingX-Standard-Contract-doc 的 "
    "REST API 全文只有三個端點,而且全部是 GET:"
    "allPosition(查持倉)、allOrders(查歷史訂單)、balance(查餘額)。"
    "文件並自陳 'currently in internal testing'、申請頁面尚未開放。"
    "持倉回應裡連 liquidationPrice(強平價)都沒有。"
    f"覆核日期 {USDT_CHECKED_ON}。"
    "要自動下單請改用**幣本位**標準合約(/openApi/cswap/v1),"
    "見 BingXStandardCoinM。"
)


class BingXStandardUSDT(ExchangeAdapter):
    """U 本位標準合約。**唯讀** —— 交易所沒有提供下單端點。

    它仍然有用:執政官在 App 上開的標準合約倉會出現在這裡,
    對帳(第十七條)需要看得到它。**看得到但不能動**,是正確的狀態。
    """

    name = "bingx-standard-usdt"
    market_type = MarketType.STANDARD
    is_live = False

    def __init__(self, client=None):
        self._client = client

    def _c(self):
        if self._client is None:
            from exchange.bingx.private import ReadOnlyClient
            self._client = ReadOnlyClient()
        return self._client

    # ── 行情:交易所沒給 ────────────────────────────────
    def contracts(self) -> dict[str, Contract]:
        raise NotSupported(USDT_REASON)

    def mark_prices(self, symbols=None) -> dict[str, float]:
        raise NotSupported(USDT_REASON)

    def last_prices(self, symbols=None) -> dict[str, float]:
        raise NotSupported(USDT_REASON)

    # ── 下單:交易所沒給 ────────────────────────────────
    def submit(self, req: OrderRequest):
        raise NotSupported(USDT_REASON)

    def cancel_all(self, symbol: str | None = None) -> int:
        raise NotSupported(USDT_REASON)

    # ── 讀帳戶:這三個是**可以**的 ──────────────────────
    def positions(self) -> list[dict]:
        """交易所回什麼就是什麼 —— **原始的那一份。**

        ⚠️ 回應裡沒有 liquidationPrice 也沒有 markPrice
        (官方文件的欄位表確認過,2026-09-13 的實測回應也一致)。

        要拿補齊過的版本請用 `rich_positions()` —— 它會算出強平價,
        並且明講那是**我們算的**。兩個方法刻意分開:
        「交易所說的」與「我們推的」混在同一個 dict 裡,
        遲早會有人把後者當成前者。
        """
        data = self._c().standard_positions()
        return list(data or [])

    def rich_positions(self) -> list:
        """補上交易所沒給的強平價。回 StandardPosition 清單。

        每一筆都帶 `liq_source`:交易所給的是 "exchange",
        我們算的是 "computed" 而且 `liq_optimistic=True`。
        **我們算的那個偏樂觀** —— 實際強平價會更近,不會更遠,
        所以它只能用來收緊判斷,不能用來放行。
        """
        from exchange.bingx.standard_usdt import positions_from
        return positions_from(self.positions())

    def balance(self) -> dict:
        """⚠️ 這個產品的餘額回應**沒有 equity**。

        官方欄位表:asset / balance / crossWalletBalance / crossUnPnl /
        availableBalance / maxWithdrawAmount / marginAvailable。
        對帳層把 equity 列為 critical,所以這裡會被如實報成缺欄位 ——
        那是對的,不要為了讓紅字消失而放寬對帳。
        """
        return self._c().standard_balance()

    def orders(self, symbol: str) -> list[dict]:
        """成交史。**symbol 是必填的**,而它的格式文件與實測不一致。

        官方文件寫 `BTC-USDT`(有槓),但 allPosition 實測回的是
        `FLOCKUSDT`(無槓),App 上顯示的也是無槓。兩種都試,
        **並且回報哪一種成功** —— 靜靜換掉會讓「格式其實是另一種」
        這件事永遠不被發現。
        """
        return self._orders_with_format(symbol)[0]

    def _orders_with_format(self, symbol: str) -> tuple:
        from exchange.bingx.private import READ_ONLY, PrivateCallFailed

        tried, errors = [], []
        for candidate in self._symbol_forms(symbol):
            if candidate in tried:
                continue
            tried.append(candidate)
            try:
                data = self._c().get(READ_ONLY["std_orders"],
                                     {"symbol": candidate})
            except PrivateCallFailed as e:
                errors.append(f"{candidate}: {e}")
                continue
            if data:
                return list(data), candidate

        if errors and len(errors) == len(tried):
            raise NotSupported(
                f"{symbol} 的成交史兩種代號格式都問不到:\n  "
                + "\n  ".join(errors))
        # 問得到但是空的 —— 那是「這個標的沒有成交過」,不是失敗。
        return [], (tried[0] if tried else symbol)

    @staticmethod
    def _symbol_forms(symbol: str) -> list:
        """`BTCUSDT` 與 `BTC-USDT` 兩種寫法都給出來。"""
        plain = symbol.replace("-", "")
        forms = [symbol, plain]
        for quote in ("USDT", "USDC", "USD"):
            if plain.endswith(quote) and len(plain) > len(quote):
                forms.append(f"{plain[:-len(quote)]}-{quote}")
                break
        seen, out = set(), []
        for f in forms:
            if f not in seen:
                seen.add(f)
                out.append(f)
        return out

    def infer_spec(self, symbol: str):
        """規格從成交史反推 —— 因為這個產品沒有 contracts 端點。

        ⚠️ 反推出來的精度是**下界**,不是規格。用來擋明顯不合規的單
        可以,拿來當四捨五入的依據不行。詳見 standard_usdt.infer_spec。
        """
        from exchange.bingx.standard_usdt import infer_spec
        rows, used = self._orders_with_format(symbol)
        spec = infer_spec(symbol, rows)
        if used != symbol:
            spec.reason = (spec.reason or "") + f"(代號實際用 {used} 問到)"
        return spec


# ══════════════════════════════════════════════════════════
# 二、幣本位標準合約(Coin-M)—— 交易所給了,我方帳本還沒好
# ══════════════════════════════════════════════════════════

COINM_CHECKED_ON = "2026-09-13"

#: submit() 為什麼還是拋。**這是我方的限制,不是交易所的。**
COINM_LEDGER_REASON = (
    "BingX 幣本位標準合約(/openApi/cswap/v1)**交易所這邊是通的** —— "
    "POST /openApi/cswap/v1/trade/order 存在,還支援下單時直接附上 "
    "stopLoss(第十九條的強制停損可以跟進場同一個原子動作完成)。"
    "擋住的是我方:一、這是**反向合約**,盈虧以幣結算且對價格非線性,"
    "而 portfolio/account.py 與 paper.py 每一條算式都是正向的;"
    "二、**一張合約等於多少 USD 還沒定案**(文件說 10,ticker 回推 ~100,"
    "差十倍);三、金鑰目前沒有交易權限(實測 code=100004)。"
    f"覆核日期 {COINM_CHECKED_ON}。"
    "三件事都解決之前,這裡只讀不寫。"
)


class BingXStandardCoinM(ExchangeAdapter):
    """幣本位標準合約 = 官方文件的 Coin-M perpetual。

    行情與帳戶**已經可用**;下單刻意還沒開,理由見 COINM_LEDGER_REASON。
    """

    name = "bingx-standard-coinm"
    market_type = MarketType.STANDARD
    is_live = False

    def __init__(self, client=None):
        self._client = client

    def _c(self):
        if self._client is None:
            from exchange.bingx.private import ReadOnlyClient
            self._client = ReadOnlyClient()
        return self._client

    # ── 行情 ──────────────────────────────────────────
    def contracts(self) -> dict[str, Contract]:
        """向交易所要規格。**不寫死任何常數。**

        費率取自 /openApi/cswap/v1/user/commissionRate(實測 taker 0.05%、
        maker 0.02%),拿不到就整個拋 —— 一個猜出來的費率會讓回測
        系統性地偏樂觀,而那種偏差不會有人發現。
        """
        rows = self._c().coinm_contracts() or []
        taker, maker = self._fees()

        out: dict[str, Contract] = {}
        for row in rows:
            sym = row.get("symbol")
            if not sym:
                continue
            out[sym] = Contract(
                symbol=sym,
                market_type=MarketType.STANDARD,
                # 幣本位的下單量是**張數**,而張數是整數。
                quantity_precision=0,
                price_precision=int(row.get("pricePrecision") or 0),
                min_qty=float(row.get("minQty") or 0),
                min_notional=float(row.get("minTradeValue") or 0),
                taker_fee_pct=taker,
                maker_fee_pct=maker,
                # ⚠️ 文件把 minTickSize 定義成「最小合約價值(USD)」,
                #    但 ticker 回推對不上(見檔頭)。這裡照抄文件值,
                #    **並且不宣稱它是對的** —— measure_contract_size()
                #    是用來拆穿它的。
                contract_size=float(row.get("minTickSize") or 0) or 1.0,
                margin_asset=sym.split("-")[0],
                settlement_asset=sym.split("-")[0],
                tradable=int(row.get("status") or 0) == 1,
                expiry=None,          # 官方文件:perpetual,無到期
                inverse=True,         # 以幣結算
                funding=True,         # premiumIndex 實測回 lastFundingRate
            )
        return out

    def _fees(self) -> tuple[float, float]:
        data = self._c().coinm_commission() or {}
        if isinstance(data, list):
            data = data[0] if data else {}
        taker = data.get("takerCommissionRate")
        maker = data.get("makerCommissionRate")
        if taker is None or maker is None:
            raise Unverified(
                "問不到幣本位的實際費率 —— **不套用預設值**。"
                "費率猜低了,回測會系統性地比實際好看,"
                "而那種偏差不會有人發現(第三十六條)。")
        return float(taker) * 100.0, float(maker) * 100.0

    def mark_prices(self, symbols=None) -> dict[str, float]:
        """標記價。**強平是用標記價判定的,不是最新成交價。**

        premiumIndex 一次只回一檔,所以要逐檔問 —— 限流器會擋著
        (2 rps,恰好等於官方標的 2/s per IP)。
        """
        out: dict[str, float] = {}
        for sym in self._symbols(symbols):
            data = self._c().coinm_premium(sym) or {}
            if isinstance(data, list):
                data = data[0] if data else {}
            value = data.get("markPrice")
            if value is not None:
                out[sym] = float(value)
        return out

    def last_prices(self, symbols=None) -> dict[str, float]:
        from exchange.bingx.private import READ_ONLY
        data = self._c().get(READ_ONLY["coinm_ticker"]) or []
        if isinstance(data, dict):
            data = [data]
        wanted = set(self._symbols(symbols)) if symbols else None
        out: dict[str, float] = {}
        for row in data:
            sym = row.get("symbol")
            if not sym or (wanted is not None and sym not in wanted):
                continue
            value = row.get("lastPrice")
            if value is not None:
                out[sym] = float(value)
        return out

    def funding_rates(self, symbol: str, since_ms: int,
                      until_ms: int) -> list[dict]:
        """**幣本位有資金費,但這一支拿不到歷史。**

        premiumIndex 只回**當期**的 lastFundingRate。第五十條要的是
        區間內實際結算的紀錄 —— 那個目前沒有對應的端點。

        差別要講清楚:「有資金費但查不到歷史」跟「沒有資金費」
        是兩件事。後者會讓回測少扣一筆持續性成本。
        """
        raise NotSupported(
            "幣本位標準合約**有**資金費(premiumIndex 回 lastFundingRate,"
            f"實測 {COINM_CHECKED_ON} 為 0.000071),但官方沒有提供"
            "歷史結算查詢端點,只回得到當期。"
            "**不要因此把資金費當成 0** —— 回測會系統性偏樂觀。")

    def current_funding(self, symbol: str) -> dict:
        """當期資金費率 + 下次結算時間。回測不能用,盯盤可以。"""
        data = self._c().coinm_premium(symbol) or {}
        if isinstance(data, list):
            data = data[0] if data else {}
        return {
            "symbol": symbol,
            "lastFundingRate": data.get("lastFundingRate"),
            "markPrice": data.get("markPrice"),
            "indexPrice": data.get("indexPrice"),
            "nextFundingTime": data.get("nextFundingTime"),
        }

    # ── 帳戶 ──────────────────────────────────────────
    def positions(self) -> list[dict]:
        """幣本位持倉。**這一組有 liquidationPrice。**"""
        return list(self._c().coinm_positions() or [])

    def balance(self) -> dict:
        return self._c().coinm_balance()

    # ── 下單:刻意還沒開 ────────────────────────────────
    def submit(self, req: OrderRequest):
        raise NotSupported(COINM_LEDGER_REASON)

    def cancel_all(self, symbol: str | None = None) -> int:
        raise NotSupported(COINM_LEDGER_REASON)

    # ── 工具 ──────────────────────────────────────────
    def _symbols(self, symbols) -> list[str]:
        if symbols:
            return list(symbols)
        return sorted(self.contracts())

    def measure_contract_size(self, symbol: str) -> dict:
        """從 ticker 回推一張合約等於多少 USD。

        ═══ 這是推論,不是事實 ═══
        它建立在一個**文件沒有寫**的假設上:ticker 的 quoteVolume
        以**幣**計價,而 volume 以**張**計價。假設錯了,答案就錯了。

        所以回傳值裡同時給:文件宣稱的值、回推的值、以及兩者是否一致。
        **不要只讀 measured 那一格。**

        真正的定案:在 Demo 開一張最小單,讀回 positionAmt 與
        initialMargin。那是唯一不靠假設的方法。
        """
        from exchange.bingx.private import READ_ONLY
        data = self._c().get(READ_ONLY["coinm_ticker"], {"symbol": symbol})
        if isinstance(data, list):
            data = data[0] if data else {}
        data = data or {}

        try:
            volume = float(data.get("volume"))
            quote = float(data.get("quoteVolume"))
            price = float(data.get("lastPrice"))
        except (TypeError, ValueError):
            raise Unverified(
                f"{symbol} 的 ticker 少了 volume / quoteVolume / lastPrice,"
                "回推不出面額 —— 不要用預設值。") from None

        if volume <= 0 or quote <= 0 or price <= 0:
            raise Unverified(f"{symbol} 的 ticker 數字是 0,回推不出面額。")

        measured = quote * price / volume
        declared = float((self.contracts().get(symbol)
                          or Contract(symbol=symbol,
                                      market_type=MarketType.STANDARD,
                                      quantity_precision=0, price_precision=0,
                                      min_qty=0, min_notional=0,
                                      taker_fee_pct=0, maker_fee_pct=0)
                          ).contract_size)

        # 10% 之內算對得上。回推本身有誤差(VWAP ≠ lastPrice),
        # 但十倍的差距絕不是誤差。
        agree = declared > 0 and abs(measured - declared) / declared < 0.10

        return {
            "symbol": symbol,
            "declared_by_doc": declared,
            "measured_from_ticker": round(measured, 4),
            "agree": agree,
            "assumption": "quoteVolume 以幣計價、volume 以張計價"
                          "(官方文件未載明,這是推論)",
            "verdict": ("兩者一致" if agree else
                        f"**對不上**:文件 {declared:g} vs 回推 "
                        f"{measured:.4g} —— 下單量差這麼多會爆倉,"
                        "在 Demo 開一張最小單量出來之前不得下單"),
        }


#: 舊名。2026-09-10 只有一個 BingXStandard,而它指的其實是 U 本位。
#: 保留別名讓既有 import 不會爆,但**新的程式碼請明確選一個**。
BingXStandard = BingXStandardUSDT
