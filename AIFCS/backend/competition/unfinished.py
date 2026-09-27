"""Name the session a machine should carry on with after a restart.

Prints one session name and nothing else, so a batch file can read it:

    for /f %%s in ('python -m competition.unfinished') do ...

Silence means there is nothing to resume, which the caller must treat as the
normal case rather than an error — most reboots happen with no run in flight.
"""

from __future__ import annotations

import sys
from pathlib import Path

from competition.session import unfinished_sessions

DEFAULT_ROOT = Path("models/competition")


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    root = Path(argv[0]) if argv else DEFAULT_ROOT

    pending = unfinished_sessions(root)
    if not pending:
        return 1

    name, done, target = pending[0]
    print(name)
    print(f"{name}: {done:,} / {target:,} steps", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
