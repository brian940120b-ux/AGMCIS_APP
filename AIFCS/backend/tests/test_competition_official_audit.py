"""The read-only extractor for the organiser's folder (docs/OFFICIAL_AUDIT.md)."""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from competition.official_audit import KEYWORDS, audit, category_of, kind_of, main


def _folder(tmp_path: Path) -> Path:
    root = tmp_path / "official"
    (root / "程式").mkdir(parents=True)
    (root / "README.txt").write_text("Player sends PLAYER_CMD over UDP to port 5005\n", encoding="utf-8")
    (root / "程式" / "player1_Loadmodel.py").write_text(
        "import socket\nsock = socket.socket()\ndata = struct.pack('<5f', *cmd) + b'PLAYER_CMD'\n",
        encoding="utf-8",
    )
    (root / "程式" / "notes.md").write_bytes("大五碼 測試\n".encode("cp950"))
    with zipfile.ZipFile(root / "package.zip", "w") as archive:
        archive.writestr("inner/host.exe", b"\x00\x01")
    (root / "tool.exe").write_bytes(b"MZ")
    (root / "later.7z").write_bytes(b"7z")
    return root


def test_it_inventories_extracts_lists_and_greps_without_touching_the_source(tmp_path):
    root = _folder(tmp_path)
    before = {p: p.stat().st_mtime_ns for p in root.rglob("*")}

    summary = audit(root, tmp_path / "out")

    assert summary["files"] == 6 and summary["python"] == 1 and summary["archives"] == 2
    assert {p: p.stat().st_mtime_ns for p in root.rglob("*")} == before, "read-only means read-only"
    out = tmp_path / "out"
    inventory = (out / "INVENTORY.md").read_text(encoding="utf-8")
    assert "6 files." in inventory and "`程式/player1_Loadmodel.py`" in inventory
    assert "7z/rar not opened here — NOT VERIFIED" in inventory
    extracted = (out / "EXTRACT" / "程式" / "player1_Loadmodel.py.txt").read_text(encoding="utf-8")
    assert "struct.pack" in extracted
    assert "大五碼" in (out / "EXTRACT" / "程式" / "notes.md.txt").read_text(encoding="utf-8")
    assert "inner/host.exe" in (out / "ARCHIVES.md").read_text(encoding="utf-8")
    grep = (out / "GREP.md").read_text(encoding="utf-8")
    assert "| `socket` | `程式/player1_Loadmodel.py` | 1 |" in grep
    assert "| `struct.pack` | `程式/player1_Loadmodel.py` | 3 |" in grep
    assert "PLAYER_CMD" in grep
    summary_text = (out / "SUMMARY.md").read_text(encoding="utf-8")
    assert "not_extracted: 2" in summary_text and "later.7z" in summary_text and "tool.exe" in summary_text


def test_a_pdf_is_extracted_page_by_page_with_its_images(tmp_path):
    pypdf = pytest.importorskip("pypdf")
    root = tmp_path / "official"
    root.mkdir()
    writer = pypdf.PdfWriter()
    writer.add_blank_page(width=200, height=200)
    writer.add_blank_page(width=200, height=200)
    with (root / "rules.pdf").open("wb") as handle:
        writer.write(handle)

    audit(root, tmp_path / "out")

    text = (tmp_path / "out" / "EXTRACT" / "rules.pdf.txt").read_text(encoding="utf-8")
    assert "2 pages" in text and "===== PAGE 1 =====" in text and "===== PAGE 2 =====" in text


def test_kinds_and_category_guesses_are_labelled_as_guesses():
    assert kind_of(Path("a.PDF")) == "pdf" and kind_of(Path("a.py")) == "text"
    assert kind_of(Path("a.rar")) == "archive-unopened" and kind_of(Path("a.dll")) == "binary"
    assert category_of("docs/競賽規則.pdf").startswith("OFFICIAL_DOCUMENT")
    assert "guess" in category_of("host/run.py")
    assert category_of("mystery.dat") == "UNKNOWN"
    assert "struct.pack" in KEYWORDS and "PLAYER_CMD" in KEYWORDS


def test_a_missing_folder_is_refused_with_its_path(tmp_path, capsys):
    assert main([str(tmp_path / "nope"), "--out", str(tmp_path / "out")]) == 2
    assert "is not a folder" in capsys.readouterr().err


def test_the_cli_reports_the_counts(tmp_path, capsys):
    root = _folder(tmp_path)
    assert main([str(root), "--out", str(tmp_path / "out")]) == 0
    out = capsys.readouterr().out
    assert "files: 6" in out and "not_extracted: 2" in out
