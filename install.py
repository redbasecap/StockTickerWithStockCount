#!/usr/bin/env python3
"""Install dependencies for the Ticker With Price app."""

from pathlib import Path
import subprocess
import sys


def main() -> None:
    repo_root = Path(__file__).resolve().parent
    requirements = repo_root / "requirements.txt"
    if not requirements.exists():
        raise FileNotFoundError("requirements.txt is missing; cannot continue.")

    print("Installing dependencies...")
    subprocess.check_call(
        [sys.executable, "-m", "pip", "install", "-r", str(requirements)]
    )
    print("All dependencies installed. Launch the app with `python ticker.py`.")


if __name__ == "__main__":
    main()
