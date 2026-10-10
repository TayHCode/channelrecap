"""Local transcripts and notes for creators."""

import sys

if sys.version_info < (3, 11):
    print("channelrecap needs Python 3.11 or newer; you have " + sys.version.split()[0] +
          "; install uv: curl -LsSf https://astral.sh/uv/install.sh | sh\n"
          "uv installs into ~/.local/bin by default; set UV_INSTALL_DIR to choose another folder.\n"
          "Then run: uv venv --seed --python 3.12 .venv\n"
          "On Debian/Ubuntu run sudo apt install python3-venv (or the matching versioned package) first, or use uv.\n"
          "If python3 -m venv failed and left a partial .venv, clear only that failed environment:\n"
          "uv venv --clear --seed --python 3.12 .venv", file=sys.stderr)
    raise SystemExit(3)

NAME = "channelrecap"
__version__ = "0.1.0"
