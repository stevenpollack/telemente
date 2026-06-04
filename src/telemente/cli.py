"""Command-line entry point for telemente."""

from __future__ import annotations

import argparse

from telemente import __version__


def build_parser() -> argparse.ArgumentParser:
    """Construct the top-level argument parser."""
    parser = argparse.ArgumentParser(
        prog="telemente",
        description="A terminal-based chat client for the Matrix protocol.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    """Parse arguments and launch the TUI."""
    parser = build_parser()
    parser.parse_args(argv)

    # Imported lazily so that ``telemente --version`` stays fast and does not
    # require the full Textual/matrix-nio stack to be importable.
    from telemente.tui.app import TelementeApp

    TelementeApp().run()


if __name__ == "__main__":
    main()
