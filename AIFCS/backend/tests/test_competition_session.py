"""Training that survives the machine being turned off (COMP PHASE 7).

A process cannot outlive a shutdown; what can is everything on disk. These test
the part that makes "stop it, shut down, carry on tomorrow" cost minutes rather
than days, without needing to train anything.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from competition.session import (
    IncompatibleSession,
    KeepAwake,
    Session,
    SessionState,
    describe_progress,
    human_duration,
    tensorboard_log_dir,
)


def a_state(**overrides) -> SessionState:
    state = SessionState(
        name="run1",
        algorithm="sac",
        target_timesteps=1_000_000,
        reward="reference",
        environment={"opponent": "reference", "rudder_enabled": False},
    )
    for key, value in overrides.items():
        setattr(state, key, value)
    return state


# ------------------------------------------------------------------- state


def test_a_session_round_trips_through_disk(tmp_path: Path):
    session = Session(tmp_path / "run1")
    session.root.mkdir(parents=True)
    original = a_state(timesteps_done=123_456, runs=3)
    session.write_state(original)

    restored = session.read_state()
    assert restored is not None
    assert restored.timesteps_done == 123_456
    assert restored.runs == 3
    assert restored.environment == original.environment


def test_an_absent_session_reads_as_nothing(tmp_path: Path):
    assert Session(tmp_path / "nothing").read_state() is None


def test_writing_the_state_leaves_no_half_written_file(tmp_path: Path):
    """It goes to a temporary name and is renamed, which is atomic on both OSes.

    A half-written state file is worse than none: it either fails to parse or,
    worse, parses into a step count the model does not have.
    """
    session = Session(tmp_path / "run1")
    session.root.mkdir(parents=True)
    session.write_state(a_state(timesteps_done=1))
    session.write_state(a_state(timesteps_done=2))

    assert json.loads(session.state_path.read_text())["timesteps_done"] == 2
    assert not list(session.root.glob("*.tmp"))


def test_a_state_file_from_a_later_version_does_not_stop_the_run(tmp_path: Path):
    """Unknown keys are dropped rather than raising: a session outlives a field."""
    session = Session(tmp_path / "run1")
    session.root.mkdir(parents=True)
    session.write_state(a_state(timesteps_done=5))
    raw = json.loads(session.state_path.read_text())
    raw["something_added_later"] = True
    session.state_path.write_text(json.dumps(raw))

    restored = session.read_state()
    assert restored is not None and restored.timesteps_done == 5


# ----------------------------------------------------------- compatibility


@pytest.mark.parametrize(
    ("field", "value", "expected"),
    [
        ("algorithm", "ppo", "algorithm sac -> ppo"),
        ("reward", "score", "reward reference -> score"),
    ],
)
def test_resuming_against_a_different_problem_is_refused(tmp_path, field, value, expected):
    """Steps spent on two problems make a card that can only be right about one."""
    session = Session(tmp_path / "run1")
    existing = a_state(timesteps_done=20_480)
    wanted = a_state(**{field: value})

    with pytest.raises(IncompatibleSession, match="20,480 steps") as raised:
        session.check_compatible(existing, wanted)
    assert expected in str(raised.value)
    assert "--name" in str(raised.value), "it has to say what to do about it"


def test_a_changed_environment_is_refused_and_named(tmp_path: Path):
    session = Session(tmp_path / "run1")
    existing = a_state(timesteps_done=100)
    wanted = a_state(environment={"opponent": "level", "rudder_enabled": False})

    with pytest.raises(IncompatibleSession, match="opponent"):
        session.check_compatible(existing, wanted)


def test_the_same_problem_resumes_without_complaint(tmp_path: Path):
    session = Session(tmp_path / "run1")
    session.check_compatible(a_state(timesteps_done=999), a_state())


# --------------------------------------------------------------- niceties


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [(5, "5s"), (89, "89s"), (600, "10 min"), (7200, "2.0 hr"), (86400 * 3, "3.0 days")],
)
def test_a_duration_reads_the_way_someone_says_it(seconds, expected):
    assert human_duration(seconds) == expected


def test_progress_estimates_what_is_left():
    state = a_state(timesteps_done=250_000, target_timesteps=1_000_000)
    line = describe_progress(state, steps_per_second=1000.0)
    assert "250,000 / 1,000,000" in line
    assert "25.0%" in line
    assert "12 min" in line, line


def test_progress_does_not_estimate_when_it_is_finished():
    state = a_state(timesteps_done=1_000_000, target_timesteps=1_000_000)
    assert "left" not in describe_progress(state, steps_per_second=1000.0)


def test_missing_tensorboard_is_not_a_reason_to_lose_an_overnight_run(monkeypatch, tmp_path):
    """SB3 raises ImportError out of learn() when handed a log dir it cannot use.

    Not at construction, and not with a warning — so an absent way of drawing
    graphs killed a training run outright until this checked first.
    """
    import builtins

    real_import = builtins.__import__

    def refuse(name, *args, **kwargs):
        if name == "tensorboard":
            raise ImportError("no tensorboard")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", refuse)
    assert tensorboard_log_dir(tmp_path) is None


def test_keep_awake_is_harmless_where_it_does_not_apply():
    """Not being able to prevent sleep is not a reason to refuse to train."""
    with KeepAwake() as awake:
        assert isinstance(awake, KeepAwake)


# -------------------------------------------------- the double-click scripts

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
BATCH_FILES = ["train.bat", "install_shortcuts.bat", "update.bat", "start.bat", "stop.bat"]


@pytest.mark.parametrize("name", BATCH_FILES)
def test_a_batch_file_is_ascii_with_the_line_endings_windows_needs(name: str):
    """Two things that make a .bat work, and are invisible when they do not.

    CMD needs CRLF: bare LF fails in ways that look like the commands being
    wrong. And CMD's batch parser and UTF-8 do not mix — setting the codepage
    to 65001 fixes output but not the parsing of the file itself. This line::

        echo   AIFCS 訓練    開始或繼續競賽訓練

    produced::

        '開始或繼續競賽訓練' is not recognized as an internal or external command

    because the parser lost its place inside the multibyte text. The line above
    it, with different characters, printed correctly — which is what makes this
    worth a test rather than a note.

    So: .bat files are ASCII. Anything a person reads in Chinese is printed by
    PowerShell or by Python, both of which reach the Windows console through an
    API that does not go through the codepage at all.
    """
    raw = (SCRIPTS / name).read_bytes()
    assert b"\r\n" in raw
    assert raw.count(b"\n") == raw.count(b"\r\n"), "every newline must be CRLF"
    offenders = [byte for byte in raw if byte > 127]
    assert not offenders, f"{name} has {len(offenders)} non-ASCII bytes"


def test_the_training_launcher_points_at_the_training_program():
    text = (SCRIPTS / "train.bat").read_text(encoding="ascii")
    assert "backend\\competition\\train.py" in text
    assert "%*" in text, "arguments typed after the file name have to reach argparse"
    assert "--timesteps" in text


def test_the_shortcut_installer_asks_windows_where_the_desktop_is():
    """It may be redirected to OneDrive, as this machine's nearly was.

    The work is in the .ps1: PowerShell reads UTF-8 properly, and the shortcut
    names are Chinese. start.bat has used this shape all along, for the same
    reason this one had to adopt it.
    """
    script = (SCRIPTS / "install_shortcuts.ps1").read_text(encoding="utf-8")
    executable = "\n".join(line for line in script.splitlines() if not line.strip().startswith("#"))
    assert "GetFolderPath('Desktop')" in executable
    assert "$env:USERPROFILE" not in executable
    assert "start.bat" in executable and "train.bat" in executable

    launcher = (SCRIPTS / "install_shortcuts.bat").read_text(encoding="ascii")
    assert "install_shortcuts.ps1" in launcher


def test_the_bilingual_banner_comes_from_python_not_from_the_batch_file():
    """Because that is the half of the pair that can carry it safely."""
    from competition.train import BANNER

    assert "Ctrl+C" in BANNER
    assert any(ord(character) > 127 for character in BANNER), "the Chinese lines"
    assert "noqa" not in BANNER, "a suppression inside a string would be printed"

    launcher = (SCRIPTS / "train.bat").read_bytes()
    assert all(byte < 128 for byte in launcher)
