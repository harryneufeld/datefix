"""Command line adapter. All file/date operations belong to the shared core."""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict
from datetime import datetime
import json
from pathlib import Path
import sys

from .core import Offset, Request, apply, default_journal_dir, discover, preview, undo
from .metadata import find_exiftool


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="datefix", description="Preview, adjust and undo file and capture dates.",
        epilog="Date changes are previews by default. Add --apply to commit and record undo history.",
    )
    parser.add_argument("--version", action="version", version="DateFix 0.1.0")
    commands = parser.add_subparsers(dest="command")
    commands.add_parser("gui", help="Open the desktop interface")
    commands.add_parser("doctor", help="Show platform and metadata engine availability")
    history = commands.add_parser("history", help="List saved undo journals")
    history.add_argument("--journal-dir", type=Path)
    history.add_argument("--json", action="store_true")
    for verb in ("shift", "set"):
        action = commands.add_parser(verb, help="Add a calendar offset" if verb == "shift" else "Set an exact date")
        action.add_argument("paths", nargs="+", type=Path, help="Files and/or folders")
        action.add_argument("--recursive", "-r", action="store_true", help="Include nested folders")
        action.add_argument("--fields", default="modified", help="Comma-separated: modified,created,captured (default: modified)")
        action.add_argument("--timezone", choices=("local", "UTC"), default="local", help="Filesystem date timezone (default: local)")
        action.add_argument("--exiftool", help="Path to ExifTool for capture dates")
        action.add_argument("--apply", action="store_true", help="Apply changes and save undo history; otherwise preview only")
        action.add_argument("--journal-dir", type=Path, help="Override undo history folder")
        action.add_argument("--json", action="store_true", help="Machine-readable results")
        if verb == "shift":
            for unit in ("years", "days", "hours", "minutes", "seconds"):
                action.add_argument(f"--{unit}", type=int, default=0, help=f"Signed number of {unit}")
        else:
            action.add_argument("--at", required=True, help="ISO date/time, e.g. 2026-04-21T11:27:50")
    restore = commands.add_parser("undo", help="Restore dates from a saved journal")
    restore.add_argument("journal", type=Path)
    restore.add_argument("--json", action="store_true")
    return parser


def _print_json(value: object) -> None:
    print(json.dumps(value, indent=2, ensure_ascii=False))


def _print_plan(plan) -> None:
    print("PREVIEW — no changes have been made")
    for item in plan.files:
        print(f"\n{item.path}")
        for change in item.changes:
            print(f"  {change.field}: {change.before}  ->  {change.after}")
        for warning in item.warnings:
            print(f"  Note: {warning}")
        for error in item.errors:
            print(f"  Cannot apply: {error}")
        if not item.changes and not item.errors:
            print("  No changes needed")
    ready = sum(bool(item.changes) and not item.errors for item in plan.files)
    print(f"\n{len(plan.files)} files reviewed; {ready} ready. Add --apply to save changes.")


def _print_result(result, machine: bool) -> int:
    if machine:
        _print_json(asdict(result))
    else:
        for item in result.files:
            print(f"{item.status.upper()}: {item.path}")
            if item.message:
                print(f"  {item.message}")
        counts = Counter(item.status for item in result.files)
        print(", ".join(f"{number} {status}" for status, number in sorted(counts.items())) or "No files changed")
        if result.journal_path:
            print(f"Undo journal: {result.journal_path}")
    return 1 if any(item.status == "error" for item in result.files) else 0


def main(argv: list[str] | None = None) -> int:
    # Avoid failures printing non-ASCII filenames in legacy Windows consoles.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    parser = _parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 0
    try:
        if args.command == "gui":
            try:
                from .gui import main as gui_main
            except ImportError as exc:
                raise RuntimeError("Desktop interface needs the desktop extra: pip install '.[desktop]'") from exc
            return gui_main()
        if args.command == "doctor":
            import os
            _print_json({
                "version": "0.1.0", "python": sys.version.split()[0], "platform": sys.platform,
                "file_modified": True, "file_created": os.name == "nt", "exiftool": find_exiftool(),
                "history": str(default_journal_dir()),
            })
            return 0
        if args.command == "history":
            folder = args.journal_dir or default_journal_dir()
            records = sorted(folder.glob("*/journal.json"), key=lambda path: path.stat().st_mtime_ns, reverse=True)
            if args.json:
                _print_json([str(path) for path in records])
            else:
                print("\n".join(str(path) for path in records) or f"No history yet in {folder}")
            return 0
        if args.command == "undo":
            return _print_result(undo(args.journal), args.json)
        fields = tuple(value.strip() for value in args.fields.split(","))
        if args.command == "shift":
            offset = Offset(**{unit: getattr(args, unit) for unit in ("years", "days", "hours", "minutes", "seconds")})
            request = Request(offset=offset, targets=fields, timezone=args.timezone)
        else:
            request = Request(fixed=datetime.fromisoformat(args.at.replace("Z", "+00:00")), targets=fields, timezone=args.timezone)
        files = discover(args.paths, recursive=args.recursive)
        if not files:
            raise ValueError("No files found in the selected paths.")
        plan = preview(files, request, exiftool=args.exiftool)
        if args.apply:
            return _print_result(apply(plan, journal_dir=args.journal_dir), args.json)
        if args.json:
            # Export human-reviewable preview only; imported plans are not executable CLI input.
            _print_json({"preview": True, "files": [asdict(item) for item in plan.files]})
        else:
            _print_plan(plan)
        return 1 if any(item.errors for item in plan.files) else 0
    except (OSError, ValueError, RuntimeError) as exc:
        if getattr(args, "json", False):
            _print_json({"error": str(exc)})
        else:
            print(f"DateFix: {exc}", file=sys.stderr)
        return 1
