"""
AGMCIS — 測量官(Gauge・觀察模式)
執行:systemd timer 每 15 分鐘(也可手動:.venv/bin/python scripts/gauge.py)

═══ 職權(寫死)═══
做:採集交易池各幣的「結構性數據」—— 資金費率(funding rate)與
    未平倉量(open interest)—— 寫入 gauge_board.json(最新快照)
    與 gauge_history.jsonl(歷史,供未來假說做無前視回測)。
不做:永不開倉、永不觸發任何交易訊號。
      「費率極端該反向」「OI 背離該過濾」都是待證假說 ——
      證據錄滿一兩個月,才有資格上法庭。

為什麼是這兩個數:
  資金費率 = 永續合約多空博弈的體溫計,極端值 = 單邊擁擠;
  OI 變化 = 錢的進出。價漲+OI增 = 新錢進場;價漲+OI減 = 空頭回補。
  這些是「多數散戶不看」的結構性資訊 —— 不比速度,比理解。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path as _P
sys.path.insert(0, str(_P(__file__).resolve().parents[1]))
from core.atomic import write_json_atomic
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.logging import get_logger
from market_data.universe import load as load_pool

log = get_logger("gauge")
DATA = Path(__file__).resolve().parents[1] / "data"
BOARD = DATA / "gauge_board.json"
HISTORY = DATA / "gauge_history.jsonl"

API = "https://open-api.bingx.com"
TIMEOUT = 10


def _get(path: str, symbol: str) -> dict:
    try:
        r = requests.get(f"{API}{path}", params={"symbol": symbol},
                         timeout=TIMEOUT)
        d = r.json()
        return d.get("data") or {}
    except Exception as e:
        log.warning(f"{symbol} {path} 失敗:{e}")
        return {}


def fetch_all_premium() -> dict[str, dict]:
    """一次抓全市場費率與標記價(2026-09-05)。

    premiumIndex 不帶 symbol 參數時回傳全部 919 個交易對 ——
    原本每幣打一次是 10 次呼叫,現在 1 次。省下來的不只是配額:
    全池費率是「同一瞬間」的快照,逐幣輪詢會有數秒的時間偏移,
    比較跨幣費率時那個偏移是雜訊。

    順帶把 markPrice 一起收下來 —— 它本來就在同一份回應裡,不多花任何成本。
    在此之前測量官只存費率與 OI 不存價格,導致案五要算前向報酬時
    得回頭翻 K 線快取(慢、且輪替出池的幣會查不到)。
    """
    try:
        r = requests.get(f"{API}/openApi/swap/v2/quote/premiumIndex",
                         timeout=TIMEOUT)
        rows = r.json().get("data") or []
    except Exception as e:
        log.warning(f"全市場費率抓取失敗,退回逐幣模式:{e}")
        return {}
    out = {}
    for d in rows:
        s = d.get("symbol")
        if s:
            out[s] = d
    return out


def _f(d: dict, key: str) -> float | None:
    try:
        return float(d.get(key))
    except (TypeError, ValueError):
        return None


def measure(symbol: str, prem: dict | None = None) -> dict | None:
    """單幣測量。prem 由批次結果餵入時不再打網路(省 1 次呼叫/幣)。"""
    if prem is None:
        prem = _get("/openApi/swap/v2/quote/premiumIndex", symbol)
    oi = _get("/openApi/swap/v2/quote/openInterest", symbol)
    fr = _f(prem, "lastFundingRate")
    oi_v = _f(oi, "openInterest")
    if fr is None and oi_v is None:
        return None
    return {"symbol": symbol,
            "funding_rate": fr,                      # 例:0.0001 = 0.01%/8h
            "funding_pct_8h": round(fr * 100, 4) if fr is not None else None,
            "open_interest": oi_v,
            # 2026-09-05 新增:案五算前向報酬需要事件當下的價格
            "mark_price": _f(prem, "markPrice")}


def main() -> int:
    pool = load_pool()
    prem_all = fetch_all_premium()
    rows = [r for s in pool if (r := measure(s, prem_all.get(s)))]
    if not rows:
        print("測量失敗:全池無回應")
        return 1
    ts = datetime.now(timezone.utc).isoformat()

    # 附上一次 OI,算變化率(給人讀;歷史檔存原始值,回測自己算)
    prev = {}
    try:
        prev = {r["symbol"]: r for r in
                json.loads(BOARD.read_text(encoding="utf-8")).get("rows", [])}
    except Exception:
        pass
    for r in rows:
        p = prev.get(r["symbol"], {}).get("open_interest")
        r["oi_change_pct"] = (round((r["open_interest"] / p - 1) * 100, 2)
                              if p and r["open_interest"] else None)

    DATA.mkdir(parents=True, exist_ok=True)
    write_json_atomic(BOARD, {                # 原子寫入(2026-09-07)
        "updated": ts, "rows": rows,
        "note": "測量官觀察模式:僅記錄,不開倉。"})
    with HISTORY.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": ts, "rows": rows}, ensure_ascii=False)
                + "\n")

    print(f"=== 測量官結構快照({len(rows)} 幣・觀察,不開倉)===")
    rows.sort(key=lambda r: abs(r.get("funding_pct_8h") or 0), reverse=True)
    for r in rows:
        fp = r.get("funding_pct_8h")
        oc = r.get("oi_change_pct")
        print(f"{r['symbol']:<14} 費率 "
              f"{(f'{fp:+.4f}%' if fp is not None else '   —  '):>9}/8h"
              f" | OI {(f'{oc:+.2f}%' if oc is not None else '—'):>8}(15m)")

    # ── 掛載式副作用(2026-09-05)────────────────────────────
    # 兩者都是輔助測量,失敗只印警告 —— 測量官的本職是採集,
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
