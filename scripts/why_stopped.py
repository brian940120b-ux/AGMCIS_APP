"""
探針:記帳為什麼停了 · 2026-09-25

═══ 起因 ═══
2026-09-25 查出來的兩個數字:

    screened  最後記帳 5.50 天前   在倉 48   ← 交易池上限是 10
    main      最後記帳 0.12 天前   在倉  7

screened 就是 PRIMARY —— **現在真正在跑的那一組**。它停了五天半,
而且手上有 48 檔,是池子上限的四倍八。

同時 `journalctl -u agmcis-portfolio | grep SpecMissing` **什麼都沒印**。

⚠️ **那不代表沒有錯誤。** 它可能只代表那個 unit 名字不對。
「沒查出來」跟「沒有」是兩件事 —— 這個專案栽在這上面不只一次,
所以這支的第一件事就是去問系統**到底有哪些 unit**,而不是假設。

═══ 這支只讀不寫 ═══
它不呼叫 tick()、不改任何帳本、不下任何單。它做四件事:

  一、問 systemd 有哪些 agmcis 的 unit、狀態如何、最後跑在什麼時候
  二、逐組印出帳本與權益曲線的最後時點(兩者應該一致)
  三、印在倉檔數與池子上限的對照
  四、**逐檔重現 funding_rate_sum()**,把真正的例外原封不動印出來

第四件是重點:tick() 死在哪一行,這支就在那一行前面停下來看。
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import interpreter

interpreter.require()

LINE = "═" * 64


def _run(cmd: list[str]) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True,
                              timeout=20).stdout.strip()
    except Exception as e:                           # noqa: BLE001
        return f"(跑不起來:{type(e).__name__}: {e})"


def units() -> None:
    print(f"\n{LINE}\n  一、systemd 上到底有哪些 agmcis 的 unit\n{LINE}\n")
    out = _run(["systemctl", "list-units", "--all", "--no-pager",
                "--no-legend", "agmcis*"])
    print(out or "  (一個都沒有 —— 那本身就是答案)")
    print(f"\n  ── timer ──")
    print(_run(["systemctl", "list-timers", "--all", "--no-pager",
                "--no-legend", "agmcis*"]) or "  (沒有 timer)")


def logs() -> None:
    print(f"\n{LINE}\n  二、每個 unit 最近的錯誤(不猜名字,問出來的)\n{LINE}")
    raw = _run(["systemctl", "list-units", "--all", "--no-pager",
                "--no-legend", "agmcis*"])
    names = [ln.split()[0] for ln in raw.splitlines()
             if ln.strip() and ln.split()[0].startswith("agmcis")]
    if not names:
        print("\n  沒有 unit 可以查。")
        return
    for n in names:
        print(f"\n  ── {n} ──")
        out = _run(["journalctl", "-u", n, "-n", "400", "--no-pager"])
        hits = [ln for ln in out.splitlines()
                if any(k in ln for k in
                       ("SpecMissing", "Traceback", "ERROR", "資金費",
                        "Error", "error"))]
        if hits:
            for ln in hits[-12:]:
                print("    " + ln[:200])
        else:
            print(f"    最近 400 行裡沒有錯誤字樣"
                  f"(這行只說「沒找到」,不說「沒有」)")


def ledgers() -> None:
    print(f"\n{LINE}\n  三、帳本與權益曲線的最後時點\n{LINE}\n")
    from portfolio.account import Account
    from portfolio.paper import CONTROL, MAX_UNIVERSE, PRIMARY
    now = datetime.now(timezone.utc)
    for cfg in (PRIMARY, CONTROL):
        tag = "PRIMARY(系統本身)" if cfg is PRIMARY else "CONTROL(對照組)"
        print(f"  ── {cfg.name}  {tag} ──")
        try:
            a = Account.load(cfg.state_path)
        except Exception as e:                       # noqa: BLE001
            print(f"     帳本讀不到:{type(e).__name__}: {e}\n")
            continue
        ms = a.funding_through_ms
        if ms:
            age = (now.timestamp() * 1000 - ms) / 3600_000
            when = datetime.fromtimestamp(ms / 1000, timezone.utc)
            print(f"     帳本最後記帳  {when}  ({age:.1f} 小時前)")
        else:
            print("     帳本最後記帳  從來沒有")

        last = None
        n = 0
        try:
            with cfg.curve_path.open(encoding="utf-8") as fh:
                for ln in fh:
                    ln = ln.strip()
                    if not ln:
                        continue
                    n += 1
                    try:
                        last = json.loads(ln).get("ts") or last
                    except json.JSONDecodeError:
                        continue
        except OSError as e:
            print(f"     權益曲線      讀不到:{e}")
        else:
            print(f"     權益曲線      {n} 點,最後一點 {last}")

        cap = MAX_UNIVERSE
        k = len(a.positions)
        flag = "  ⚠️ **超過池子上限**" if k > cap else ""
        print(f"     在倉          {k} 檔(池子上限 {cap}){flag}")
        if k > cap:
            print(f"                   {', '.join(sorted(a.positions))}")
        print()


def reproduce() -> None:
    """逐檔重現 tick() 死掉的那一行。**這一段是重點。**"""
    print(f"\n{LINE}\n  四、逐檔重現 funding_rate_sum() —— tick() 死在這裡"
          f"\n{LINE}\n")
    from portfolio import specs
    from portfolio.account import Account
    from portfolio.paper import CONTROL, PRIMARY
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    for cfg in (PRIMARY, CONTROL):
        try:
            a = Account.load(cfg.state_path)
        except Exception:                            # noqa: BLE001
            continue
        since = a.funding_through_ms or (now_ms - 24 * 3600 * 1000)
        frm = datetime.fromtimestamp(since / 1000, timezone.utc)
        print(f"  ── {cfg.name}(自 {frm} 起算)──")
        if not a.positions:
            print("     沒有持倉 —— 這一段不會被呼叫。\n")
            continue
        bad = 0
        for s in sorted(a.positions):
            try:
                v = specs.funding_rate_sum(s, since, now_ms)
            except Exception as e:                   # noqa: BLE001
                bad += 1
                print(f"     ✗ {s:<16}{type(e).__name__}: "
                      f"{str(e)[:120]}")
            else:
                print(f"     ✓ {s:<16}{v:+.6f}")
        print(f"\n     {bad} / {len(a.positions)} 檔會讓 tick() 當場拋例外。")
        if bad:
            print("     tick() 在 paper.py 的 daily_rates 那一行沒有接,")
            print("     所以它之後的每一件事都不會發生 —— 包括寫權益曲線。")
        print()


def main() -> int:
    print(f"\n{LINE}\n  記帳為什麼停了 · 只讀不寫\n{LINE}")
    for fn in (units, logs, ledgers, reproduce):
        try:
            fn()
        except Exception as e:                       # noqa: BLE001
            print(f"\n  ({fn.__name__} 這一段自己掛了:"
                  f"{type(e).__name__}: {e})")
    print(f"\n{LINE}\n  這支不下判斷,只把系統說的話貼出來。\n{LINE}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
