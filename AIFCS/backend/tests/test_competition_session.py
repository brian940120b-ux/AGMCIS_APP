"""Training that survives the machine being turned off (COMP PHASE 7).

A process cannot outlive a shutdown; what can is everything on disk. These test
the part that makes "stop it, shut down, carry on tomorrow" cost minutes rather
than days, without needing to train anything.
"""

from __future__ import annotations

import json
import re
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
BATCH_FILES = [
    "train.bat",
    "install_shortcuts.bat",
    "update.bat",
    "start.bat",
    "stop.bat",
    "tryout.bat",
]


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


@pytest.mark.parametrize("path", sorted(SCRIPTS.glob("*.ps1")), ids=lambda p: p.name)
def test_a_powershell_script_with_chinese_starts_with_a_utf8_bom(path: Path):
    """Windows PowerShell 5.1 reads a .ps1 as ANSI unless a BOM says otherwise.

    On a Traditional Chinese machine that is CP950, so UTF-8 text comes out as
    mojibake — and worse than mojibake: one of the misread bytes ended a quoted
    string early, and the shell printed

        Write-Host  AIFCS 閮毀 ???匱蝥奎鞈質?蝺?

    having taken the rest of the line as arguments. start.ps1 and stop.ps1 had
    the BOM all along; install_shortcuts.ps1, written later, did not.

    ASCII-only scripts do not need one, so the requirement follows the content.
    """
    raw = path.read_bytes()
    bom = b"\xef\xbb\xbf"
    body = raw[len(bom) :] if raw.startswith(bom) else raw
    body.decode("utf-8")  # it must be UTF-8 whatever else is true

    if any(byte > 127 for byte in body):
        assert raw.startswith(bom), (
            f"{path.name} has non-ASCII text and no UTF-8 BOM, so PowerShell 5.1 "
            "will decode it with the system codepage"
        )


# ------------------------------------------- what makes a run a different run


def test_a_changed_discount_is_refused_like_a_changed_reward(tmp_path: Path):
    """Gamma is not a knob you turn mid-session.

    The discount decides how far ahead the value function can see — 1/(1-gamma)
    frames, which at 60 Hz is 1.7 s at 0.99 and 20 s at 0.999. Steps taken
    under one and steps taken under the other are answers to different
    questions, and a card that averages them is honest about neither.
    """
    session = Session(tmp_path / "s")
    trained = SessionState(name="s", algorithm="sac", reward="score", hyperparameters={"gamma": 0.99})
    wanted = SessionState(name="s", algorithm="sac", reward="score", hyperparameters={"gamma": 0.999})

    with pytest.raises(IncompatibleSession) as refusal:
        session.check_compatible(trained, wanted)
    assert "gamma 0.99 -> 0.999" in str(refusal.value)


def test_a_session_from_before_the_setting_existed_still_resumes(tmp_path: Path):
    """run1 was already two million steps in when this was added.

    The check walks the keys the session recorded, not the ones it might have:
    a run that never wrote down its discount cannot be found to disagree about
    it, so adding a setting does not strand the runs that predate it.
    """
    session = Session(tmp_path / "s")
    trained = SessionState(name="s", algorithm="sac", timesteps_done=1_921_544)
    assert trained.hyperparameters == {}

    wanted = SessionState(
        name="s",
        algorithm="sac",
        hyperparameters={"gamma": 0.99, "batch_size": 256, "gradient_steps": 1},
    )
    session.check_compatible(trained, wanted)  # must not raise


# --------------------------------------------------- what a checkpoint records


class _FakeModel:
    """Enough of a model to be saved, and nothing else."""

    def save(self, path) -> None:
        Path(path).write_text("model", encoding="utf-8")


def test_a_run_records_the_time_it_took_not_a_multiple_of_it(tmp_path: Path):
    """Found while answering "where is the progress?" rather than by a failure.

    `elapsed_s` is the whole of the run so far, and every checkpoint added it
    to a running total — so the same seconds were counted again at each one.
    Three checkpoints in a five-minute run recorded ten minutes. The overnight
    runs take a checkpoint every 25,000 steps, so five million steps would
    record a number roughly a hundred times the truth, on the model card, where
    it is read as the cost of training.
    """
    from competition.session import save_checkpoint

    session = Session(tmp_path / "s")
    state = SessionState(name="s", algorithm="sac", target_timesteps=100_000)
    before = state.wall_clock_s

    for steps, elapsed in ((25_000, 100.0), (50_000, 200.0), (75_000, 300.0)):
        save_checkpoint(
            _FakeModel(),
            session,
            state,
            steps_this_run=steps,
            started_steps=0,
            wall_clock_before=before,
            elapsed_s=elapsed,
            save_buffer=False,
        )

    assert state.wall_clock_s == 300.0, "three checkpoints, one run, three hundred seconds"
    assert state.timesteps_done == 75_000


def test_time_already_spent_on_a_session_is_carried_not_lost(tmp_path: Path):
    """The opposite mistake: setting the total instead of adding to it would
    make every resume forget what the session had already cost."""
    from competition.session import save_checkpoint

    session = Session(tmp_path / "s")
    state = SessionState(name="s", algorithm="sac", target_timesteps=100_000)
    state.wall_clock_s = 900.0

    save_checkpoint(
        _FakeModel(),
        session,
        state,
        steps_this_run=1_000,
        started_steps=50_000,
        wall_clock_before=900.0,
        elapsed_s=300.0,
        save_buffer=False,
    )

    assert state.wall_clock_s == 1200.0
    assert state.timesteps_done == 51_000


# ------------------------------------------------ the platform launchers

LAUNCHERS = {"start.sh": "sh", "start.ps1": "ps1"}


@pytest.mark.parametrize("name", sorted(LAUNCHERS))
def test_the_dashboard_probe_tries_both_names_for_this_machine(name: str):
    """Reported: Vite logged "ready in 2794 ms" and the launcher killed it.

        VITE v6.4.3  ready in 2794 ms
        ➜  Local:   http://localhost:5173/
        !!  Dashboard did not start in 30s.

    "localhost" and "127.0.0.1" are the same machine and not always the same
    address — the first can resolve to the IPv6 loopback where the dev server
    is listening on IPv4, or the reverse. Probing one name and reporting "did
    not start" says the opposite of what the log beside it says.
    """
    raw = (SCRIPTS / name).read_bytes()
    text = raw.lstrip(b"\xef\xbb\xbf").decode("utf-8")
    assert "127.0.0.1:$FRONTEND_PORT" in text or "127.0.0.1:$FrontendPort" in text
    assert "localhost:$FRONTEND_PORT" in text or "localhost:$FrontendPort" in text


@pytest.mark.parametrize("name", sorted(LAUNCHERS))
def test_a_failed_dashboard_says_what_it_tried(name: str):
    """A timeout with no evidence is not a diagnosis.

    The failure now prints each URL with what came back, and what is listening
    on the port, so the next report carries the answer with it.
    """
    raw = (SCRIPTS / name).read_bytes()
    text = raw.lstrip(b"\xef\xbb\xbf").decode("utf-8")
    assert "did not answer" in text, "the message should describe what happened"
    assert "What was tried" in text
    assert "netstat" in text


@pytest.mark.parametrize("name", sorted(LAUNCHERS))
def test_the_startup_deadlines_allow_for_a_cold_first_start(name: str):
    """Measured here, same code, same machine, two runs in a row:

        !!  Backend did not become healthy in 30s.     (first start)
            Backend ready — http://127.0.0.1:8080/docs (next start, 2s)

    Startup imports torch so the training subsystem can report whether it is
    available, and a CUDA build's first import reads hundreds of megabytes of
    libraries. Cold page cache, or antivirus reading each one, and half a
    minute is not close to enough. The old deadline did not find a broken
    backend; it made a slow one look broken.
    """
    raw = (SCRIPTS / name).read_bytes()
    text = raw.lstrip(b"\xef\xbb\xbf").decode("utf-8")
    # Anchored on the variable name rather than on "some number in the file":
    # a first draft of this matched the port numbers too, and would have passed
    # on 8080 while the real deadline was 30.
    waits = [int(value) for value in re.findall(r"TIMEOUT_S[^\n]*?(\d{2,})", text)]
    assert len(waits) == 2, f"{name} needs a deadline for the backend and one for the dashboard"
    assert min(waits) >= 120, f"{name} gives up after {min(waits)}s; a first start on a laptop takes longer"


@pytest.mark.parametrize("name", sorted(LAUNCHERS))
def test_a_slow_start_says_it_is_still_working(name: str):
    """Three minutes of silence is indistinguishable from a hang.

    Raising the deadline without saying anything would trade one bad experience
    for another, so the wait accounts for itself once it stops being quick.
    """
    raw = (SCRIPTS / name).read_bytes()
    text = raw.lstrip(b"\xef\xbb\xbf").decode("utf-8")
    assert "Still starting" in text
    assert "still waiting" in text
    assert "PyTorch" in text, "say which slow thing is being waited for"


@pytest.mark.parametrize("name", sorted(LAUNCHERS))
def test_the_dashboard_does_not_need_the_dev_server(name: str):
    """Reported from the laptop, after the backend deadline was fixed:

        Backend ready - http://127.0.0.1:8080/docs
        ==> Starting dashboard / 啟動儀表板…
            ...still waiting (149s of 180s)
        Listening on 5173:
        !!  Dashboard did not answer in 180s.

    Nothing was listening: Vite never got as far as binding the port. The dev
    server is a development tool, and starting the platform is not development
    — so the default stopped being the thing that had failed twice. The backend
    serves the built bundle, and the dev server stays one flag away.
    """
    raw = (SCRIPTS / name).read_bytes()
    text = raw.lstrip(b"\xef\xbb\xbf").decode("utf-8")
    assert "built" in text and "dev" in text, f"{name} should offer both ways of serving"
    assert "npm run build" in text or "npm run build" in text.replace("'", "")
    # And the default is the one that does not need Vite running.
    assert re.search(r"(AIFCS_DASHBOARD[^\n]*built|else \{ 'built' \})", text), (
        f"{name} should default to the built bundle"
    )


@pytest.mark.parametrize("name", sorted(LAUNCHERS))
def test_a_dead_dev_server_points_at_the_way_that_works(name: str):
    """A failure that names no next step leaves its reader where they started."""
    raw = (SCRIPTS / name).read_bytes()
    text = raw.lstrip(b"\xef\xbb\xbf").decode("utf-8")
    assert "The dev server is not the only way to run the dashboard." in text


def test_the_shell_launcher_is_still_valid_shell():
    """bash -n, because a launcher that does not parse fails at the worst time."""
    import subprocess

    result = subprocess.run(["bash", "-n", str(SCRIPTS / "start.sh")], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


# ------------------------------------------------- what the dev server reads

FRONTEND_SRC = Path(__file__).resolve().parents[2] / "frontend" / "src"


def test_the_three_d_view_imports_only_the_two_components_it_uses():
    """Reported from the laptop, as the dev server dying just after saying ready:

        Cannot read file "node_modules/@react-three/drei/core/TrailTexture.js":
        系統資源不足，無法完成要求的服務。

    That is Windows' ERROR_NO_SYSTEM_RESOURCES: handles or paged pool exhausted.
    esbuild opens a great many files at once while pre-bundling, and importing
    from `@react-three/drei` pulls its barrel — 320 files — to reach two
    components. Naming the two directly took the pre-bundle from 28.0 MB to
    18.3 MB on this machine.

    It reduces the pressure rather than removing the limit, so this is a test
    to stop it creeping back, not a claim that the limit cannot be reached.
    """  # noqa: RUF002  (the message is quoted as Windows printed it)
    offenders = []
    for path in FRONTEND_SRC.rglob("*.tsx"):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if "'@react-three/drei'" in line:
                offenders.append(f"{path.name}:{number}")
    assert not offenders, "import the component's own module instead of the barrel: " + ", ".join(offenders)


def test_the_components_that_are_imported_are_the_ones_that_are_used():
    """A narrow import that names the wrong module is worse than a broad one."""
    aircraft = (FRONTEND_SRC / "three" / "Aircraft.tsx").read_text(encoding="utf-8")
    camera = (FRONTEND_SRC / "three" / "CameraRig.tsx").read_text(encoding="utf-8")
    assert "@react-three/drei/web/Html" in aircraft
    assert "<Html" in aircraft
    assert "@react-three/drei/core/OrbitControls" in camera
    assert "<OrbitControls" in camera


def test_the_tryout_runs_every_upgrade_and_says_which_one_broke():
    """One command that exercises the new options on the machine that matters.

    The suite answers "is it correct"; this answers "does it work on your
    hardware, and what are the numbers there". Pinned because a check that
    quietly stops covering something is worse than no check — the point of it
    is the list.
    """
    source = (SCRIPTS.parent / "backend" / "competition" / "tryout.py").read_text(encoding="utf-8")
    for upgrade in (
        "ground_avoidance",
        "reward",
        "observation",
        "opponent",
        "action_repeat",
        "training",
    ):
        assert upgrade in source, f"the tryout should exercise {upgrade}"
    # And it has to fail loudly rather than print numbers and exit zero.
    assert "return 1" in source
