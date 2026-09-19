"""
新系統 — 訂單層 · 2026-09-08

═══ 為什麼需要獨立一層 ═══
策略算出來的是「目標權重」,不是訂單。交易員能執行的是訂單:
買哪個、多少數量、什麼價位、停損放哪、為什麼。
中間這一步不做出來,系統就永遠停在「研究工具」而不是「交易系統」。

═══ 停損是策略本身,不是另外加的數字 ═══
這條策略的出場規則就是「收盤跌破 50 日均線就空手」。
所以每個部位的停損價 = 它的 50 日均線 —— **不是我另外挑一個百分比**。
另挑一個停損等於偷偷加了一個沒被回測過的參數,
而回測的 Calmar 1.33 是在「跌破均線才出場」這條規則下算出來的。
停損價會隨均線每天移動,這是事實,面板要照實顯示。

═══ 這一層不執行任何東西 ═══
只產生訂單物件。誰去執行、執行到哪裡(紙上 / 實盤)由 execution 層決定,
而實盤閘門永遠人工。
"""
from __future__ import annotations

import sys
from dataclasses import asdict, dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from portfolio import specs

# ── 訂單過濾:低於這些就不值得付手續費 ──────────────────────
# 注意兩個「最小」不是同一件事,不可互相取代:
#   · MIN_ORDER_USDT 是**我們自己的**經濟門檻 —— 交易所肯收 2 USDT 的單,
#     但那種單的手續費佔比太高,不值得下。這是策略選擇。
#   · specs.min_notional() 是**交易所的**硬性下限 —— 低於它一定被拒。
# 兩個都要過。我們的門檻比交易所嚴,所以實務上前者先擋下來。
MIN_ORDER_USDT = 10.0        # 我們自己的經濟門檻(交易所硬下限是 2)
MIN_WEIGHT_DELTA = 0.005     # 權重變動 <0.5% 不動作(避免每日微調磨手續費)
# ─────────────────────────────────────────────────────────


@dataclass
class Order:
    symbol: str
    side: str                        # BUY / SELL
    qty: float
    price: float                     # 預期成交價(隔日開盤)
    notional: float
    reason: str
    #: 一條參考線(`build_orders` 的 `mas` 給的)。
    #: ⚠️ **這不保證是策略的出場線** —— mas 用的是 cfg.vol_lookback,
    #: 現在剛好也是 50,但那是巧合。策略真正的出場線走
    #: `rules.exit_level_of()`,而指令單用的是那一個。
    stop: float | None = None
    stop_pct: float | None = None    # 現價距停損多遠(%)
    weight_from: float = 0.0
    weight_to: float = 0.0

    def to_dict(self) -> dict:
        d = asdict(self)
        for k in ("qty", "price", "notional", "stop", "stop_pct",
                  "weight_from", "weight_to"):
            if d.get(k) is not None:
                d[k] = round(float(d[k]), 8)
        return d


def build_orders(target_w: dict[str, float], held_w: dict[str, float],
                 equity: float, prices: dict[str, float],
                 mas: dict[str, float] | None = None,
                 held_qty: dict[str, float] | None = None,
                 strategy: str = "") -> list[Order]:
    """由目標權重與現有權重產生訂單。

    · 只下「差額」—— 已持有的部分不重複下單
    · 過小的調整直接略過(手續費會吃掉它)
    · 每張買單附一條參考線(`mas` 傳進來的那條)與距離

    ═══ `strategy` 為什麼要傳進來 ═══
    2026-09-19 反查時發現:`reason` 寫死了「收盤站上 50 日均線」。
    那句話會一路印到指令單的「訊號」欄 —— **使用者按單前看的那一行**。

    策略換成 100 日均線或突破族,它會繼續說 50 日均線,而旁邊的數字
    是另一條線的。這是同一類錯的第四處(出場價、止損說明、證據,
    現在是訊號),而它們的共通點是:**寫的當下是真的。**

    這裡不去拼湊「站上/突破」那種句子 —— 進場條件與出場條件對突破族
    根本不是同一條線(進場看 N 日高,出場看 M 日低),拼出來的句子
    會是另一種假話。直接講策略的名字:它永遠是準的。
    """
    mas = mas or {}
    rule = strategy or "現役策略"
    out: list[Order] = []
    for sym in sorted(set(target_w) | set(held_w)):
        w0 = float(held_w.get(sym, 0.0))
        w1 = float(target_w.get(sym, 0.0))
        dw = w1 - w0

        # 全平倉(目標歸零且手上真的有貨)不受任何「不值得下單」的門檻擋。
        # 那些門檻是為了避免每日微調磨手續費,但一個該歸零的部位就該歸零:
        # 留著的殘倉會繼續付資金費、繼續佔保證金,而且下一輪會被同一個
        # 門檻再擋一次 —— 永遠清不掉。交易所本來就允許平掉既有部位。
        exact_close = (w1 == 0.0 and held_qty is not None
                       and abs(float(held_qty.get(sym) or 0.0)) > 0)

        if not exact_close:
            if abs(dw) < MIN_WEIGHT_DELTA:
                continue
        px = float(prices.get(sym) or 0)
        if px <= 0:
            continue
        notional = abs(dw) * equity
        if not exact_close and notional < MIN_ORDER_USDT:
            continue
        ma = mas.get(sym)
        if dw > 0:
            reason = (f"新開倉:訊號成立({rule})" if w0 <= 0
                      else "加碼:波動下降,目標規模上調")
        else:
            reason = (f"平倉:訊號結束({rule})" if w1 <= 0
                      else "減碼:波動上升,目標規模下調")

        # ── 交易所規格(2026-09-09 加)────────────────────────
        # 原本直接送 notional/px 的全精度浮點數,實測七個持倉的數量
        # **全部**不符合 BingX 數量精度,真的派單會一張都送不出去。
        # 紙上交易看不出來:帳本照收、面板照顯示、測試照過。
        # 全平倉:用**實際持有量**,不要用權重反推出來的數量。
        # 反推會經過 notional/price 一來一回的浮點運算,再被無條件捨去,
        # 只要少了最後一位就會留下殘倉 —— 例如 498 顆 XRP 只賣掉 497。
        # 殘倉會繼續付資金費,而且下一輪同樣的邏輯還是平不掉它。
        # 現在恰好都算得剛好,但那是浮點數的運氣,不是保證。
        if exact_close:
            qty = abs(float(held_qty[sym]))
        else:
            qty = specs.round_qty(sym, notional / px)
        px = specs.round_price(sym, px)
        if qty <= 0:
            continue                       # 捨去後歸零 = 這單小到不存在
        notional = qty * px                # 名目以**進位後**的數量重算
        # 全平倉不受最小量/最小名目擋下:殘倉留著只會繼續付資金費,
        # 而交易所本來就允許把一個既有部位平掉(reduce-only)。
        if not exact_close:
            if qty < specs.min_qty(sym):
                continue                   # 低於交易所最小量,一定被拒
            if notional < specs.min_notional(sym):
                continue                   # 低於交易所硬性下限

        out.append(Order(
            symbol=sym, side="BUY" if dw > 0 else "SELL",
            qty=qty, price=px, notional=notional, reason=reason,
            stop=specs.round_price(sym, ma) if (ma and dw > 0) else None,
            stop_pct=((px - ma) / px * 100) if (ma and dw > 0 and px > 0)
            else None,
            weight_from=w0, weight_to=w1))
    return out


def format_orders(orders: list[Order], html: bool = False) -> str:
    """人看的格式。CLI 用純文字,Telegram 用 HTML(html=True)——
    同一份資料兩種排版,不是兩份實作。"""
    if not orders:
        return "今日無新單 —— 目標配置與現有持倉一致。"
    b = (lambda s: f"<b>{s}</b>") if html else (lambda s: s)
    c = (lambda s: f"<code>{s}</code>") if html else (lambda s: s)
    lines = []
    for o in orders:
        mark = "🟢 買入" if o.side == "BUY" else "🔴 賣出"
        s = (f"{mark} {b(o.symbol.replace('-USDT', ''))} "
             f"{c(f'{o.qty:.6g}')} @ {c(f'{o.price:,.6g}')}"
             f"({o.notional:,.0f} USDT)")
        if o.stop:
            s += f"\n    出場線 {c(f'{o.stop:,.6g}')}(距 {o.stop_pct:.1f}%)"
        s += f"\n    {o.reason}"
        lines.append(s)
    return "\n".join(lines)
