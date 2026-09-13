"""
腳本要能被**當成腳本**跑起來 · 2026-09-13

═══ 這一組是為了一次真實的停機而寫的 ═══
面板 `agmcis-dash` 從 commit `e4f404a` 起就再也起不來:

    File "/root/agmcis/scripts/dashboard.py", line 33, in <module>
        from core import ratelimit
    ModuleNotFoundError: No module named 'core'
    agmcis-dash.service: Scheduled restart job, restart counter is at 93

原因只有一行:`from core import ratelimit` 被排到了
`sys.path.insert(0, 根目錄)` 的**上面**。

`python scripts/dashboard.py` 的 `sys.path[0]` 是 `scripts/`,不是
根目錄 —— 所以那一行 import 必然失敗。

═══ 為什麼 331 條測試全綠 ═══
**pytest 會自己把 rootdir 放進 `sys.path`。** 所以測試裡
`import scripts.dashboard` 一路順暢,而 systemd 用
`ExecStart=.../python scripts/dashboard.py` 跑的時候必死。

    **測試跑得起來,不代表服務跑得起來。**

而它壞掉的方式最惡劣:systemd 的 Type=simple 在行程 fork 出來
那一刻就報 active,`systemctl restart` 看起來成功、`is-active`
說 active —— 只有 journalctl 裡那個一路往上跳的 restart counter
知道真相,而沒有人在看它。

這一組就是那個「有人在看」。
"""
from __future__ import annotations

import ast
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

BASE = Path(__file__).resolve().parents[1]
SCRIPTS = BASE / "scripts"

#: 這個倉庫自己的頂層套件 —— 它們全部要靠 sys.path 那一行才 import 得到。
FIRST_PARTY = {p.name for p in BASE.iterdir()
               if p.is_dir() and (p / "__init__.py").exists()} | {"portfolio"}


def scripts() -> list:
    return sorted(p for p in SCRIPTS.glob("*.py") if p.name != "__init__.py")


def bootstrap_line(tree: ast.AST) -> int | None:
    """`sys.path.insert(...)` 在第幾行。找不到回 None。"""
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        if (isinstance(fn, ast.Attribute) and fn.attr == "insert"
                and isinstance(fn.value, ast.Attribute)
                and fn.value.attr == "path"
                and isinstance(fn.value.value, ast.Name)
                and fn.value.value.id == "sys"):
            return node.lineno
    return None


def first_party_imports(tree: ast.AST) -> list:
    """(行號, 套件名) —— 只看模組層,函式內的 import 不算。

    函式內的 import 在**呼叫時**才執行,那時 sys.path 早就補好了。
    會炸的只有模組層那些。
    """
    out = []
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            top = node.module.split(".")[0]
            if top in FIRST_PARTY:
                out.append((node.lineno, node.module))
        elif isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.split(".")[0]
                if top in FIRST_PARTY:
                    out.append((node.lineno, alias.name))
    return out


@pytest.mark.parametrize("path", scripts(), ids=lambda p: p.name)
def test_first_party_imports_come_after_the_sys_path_line(path):
    """**這條就是那個 93 次重啟。**

    `python scripts/X.py` 的 sys.path[0] 是 `scripts/`,所以任何
    第一方 import 都必須排在 `sys.path.insert(根目錄)` 之後。
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imports = first_party_imports(tree)
    if not imports:
        return                                   # 沒用到第一方套件,無所謂

    line = bootstrap_line(tree)
    assert line is not None, (
        f"{path.name} 在模組層 import 了 "
        f"{', '.join(m for _, m in imports)},卻沒有 sys.path.insert —— "
        "當成腳本跑會 ModuleNotFoundError")

    early = [(n, m) for n, m in imports if n < line]
    assert not early, (
        f"{path.name} 這些 import 排在 sys.path.insert(第 {line} 行)"
        "**之前**:\n  "
        + "\n  ".join(f"第 {n} 行 {m}" for n, m in early)
        + "\n\n`python scripts/{}` 會當場 ModuleNotFoundError。"
          "\n2026-09-13 面板就是這樣死了 93 次,而測試全綠 ——"
          "\npytest 會自己把根目錄放進 sys.path,systemd 不會。"
          .format(path.name))


def test_the_dashboard_really_starts_the_way_systemd_starts_it():
    """不是靜態檢查 —— **真的用 systemd 的方式跑一次**。

    `ExecStart=/root/agmcis/.venv/bin/python scripts/dashboard.py`
    照這個跑,而且**清掉 PYTHONPATH** —— 測試環境的 sys.path
    正是讓這個 bug 藏了一整天的東西。
    """
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    env["DASHBOARD_PORT"] = "8791"

    proc = subprocess.Popen(
        [sys.executable, "scripts/dashboard.py"],
        cwd=str(BASE), env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        deadline = time.time() + 30
        while time.time() < deadline:
            if proc.poll() is not None:
                out = proc.stdout.read() if proc.stdout else ""
                pytest.fail(
                    "面板當成腳本跑會當場死掉 —— systemd 就是這樣跑的:\n"
                    + out[-1500:])
            time.sleep(0.5)
            # 綁上埠就算起來了
            import socket
            with socket.socket() as s:
                if s.connect_ex(("127.0.0.1", 8791)) == 0:
                    return
        pytest.fail("30 秒還沒綁上埠")
    finally:
        proc.kill()
        proc.wait(timeout=10)
