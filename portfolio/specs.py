"""
新系統 — 交易所合約規格 · 2026-09-09

═══ 為什麼需要這一支 ═══
2026-09-09 排查發現:七個持倉的數量**全部**不符合 BingX 的數量精度,
真的送出去會被交易所直接拒單。原因是訂單層用 `notional / price` 算數量,
得到的是全精度浮點數(例如 UNI 103.14679692),而交易所只接受
該幣種規定的位數(UNI 是 0 位,只能是整數)。

紙上交易看不出這件事 —— 帳本照收、面板照顯示、測試照過,
但那些單在真實交易所一張都送不出去。這正是「乾淨退出 0 不代表
承諾的事真的發生了」的同一類錯:每個環節看起來都正常。

═══ 規格來自交易所本身,不是我寫的常數 ═══
公開端點 /openApi/swap/v2/quote/contracts(不需金鑰,已實測),
落地快取到 data/bingx_specs.json,由 daily.py 每日更新。

═══ 缺規格就報錯,絕不用預設值頂替 ═══
舊系統的教訓第五條:`getattr` 靜靜回 None → 檢查被跳過 → 稽核天天
回報「未發現異常」。這裡同樣:查不到某個幣的規格就拋例外,
讓它當場停下來,而不是猜一個精度然後產生一張會被拒的單。
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.atomic import write_json_atomic
from core.logging import get_logger

log = get_logger("portfolio.specs")

BASE = Path(__file__).resolve().parents[1]
CACHE = BASE / "data" / "bingx_specs.json"
ENDPOINT = ("https://open-api.bingx.com/openApi/swap/v2/quote/contracts")
STALE_S = 7 * 24 * 3600          # 超過七天沒更新就警告(規格很少變,但會變)

_MEM: dict = {}


class SpecMissing(RuntimeError):
    """查不到規格。故意讓它中斷,不給預設值。"""


def refresh() -> int:
    """從交易所抓一次規格並落地。回傳抓到幾個合約。"""
    import urllib.request
    with urllib.request.urlopen(ENDPOINT, timeout=15) as r:
        d = json.loads(r.read().decode("utf-8"))
    if str(d.get("code")) != "0":
        raise RuntimeError(f"BingX contracts code={d.get('code')} {d.get('msg')}")
    rows = {}
    for c in (d.get("data") or []):
        sym = c.get("symbol")
        if not sym:
            continue
        rows[sym] = {
            "size": float(c["size"]),
            "quantity_precision": int(c["quantityPrecision"]),
            "price_precision": int(c["pricePrecision"]),
            "min_qty": float(c["tradeMinQuantity"]),
            "min_notional": float(c["tradeMinUSDT"]),
            "taker_fee": float(c["takerFeeRate"]),
            "maker_fee": float(c["makerFeeRate"]),
            "status": int(c.get("status", 0)),
        }
    write_json_atomic(CACHE, {"updated": time.time(), "contracts": rows})
    _MEM.clear()
    log.info(f"交易所規格更新 {len(rows)} 個合約")
    return len(rows)


def _load() -> dict:
    if _MEM:
        return _MEM
    try:
        d = json.loads(CACHE.read_text(encoding="utf-8"))
    except Exception as e:
        raise SpecMissing(
            f"讀不到交易所規格快取 {CACHE.name}:{e}。"
            "先跑 python -c 'from portfolio.specs import refresh; refresh()'"
        ) from e
    age = time.time() - float(d.get("updated") or 0)
    if age > STALE_S:
        log.warning(f"交易所規格已 {age / 86400:.1f} 天沒更新")
    _MEM.update(d.get("contracts") or {})
    if not _MEM:
        raise SpecMissing("交易所規格快取是空的")
    return _MEM


def spec(symbol: str) -> dict:
    """取一個幣的規格。查不到就拋 SpecMissing —— 不猜。"""
    s = _load().get(symbol)
    if s is None:
        raise SpecMissing(
            f"交易所沒有 {symbol} 的合約規格 —— 這個幣不能下單。"
            "(不給預設值:猜一個精度只會產生一張會被拒的單)")
    return s


def round_qty(symbol: str, qty: float) -> float:
    """把數量調整成交易所接受的精度。

    **一律無條件捨去,不四捨五入。** 進位會讓實際部位大於本來要的規模,
    佔用比預期多的保證金;捨去最多只是少買一點點。在「可能超出保證金」
    與「少買一點」之間,永遠選後者。
    """
    p = spec(symbol)["quantity_precision"]
    f = 10 ** p
    # 先做極小量的容差修正,避免 0.28999999999 這種浮點誤差被砍成 0.28
    return int(abs(qty) * f + 1e-9) / f * (1 if qty >= 0 else -1)


def round_price(symbol: str, price: float) -> float:
    """把價格調整成交易所接受的精度(價格用四捨五入,不影響部位大小)。"""
    return round(price, spec(symbol)["price_precision"])


def min_qty(symbol: str) -> float:
    return spec(symbol)["min_qty"]


def min_notional(symbol: str) -> float:
    """交易所的最小名目(USDT)。BingX 目前七個幣都是 2。"""
    return spec(symbol)["min_notional"]


def taker_fee_pct(symbol: str) -> float:
    """該幣的吃單費率(%)。交易所是逐幣給的,不是全域常數。"""
    return spec(symbol)["taker_fee"] * 100.0


def maker_fee_pct(symbol: str) -> float:
    return spec(symbol)["maker_fee"] * 100.0


def tradable(symbol: str) -> bool:
    return spec(symbol)["status"] == 1


def main() -> int:
    n = refresh()
    print(f"已更新 {n} 個合約規格 → {CACHE}")
    from portfolio.paper import SYMBOLS
    print()
    print(f"{'幣種':12s} {'數量精度':>8s} {'價格精度':>8s} {'最小量':>10s} "
          f"{'最小USDT':>9s} {'taker%':>8s}")
    for s in SYMBOLS:
        sp = spec(s)
        print(f"{s:12s} {sp['quantity_precision']:>8} "
              f"{sp['price_precision']:>8} {sp['min_qty']:>10} "
              f"{sp['min_notional']:>9} {taker_fee_pct(s):>8.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


# ══════════════════════════════════════════════════════════
# 資金費率:交易所**實際結算過**的歷史
#
# 2026-09-09 排查發現:我們原本用 gauge.py 抽樣快照的中位數當費率,
# 但交易所有公布每一次結算的**真實費率**(/quote/fundingRate,最多
# 1000 筆 ≈ 333 天)。兩者實測相差:平均年化 1.74%,UNI 差到 7.34%,
# 而且方向是**我們少收**—— 前向績效因此被美化。
#
# 抽樣中位數與實際結算是兩件不同的東西:
#   · gauge 抽的是任意時點的**預測費率**(它會一直變動到結算那一刻)
#   · 這裡拿的是結算當下**真的被收走**的那個數字
# 要跟交易所一樣,就該用後者。gauge 的資料保留作獨立交叉驗證。
# ══════════════════════════════════════════════════════════
FUNDING_CACHE = BASE / "data" / "bingx_funding.json"
FUNDING_EP = "https://open-api.bingx.com/openApi/swap/v2/quote/fundingRate"
_FUND: dict = {}


def refresh_funding(symbols: list[str], limit: int = 1000) -> int:
    """抓每個幣實際結算過的資金費率歷史並落地。回傳總筆數。"""
    import urllib.parse
    import urllib.request
    out: dict[str, list] = {}
    for sym in symbols:
        q = urllib.parse.urlencode({"symbol": sym, "limit": int(limit)})
        with urllib.request.urlopen(f"{FUNDING_EP}?{q}", timeout=20) as r:
            d = json.loads(r.read().decode("utf-8"))
        if str(d.get("code")) != "0":
            raise RuntimeError(
                f"BingX fundingRate {sym} code={d.get('code')} {d.get('msg')}")
        rows = []
        for x in (d.get("data") or []):
            try:
                rows.append({"t": int(x["fundingTime"]),
                             "rate": float(x["fundingRate"])})
            except (KeyError, TypeError, ValueError):
                continue
        rows.sort(key=lambda r: r["t"])
        out[sym] = rows
    write_json_atomic(FUNDING_CACHE,
                      {"updated": time.time(), "symbols": out})
    _FUND.clear()
    n = sum(len(v) for v in out.values())
    log.info(f"資金費率歷史更新 {len(out)} 幣、{n} 筆結算")
    return n


def _load_funding() -> dict:
    if _FUND:
        return _FUND
    try:
        d = json.loads(FUNDING_CACHE.read_text(encoding="utf-8"))
    except Exception as e:
        raise SpecMissing(
            f"讀不到資金費率快取 {FUNDING_CACHE.name}:{e}。"
            "先跑 scripts/daily.py 或 portfolio/specs.py"
        ) from e
    _FUND.update(d.get("symbols") or {})
    if not _FUND:
        raise SpecMissing("資金費率快取是空的")
    return _FUND


def funding_settlements(symbol: str, since_ms: int,
                        until_ms: int) -> list[dict]:
    """(since, until] 之間交易所**實際結算**的資金費列表。

    區間刻意左開右閉:結算時點正好落在邊界時只能被算一次,
    不然跨日重跑會重複收費。
    """
    rows = _load_funding().get(symbol)
    if rows is None:
        raise SpecMissing(f"沒有 {symbol} 的資金費率歷史 —— 不猜一個數字")
    return [r for r in rows if since_ms < r["t"] <= until_ms]


def expected_settlements(since_ms: int, until_ms: int) -> int:
    """(since, until] 之間**應該**發生幾次結算。

    交易所固定在 00:00 / 08:00 / 16:00 UTC 結算,所以這件事算得出來,
    不需要跟交易所要。用途見 funding_rate_sum 的守門。
    """
    if until_ms <= since_ms:
        return 0
    step = 8 * 3600 * 1000
    first = (since_ms // step + 1) * step        # since 之後的第一個結算點
    if first > until_ms:
        return 0
    return int((until_ms - first) // step) + 1


def funding_rate_sum(symbol: str, since_ms: int, until_ms: int) -> float:
    """該區間交易所實際收走的資金費率合計(小數,非百分比)。

    這就是「跟交易所一模一樣」的收法:不估計、不取中位數,
    把那段時間真的結算過的每一筆加起來。

    ═══ 快取過期必須出聲,不能安靜地回 0 ═══
    如果快取沒更新,這個函式會找不到任何結算而回傳 0 —— 等於**不收
    資金費**,帳本、面板、測試全部正常,績效被靜靜美化。那正是這座
    城邦一路在防的錯:「絕不用預設值吞掉錯誤」。
    所以這裡比對「應該結算幾次」與「實際有幾筆」,少了就拋例外。
    """
    got = funding_settlements(symbol, since_ms, until_ms)
    want = expected_settlements(since_ms, until_ms)
    if len(got) < want:
        raise SpecMissing(
            f"{symbol} 資金費資料不全:該區間應有 {want} 次結算,"
            f"快取只有 {len(got)} 筆 —— 快取過期。"
            "拒絕用不完整的資料記帳(那會靜靜少收資金費、美化績效)。"
            "跑 scripts/daily.py 或 portfolio.specs.refresh_funding() 更新。")
    return sum(r["rate"] for r in got)


def funding_recent_median_8h_pct(symbol: str, n: int = 90) -> float:
    """最近 n 次實際結算費率的中位數(%/8h)。

    用途是估算(例如回測、面板顯示年化成本),不是用來記帳 ——
    記帳一律用 funding_rate_sum() 收真實結算值。
    """
    import statistics
    rows = _load_funding().get(symbol)
    if not rows:
        raise SpecMissing(f"沒有 {symbol} 的資金費率歷史")
    vals = [r["rate"] * 100 for r in rows[-n:]]
    return statistics.median(vals)
