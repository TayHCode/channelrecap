"""Run with the system Python before creating a virtual environment."""
import importlib.util
import sys


def main():
    if sys.version_info < (3, 11):
        print("channelrecap needs Python 3.11 or newer; you have " + sys.version.split()[0] +
              "; install uv: curl -LsSf https://astral.sh/uv/install.sh | sh\n"
              "uv installs in ~/.local/bin by default; use UV_INSTALL_DIR to choose another folder.\n"
              "Then run: uv venv --seed --python 3.12 .venv\n"
              "On Debian/Ubuntu, run sudo apt install python3-venv (or the matching versioned package) first if using python3 -m venv.\n"
              "If that command failed and left a partial .venv, clear only that failed environment:\n"
              "uv venv --clear --seed --python 3.12 .venv", file=sys.stderr)
        return 3
    if importlib.util.find_spec("ensurepip") is None:
        print("ensurepip is missing: on Debian/Ubuntu run sudo apt install python3-venv (or the matching versioned package) first, "
              "or use uv venv --seed --python 3.12 .venv. "
              "A failed python3 -m venv can leave a partial .venv; clear only that failed environment "
              "with uv venv --clear --seed --python 3.12 .venv.", file=sys.stderr)
        return 3
    print("Python " + sys.version.split()[0] + ": ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
