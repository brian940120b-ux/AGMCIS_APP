"""A .bat with a stray LF is a .bat that may not parse. Guard the whole set."""

from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent.parent / "scripts"


def test_every_batch_file_is_ascii_and_crlf_throughout():
    """CMD's parser and UTF-8 do not mix, and a mixed-ending file is worse than
    a consistently wrong one: it parses until it does not.

    train_status.bat picked up a lone LF from an edit that inserted a Python
    newline into a CRLF file — invisible in every diff and in the editor.
    """
    offenders = []
    for path in sorted(SCRIPTS.glob("*.bat")):
        raw = path.read_bytes()
        try:
            raw.decode("ascii")
        except UnicodeDecodeError:
            offenders.append(f"{path.name}: not ASCII")
            continue
        if raw.replace(b"\r\n", b"").count(b"\n"):
            offenders.append(f"{path.name}: has a bare LF")

    assert not offenders, "\n".join(offenders)
