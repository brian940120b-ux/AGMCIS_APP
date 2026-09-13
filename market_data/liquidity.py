"""
AGMCIS — 流動性剖析(每幣種滑點模型)
對應架構文件:回測必須「含手續費、滑點」—— 但滑點不該是拍腦袋的固定值。

問題:
  我們原本對所有幣用同一個滑點(先 0.02%,後改 0.08%),兩個都是猜的。
  BTC 的真實價差可能只有 0.01%,而小型幣可能是 0.15% —— 用平均值套所有幣,
  等於同時高估了大幣的成本、低估了小幣的成本。兩邊都錯。

做法:
  用訂單簿的真實買賣價差(spread)推估滑點:
    spread_pct = (ask - bid) / mid × 100
    市價單至少要跨過半個價差 → 基礎滑點 = spread_pct / 2
    再乘上衝擊係數(市價單會吃掉不只第一檔)
  下限保護:再流動的幣也不假設零滑點。

誠實聲明:
  這是「靜態快照」推估,不是完整的訂單簿衝擊模型。真實滑點還取決於下單當下
  的簿子厚度與波動。所以我們一律取「悲觀方向」—— 寧可高估自己的成本。
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from core import ratelimit
from core.logging import get_logger

log = get_logger("liquidity")

STATE = Path(__file__).resolve().parents[1] / "data" / "liquidity.json"
DEPTH_STATE = Path(__file__).resolve().parents[1] / "data" / "depth_impact.json"

IMPACT_MULTIPLIER = 2.0      # 市價單不只吃第一檔:半個價差 × 此係數
MIN_SLIPPAGE_PCT = 0.02      # 下限:再流動的幣也不假設零滑點
MAX_SLIPPAGE_PCT = 0.50      # 上限:超過此值代表流動性太差,不該交易
STALE_HOURS = 24.0           # 超過此時數的剖析視為過期


@dataclass(frozen=True)
class LiquidityProfile:
    symbol: str
    spread_pct: float
    slippage_pct: float
    tradable: bool
    note: str = ""


def measure_impact(symbol: str, notional_usdt: float,
                   api: str = "https://open-api.bingx.com") -> dict:
    """用**真實訂單簿**量「這個規模的市價單會付多少衝擊成本」。

    ═══ 2026-09-06:為什麼要量而不是假設 ═══
    法庭的悲觀檔用 0.19% 當滑點下限,而實測 BingX:
      半價差中位數 0.0034%
      10,000 USDT 市價單的衝擊成本 BTC 0.0000% / ETH 0.0005% / ENA 0.0072%
      簿子深度 570 萬 ~ 6,300 萬 USDT —— 我們的單是簿子的 0.02%
    也就是假設值比實測高 **38 倍**。

    那不是保守,是**在量另一個遊戲**:一個 38 倍成本的世界裡,
    任何合理的短線優勢都會被判死。1351 案幾乎全滅,
    有多少是策略不好、有多少是成本假設不對,在量出來之前沒有人知道。

    本函式不改任何門檻,只把「真實成本是多少」變成可查證的數字。
    要不要換判準是執政官的裁決 —— 但那個裁決必須建立在量測上,
    不是建立在另一個猜測上。
    """
    import requests
    out = {"symbol": symbol, "notional": notional_usdt,
           "measured_at": datetime.now(timezone.utc).isoformat()}
    try:
        r = ratelimit.requests_get(
            None, f"{api}/openApi/swap/v2/quote/depth",
            params={"symbol": symbol, "limit": 50}, timeout=10)
        d = r.json().get("data") or {}
        asks = sorted((float(p), float(q)) for p, q in (d.get("asks") or []))
        bids = sorted(((float(p), float(q)) for p, q in (d.get("bids") or [])),
                      reverse=True)
    except Exception as e:
        log.warning(f"{symbol} 訂單簿取得失敗:{e}")
        return dict(out, available=False, error=str(e))
    if not asks or not bids:
        return dict(out, available=False, error="訂單簿為空")

    def _walk(levels: list[tuple[float, float]]) -> float | None:
        """吃掉 notional 需要的均價,回傳相對最佳價的偏離(%)。"""
        best = levels[0][0]
        need, spent, got = notional_usdt, 0.0, 0.0
        for px, qty in levels:
            take = min(px * qty, need)
            got += take / px
            spent += take
            need -= take
            if need <= 0:
                break
        if need > 0 or got <= 0:
            return None            # 簿子不夠深 —— 不猜,回 None
        return abs(spent / got / best - 1) * 100

    buy, sell = _walk(asks), _walk(bids)
    mid = (asks[0][0] + bids[0][0]) / 2
    out.update({
        "available": buy is not None and sell is not None,
        "spread_pct": round((asks[0][0] - bids[0][0]) / mid * 100, 6),
        "impact_buy_pct": round(buy, 6) if buy is not None else None,
        "impact_sell_pct": round(sell, 6) if sell is not None else None,
        "book_depth_usdt": round(sum(p * q for p, q in asks), 0),
    })
    if out["available"]:
        # 單邊實際滑點 = 半價差 + 衝擊(取買賣較差的一邊,保守)
        out["slippage_pct"] = round(out["spread_pct"] / 2
                                    + max(buy, sell), 6)
    return out


def profile_symbol(client, symbol: str) -> LiquidityProfile:
    """用 ticker 的 bid/ask 推估。失敗 → 回退保守值(不讓資料缺失變成樂觀假設)。"""
    try:
        t = client.get_ticker(symbol)
        bid, ask = t.bid, t.ask
        if not bid or not ask or bid <= 0 or ask <= 0 or ask < bid:
            raise ValueError("bid/ask 無效")
        mid = (bid + ask) / 2
        spread_pct = (ask - bid) / mid * 100
    except Exception as e:
        log.warning(f"{symbol} 流動性剖析失敗,採用保守預設:{e}")
        return LiquidityProfile(symbol, 0.0, MAX_SLIPPAGE_PCT / 2, True,
                                "無報價資料,採保守滑點")

    slip = spread_pct / 2 * IMPACT_MULTIPLIER
    slip = max(MIN_SLIPPAGE_PCT, min(MAX_SLIPPAGE_PCT, slip))
    tradable = slip < MAX_SLIPPAGE_PCT
    note = "" if tradable else "價差過大,流動性不足以交易"
    return LiquidityProfile(symbol, round(spread_pct, 4), round(slip, 4),
                            tradable, note)


def profile_all(client, symbols: list[str]) -> dict:
    profiles = {}
    for s in symbols:
        p = profile_symbol(client, s)
        profiles[s] = {"spread_pct": p.spread_pct, "slippage_pct": p.slippage_pct,
                       "tradable": p.tradable, "note": p.note}
    payload = {"updated": datetime.now(timezone.utc).isoformat(),
               "profiles": profiles,
               "method": "spread/2 × 衝擊係數,取悲觀方向"}
    try:
        STATE.parent.mkdir(parents=True, exist_ok=True)
        STATE.write_text(json.dumps(payload, ensure_ascii=False, indent=1),
                         encoding="utf-8")
    except Exception as e:
        log.warning(f"流動性剖析寫入失敗:{e}")
    log.info(f"流動性剖析完成:{len(profiles)} 個幣種")
    return payload


def load() -> dict:
    if not STATE.exists():
        return {}
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def slippage_for(symbol: str, fallback: float = 0.08) -> float:
    """取得該幣種的滑點。無資料或過期 → 回退到保守預設(不因缺資料而樂觀)。"""
    d = load()
    if not d:
        return fallback
    try:
        age_h = (datetime.now(timezone.utc)
                 - datetime.fromisoformat(d["updated"])).total_seconds() / 3600
        if age_h > STALE_HOURS:
            return fallback
        p = d.get("profiles", {}).get(symbol)
        return float(p["slippage_pct"]) if p else fallback
    except Exception:
        return fallback


def is_tradable(symbol: str) -> tuple[bool, str]:
    """流動性是否足以交易(風控會用)。"""
    d = load()
    p = (d.get("profiles") or {}).get(symbol)
    if not p:
        return True, ""      # 無資料不阻擋,由滑點的保守預設承擔
    return bool(p.get("tradable", True)), str(p.get("note", ""))
