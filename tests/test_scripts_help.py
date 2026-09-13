"""
每一支腳本的 --help 都要能跑 · 2026-09-13

═══ 為什麼需要這條 ═══
`scripts/ticket.py` 的 help 字串裡有 `25.0% ——`,而 argparse 會拿
help 去做 `%` 格式化 —— `% —` 被當成格式符,`--help` 當場拋:

    ValueError: unsupported format character '?' (0x2014)

那不是小事:**執政官在 iPhone 上用 Termius,`--help` 是他唯一
看得到用法的地方**。而它壞掉的方式是「只有 --help 壞」——
腳本本身跑得好好的,平常的測試一條都不會紅。
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

BASE = Path(__file__).resolve().parents[1]
SCRIPTS = BASE / "scripts"


def with_argparse() -> list:
    """有用 argparse 的腳本。沒用的就沒有 --help 可以壞。"""
    out = []
    for path in sorted(SCRIPTS.glob("*.py")):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        if "argparse" in text and "add_argument" in text:
            out.append(path)
    return out


@pytest.mark.parametrize("script", with_argparse(), ids=lambda p: p.name)
def test_help_renders(script):
    """`--help` 要回 0,而且不能拋例外。"""
    proc = subprocess.run(
        [sys.executable, str(script), "--help"],
        capture_output=True, text=True, timeout=90, cwd=str(BASE))
    assert proc.returncode == 0, (
        f"{script.name} --help 掛了:\n{proc.stderr[-1500:]}")
    assert proc.stdout.strip(), f"{script.name} --help 什麼都沒印"


def test_there_is_at_least_one_script_to_check():
    """掃不到任何腳本的話,上面那組會靜靜地一條都不跑。"""
    assert with_argparse(), "scripts/ 底下掃不到用 argparse 的腳本"
