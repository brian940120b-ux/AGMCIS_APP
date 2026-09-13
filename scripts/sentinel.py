"""
AGMCIS — 檢修官 v4(Sentinel・雙城巡檢)
執行:systemd timer 每 10 分鐘(也可手動:.venv/bin/python scripts/sentinel.py)

═══ 版本史 ═══
v3(2026-07-16):主城十三項 + 檢修手冊七條白名單自癒
(2026-07-17 事故:放養組建設期間本檔被 wild 哨兵覆蓋,主城巡檢停擺)
v4(2026-07-20 維護日):復國 + 合併 —— 一個哨兵守兩座城:
  主城 14 項(新增:測量官時效;研究心跳改盯「開庭嘗試」)
  放養組 5 項(服務 / 引擎心跳 / 學習 / 解析 / API 額度)

《檢修手冊》動詞白名單(絕無其他):
  systemctl restart / enable --now / start 指定單位
  journalctl --vacuum-size(日誌瘦身)
永遠不做:改任何源代碼 / 碰 .env / 帳本 / 名冊 / 資料檔 / 觸發交易。
安全四鎖:確診才修|白名單步驟|40 秒複檢未癒即升級|
          配額 2/時・4/日(同一問題),超額停手。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path as _P
sys.path.insert(0, str(_P(__file__).resolve().parents[1]))
from core import ratelimit
from core.atomic import write_json_atomic
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
STATE = BASE / "data" / "sentinel_state.json"
MS = BASE / "data" / "paper" / "market_state.json"
LEDGER = BASE / "data" / "research" / "ledger.json"
HUNTER = BASE / "data" / "hunter_board.json"
GAUGE = BASE / "data" / "gauge_board.json"
PORTFOLIO = BASE / "data" / "portfolio_account.json"
WILD = Path("/root/agmcis_wild/data")

DAILY_DATA_STALL_H = 30   # 組合每日記帳會刷新;給補跑緩衝
DISK_LIMIT_PCT = 90
TB_WINDOW_MIN = 30
PORTFOLIO_STALL_MIN = 26 * 60   # 每日 00:30 記帳 + 補跑緩衝
HUNTER_STALL_MIN = 45
GAUGE_STALL_MIN = 25
WILD_EQUITY_MIN = 40
WILD_LEARN_H = 4.5
REPAIR_VERIFY_WAIT_S = 40
MAX_REPAIRS_PER_HOUR = 2
MAX_REPAIRS_PER_DAY = 4

# 2026-09-05 補三個漏網:signals(影子掃描器,每 15 分鐘)、
# scholar(每週學習)、deepaudit(每日深度稽核)。
# 在此之前這三個 timer 若停擺,檢修官會照樣印「全部 timer 在崗」——
# 訊號台是整個前向測試的資料來源,它掛了而巡檢說一切正常,是最糟的組合。
# 2026-09-08:新系統上線,退役 15m 型態管線的四個 timer 已停用
#（signals/hunter/scholar/deepaudit —— 它們只服務被蓋棺的假說空間)。
# 白名單同步移除,否則哨兵會把「刻意停掉」報成「停擺」——
# 一個天天喊狼來了的警報器,等於沒有警報器。
TIMERS = ["agmcis-sentinel.timer", "agmcis-gauge.timer",
          "agmcis-archivist.timer",
          # 新系統唯一在跑的策略。停擺等於前向證據斷掉,而斷掉是靜悄悄的。
          "agmcis-portfolio.timer",
          # 測試組:平行前向對照,斷掉也是靜悄悄的
          "agmcis-trial.timer"]
DATA_FILES = [BASE / "data" / "portfolio_account.json",
              BASE / "data" / "portfolio_equity.jsonl",
              BASE / "data" / "gauge_board.json",
              # 交易所合約規格:壞掉或過期會讓訂單層算出被拒單的數量
              BASE / "data" / "bingx_specs.json"]


def _env() -> dict:
    out = {}
    try:
        for line in (BASE / ".env").read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip()
    except Exception:
        pass
    return out


def _sh(cmd: list[str], timeout: int = 25) -> tuple[int, str]:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, (r.stdout + r.stderr)
    except Exception as e:
        return 1, str(e)


def _age_min(p: Path) -> float | None:
    try:
        return (time.time() - p.stat().st_mtime) / 60
    except Exception:
        return None


def _jload(p: Path, default):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return default


# ── 主城十四項 ──────────────────────────────────────────────

def chk_service_agmcis():
    rc, out = _sh(["systemctl", "is-active", "agmcis"])
    return out.strip() == "active", f"agmcis: {out.strip()}", \
        "journalctl -u agmcis -n 30 --no-pager"


def chk_service_dash():
    rc, out = _sh(["systemctl", "is-active", "agmcis-dash"])
    return out.strip() == "active", f"agmcis-dash: {out.strip()}", \
        "journalctl -u agmcis-dash -n 30 --no-pager"


def chk_daily_data():
    """組合依賴的日線資料夠不夠新?

    ═══ 2026-09-08:取代 market_state 新鮮度 ═══
    market_state.json 是舊 15m 紙上引擎(job_paper)寫的,而它已隨法庭停轉。
    那條檢查會永遠紅著 —— 但紅的不是故障,是它盯的東西已經不存在了。
    **一條盯著不存在的東西的檢查,比沒有檢查更糟**:它會讓人習慣忽略紅燈。

    新系統真正依賴的是日線快取。組合每天用它算 50 日均線與波動;
    快取斷掉的話,策略會拿舊價格做決策,而那是靜悄悄的。
    """
    import time as _t
    hist = BASE / "data" / "history"
    ages = []
    try:
        from portfolio.paper import SYMBOLS
    except Exception:
        SYMBOLS = ["BTC-USDT", "ETH-USDT"]
    for sym in SYMBOLS:
        f = hist / f"{sym}_1d.csv"
        if f.exists():
            ages.append(((_t.time() - f.stat().st_mtime) / 3600, sym))
    if not ages:
        return False, "日線快取不存在", \
            ".venv/bin/python portfolio/paper.py"
    ages.sort(reverse=True)
    worst_h, worst_sym = ages[0]
    limit = DAILY_DATA_STALL_H
    return worst_h < limit, \
        f"日線快取最舊 {worst_h:.1f} 小時({worst_sym},上限 {limit});" \
        f"共 {len(ages)}/{len(SYMBOLS)} 個幣有資料", \
        "journalctl -u agmcis-portfolio -n 30 --no-pager"


def chk_dashboard():
    key = _env().get("DASHBOARD_KEY", "")
    rc, out = _sh(["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}",
                   f"http://127.0.0.1:8765/?key={key}"], timeout=10)
    code = out.strip() if out.strip().isdigit() else ""
    detail = (f"面板本機回應 HTTP {code}" if code
              else "面板本機無回應(逾時或連線失敗)")
    return code == "200", detail, "systemctl restart agmcis-dash"


def chk_portfolio():
    """組合有沒有按時記帳?

    ═══ 2026-09-08:取代「研究心跳」═══
    舊的 chk_research 盯的是法庭開庭時間。法庭已停轉
    (執政官裁定「法庭那套就不要了」),那條檢查會開始天天誤報 ——
    而一個天天誤報的警報器等於沒有警報器。

    現在盯唯一在跑的東西:組合每日記帳。它斷掉是靜悄悄的 ——
    面板照樣顯示昨天的數字,沒有任何地方會變紅。
    每日 00:30 執行,給 26 小時緩衝(含補跑)。
    """
    age = _age_min(PORTFOLIO)
    if age is None:
        return False, "組合帳戶不存在(尚未第一次記帳?)", \
            ".venv/bin/python portfolio/paper.py"
    limit = PORTFOLIO_STALL_MIN
    detail = f"組合距上次記帳 {age / 60:.1f} 小時(上限 {limit / 60:.0f})"
    try:
        d = _jload(PORTFOLIO, {})
        detail += f";權益 {float(d.get('equity', 0)):,.2f}、天數 {d.get('days', 0)}"
    except Exception:
        pass
    return age < limit, detail, "journalctl -u agmcis-portfolio -n 40 --no-pager"


def chk_timers():
    dead = []
    for t in TIMERS:
        rc, out = _sh(["systemctl", "is-active", t])
        if out.strip() != "active":
            dead.append(t)
    return not dead, \
        ("全部 timer 在崗" if not dead else f"timer 停擺:{','.join(dead)}"), \
        "systemctl list-timers 'agmcis-*' --all --no-pager"


STALE_UNITS = {"agmcis": ["research", "governance", "strategies",
                          "market_data", "core", "scripts/autopilot.py"],
               "agmcis-dash": ["scripts/dashboard.py"]}


def chk_stale_process():
    """常駐進程載入的程式碼,比磁碟上的還舊嗎?

    ═══ 為什麼要有這一條(2026-09-07)═══
    autopilot 是常駐服務,Python 不會熱重載。它從 9/3 05:24 起沒重啟過,
    而 9/6 12:31 執政官裁定「判準改成 measured」—— 那次改動、以及之後
    四天 18 個檔案的全部改動,**一行都沒有在跑**。
    帳本裡 9/6 之後入案的 11 案全部仍標 pessimistic,
    git 說改好了,系統做的還是舊的那件事。

    這是部署層的「系統說假話」:測試全過、commit 乾淨、面板正常,
    唯獨真正執行的那份程式碼是四天前的。沒有任何一條既有檢查會發現。

    只比對 .py 的 mtime —— 資料檔天天在動,不算數。
    """
    import os
    stale = []
    for unit, paths in STALE_UNITS.items():
        rc, pid = _sh(["systemctl", "show", unit, "-p", "MainPID", "--value"])
        pid = (pid or "").strip()
        if not pid.isdigit() or pid == "0":
            continue
        try:
            started = os.stat(f"/proc/{pid}").st_mtime
        except OSError:
            continue
        newest, who = 0.0, ""
        for p in paths:
            f = BASE / p
            if f.is_file():
                cand = [(f.stat().st_mtime, p)]
            elif f.is_dir():
                cand = [(x.stat().st_mtime, str(x.relative_to(BASE)))
                        for x in f.rglob("*.py")]
            else:
                continue
            for m, name in cand:
                if m > newest:
                    newest, who = m, name
        if newest > started:
            age_h = (newest - started) / 3600
            stale.append(f"{unit}(程式碼比進程新 {age_h:.1f} 小時,最新 {who})")
    return not stale, \
        ("常駐進程都跑在最新程式碼上" if not stale else "; ".join(stale)), \
        "systemctl restart " + (stale[0].split("(")[0] if stale else "agmcis")


SLOW_UNITS = {"agmcis-portfolio.service": 24 * 3600}   # 單位:秒(timer 間隔)
SLOW_WARN_RATIO = 0.5                              # 用掉一半間隔就該知道


def chk_round_duration():
    """週期性任務的單輪耗時,有沒有在逼近它自己的排程間隔?

    ═══ 為什麼要有這一條(2026-09-07)═══
    訊號台每 15 分鐘跑一次,目前約 30 秒。但同一天把影子的成交模型
    對齊法庭之後,成交率從 39% 變成 100% —— 未平倉部位會顯著變多,
    而每一輪都要逐筆回放它們的 K 線。耗時會跟著長。

    真正的問題不是「有一天會超過 15 分鐘」,是**它會安靜地長**:
    systemd 不會讓 oneshot 重入,所以超時的表現不是報錯,
    而是「這一輪被跳過」—— 面板照常、日誌照常,只是觀測密度悄悄減半。
    這座城邦已經被這種無聲降級咬過太多次。用掉一半間隔就先講。
    """
    slow = []
    for unit, budget in SLOW_UNITS.items():
        rc, out = _sh(["systemctl", "show", unit,
                       "-p", "ExecMainStartTimestampMonotonic",
                       "-p", "ExecMainExitTimestampMonotonic"])
        vals = {}
        for line in (out or "").splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                vals[k] = v.strip()
        try:
            a = int(vals["ExecMainStartTimestampMonotonic"])
            b = int(vals["ExecMainExitTimestampMonotonic"])
        except (KeyError, ValueError):
            continue
        if b <= a:                      # 正在跑,這一輪還沒有耗時可讀
            continue
        dur = (b - a) / 1e6
        if dur > budget * SLOW_WARN_RATIO:
            slow.append(f"{unit} 單輪 {dur:.0f}s / 間隔 {budget}s"
                        f"({dur / budget * 100:.0f}%)")
    return not slow, \
        ("週期任務耗時都在預算內" if not slow else "; ".join(slow)), \
        "journalctl -u agmcis-signals -n 40 --no-pager"


def chk_failed_units():
    """有沒有 agmcis 單位處於 failed?

    ═══ 為什麼要單獨一條(2026-09-07)═══
    chk_timers 只問「timer 還活著嗎」—— 而 timer **永遠是 active**,
    即使它底下的 service 每一次執行都失敗。學者官從 8/30 起連兩期
    503 陣亡,systemctl 一直標著 failed,哨兵卻每 10 分鐘回報
    「全部 timer 在崗」。系統對自己說了兩週的假話,沒人發現。

    timer 管的是「有沒有被叫起來」,failed 管的是「叫起來之後有沒有做完」。
    這是兩個問題,要兩條檢查。
    """
    rc, out = _sh(["systemctl", "--failed", "--no-legend", "--no-pager",
                   "--plain"])
    # 排除哨兵自己:它一旦查出任何問題就 exit 1,systemd 隨即把
    # agmcis-sentinel.service 標成 failed;下一輪這條檢查看到那個 failed
    # 又 exit 1 —— 自己餵自己,原始問題修好了也永遠解不開。
    # 哨兵的健康由「它有沒有按時跑」判定(timer + 外部心跳),不是由這條。
    bad = [ln.split()[0] for ln in (out or "").splitlines()
           if ln.strip().startswith("agmcis")
           and not ln.split()[0].startswith("agmcis-sentinel")]
    return not bad, \
        ("無 failed 單位" if not bad
         else f"單位執行失敗:{', '.join(bad)}"), \
        ("journalctl -u " + (bad[0] if bad else "agmcis") +
         " -n 30 --no-pager")


def chk_hunter():   # 2026-09-08 已停用(獵手 timer 關閉),保留供日後復活
    age = _age_min(HUNTER)
    if age is None:
        return False, "獵手榜不存在", "journalctl -u agmcis-hunter -n 20 --no-pager"
    return age < HUNTER_STALL_MIN, \
        f"獵手榜距上次更新 {age:.0f} 分鐘(上限 {HUNTER_STALL_MIN})", \
        "journalctl -u agmcis-hunter -n 20 --no-pager"


def chk_gauge():
    """v4 新增第 14 項:測量官時效(7/16 上崗,7/20 巡檢補齊)。"""
    age = _age_min(GAUGE)
    if age is None:
        return False, "測量官快照不存在", \
            "journalctl -u agmcis-gauge -n 20 --no-pager"
    return age < GAUGE_STALL_MIN, \
        f"測量官快照距上次 {age:.0f} 分鐘(上限 {GAUGE_STALL_MIN})", \
        "journalctl -u agmcis-gauge -n 20 --no-pager"


def chk_data_integrity():
    """核心資料檔完整性。

    2026-09-09 修:portfolio_equity.jsonl 是 **JSONL**(一行一個 JSON 物件),
    不是單一 JSON 文件。原本整檔丟給 json.loads() 解析,
    多行 JSONL 用這個方法解析必定失敗 —— 不管內容多正確。
    這個檔案在今天稍早被加進 DATA_FILES,累積到第 2 行就開始
    每一輪都誤報「JSON 損壞」,而沒有人發現,直到部署面板時順手複驗才抓到。
    這正是「稽核自己也會壞,而且壞得無聲」的又一個例子。
    """
    bad = []
    for f in DATA_FILES:
        if not f.exists():
            continue
        try:
            if f.suffix == ".jsonl":
                with f.open(encoding="utf-8") as fh:
                    for line in fh:
                        line = line.strip()
                        if line:
                            json.loads(line)
            else:
                json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            bad.append(f.name)
    return not bad, \
        ("核心資料檔完整" if not bad else f"JSON 損壞:{','.join(bad)}"), \
        "從守墓人最近的 Telegram 備份包還原對應檔案(先 cp 損壞檔留證)"


def chk_telegram():
    t = _env().get("TELEGRAM_BOT_TOKEN", "")
    if not t:
        return False, "TELEGRAM_BOT_TOKEN 未設定", "檢查 /root/agmcis/.env"
    rc, out = _sh(["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}",
                   f"https://api.telegram.org/bot{t}/getMe"], timeout=10)
    code = out.strip() if out.strip().isdigit() else ""
    return code == "200", f"Telegram API HTTP {code or '無回應'}", \
        "檢查主機對外網路;若 401 = token 被撤銷,需 BotFather 重發並更新 .env"


def chk_bingx():
    rc, out = _sh(["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}",
                   "https://open-api.bingx.com/openApi/swap/v2/quote/contracts"],
                  timeout=10)
    code = out.strip() if out.strip().isdigit() else ""
    return code == "200", f"BingX API HTTP {code or '無回應'}", \
        "檢查主機對外網路 / BingX 狀態頁;行情中斷時系統會自動 WAIT"


def chk_disk():
    rc, out = _sh(["df", "--output=pcent", "/"])
    try:
        pct = int(out.strip().splitlines()[-1].replace("%", "").strip())
    except Exception:
        return True, "磁碟資訊讀取失敗(略過)", ""
    return pct < DISK_LIMIT_PCT, f"磁碟使用 {pct}%(上限 {DISK_LIMIT_PCT}%)", \
        "du -sh /root/agmcis/data/* /root/agmcis_wild/data/* | sort -h | tail"


def chk_traceback():
    rc, out = _sh(["journalctl", "-u", "agmcis",
                   "--since", f"{TB_WINDOW_MIN} min ago", "--no-pager"],
                  timeout=20)
    n = out.count("Traceback (most recent call last)")
    return n == 0, f"最近 {TB_WINDOW_MIN} 分鐘 Traceback ×{n}", \
        f"journalctl -u agmcis --since '{TB_WINDOW_MIN} min ago' " \
        f"--no-pager | grep -B2 -A15 Traceback"


def chk_dash_traceback():
    rc, out = _sh(["journalctl", "-u", "agmcis-dash",
                   "--since", f"{TB_WINDOW_MIN} min ago", "--no-pager"],
                  timeout=20)
    n = out.count("Traceback (most recent call last)")
    return n == 0, f"面板最近 {TB_WINDOW_MIN} 分鐘 Traceback ×{n}", \
        f"journalctl -u agmcis-dash --since '{TB_WINDOW_MIN} min ago' " \
        f"--no-pager | grep -B2 -A15 Traceback"


# ── 放養組五項(目錄不存在即略過)────────────────────────────

def _wild_absent():
    return not WILD.exists()


def chk_wild_service():
    if _wild_absent():
        return True, "放養組目錄不存在(略過)", ""
    rc, out = _sh(["systemctl", "is-active", "agmcis-wild"])
    return out.strip() == "active", f"agmcis-wild: {out.strip()}", \
        "journalctl -u agmcis-wild -n 30 --no-pager"


def chk_wild_engine():
    if _wild_absent():
        return True, "略過", ""
    age = _age_min(WILD / "equity.json")
    if age is None:
        return False, "放養組 equity.json 無法讀取", \
            "journalctl -u agmcis-wild -n 30 --no-pager"
    return age < WILD_EQUITY_MIN, \
        f"放養組權益距上次更新 {age:.0f} 分鐘(上限 {WILD_EQUITY_MIN})", \
        "journalctl -u agmcis-wild -n 30 --no-pager"


def chk_wild_learning():
    if _wild_absent():
        return True, "略過", ""
    age = _age_min(WILD / "learning_log.jsonl")
    if age is None:
        return False, "放養組學習紀錄無法讀取", \
            "journalctl -u agmcis-wild -n 40 --no-pager | grep -i learn"
    h = age / 60
    return h < WILD_LEARN_H, \
        f"放養組距上次學習 {h:.1f} 小時(上限 {WILD_LEARN_H})", \
        "journalctl -u agmcis-wild -n 40 --no-pager | grep -i learn"


def chk_wild_parse():
    if _wild_absent():
        return True, "略過", ""
    rt = _jload(WILD / "runtime.json", {})
    pf = int(rt.get("parse_fails", 0) or 0)
    return pf < 2, f"放養組學習解析連續失敗 {pf} 次", \
        "journalctl -u agmcis-wild -n 60 --no-pager | grep -i parse"


def chk_wild_budget():
    if _wild_absent():
        return True, "略過", ""
    b = _jload(WILD / "api_budget.json", {})
    calls = int(b.get("calls", 0) or 0)
    return calls < 40, f"放養組今日 API 已用 {calls}/40", \
        "額度日界自動重置;若異常暴衝,查 journalctl -u agmcis-wild"


def chk_exchange_specs():
    """交易所合約規格必須存在、涵蓋所有交易標的、且沒有過期。

    2026-09-09 建立。排查發現七個持倉的數量全部不符 BingX 數量精度,
    真的派單會一張都送不出去 —— 而紙上交易完全看不出來:帳本照收、
    面板照顯示、測試照過。訂單層現在靠這份規格把數量調到交易所接受的
    位數,所以規格檔案本身變成了一個會無聲害人的單點。
    """
    from datetime import datetime, timezone
    f = BASE / "data" / "bingx_specs.json"
    fix = ("cd /root/agmcis && .venv/bin/python3 portfolio/specs.py")
    if not f.exists():
        return False, "交易所規格快取不存在 —— 訂單層無法產生合規數量", fix
    try:
        d = json.loads(f.read_text(encoding="utf-8"))
        contracts = d.get("contracts") or {}
        age_h = (datetime.now(timezone.utc).timestamp()
                 - float(d.get("updated") or 0)) / 3600
    except Exception as e:
        return False, f"交易所規格檔解析失敗:{e}", fix
    sys.path.insert(0, str(BASE))
    from portfolio.paper import SYMBOLS
    missing = [s for s in SYMBOLS if s not in contracts]
    if missing:
        return False, f"規格缺少交易標的:{','.join(missing)}", fix
    if age_h > 24 * 7:
        return False, f"交易所規格已 {age_h / 24:.1f} 天未更新(上限 7 天)", fix
    return True, (f"交易所規格 {len(contracts)} 個合約、"
                  f"{len(SYMBOLS)}/{len(SYMBOLS)} 標的齊全、"
                  f"{age_h:.1f} 小時前更新"), fix


def chk_universe_health():
    """交易池的 7 個幣是否仍然可交易、流動性是否仍然足夠。

    2026-09-09 建立。交易池是寫死的 7 個幣,不會自己更新 —— 這是
    刻意的決定(逐時點動態池的回測沒有支持它,而前向樣本才第 1 天,
    沒有乾淨證據能區分兩者)。但「寫死」帶來一個風險:名單會過期。
    幣可能下架、流動性可能萎縮到我們的成本模型不再成立。

    所以不改策略,改成**有人盯著**:任何一個幣掉出門檻就出聲,
    由執政官裁決要不要換。門檻取 1,000 萬 USDT —— 與 2026-09-09
    交易池篩選實驗所用的同一個值,不另訂一個。
    """
    import urllib.request
    fix = "檢視該幣是否仍適合交易,必要時由執政官裁決調整 portfolio/paper.py 的 SYMBOLS"
    MIN_VOL = 1e7
    sys.path.insert(0, str(BASE))
    try:
        from portfolio.paper import SYMBOLS
        req = urllib.request.Request(
            "https://open-api.bingx.com/openApi/swap/v2/quote/ticker",
            headers={"User-Agent": "agmcis/1.0"})
        with ratelimit.urlopen(req, timeout=15) as r:
            tick = {t["symbol"]: t for t in json.loads(r.read())["data"]}
    except Exception as e:
        return False, f"交易池健康檢查失敗:{type(e).__name__}: {e}", fix

    missing, thin = [], []
    for s in SYMBOLS:
        t = tick.get(s)
        if t is None:
            missing.append(s)
            continue
        try:
            qv = float(t["quoteVolume"])
        except (KeyError, TypeError, ValueError):
            missing.append(s)
            continue
        if qv < MIN_VOL:
            thin.append(f"{s} {qv / 1e6:.1f}M")
    if missing:
        return False, f"交易池有幣在交易所查無行情(可能下架):{','.join(missing)}", fix
    if thin:
        return False, (f"交易池有幣流動性掉到 1,000 萬以下:{'; '.join(thin)}"
                       " —— 成本模型可能不再成立"), fix
    lo = min(float(tick[s]["quoteVolume"]) for s in SYMBOLS)
    return True, (f"交易池 {len(SYMBOLS)} 幣全部在架,最低成交額 "
                  f"{lo / 1e6:.0f}M USDT(門檻 10M)"), fix


def chk_trial():
    """測試組有沒有按時記帳、有沒有誤寫主城帳本。

    測試組(portfolio/trial.py)是平行的紙上帳戶,跑動態交易池,
    用同一段真實價格跟主城對照 —— 它存在的理由是:回測分不出
    「動態池比較差」與「固定 7 幣被後見之明美化」,只有前向能回答。
    它斷掉是靜悄悄的(主城照跑、面板照顯示),所以要有人盯著。
    """
    trial_state = BASE / "data" / "trial_account.json"
    fix = ".venv/bin/python3 portfolio/trial.py"
    if not trial_state.exists():
        return False, "測試組帳本不存在", fix
    age = _age_min(trial_state)
    if age is None:
        return False, "測試組帳本讀不到時間", fix
    d = _jload(trial_state, {})
    # 結構性隔離:測試組的帳本不得是主城那一個
    if trial_state.resolve() == PORTFOLIO.resolve():
        return False, "測試組與主城共用帳本 —— 結構性隔離已破", fix
    ok = age < PORTFOLIO_STALL_MIN
    return ok, (f"測試組距上次記帳 {age / 60:.1f} 小時"
                f"(上限 {PORTFOLIO_STALL_MIN / 60:.0f});"
                f"權益 {float(d.get('equity', 0)):,.2f}、"
                f"持倉 {len(d.get('positions') or {})} 檔"), fix


def chk_exchange_audit():
    """交易所一致性徹查(scripts/audit_exchange.py)必須全過。

    那支把每一條資金/開單算式拿去跟交易所規格與官方範例對答案。
    放進哨兵的理由:規格會變、帳本會長出新的倉,而「算式跟交易所不一樣」
    這件事在紙上交易完全看不出來 —— 帳本照收、面板照顯示、測試照過,
    要等真的派單才會發現。這種錯必須有人每 10 分鐘問一次。
    """
    import subprocess
    fix = "cd /root/agmcis && .venv/bin/python3 scripts/audit_exchange.py"
    try:
        r = subprocess.run(
            [sys.executable, str(BASE / "scripts" / "audit_exchange.py")],
            capture_output=True, text=True, timeout=90, cwd=str(BASE))
    except Exception as e:
        return False, f"徹查無法執行:{type(e).__name__}: {e}", fix
    if r.returncode == 0:
        n = r.stdout.count("✅")
        return True, f"交易所一致性 {n} 項全過", fix
    bad = [ln.strip() for ln in r.stdout.splitlines()
           if ln.strip().startswith("·")]
    return False, ("交易所一致性徹查未通過:"
                   + ("; ".join(bad[:3]) if bad else "見完整輸出")), fix


CHECKS = [
    ("面板服務", chk_service_dash),
    ("日線資料新鮮度", chk_daily_data),
    ("面板健康", chk_dashboard),
    ("組合記帳", chk_portfolio),
    ("timer 排程群", chk_timers),
    ("單位執行失敗", chk_failed_units),
        ("測量官時效", chk_gauge),
    ("核心資料完整性", chk_data_integrity),
    ("交易所規格", chk_exchange_specs),
    ("交易所算法一致性", chk_exchange_audit),
    ("交易池健康", chk_universe_health),
    ("Telegram 連通", chk_telegram),
    ("BingX 連通", chk_bingx),
    ("磁碟空間", chk_disk),
    ("面板 log 異常", chk_dash_traceback),
    # 2026-09-10:放養組五項移除。執政官裁定退役(它的問題已有答案:
    # Sharpe −1.59,拿掉規則沒有產生優勢),服務已停用、資料已封存於
    # data/wild_archive/。憲法第八條:「停用的東西不該再被當成停擺;
    # 盯著不存在的東西的檢查,會讓人習慣忽略紅燈。」
    ("測試組記帳", chk_trial),
]
_CHK = dict(CHECKS)

# ══《檢修手冊 v4》═══════════════════════════════════════════
RUNBOOK: dict[str, dict] = {
    "autopilot 服務": {
        "steps": [["systemctl", "restart", "agmcis"]],
        "verify": "autopilot 服務",
        "note": "手冊 §1:autopilot 死亡 → 重啟",
    },
    "面板服務": {
        "steps": [["systemctl", "restart", "agmcis-dash"]],
        "verify": "面板服務",
        "note": "手冊 §2:面板服務死亡 → 重啟",
    },
    "market_state 新鮮度": {
        "steps": [["systemctl", "restart", "agmcis"]],
        "verify": "autopilot 服務",
        "note": "手冊 §3:tick 凍結(28 小時事故教訓)→ 重啟 agmcis",
    },
    "面板健康": {
        "steps": [["systemctl", "restart", "agmcis-dash"]],
        "verify": "面板健康",
        "note": "手冊 §4:面板無回應 → 重啟面板",
    },
    "timer 排程群": {
        "steps": [["systemctl", "enable", "--now"] + TIMERS],
        "verify": "timer 排程群",
        "note": "手冊 §5:timer 停擺 → 全體 enable --now(冪等,安全)",
    },
    "獵手時效": {
        "steps": [["systemctl", "start", "agmcis-hunter.service"]],
        "verify": "獵手時效",
        "note": "手冊 §6:獵手榜過期 → 手動觸發一輪巡獵",
    },
    "磁碟空間": {
        "steps": [["journalctl", "--vacuum-size=300M"]],
        "verify": "磁碟空間",
        "note": "手冊 §7:磁碟吃緊 → 日誌瘦身至 300M(不碰任何資料檔)",
    },
    "測量官時效": {
        "steps": [["systemctl", "start", "agmcis-gauge.service"]],
        "verify": "測量官時效",
        "note": "手冊 §6b:測量官快照過期 → 手動觸發一輪測量",
    },
    "放養組服務": {
        "steps": [["systemctl", "restart", "agmcis-wild"]],
        "verify": "放養組服務",
        "note": "手冊 §8:放養組服務死亡 → 重啟 agmcis-wild",
    },
    "放養組引擎心跳": {
        "steps": [["systemctl", "restart", "agmcis-wild"]],
        "verify": "放養組服務",
        "note": "手冊 §8:放養組引擎昏迷 → 重啟 agmcis-wild",
    },
    # 研究心跳(上游)、資料完整性(必須人工)、連通性(外部)、
    # Traceback(需讀代碼)、放養組學習/解析/額度(觀察週不干預)
    # —— 刻意不入自癒手冊,亂修比不修危險。
}


# ── 狀態 / 配額 / 推播 ──────────────────────────────────────

def _load_state() -> dict:
    return _jload(STATE, {})


def _save_state(s: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    # 原子寫入(2026-09-07):自癒配額與各檢查的歷史狀態就在這個檔裡。
    # 寫到一半被 timer 的下一輪撞上或斷電,會留下半截 JSON ——
    # _jload 讀不到就退回空 dict,配額歸零、自癒可以無限重試。
    write_json_atomic(STATE, s)


def _quota_ok(state: dict, name: str) -> bool:
    now = time.time()
    hist = [t for t in (state.get("_repairs", {}).get(name, []))
            if now - t < 86400]
    state.setdefault("_repairs", {})[name] = hist
    hourly = sum(1 for t in hist if now - t < 3600)
    return hourly < MAX_REPAIRS_PER_HOUR and len(hist) < MAX_REPAIRS_PER_DAY


def _quota_use(state: dict, name: str) -> None:
    state.setdefault("_repairs", {}).setdefault(name, []).append(time.time())


def _heartbeat(failed: list[str]) -> None:
    """外部心跳(2026-09-03 上線)—— 給守夜人配守夜人。

    檢修官活在這台機器上:整台死亡時它跟著死,Telegram 不會響,
    執政官不會知道。這一層把「我還活著」推到城外的 healthchecks.io,
    由外部計時器在斷訊時反過來通知。

    19 項全綠 → ping 成功端點;有異常 → ping /fail 並把異常項名稱帶在
    body 裡(healthchecks 面板會顯示,不必登入伺服器就知道是哪一項)。

    鐵則:ping 只是回報層,絕不影響巡檢判定。任何失敗都只印警告。
    """
    url = _env().get("HEALTHCHECK_URL", "").strip()
    if not url:
        return                      # 未設定就靜默跳過,不視為錯誤
    try:
        if failed:
            target = url.rstrip("/") + "/fail"
            body = "異常項(%d):%s" % (len(failed), " / ".join(failed))
        else:
            target = url
            body = "19 項全綠"
        rc, _out = _sh(["curl", "-fsS", "-m", "10",
                        "--data-raw", body, target], timeout=12)
        if rc == 0:
            print("💓 外部心跳已送出:%s" % ("FAIL" if failed else "OK"))
        else:
            print("⚠️ 外部心跳送出失敗(rc=%s)—— 不影響巡檢結果" % rc)
    except Exception as e:
        print("⚠️ 外部心跳異常(%s)—— 不影響巡檢結果" % e)


def _notify(text: str) -> None:
    e = _env()
    t, cid = e.get("TELEGRAM_BOT_TOKEN", ""), e.get("TELEGRAM_CHAT_ID", "")
    if not t:
        return
    if not cid:
        rc, out = _sh(["curl", "-s",
                       f"https://api.telegram.org/bot{t}/getUpdates?limit=5"],
                      timeout=10)
        try:
            for x in reversed(json.loads(out).get("result", [])):
                m = x.get("message") or {}
                if m.get("chat"):
                    cid = str(m["chat"]["id"]); break
        except Exception:
            return
    if not cid:
        return
    _sh(["curl", "-s", "-X", "POST",
         f"https://api.telegram.org/bot{t}/sendMessage",
         "-d", f"chat_id={cid}",
         "--data-urlencode", f"text={text}"], timeout=10)


def _attempt_repair(state: dict, name: str) -> tuple[bool, str]:
    book = RUNBOOK.get(name)
    if not book:
        return False, "不在檢修手冊上,轉人工"
    if not _quota_ok(state, name):
        return False, "手冊修理配額已滿(2/時、4/日),停手轉人工"
    _quota_use(state, name)
    for step in book["steps"]:
        _sh(step, timeout=40)
    time.sleep(REPAIR_VERIFY_WAIT_S)
    ok, new_detail, _ = _CHK[book["verify"]]()
    desc = (f"{book['note']} → 複檢:{new_detail} → "
            f"{'✅ 痊癒' if ok else '❌ 未癒,升級人工'}")
    return ok, desc


def main() -> int:
    state = _load_state()
    cur, tickets, healed, recovered = {}, [], [], []

    for name, fn in CHECKS:
        try:
            ok, detail, fix = fn()
        except Exception as e:
            ok, detail, fix = False, f"巡檢項自身異常:{e}", ""
        was_ok = state.get(name, {}).get("ok", True)

        if not ok and was_ok:
            fixed, log_line = _attempt_repair(state, name)
            if fixed:
                healed.append((name, detail, log_line))
                ok, detail = True, f"(已按手冊自癒)原症狀:{detail}"
            else:
                tickets.append((name, detail, fix, log_line))
        elif ok and not was_ok:
            recovered.append((name, detail))

        cur[name] = {"ok": ok, "detail": detail}
        print(f"{'✅' if ok else '❌'} {name}:{detail}")

    now = datetime.now(timezone.utc).strftime("%m-%d %H:%M UTC")
    if healed:
        lines = [f"🔧 檢修官自癒紀錄({now})"]
        for name, detail, log_line in healed:
            lines.append(f"\n{name}\n症狀:{detail}\n{log_line}")
        _notify("\n".join(lines))
    if tickets:
        lines = [f"🚨 檢修官報修單({now})"]
        for name, detail, fix, log_line in tickets:
            lines.append(f"\n❌ {name}\n證據:{detail}\n手冊處置:{log_line}")
            if fix:
                lines.append(f"建議人工指令:\n{fix}")
        _notify("\n".join(lines))
    if recovered:
        lines = [f"🟢 檢修官銷單({now})"]
        for name, detail in recovered:
            lines.append(f"✅ {name} 已恢復:{detail}")
        _notify("\n".join(lines))

    cur["_repairs"] = state.get("_repairs", {})
    cur["_last_run"] = now
    _save_state(cur)

    # 心跳只是回報層:另外算一份異常清單,原判定式一字未動
    failed = [k for k, v in cur.items()
              if isinstance(v, dict) and "ok" in v and not v.get("ok")]
    _heartbeat(failed)

    return 0 if all(v.get("ok") for v in cur.values()
                    if isinstance(v, dict) and "ok" in v) else 1


if __name__ == "__main__":
    raise SystemExit(main())
