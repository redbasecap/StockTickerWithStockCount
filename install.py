#!/usr/bin/env python3
"""Install dependencies for the Ticker With Price app."""

import subprocess
import sys
from pathlib import Path


def main() -> None:
    repo_root = Path(__file__).resolve().parent
    requirements = repo_root / "requirements.txt"
    if not requirements.exists():
        raise FileNotFoundError("requirements.txt is missing; cannot continue.")

    venv_dir = repo_root / ".venv"
    if not venv_dir.exists():
        print("Creating virtual environment in .venv...")
        subprocess.check_call([sys.executable, "-m", "venv", str(venv_dir)])
    else:
        print("Virtual environment already exists; reusing .venv.")

    env_python = (
        venv_dir / "Scripts" / "python.exe"
        if sys.platform.startswith("win")
        else venv_dir / "bin" / "python"
    )
    if not env_python.exists():
        raise FileNotFoundError(
            f"Expected Python executable at {env_python}, but it was not found."
        )

    print("Installing dependencies into the virtual environment...")
    subprocess.check_call(
        [str(env_python), "-m", "pip", "install", "-r", str(requirements)]
    )
    env_path = repo_root / ".env"
    if not env_path.exists():
        env_path.write_text(
            "# Environment configuration for Ticker With Price\n"
            "# Add overrides as KEY=VALUE pairs. Example:\n"
            "# REFRESH_INTERVAL_SECONDS=60\n"
        )
        print("Created .env template.")

    print("All dependencies installed inside .venv.")
    if sys.platform.startswith("win"):
        print("Launch the app with `.venv\\Scripts\\python.exe ticker.py`.")
    else:
        print("Launch the app with `./.venv/bin/python ticker.py`.")


if __name__ == "__main__":
    main()
