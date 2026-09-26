"""Loading the RL stack, and failing in a way someone can act on.

Three Windows DLL failures have cost real time on this project and all three
arrive as an OSError with a number in it. A traceback ending in
`cufft64_12.dll` does not tell anyone to go and change their paging file, and
the person reading it is not going to look the number up.
"""

from __future__ import annotations

import pytest

from competition.runtime import WINDOWS_REMEDIES, StackUnavailable, explain, load_algorithm


def windows_error(code: int, message: str) -> OSError:
    error = OSError(f"[WinError {code}] {message}")
    error.winerror = code  # type: ignore[attr-defined]
    return error


def test_the_paging_file_failure_names_the_paging_file():
    """Reported from the laptop, right after a training run that worked:

        OSError: [WinError 1455] 分頁檔太小，無法完成操作。
        Error loading "...torch\\lib\\cufft64_12.dll"

    Nothing in that says "virtual memory", and the file it names is a Fourier
    transform library, which is a red herring.
    """  # noqa: RUF002  (quoted as Windows printed it)
    told = explain(windows_error(1455, "分頁檔太小"))
    assert "paging file" in told
    assert "虛擬記憶體" in told
    assert "1455" in told, "keep the number, it is what the reader will search for"


@pytest.mark.parametrize("code", sorted(WINDOWS_REMEDIES))
def test_every_remedy_says_what_to_do_in_both_languages(code: int):
    english, chinese = WINDOWS_REMEDIES[code]
    assert english.strip()
    assert any("一" <= character <= "鿿" for character in chinese), "Chinese too"


def test_an_unknown_failure_still_asks_for_the_message():
    """Better than a remedy invented for a code nobody has seen."""
    told = explain(windows_error(9999, "something new"))
    assert "9999" in told
    assert "貼出來" in told


def test_a_working_machine_gets_the_algorithm():
    pytest.importorskip("stable_baselines3")
    from stable_baselines3 import PPO, SAC

    assert load_algorithm("sac") is SAC
    assert load_algorithm("ppo") is PPO


def test_a_load_failure_becomes_one_explained_error(monkeypatch):
    """Not a traceback through whichever entry point happened to import first."""
    import builtins

    real_import = builtins.__import__

    def refuse(name, *args, **kwargs):
        if name == "stable_baselines3":
            raise windows_error(1455, "分頁檔太小")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", refuse)
    with pytest.raises(StackUnavailable) as refusal:
        load_algorithm("sac")
    assert "paging file" in str(refusal.value)


def test_a_missing_package_is_told_apart_from_a_broken_one(monkeypatch):
    """Different failures, different advice: install it, versus fix the machine."""
    import builtins

    real_import = builtins.__import__

    def absent(name, *args, **kwargs):
        if name == "stable_baselines3":
            raise ImportError("No module named 'stable_baselines3'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", absent)
    with pytest.raises(StackUnavailable) as refusal:
        load_algorithm("sac")
    assert "update.bat" in str(refusal.value)
