"""
為什麼面板還是舊的 —— 把原因問死 · 2026-09-13

═══ 為什麼需要這一支 ═══
執政官連續三輪回報「完全沒變化」「還是沒有」,而面板每次請求都重新
render、`Cache-Control: no-store`,沒有任何快取。

我只能猜:大概是沒重啟。**而猜是不行的** —— 這已經是第三輪了,
每一輪我猜一次、他試一次,兩邊都在賭同一個假設。

可能的原因至少五個,而它們在瀏覽器裡長得一模一樣:

  一、`git pull` 沒拉到(在別的目錄、或衝突擋住了)
  二、拉到了但服務沒重啟
  三、重啟了但新程式碼起不來,systemd 用舊的行程繼續撐
  四、**在聽那個埠的根本是另一個行程**(另一個服務、另一個目錄)
  五、真的換了,而我改的東西有 bug 沒渲染出來

這一支把五個都問一遍,然後直接說是哪一個。

═══ 它只讀 ═══
不重啟、不砍行程、不改檔案。**它只回答「為什麼」** ——
要怎麼修,它會把指令印出來給人自己下。

用法:  .venv/bin/python scripts/why_old.py
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

BASE = Path(__file__).resolve().parents[1]
PORT = int(os.environ.get("DASHBOARD_PORT", "8765"))
LINE = "═" * 62


def head(text: str) -> None:
    print(f"\n{LINE}\n  {text}\n{LINE}\n")


def sh(cmd: list) -> tuple:
    import subprocess
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
        return p.returncode, (p.stdout or p.stderr).strip()
    except Exception as e:                       # noqa: BLE001
        return 127, f"{type(e).__name__}: {e}"


def listeners(port: int) -> list:
    """誰在聽這個埠。純讀 /proc —— 不依賴 ss / lsof 裝了沒。"""
    targets = set()
    for proto in ("tcp", "tcp6"):
        try:
            rows = Path(f"/proc/net/{proto}").read_text().splitlines()[1:]
        except OSError:
            continue
        for row in rows:
            parts = row.split()
            if len(parts) < 10 or parts[3] != "0A":      # 0A = LISTEN
                continue
            try:
                if int(parts[1].split(":")[1], 16) != port:
                    continue
            except (IndexError, ValueError):
                continue
            targets.add(parts[9])                        # inode

    if not targets:
        return []

    out = []
    for proc in Path("/proc").iterdir():
        if not proc.name.isdigit():
            continue
        try:
            fds = list((proc / "fd").iterdir())
        except OSError:
            continue                                     # 不是我們的行程
        for fd in fds:
            try:
                link = os.readlink(fd)
            except OSError:
                continue
            if not link.startswith("socket:["):
                continue
            if link[8:-1] not in targets:
                continue
            try:
                cmdline = (proc / "cmdline").read_bytes() \
                    .replace(b"\x00", b" ").decode("utf-8", "replace").strip()
                cwd = os.readlink(proc / "cwd")
                # /proc/PID 這個目錄的 mtime 就是行程啟動時間。
                started = proc.stat().st_mtime
            except OSError:
                cmdline, cwd, started = "?", "?", 0.0
            out.append({"pid": int(proc.name), "cmd": cmdline,
                        "cwd": cwd, "started": started})
            break
    return out


def main() -> int:
    verdicts = []

    # ── 一、這個目錄現在是哪一版 ──────────────────────
    head("一、程式碼:這個目錄現在是哪一版")
    from core.build import current
    got = current(BASE / "scripts" / "dashboard.py")
    print(f"  目錄        {BASE}")
    print(f"  commit      {got.commit or '**讀不到**'}")
    if got.source_mtime:
        print("  dashboard.py 改動於 "
              + time.strftime("%m-%d %H:%M:%S",
                              time.gmtime(got.source_mtime)) + " UTC")

    rc, out = sh(["git", "-C", str(BASE), "status", "--porcelain"])
    if rc == 0 and out:
        print(f"  ⚠️ 工作目錄有未提交的改動:\n     "
              + "\n     ".join(out.splitlines()[:6]))
        print("     → `git pull` 可能被它擋住了")
        verdicts.append("工作目錄不乾淨,pull 可能沒拉進來")

    rc, out = sh(["git", "-C", str(BASE), "log", "--oneline", "-1"])
    if rc == 0:
        print(f"  最後一筆    {out}")

    has_new = (BASE / "core" / "build.py").exists()
    print(f"  core/build.py 存在?  {'是' if has_new else '**否**'}")
    if not has_new:
        verdicts.append("這個目錄根本沒有新程式碼 —— git pull 沒拉到")

    # ── 二、誰在聽那個埠 ──────────────────────────────
    head(f"二、誰在聽 {PORT} 埠")
    procs = listeners(PORT)
    if not procs:
        print(f"  沒有任何行程在聽 {PORT}(或權限不足看不到)。")
        print("  → 面板服務沒在跑,而你看到的畫面是瀏覽器的舊分頁。")
        verdicts.append(f"沒有行程在聽 {PORT}")
    for pr in procs:
        age = (time.time() - pr["started"]) / 60 if pr["started"] else -1
        print(f"  pid {pr['pid']}")
        print(f"    指令   {pr['cmd'][:100]}")
        print(f"    目錄   {pr['cwd']}")
        print(f"    啟動   {age:.0f} 分鐘前" if age >= 0 else "    啟動   ?")
        if pr["cwd"] != str(BASE):
            print(f"    ⚠️ **這個行程的工作目錄不是 {BASE}** —— "
                  "它跑的是另一份程式碼")
            verdicts.append(
                f"pid {pr['pid']} 跑在 {pr['cwd']},不是 {BASE}")
        elif age > 20:
            verdicts.append(
                f"pid {pr['pid']} 已經跑了 {age:.0f} 分鐘 —— 沒有重啟過")
    if len(procs) > 1:
        print(f"\n  ⚠️ **有 {len(procs)} 個行程在聽同一個埠。** "
              "你連到的是哪一個不一定。")
        verdicts.append(f"{len(procs)} 個行程搶同一個埠")

    # ── 三、直接問服務自己 ────────────────────────────
    head("三、問服務自己:你跑的是哪一版")
    url = f"http://127.0.0.1:{PORT}/health"
    # 走限流器。這是打自己的 localhost,不是打交易所 —— 但
    # `tests/test_ratelimit.py` 的規則是**沒有例外**,而那條規則
    # 之所以有用,正是因為它沒有例外(一個可以繞過的限流器只是
    # 一個讓人放心的裝飾品)。代價是一個 token,一次,人工執行時。
    from core.ratelimit import urlopen
    try:
        with urlopen(url, timeout=10) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            code = resp.status
    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read().decode("utf-8"))
        except Exception:                        # noqa: BLE001
            body = {}
        code = e.code
    except Exception as e:                       # noqa: BLE001
        print(f"  {url} 連不上:{type(e).__name__}: {e}")
        verdicts.append("/health 連不上")
        body, code = None, None

    if body is not None:
        print(f"  HTTP {code}")
        build = body.get("build")
        if build:
            print(f"  服務回報版本  {build.get('describe')}")
            if build.get("commit") and got.commit \
                    and build["commit"] != got.commit:
                print("  ⚠️ **服務的 commit 跟這個目錄不一樣**")
                verdicts.append("服務跑的 commit 與目錄不同")
            elif build.get("uptime_s", 0) > 1200:
                verdicts.append(
                    f"服務已經跑了 {build['uptime_s'] / 60:.0f} 分鐘,沒重啟過")
        else:
            print("  ⚠️ **回應裡沒有 build 欄位。**")
            print("     版本戳是 2026-09-13 才加的 —— 沒有它,")
            print("     就代表這個服務跑的是**那之前**的程式碼。")
            verdicts.append("/health 沒有 build 欄位 = 服務跑的是舊版")

    # ── 四、服務單元本身 ──────────────────────────────
    head("四、systemd 怎麼說")
    for unit in ("agmcis-dash", "agmcis-wild"):
        rc, state = sh(["systemctl", "is-active", unit])
        rc2, since = sh(["systemctl", "show", unit,
                         "-p", "ActiveEnterTimestamp", "--value"])
        print(f"  {unit:<16}{state:<10}{since if rc2 == 0 else ''}")
        rc3, execstart = sh(["systemctl", "show", unit,
                             "-p", "ExecStart", "--value"])
        if rc3 == 0 and execstart:
            print(f"  {'':<16}{execstart[:96]}")

    # ── 五、結論 ──────────────────────────────────────
    head("五、所以是哪一個")
    if not verdicts:
        print("  查不出問題 —— 目錄是新的、服務也回報新版本。")
        print("  那就剩最後一種可能:**我改的東西有 bug,沒渲染出來**。")
        print("  把上面整段貼給我,那是我的問題不是你的。")
    else:
        for i, v in enumerate(verdicts, 1):
            print(f"  {i}. {v}")
        print("\n  修法(照順序下,一行一行來):\n")
        print("    cd /root/agmcis")
        print("    git status --porcelain          # 有東西就先處理")
        print("    git pull origin agmcis-base")
        print("    git log --oneline -1            # 要看到最新那筆")
        print("    sudo systemctl restart agmcis-dash")
        print("    sleep 3 && curl -s localhost:8765/health | head -c 300")
        print("\n  最後那行要看到 \"build\" 跟一個 commit —— 看到了就成了。")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
