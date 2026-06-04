"""Command-line entry point for telemente."""

from __future__ import annotations

import argparse
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from telemente import __version__


def _configure_logging(level: str, log_file: Path) -> None:
    """Configure root logger with a rotating file handler.

    Parameters
    ----------
    level:
        Log level string (e.g. ``"DEBUG"``, ``"INFO"``).
    log_file:
        Destination log file path. Parent directories are created if needed.
    """
    log_file.parent.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(log_file, maxBytes=5_000_000, backupCount=3)
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s %(levelname)-8s %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    root.addHandler(handler)

    # Quiet noisy third-party loggers unless the user explicitly asks for DEBUG.
    if level.upper() != "DEBUG":
        for noisy in ("nio", "peewee", "h11", "h2", "httpcore"):
            logging.getLogger(noisy).setLevel(logging.WARNING)
    else:
        # Even at DEBUG, keep peewee and nio.responses at INFO to avoid
        # multi-MB log files from schema validation and SQL queries.
        logging.getLogger("peewee").setLevel(logging.INFO)
        logging.getLogger("nio.responses").setLevel(logging.INFO)


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
    parser.add_argument(
        "--log-level",
        default="INFO",
        metavar="LEVEL",
        help="Logging level (DEBUG, INFO, WARNING, ERROR). Default: INFO.",
    )
    parser.add_argument(
        "--log-file",
        default=None,
        metavar="PATH",
        type=Path,
        help=(
            "Path to the log file. "
            "Default: <data-dir>/telemente.log (usually ~/.local/share/telemente/telemente.log)."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    """Parse arguments and launch the TUI."""
    parser = build_parser()
    args = parser.parse_args(argv)

    # Resolve the default log file path using the XDG data directory.
    from telemente.config import Paths

    log_file: Path = args.log_file or (Paths.default().data_dir / "telemente.log")
    _configure_logging(args.log_level, log_file)

    # Imported lazily so that ``telemente --version`` stays fast and does not
    # require the full Textual/matrix-nio stack to be importable.
    from telemente.tui.app import TelementeApp

    TelementeApp().run()


if __name__ == "__main__":
    main()
