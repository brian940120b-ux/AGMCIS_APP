"""
歷史 K 線下載 + 本地累積式快取(供回測與研究實驗室共用)。

═══ 2026-09-06 重寫:為什麼舊版是全系統最貴的一個 bug ═══
舊版的快取鍵是 `{幣}_{週期}_{起日}_{訖日}`,而訖日永遠是「今天」。
於是每過一天就產生一個新鍵 → 新檔 → 重新向交易所下載。後果三重:

  一、**歷史被永久丟棄。** 交易所各週期的保留量不同,實測:
      15m ≈ 9,600 根(100 天)· 4h ≈ 2,000 根(333 天)· 1d ≈ 1,943 根(5.3 年)
      2026-07-18 下載的 15m 檔有 24,237 根(252 天)—— 那段歷史交易所
      現在已經不給了,但它就躺在 data/history 裡,因為鍵不匹配而永遠讀不到。
  二、**研究窗口從 252 天縮到 100 天,而且沒有任何人知道。**
      lab.py 寫 DAYS=300,實際只拿到 100 天;守門條件是
      `len(k15) < warmup + 500`(2000 根),9,600 根輕鬆通過,不會報錯。
      歷史越短,誤判越多 —— 真有優勢的策略因樣本不足被斃,
      沒優勢的因雜訊蒙混過關。這直接傷害整個法庭的判決品質。
  三、2,519 個快取檔、6.0 GB,絕大多數是同幾個幣的重複下載。

新版:鍵只有 `{幣}_{週期}`,一個幣一個週期永遠只有一份,持續累積。
  · 讀取時只補下載「本地最後一根之後」的缺口,不重抓全部
  · 合併後去重、排序、原子寫回
  · 首次遇到某幣某週期時,自動從舊版檔案裡挑最深的一份當種子 ——
    把那 252 天的歷史搶救回來(遷移只做一次,之後舊檔可以刪)

**歷史只進不出**:合併永遠是聯集,絕不因為交易所不再提供就刪掉本地已有的。
系統看過的資料就是系統的資產。
"""
from __future__ import annotations

import csv
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from core.logging import get_logger
from market_data.bingx_client import BingXClient
from models.market import Kline

log = get_logger("history")

CACHE_DIR = Path(__file__).resolve().parents[1] / "data" / "history"
_LEGACY_RE = re.compile(r"^(?P<sym>.+)_(?P<iv>[0-9]+[mhdw])_"
                        r"\d{4}-\d{2}-\d{2}_\d{4}-\d{2}-\d{2}$")
_HEADER = ["open_time", "open", "high", "low", "close",
           "volume", "source", "fetched_at"]


def _store(symbol: str, interval: str) -> Path:
    return CACHE_DIR / f"{symbol}_{interval}.csv"


def _floor_path(symbol: str, interval: str) -> Path:
    return CACHE_DIR / f"{symbol}_{interval}.floor"


def _read_floor(symbol: str, interval: str) -> datetime | None:
    """已經試過往回抓到哪一天。

    交易所的保留量只會往前滾,一旦某個時點抓不到,以後也不會有 ——
    所以「試過而落空」的結果可以永久記住。沒有這個記號的話,
    每次呼叫都會重試一次注定失敗的往回抓;研究回合是
    1322 配置 × 10 幣,那是幾千次無效 API 呼叫。
    """
    try:
        return datetime.fromisoformat(
            _floor_path(symbol, interval).read_text(encoding="utf-8").strip())
    except Exception:
        return None


def _write_floor(symbol: str, interval: str, when: datetime) -> None:
    try:
        _floor_path(symbol, interval).write_text(when.isoformat(),
                                                 encoding="utf-8")
    except OSError:
        pass


def _row_to_kline(r: dict, symbol: str, interval: str) -> Kline | None:
    try:
        return Kline(symbol=symbol, interval=interval,
                     open_time=datetime.fromisoformat(r["open_time"]),
                     open=float(r["open"]), high=float(r["high"]),
                     low=float(r["low"]), close=float(r["close"]),
                     volume=float(r["volume"]),
                     source=r.get("source") or "cache",
                     fetched_at=datetime.fromisoformat(r["fetched_at"]))
    except Exception:
        return None


def _read_csv(path: Path, symbol: str, interval: str) -> list[Kline]:
    if not path.exists():
        return []
    try:
        with path.open(encoding="utf-8") as f:
            return [k for r in csv.DictReader(f)
                    if (k := _row_to_kline(r, symbol, interval))]
    except OSError as e:
        log.warning(f"{path.name} 讀取失敗:{e}")
        return []


def _write_atomic(path: Path, ks: list[Kline]) -> None:
    """tmp + fsync + os.replace —— 快取寫到一半斷電不會留下半截檔。"""
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(_HEADER)
        for k in ks:
            w.writerow([k.open_time.isoformat(), k.open, k.high, k.low,
                        k.close, k.volume, k.source, k.fetched_at.isoformat()])
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _seed_from_legacy(symbol: str, interval: str) -> list[Kline]:
    """把**所有**舊版帶日期的檔案合併成種子(只在首次遷移時跑一次)。

    這一步就是把 2026-07 那些 252 天的 15m 歷史搶救回來的地方 ——
    交易所現在只給 100 天,那段資料是買不回來的。

    ═══ 2026-09-07 修:原本只挑「最深的一份」═══
    舊版每天用不同起訖日各存一份,不同檔案的覆蓋區間**不完全重疊**:
    有的往前多幾根、有的往後多幾根。只取最深的那一份,其餘檔案獨有的
    K 線就永遠讀不進累積檔 —— 實測 5 個檔案共 576 根 5m 被孤立在磁碟上。

    量不大,但方向錯了:守則寫的是「歷史只進不出」。既然舊檔就躺在
    同一個目錄裡,沒有理由讓它們讀得到卻用不到。全部合併,_merge 本來
    就依 open_time 去重。
    """
    prefix = f"{symbol}_{interval}_"
    files: list[Path] = []
    try:
        for p in CACHE_DIR.glob(f"{prefix}*.csv"):
            if _LEGACY_RE.match(p.stem):
                files.append(p)
    except OSError:
        return []
    if not files:
        return []
    ks: list[Kline] = []
    for p in sorted(files):
        ks = _merge(ks, _read_csv(p, symbol, interval))
    if ks:
        log.info(f"{symbol} {interval} 由 {len(files)} 個舊快取合併 {len(ks)} 根"
                 f"(自 {ks[0].open_time.date()})")
    return ks


def _merge(a: list[Kline], b: list[Kline]) -> list[Kline]:
    """依 open_time 取聯集去重。後來者覆蓋(較新的抓取結果較可信)。"""
    m: dict[datetime, Kline] = {k.open_time: k for k in a}
    m.update({k.open_time: k for k in b})
    return [m[t] for t in sorted(m)]


def load_or_download(symbol: str, days: int, client: BingXClient | None = None,
                     interval: str = "15m") -> list[Kline]:
    """回傳最近 `days` 天的 K 線。本地有多少用多少,只補下載缺口。

    回傳的根數可能少於 `days` 所隱含的數量 —— 交易所的保留量就是上限,
    這是事實不是錯誤。呼叫端若對樣本量有要求,必須自己檢查 len()。
    """
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    store = _store(symbol, interval)
    end = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    start = end - timedelta(days=days)

    have = _read_csv(store, symbol, interval)
    if not have:
        have = _seed_from_legacy(symbol, interval)

    # 缺口有兩邊,兩邊都要補(2026-09-06 修正)。
    # 首版只補「本地最後一根之後」,結果本地若只存了近期一小段,
    # 要求 340 天也只會回傳那一小段 —— 實測 4h 卡在 41 天,
    # 比舊版(每次重抓 333 天)還糟。累積式快取必須能往回長。
    fresh: list[Kline] = []
    cli = None

    def _fetch(a: datetime, b: datetime) -> None:
        nonlocal fresh, cli
        if b <= a:
            return
        try:
            cli = cli or client or BingXClient()
            fresh += cli.get_klines_history(symbol, interval, a, b)
        except Exception as e:
            log.warning(f"{symbol} {interval} 下載失敗({a.date()}~{b.date()}),"
                        f"改用本地快取:{e}")

    if not have:
        _fetch(start, end)
    else:
        # 往後補新的
        if (end - have[-1].open_time) >= timedelta(minutes=1):
            _fetch(max(start, have[-1].open_time), end)
        # 往回補舊的 —— 交易所可能已經不提供,抓不到就算了,本地不受影響。
        # 只在「比過去試過的最深處還要更深」時才出手,避免每次都白試一趟。
        if start < have[0].open_time:
            tried = _read_floor(symbol, interval)
            if tried is None or start < tried:
                _fetch(start, have[0].open_time)
                _write_floor(symbol, interval, start)

    merged = _merge(have, fresh) if (have or fresh) else []
    if merged and merged != have:
        try:
            _write_atomic(store, merged)
        except OSError as e:
            log.warning(f"{symbol} {interval} 快取寫入失敗:{e}")

    return [k for k in merged if k.open_time >= start]
