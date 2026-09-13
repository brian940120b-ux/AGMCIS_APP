"""
/health —— Master Prompt 第六十六條 · 2026-09-13

═══ 為什麼要有這一支 ═══
巡檢(sentinel)已經在跑,而且跑得不錯。但它的輸出是給人看的:
一個 JSON 狀態檔、一封 Telegram。**沒有任何機器可以問一句
「你現在好不好」然後得到一個是或否。**

差別在哪裡:sentinel 每十分鐘醒一次。如果面板整個掛了,
下一次有人知道是十分鐘後 —— 而且前提是通知那條路還通。
`CLAUDE.md` 教訓第 11 條記著:日報從腳本存在那天起就沒有真的送出去過。
**依賴「會有人通知我」的監控,壞掉的時候剛好也不會通知你。**

一個 HTTP 端點是相反的:外面的東西主動來問,問不到就是壞了。

═══ 這支的死規則:不可以永遠回 OK ═══
一個永遠回 200 的 /health 比沒有 /health 危險 —— 它會讓上面接的
每一層監控都變成綠燈,而綠燈的理由是它什麼都沒在看。
(教訓第 5 條:說謊的稽核比沒有稽核危險。)

所以:

  · 任何一條檢查不過 -> HTTP 503,不是 200。
  · **算不出來的檢查一律當成不過。** 讀不到檔案、解析失敗、
    拋例外 —— 全部是 fail,不是 skip、不是「假設沒問題」。
  · 連檢查本身拋例外都要變成一條 fail 的檢查,而不是讓整個
    /health 500 掉(那樣監控看到的是「服務掛了」,不是「哪裡壞了」)。

═══ 有沒有金鑰看到的東西不一樣 ═══
監控探針通常沒有辦法帶 DASHBOARD_KEY。所以不帶金鑰也能問,
但只會拿到每條檢查的名字與過或不過 —— 那正是探針需要的全部。
數字、路徑、時間細節要帶金鑰才給。
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
DATA = BASE / "data"

# 記帳超過這麼久沒動就是不健康。與風控的 max_data_age_hours 對齊:
# 每日週期 24 小時 + 補跑緩衝。
STALE_BOOKKEEPING_H = 30.0

# 交易所規格快取。與 specs.STALE_S 同一個依據(規格很少變,但會變)。
STALE_SPECS_DAYS = 7.0


@dataclass
class Check:
    name: str
    ok: bool
    detail: str

    def public(self) -> dict:
        """不帶金鑰時能看到的:名字與過不過,沒有細節。"""
        return {"name": self.name, "ok": self.ok}

    def full(self) -> dict:
        return {"name": self.name, "ok": self.ok, "detail": self.detail}


def _json(path: Path):
    """讀不到就拋 —— 這一支裡「讀不到」永遠是一條 fail,不是 None。"""
    return json.loads(path.read_text(encoding="utf-8"))


def _last_line(path: Path) -> dict:
    last = None
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                last = line
    if last is None:
        raise ValueError("檔案是空的")
    return json.loads(last)


def _guard(name: str, fn) -> Check:
    """
    跑一條檢查。它自己爆炸的話,那就是一條 fail 的檢查。

    **不可以讓它變成整個 /health 500。** 500 告訴監控「服務掛了」,
    而實際上是「有一條檢查有問題」—— 兩件事的處理方式不一樣。
    """
    try:
        return fn()
    except Exception as e:                      # noqa: BLE001 — 見上
        return Check(name, False,
                     f"檢查本身失敗:{type(e).__name__}: {e}")


def check_bookkeeping() -> Check:
    def run():
        row = _last_line(DATA / "portfolio_equity.jsonl")
        stamped = row.get("t")
        if not stamped:
            return Check("記帳", False, "最後一筆沒有時間戳")
        from datetime import datetime, timezone
        when = datetime.fromisoformat(stamped)
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        hours = (datetime.now(timezone.utc) - when).total_seconds() / 3600
        ok = hours <= STALE_BOOKKEEPING_H
        return Check("記帳", ok,
                     f"最後一次 {stamped}({hours:.1f} 小時前,"
                     f"上限 {STALE_BOOKKEEPING_H:.0f})")
    return _guard("記帳", run)


def check_exchange_specs() -> Check:
    def run():
        d = _json(DATA / "bingx_specs.json")
        days = (time.time() - float(d.get("updated") or 0)) / 86400
        n = len(d.get("contracts") or {})
        if n == 0:
            return Check("交易所規格", False, "快取是空的")
        ok = days <= STALE_SPECS_DAYS
        return Check("交易所規格", ok,
                     f"{n} 個合約,{days:.1f} 天前更新"
                     f"(上限 {STALE_SPECS_DAYS:.0f} 天)")
    return _guard("交易所規格", run)


def check_live_gate() -> Check:
    """
    **這一條是整個 /health 最重要的。**

    其他每一條講的是「系統好不好」,這一條講的是「它有沒有在動真錢」。
    LIVE_ENABLED 是原始碼常數(第四十五 / 七十八條),它變成 True
    只可能是有人 commit 了那個改動。/health 要能當場說出來。
    """
    def run():
        from portfolio.execution import LIVE_ENABLED
        return Check("實盤閘門", LIVE_ENABLED is False,
                     "LIVE_ENABLED=False(紙上)" if LIVE_ENABLED is False
                     else f"⚠️ LIVE_ENABLED={LIVE_ENABLED!r} —— 這是真錢")
    return _guard("實盤閘門", run)


def check_risk() -> Check:
    def run():
        d = _json(DATA / "portfolio_risk.json")
        verdict = d.get("verdict")
        failures = [c.get("name") for c in (d.get("checks") or [])
                    if not c.get("passed")]
        # REJECT 不代表系統壞了 —— 代表風控正在做它的工作。
        # 但它必須看得見,所以照樣回報,只是不算 fail。
        ok = verdict in ("ALLOW", "REDUCE", "REJECT")
        detail = f"最近判決 {verdict}"
        if failures:
            detail += ";未通過:" + "、".join(str(f) for f in failures)
        return Check("風控", ok, detail)
    return _guard("風控", run)


def check_rate_limit() -> Check:
    def run():
        from core import ratelimit
        s = ratelimit.state()
        if not s.get("readable"):
            # 讀不懂就是不正常。回一個 tokens=None 然後當成健康,
            # 是這一整支最不該犯的錯。
            return Check("限流", False, "限流狀態檔讀不懂 —— 有東西在亂寫它")
        if s.get("blocked"):
            return Check("限流", False,
                         f"冷卻中,還有 {s.get('blocked_for_s')} 秒 —— "
                         "交易所回過 429 或 418")
        return Check("限流", True,
                     f"令牌 {s.get('tokens')}/{s.get('burst')}"
                     f"({s.get('rate_per_s')}/s)")
    return _guard("限流", run)


def check_contract() -> Check:
    """
    畢業契約的進度。**進度不足不算不健康** —— 它本來就該不足,
    這套系統的立場是還沒有資格碰真錢。這裡只是把數字說出來。
    """
    def run():
        d = _json(DATA / "portfolio_contract.json")
        passed, total = d.get("passed", 0), d.get("total", 8)
        return Check("實盤資格契約", True, f"{passed}/{total} 條通過")
    return _guard("實盤資格契約", run)


def check_events() -> Check:
    """
    事件日曆(第五十一條)。

    「沒有載入日曆」與「今天沒有事件」是兩句完全不同的話,
    這裡不會把前者講成後者。

    沒有日曆算不健康嗎?**算。** 不是因為今天會出事,是因為
    這一項本來就該有人維護,而沒有維護是一個要被看見的狀態 ——
    一份停在半年前的日曆會永遠回「沒有事件」,那是最安靜的壞法。
    """
    def run():
        from portfolio import events
        st = events.status()
        if not st["loaded"]:
            return Check("事件日曆", False,
                         "沒有載入 —— 這代表沒有在看,不代表沒有事件")
        if st["stale"]:
            return Check("事件日曆", False, st["reason"])
        today = st["today"]
        soon = st["upcoming"]
        if today:
            names = "、".join(e["kind"] for e in today)
            return Check("事件日曆", True, f"今天:{names}")
        return Check("事件日曆", True,
                     f"今天無事件,未來 14 天內 {len(soon)} 筆")
    return _guard("事件日曆", run)


CHECKS = (check_bookkeeping, check_exchange_specs, check_live_gate,
          check_risk, check_rate_limit, check_contract, check_events)


def report(detailed: bool = False) -> tuple:
    """
    回 (HTTP 狀態碼, payload)。

    任何一條不過就是 503。**沒有「大部分還好」這個選項** ——
    一個會自己決定哪些失敗不算數的健康檢查,遲早會把所有失敗
    都算成不算數。
    """
    checks = [fn() for fn in CHECKS]
    healthy = all(c.ok for c in checks)

    payload = {
        "status": "ok" if healthy else "unhealthy",
        "checks": [c.full() if detailed else c.public() for c in checks],
    }
    if detailed:
        from datetime import datetime, timezone
        payload["t"] = datetime.now(timezone.utc).isoformat()

    return (200 if healthy else 503), payload
