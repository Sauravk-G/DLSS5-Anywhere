"""
DLSS5-Anywhere Entry Point.
Launches the Qt GUI by default, or runs CLI commands if arguments are supplied.
"""

from pathlib import Path
import sys

# Ensure package root is in sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent))

from dlss5_anywhere.cli import main as cli_main


def main():
    # If arguments are passed, execute CLI command; otherwise launch GUI
    if len(sys.argv) > 1 and sys.argv[1] not in ("--gui", "-g"):
        sys.exit(cli_main())

    # Imported here rather than at the top so the CLI does not need Qt. PySide6 is a
    # 250 MB dependency and a scripted `detect` or `install` has no use for it.
    from dlss5_anywhere.qtgui.app import launch_gui
    launch_gui()


if __name__ == "__main__":
    main()
