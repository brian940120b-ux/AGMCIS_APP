"""Importing torch, and saying something useful when Windows will not let us.

Three different Windows failures have now cost real time on this project, and
all three arrive as an `OSError` from a DLL load with a number in it that means
nothing to the person reading it. A traceback ending in `cufft64_12.dll` does
not tell anyone to go and change their paging file.

This is the one place that imports the stack, so it is the one place that has
to explain itself.
"""

from __future__ import annotations

from typing import Any

#: Windows error codes seen on this project, and what each one actually wants.
#: Keyed by the number in the message, because that is what the reader has.
WINDOWS_REMEDIES: dict[int, tuple[str, str]] = {
    1455: (
        "The Windows paging file is too small to load PyTorch's CUDA libraries.",
        "Windows 的分頁檔(虛擬記憶體)太小,載不進 PyTorch 的 CUDA 函式庫。\n"
        "    修法:設定 → 系統 → 系統資訊 → 進階系統設定 → 效能「設定」→\n"
        "          進階 → 虛擬記憶體「變更」→ 勾選「自動管理」→ 重新開機。\n"
        "    或先關掉還開著的訓練視窗和吃記憶體的程式,再試一次。",
    ),
    1114: (
        "A DLL failed to initialise — usually the Visual C++ runtime is missing.",
        "動態函式庫初始化失敗,通常是缺少 Visual C++ 可轉散發套件。\n"
        "    裝 Microsoft Visual C++ Redistributable (x64) 再試一次。",
    ),
    1450: (
        "Windows is out of system resources — handles or paged pool exhausted.",
        "Windows 系統資源不足(handle 或 paged pool 用完)。\n"
        "    重新開機,並把 repo 資料夾加進 Defender 排除清單。",
    ),
}


class StackUnavailable(RuntimeError):
    """The RL stack is installed and will not load, with the reason spelled out."""


def _windows_code(error: BaseException) -> int | None:
    """The WinError number, if this is one."""
    code = getattr(error, "winerror", None)
    return int(code) if isinstance(code, int) else None


def explain(error: BaseException) -> str:
    """The failure, and what to do about it, in both languages."""
    lines = [f"{type(error).__name__}: {error}", ""]
    remedy = WINDOWS_REMEDIES.get(_windows_code(error) or -1)
    if remedy is not None:
        english, chinese = remedy
        lines.append(f"    {english}")
        lines.append(f"    {chinese}")
    else:
        lines.append("    The RL stack is installed but will not load on this machine.")
        lines.append("    RL 套件有裝,但在這台機器上載不起來。把這整段貼出來。")
    return "\n".join(lines)


def load_algorithm(algorithm: str) -> Any:
    """`SAC` or `PPO`, or a `StackUnavailable` that says what to do instead.

    Every competition entry point goes through here rather than importing
    Stable-Baselines3 itself, so a machine that cannot load it fails once, in
    one voice, with a remedy attached.
    """
    try:
        from stable_baselines3 import PPO, SAC
    except ImportError as error:
        raise StackUnavailable(
            f"{error}\n\n    The RL stack is not installed.\n    RL 套件沒有安裝。執行 scripts\\update.bat。"
        ) from error
    except Exception as error:  # a load failure, not an absence
        raise StackUnavailable(explain(error)) from error

    return PPO if algorithm == "ppo" else SAC
