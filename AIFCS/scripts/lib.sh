# Shared helpers for the AIFCS scripts.
# Sourced, never executed directly.
#
# Handles the two ways Python differs across platforms:
#   * the interpreter is `python3` on macOS/Linux but usually `python` on Windows
#   * a virtualenv puts binaries in `bin/` on macOS/Linux and `Scripts/` on Windows
# Windows users run these through Git Bash, so the shell itself is the same.

MIN_PYTHON="3.11"

# Echo the name of a system Python that meets the minimum version, or return 1.
find_python() {
  local candidate
  for candidate in python3 python py; do
    command -v "$candidate" >/dev/null 2>&1 || continue
    if "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' >/dev/null 2>&1; then
      echo "$candidate"
      return 0
    fi
  done
  return 1
}

# Echo the path to the virtualenv's Python, or return 1 if the venv is missing.
venv_python() {
  local root="${1:-$ROOT}"
  if [ -x "$root/.venv/bin/python" ]; then
    echo "$root/.venv/bin/python"
  elif [ -x "$root/.venv/Scripts/python.exe" ]; then
    echo "$root/.venv/Scripts/python.exe"
  else
    return 1
  fi
}

# Echo the virtualenv's binary directory (bin or Scripts), or return 1.
venv_bin() {
  local root="${1:-$ROOT}"
  if [ -d "$root/.venv/bin" ]; then
    echo "$root/.venv/bin"
  elif [ -d "$root/.venv/Scripts" ]; then
    echo "$root/.venv/Scripts"
  else
    return 1
  fi
}
