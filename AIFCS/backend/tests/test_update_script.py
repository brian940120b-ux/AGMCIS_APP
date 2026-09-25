"""Tests for the one-command updater, `scripts/update.sh` (PHASE 20).

These run the real script in a throwaway git repository with no `origin`, so it
always dies at the fetch in step 2. That is the point: what step 2 says tells us
whether step 1 let the run through, and step 1 is the part that has been wrong.

The bug these pin: step 1 refused to update whenever `git status` reported
anything at all, including untracked files. A leftover folder from an earlier
failed download — which `git pull --ff-only` never touches — was enough to block
the whole update, under a message claiming the user had unsaved changes.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
UPDATE_SH = PROJECT_ROOT / "scripts" / "update.sh"

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None or shutil.which("git") is None,
    reason="needs bash and git",
)


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def _sandbox(tmp_path: Path) -> Path:
    """A git repo holding a copy of scripts/, shaped like the real checkout.

    `update.sh` resolves its own root from BASH_SOURCE, so the copy must sit at
    <repo>/AIFCS/scripts/ for the AIFCS-only pathspec to mean the same thing.
    """
    root = tmp_path / "AIFCS"
    (root / "scripts").mkdir(parents=True)
    for name in ("update.sh", "lib.sh", "start.sh"):
        shutil.copy2(PROJECT_ROOT / "scripts" / name, root / "scripts" / name)
    (root / "tracked.txt").write_text("original\n", encoding="utf-8")

    _git(tmp_path, "init", "--initial-branch=main")
    _git(tmp_path, "config", "user.email", "test@example.invalid")
    _git(tmp_path, "config", "user.name", "test")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-m", "initial")
    return root


def _run(root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(root / "scripts" / "update.sh"), "--no-start"],
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_leftover_untracked_files_do_not_block_the_update(tmp_path: Path) -> None:
    """The reported failure: `?? AGMCIS_APP/` stopped a working update."""
    root = _sandbox(tmp_path)
    (root / "AGMCIS_APP").mkdir()
    (root / "AGMCIS_APP" / "leftover.txt").write_text("junk\n", encoding="utf-8")

    result = _run(root)

    assert "Nothing unsaved" in result.stdout
    assert "AGMCIS_APP" in result.stdout, "the extra files should still be named"
    assert "not committed" not in result.stderr
    # It got past step 1 and died where a repo with no origin has to die.
    assert "Could not reach GitHub" in result.stderr


def test_edits_to_tracked_files_still_stop_the_update(tmp_path: Path) -> None:
    """Loosening step 1 must not have given up the protection it exists for."""
    root = _sandbox(tmp_path)
    (root / "tracked.txt").write_text("my unsaved work\n", encoding="utf-8")

    result = _run(root)

    assert result.returncode != 0
    assert "not committed" in result.stderr
    assert "Nothing unsaved" not in result.stdout
    assert "Could not reach GitHub" not in result.stderr, "should not have got that far"


def test_changes_outside_aifcs_are_not_our_business(tmp_path: Path) -> None:
    """The trading side of the repo shares this checkout and is not ours to guard."""
    root = _sandbox(tmp_path)
    (tmp_path / "trading_notes.txt").write_text("someone else's work\n", encoding="utf-8")

    result = _run(root)

    assert "Nothing unsaved" in result.stdout
    assert "trading_notes" not in result.stdout, "outside AIFCS/, so not listed"
