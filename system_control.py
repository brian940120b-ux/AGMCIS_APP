from pathlib import Path

MODE_FILE = "system_mode.txt"

def get_mode():
    p = Path(MODE_FILE)

    if not p.exists():
        p.write_text("RUNNING")

    return p.read_text().strip()

def pause_system():
    Path(MODE_FILE).write_text("PAUSED")
    return True

def resume_system():
    Path(MODE_FILE).write_text("RUNNING")
    return True
