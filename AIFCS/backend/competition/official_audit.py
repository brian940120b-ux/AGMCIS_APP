"""Read-only extraction of the organiser's folder, so it can be audited elsewhere.

The audit itself — reading every document, every program, every table image
— has to happen where the files are, and the files are on one laptop, on
purpose: this repository is public and the organiser's package is not ours to
publish. This tool runs on that laptop and writes everything a reader needs
into one folder that git ignores:

    INVENTORY.md    every file: path, size, type, sha256, category guess
    EXTRACT/        the text of every readable file, path for path
                    (.pdf via pypdf page by page, .docx via its XML, text
                    formats as they are); nothing is summarised
    IMAGES/         every image embedded in a PDF, because tables and
                    figures the organiser embedded as pictures are not text
    ARCHIVES.md     the listing of every zip (never extracted to disk)
    GREP.md         where the words that matter appear, file and line
    SUMMARY.md      the counts, mechanically

Nothing is modified, moved, renamed or deleted; every file is opened for
reading only. Nothing is interpreted: a missing extractor is reported as
NOT EXTRACTED, not worked around. What the output means is a separate step
done by a person, or by an assistant given the output — this only makes sure
that step has the whole folder in front of it and not a memory of it.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
import zipfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

TEXT_SUFFIXES = {
    ".txt",
    ".md",
    ".py",
    ".bat",
    ".cmd",
    ".ps1",
    ".sh",
    ".json",
    ".yaml",
    ".yml",
    ".ini",
    ".cfg",
    ".toml",
    ".xml",
    ".html",
    ".htm",
    ".css",
    ".js",
    ".ts",
    ".tsx",
    ".csv",
    ".log",
    ".acmi",
    ".rst",
    ".env",
    ".gitignore",
}
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}
ARCHIVE_SUFFIXES = {".zip"}
UNOPENED_ARCHIVES = {".7z", ".rar"}
BINARY_SUFFIXES = {".exe", ".dll", ".pth", ".pt", ".zip", ".pkl", ".so", ".pyd"}

#: The words the audit brief asks to be found in the organiser's programs.
KEYWORDS = (
    "socket",
    "sendto",
    "recvfrom",
    "recv(",
    "send(",
    "struct.pack",
    "struct.unpack",
    "PLAYER_CMD",
    "player_state",
    "LISTEN_IP",
    "LISTEN_PORT",
    "TARGET_IP",
    "TARGET_PORT",
    "127.0.0.1",
    "port",
    "jsbsim",
    "JSBSim",
    "f16",
    "F16",
    "gymnasium",
    "gym.",
    "stable_baselines",
    "SAC",
    "PPO",
    "torch",
    "reward",
    "observation",
    "action_space",
    "observation_space",
    "acmi",
    "ACMI",
    "Tacview",
    "tacview",
    "csv",
    "CSV",
    "conda",
    "pip install",
    "python==",
    "version",
    "on_target",
    "3d_angle",
    "kill",
    "advantage",
    "9G",
    "n-pilot-z",
    "randint",
    "ic/",
    "run_ic",
    "set_dt",
    "dt",
    "60",
    "0.0166",
)

#: A first guess at what a file is for, by name. A guess, and labelled so:
#: the reader decides.
CATEGORY_HINTS: tuple[tuple[str, str], ...] = (
    ("host", "HOST"),
    ("player", "PLAYER"),
    ("train", "TRAINING"),
    ("test", "TESTING"),
    ("env", "TRAINING"),
    ("install", "INSTALLATION"),
    ("環境", "INSTALLATION"),
    ("安裝", "INSTALLATION"),
    ("規則", "OFFICIAL_DOCUMENT"),
    ("公告", "OFFICIAL_DOCUMENT"),
    ("辦法", "OFFICIAL_DOCUMENT"),
    ("readme", "README"),
    ("acmi", "REPLAY"),
    ("tacview", "REPLAY"),
    ("model", "MODEL"),
    ("requirement", "INSTALLATION"),
)


@dataclass
class Entry:
    path: Path
    relative: str
    size: int
    sha256: str
    kind: str
    category_guess: str
    extracted: str  # "yes", "no: <reason>", "n/a"


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def kind_of(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return "pdf"
    if suffix == ".docx":
        return "docx"
    if suffix in TEXT_SUFFIXES:
        return "text"
    if suffix in IMAGE_SUFFIXES:
        return "image"
    if suffix in ARCHIVE_SUFFIXES:
        return "archive"
    if suffix in UNOPENED_ARCHIVES:
        return "archive-unopened"
    if suffix in BINARY_SUFFIXES:
        return "binary"
    return "unknown"


def category_of(relative: str) -> str:
    lowered = relative.lower()
    for needle, category in CATEGORY_HINTS:
        if needle in lowered:
            return category + " (guess by name)"
    return "UNKNOWN"


# ------------------------------------------------------------- extractors


def extract_pdf(path: Path, images_dir: Path) -> tuple[str, list[str]]:
    """Every page's text, page-numbered, and every embedded image saved."""
    try:
        from pypdf import PdfReader
    except ImportError as missing:
        raise RuntimeError("pypdf is not installed: pip install pypdf") from missing
    reader = PdfReader(str(path))
    parts = [f"# {path.name}: {len(reader.pages)} pages\n"]
    saved: list[str] = []
    for number, page in enumerate(reader.pages, 1):
        parts.append(f"\n\n===== PAGE {number} =====\n")
        parts.append(page.extract_text() or "(no extractable text on this page)")
        try:
            for index, image in enumerate(page.images):
                name = f"{path.stem}-p{number:02d}-img{index + 1}{Path(image.name).suffix or '.bin'}"
                target = images_dir / name
                target.write_bytes(image.data)
                saved.append(name)
                parts.append(f"\n[embedded image saved: IMAGES/{name}]")
        except Exception as error:
            parts.append(f"\n[images on this page could not be extracted: {error}]")
    return "".join(parts), saved


def extract_docx(path: Path) -> str:
    """Paragraph text out of the document's XML, with the standard library only."""
    import re

    with zipfile.ZipFile(path) as archive:
        xml = archive.read("word/document.xml").decode("utf-8", errors="replace")
    paragraphs = re.findall(r"<w:p[ >].*?</w:p>", xml, flags=re.S)
    lines = []
    for paragraph in paragraphs:
        text = "".join(re.findall(r"<w:t[^>]*>(.*?)</w:t>", paragraph, flags=re.S))
        lines.append(text)
    return "\n".join(lines)


def read_text(path: Path) -> str:
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "utf-8", "cp950", "big5", "gbk", "latin-1"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def list_archive(path: Path) -> str:
    with zipfile.ZipFile(path) as archive:
        rows = [f"  {info.file_size:>10}  {info.filename}" for info in archive.infolist()]
    return f"## {path}\n\n```\n" + "\n".join(rows) + "\n```\n"


# ------------------------------------------------------------------- audit


def audit(root: Path, out: Path) -> dict[str, int]:
    """Walk `root` read-only; write the six outputs under `out`."""
    if not root.is_dir():
        raise FileNotFoundError(f"{root} is not a folder")
    extract_dir, images_dir = out / "EXTRACT", out / "IMAGES"
    extract_dir.mkdir(parents=True, exist_ok=True)
    images_dir.mkdir(parents=True, exist_ok=True)

    entries: list[Entry] = []
    archives: list[str] = []
    grep_hits: list[str] = []

    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        relative = path.relative_to(root).as_posix()
        kind = kind_of(path)
        extracted = "n/a"
        text: str | None = None
        try:
            if kind == "pdf":
                text, _ = extract_pdf(path, images_dir)
                extracted = "yes"
            elif kind == "docx":
                text = extract_docx(path)
                extracted = "yes"
            elif kind == "text":
                text = read_text(path)
                extracted = "yes"
            elif kind == "archive":
                archives.append(list_archive(path))
                extracted = "listed only (not extracted to disk)"
            elif kind == "archive-unopened":
                extracted = "no: 7z/rar not opened here — NOT VERIFIED"
            elif kind == "image":
                extracted = "no: image — look at it directly"
            elif kind == "binary":
                extracted = "no: binary"
            else:
                extracted = "no: unknown type"
        except Exception as error:
            extracted = f"no: {error}"

        if text is not None:
            target = extract_dir / (relative + ".txt")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
            for number, line in enumerate(text.splitlines(), 1):
                for word in KEYWORDS:
                    if word in line:
                        grep_hits.append(f"| `{word}` | `{relative}` | {number} | `{line.strip()[:120]}` |")
                        break

        entries.append(
            Entry(
                path, relative, path.stat().st_size, sha256_of(path), kind, category_of(relative), extracted
            )
        )

    # INVENTORY.md
    rows = [
        "| # | Path | Size | Type | Category (guess) | sha256 | Extracted |",
        "|---|---|---|---|---|---|---|",
    ]
    for index, entry in enumerate(entries, 1):
        rows.append(
            f"| {index} | `{entry.relative}` | {entry.size:,} | {entry.kind} | {entry.category_guess} "
            f"| `{entry.sha256[:16]}…` | {entry.extracted} |"
        )
    (out / "INVENTORY.md").write_text(
        f"# OFFICIAL_FILE_INVENTORY\n\nRoot: `{root}`\n\n{len(entries)} files.\n\n" + "\n".join(rows) + "\n",
        encoding="utf-8",
    )
    (out / "ARCHIVES.md").write_text(
        "# Archive listings\n\n" + ("\n".join(archives) or "(none)\n"), encoding="utf-8"
    )
    (out / "GREP.md").write_text(
        "# Keyword hits (first keyword per line)\n\n| Keyword | File | Line | Text |\n|---|---|---|---|\n"
        + "\n".join(grep_hits)
        + "\n",
        encoding="utf-8",
    )

    counts = Counter(entry.kind for entry in entries)
    by_category = Counter(entry.category_guess.split(" ")[0] for entry in entries)
    summary = {
        "files": len(entries),
        "pdf": counts["pdf"],
        "docx": counts["docx"],
        "python": sum(1 for e in entries if e.path.suffix.lower() == ".py"),
        "archives": counts["archive"] + counts["archive-unopened"],
        "images_in_pdfs": len(list(images_dir.iterdir())),
        "not_extracted": sum(1 for e in entries if e.extracted.startswith("no:")),
    }
    lines = ["# SUMMARY (mechanical counts, no interpretation)", ""]
    lines += [f"- {key}: {value}" for key, value in summary.items()]
    lines += ["", "## Category guesses by filename (a guess; the reader decides)", ""]
    lines += [f"- {name}: {count}" for name, count in sorted(by_category.items())]
    lines += ["", "## Files not extracted", ""]
    lines += [f"- `{e.relative}` — {e.extracted}" for e in entries if e.extracted.startswith("no:")] or [
        "- (none)"
    ]
    (out / "SUMMARY.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Read-only extraction of the organiser's folder for auditing."
    )
    parser.add_argument("root", type=Path, help="the organiser's folder, e.g. the one on the Desktop")
    parser.add_argument(
        "--out", type=Path, default=Path("official_audit"), help="where to write (git-ignored)"
    )
    args = parser.parse_args(argv)
    try:
        summary = audit(args.root.expanduser(), args.out)
    except FileNotFoundError as missing:
        print(f"!! {missing}", file=sys.stderr)
        return 2
    print(f"wrote {args.out.resolve()}")
    for key, value in summary.items():
        print(f"  {key}: {value}")
    if summary["not_extracted"]:
        print("  some files were not extracted — SUMMARY.md lists them and why")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
