"""Bundle a console executable for the OS running this script."""

import os
from pathlib import Path


ROOT = Path(__file__).resolve().parent


def main() -> int:
    try:
        from PyInstaller.__main__ import run
    except ImportError:
        print('Install build dependencies first: python -m pip install ".[bundle]"')
        return 1

    run([
        "--onefile",
        "--console",
        "--name", "gitlet",
        "--clean",
        "--noconfirm",
        "--paths", str(ROOT),
        "--distpath", str(ROOT / "dist"),
        "--workpath", str(ROOT / "build" / "pyinstaller"),
        "--specpath", str(ROOT / "build"),
        str(ROOT / "gitlet_cli.py"),
    ])
    executable = ROOT / "dist" / ("gitlet.exe" if os.name == "nt" else "gitlet")
    print(f"Built: {executable}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
