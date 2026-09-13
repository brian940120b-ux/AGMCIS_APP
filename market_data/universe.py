"""
AGMCIS — 幣種篩選器(Universe Selection)
從 BingX 全市場,用客觀且可重現的規則挑出交易池。

為什麼不用「我覺得這幾個幣好」:
  那是主觀偏見,而且無法回測、無法追溯。這裡的每一條規則都是數字,
  換誰來跑都得到同一份清單。

篩選規則(全部寫死):
  1. USDT 永續合約(排除其他計價與現貨)
  2. 24h 成交額 ≥ MIN_QUOTE_VOLUME(流動性:太薄的幣滑點會吃掉一切)
  3. 排除穩定幣與包裝幣(USDC/DAI/WBTC... 沒有波動,策略無用武之地)
  4. 排除代幣化傳統資產(商品/股票/指數)—— 名稱模式確定性硬擋
  5. 剩下的按「流動性 × 波動度」綜合分排序,取前 N 名

波動度用 24h 振幅(high-low)/ 收盤價 —— 策略需要價格會動,
但流動性權重更高:寧可交易一個穩定會動的大幣,不碰一個暴動的小幣。
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from core.logging import get_logger
from market_data.bingx_client import BingXClient

log = get_logger("universe")

STATE = Path(__file__).resolve().parents[1] / "data" / "universe.json"

MIN_QUOTE_VOLUME = 20_000_000.0     # 24h 成交額下限(USDT)
MIN_AMPLITUDE_PCT = 1.5             # 24h 振幅下限(%):完全不動的幣沒有機會
MAX_AMPLITUDE_PCT = 25.0            # 上限:暴動的幣多半是插針與拉盤,不碰
DEFAULT_SIZE = 10

# 穩定幣與包裝幣:沒有方向性行情
EXCLUDE_BASES = {
    "USDC", "USDT", "DAI", "TUSD", "BUSD", "FDUSD", "USDD", "PYUSD",
    "WBTC", "WETH", "STETH", "WSTETH", "CBETH",
}

# 代幣化傳統資產(原油/黃金/股票/指數):確定性名稱模式排除。
# 2026-07-15 教訓:LLM 稽核官失聲時,原油黃金混進了加密交易池,
# 一路走到讓紅隊在第一席的卷宗裡抓到「跨資產混雜」。
# 第一道防線必須是不會失聲的規則,LLM 只能是第二道。
TOKENIZED_PREFIXES = ("NCCO", "NCSK", "NCST", "NCIX")
TOKENIZED_MARKERS = ("2USD",)

# 錨定席(2026-07-16 操作者裁決):基準大幣永駐交易池,
# 不受振幅門檻與名次輪替影響。理由:樣本連續性 —— 大幣安靜日出池
# 會讓 BTC/ETH 採樣斷續、研究分組基準漂移。要加幣,改這行即可。
ANCHOR_SYMBOLS = ("BTC-USDT", "ETH-USDT", "SOL-USDT")


def _is_tokenized(base: str) -> bool:
    b = base.upper()
    if b.startswith(TOKENIZED_PREFIXES):
        return True
    return any(m in b for m in TOKENIZED_MARKERS)


@dataclass(frozen=True)
class Candidate:
    symbol: str
    quote_volume: float
    amplitude_pct: float
    last_price: float

    @property
    def score(self) -> float:
        """流動性為主(log 壓縮避免大幣完全壟斷),波動度為輔。"""
        import math
        liq = math.log10(max(self.quote_volume, 1.0))
        vol = min(self.amplitude_pct, 12.0) / 12.0     # 波動度封頂,避免追暴動幣
        return round(liq * (1.0 + vol), 4)


def _base(symbol: str) -> str:
    return symbol.split("-")[0].upper()


def screen(client: BingXClient | None = None,
           size: int = DEFAULT_SIZE) -> list[Candidate]:
    """跑一次全市場篩選,回傳排序後的候選清單。"""
    c = client or BingXClient()
    tickers = c.get_all_tickers()
    log.info(f"全市場 {len(tickers)} 個交易對")

    out: list[Candidate] = []
    anchors: list[Candidate] = []
    dropped_tok = 0
    for t in tickers:
        sym = t.get("symbol", "")
        if not sym.endswith("-USDT"):
            continue
        if _base(sym) in EXCLUDE_BASES:
            continue
        if _is_tokenized(_base(sym)):
            dropped_tok += 1
            continue
        try:
            qv = float(t.get("quoteVolume") or 0)
            hi = float(t.get("highPrice") or 0)
            lo = float(t.get("lowPrice") or 0)
            last = float(t.get("lastPrice") or 0)
        except (TypeError, ValueError):
            continue
        if qv < MIN_QUOTE_VOLUME or last <= 0 or hi <= 0 or lo <= 0:
            continue
        amp = (hi - lo) / last * 100
        if sym in ANCHOR_SYMBOLS:
            anchors.append(Candidate(sym, qv, round(amp, 2), last))
            continue
        if not (MIN_AMPLITUDE_PCT <= amp <= MAX_AMPLITUDE_PCT):
            continue
        out.append(Candidate(sym, qv, round(amp, 2), last))
    if dropped_tok:
        log.info(f"確定性排除代幣化資產 {dropped_tok} 個(NCCO/NCSK/2USD 系)")

    out.sort(key=lambda x: x.score, reverse=True)
    anchors.sort(key=lambda x: x.score, reverse=True)
    free = max(0, size - len(anchors))
    picked = anchors + out[:free]
    log.info(f"錨定席 {len(anchors)}({','.join(c.symbol for c in anchors)})"
             f" + 競爭席 {min(free, len(out))}(通過門檻 {len(out)} 個)")

    # 第二道防線:稽核官(只能否決,不能批准)
    # NCCOGOLD2USD 事件的教訓 —— 數值篩選擋不住「名字正常但本質不是加密貨幣」的東西
    try:
        from agents.auditor import audit_symbols
        kept_syms, res = audit_symbols([c.symbol for c in picked])
        if res.blocked:
            log.warning(f"稽核官擋下:{res.subject} — {'; '.join(res.reasons)}")
            kept = [c for c in picked
                    if c.symbol in kept_syms or c.symbol in ANCHOR_SYMBOLS]
            # 從候補遞補,補滿名額(錨定席不參與競爭)
            for c in out[free:]:
                if len(kept) >= size:
                    break
                if c.symbol not in {x.symbol for x in kept}:
                    kept.append(c)
            picked = kept[:size]
    except Exception as e:
        log.warning(f"稽核官不可用,沿用數值篩選結果:{e}")

    return picked


# 遲滯:現任成員只要仍在「前 N × HYSTERESIS」名內就不換掉。
# 目的:避免交易池每天追著漲幅榜換人 —— 那會讓任何幣都累積不到樣本,
#      而且本質上是在追高。穩定的池子才能產生可審判的數據。
HYSTERESIS = 1.5


def apply_hysteresis(new_ranked: list[Candidate], current: list[str],
                     size: int) -> list[Candidate]:
    """現任優先:仍在寬鬆名次內的成員留任,空缺才由新人遞補。
    例外:代幣化資產不受遲滯保護 —— 它們本就不該在池裡,一律逐出。"""
    current = [s for s in (current or []) if not _is_tokenized(_base(s))]
    if not current:
        return new_ranked[:size]
    keep_zone = new_ranked[:int(size * HYSTERESIS)]
    survivors = [c for c in keep_zone if c.symbol in current]
    out = list(survivors)
    for c in new_ranked:
        if len(out) >= size:
            break
        if c.symbol not in {x.symbol for x in out}:
            out.append(c)
    # 錨定席永不被遲滯輪替擠出
    anc = [c for c in new_ranked if c.symbol in ANCHOR_SYMBOLS]
    rest = [c for c in out if c.symbol not in ANCHOR_SYMBOLS]
    return (anc + rest)[:size]


def save(picked: list[Candidate]) -> dict:
    payload = {
        "updated": datetime.now(timezone.utc).isoformat(),
        "rules": {
            "min_quote_volume": MIN_QUOTE_VOLUME,
            "amplitude_pct": [MIN_AMPLITUDE_PCT, MAX_AMPLITUDE_PCT],
            "excluded_bases": sorted(EXCLUDE_BASES),
            "tokenized_excluded": list(TOKENIZED_PREFIXES) + list(TOKENIZED_MARKERS),
            "anchors": list(ANCHOR_SYMBOLS),
        },
        "symbols": [c.symbol for c in picked],
        "detail": [{"symbol": c.symbol, "quote_volume": round(c.quote_volume),
                    "amplitude_pct": c.amplitude_pct, "score": c.score}
                   for c in picked],
    }
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                     encoding="utf-8")
    return payload


def load(fallback: list[str] | None = None) -> list[str]:
    """讀取目前交易池。沒有檔案 → 回退到預設五幣(不讓系統因此停擺)。"""
    default = fallback or ["BTC-USDT", "ETH-USDT", "SOL-USDT",
                           "XRP-USDT", "BNB-USDT"]
    if not STATE.exists():
        return default
    try:
        d = json.loads(STATE.read_text(encoding="utf-8"))
        syms = d.get("symbols") or []
        return syms if syms else default
    except Exception as e:
        log.warning(f"讀取交易池失敗,回退預設:{e}")
        return default
