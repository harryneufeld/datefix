"""Entry point for a windowed PyInstaller build."""
import json
from pathlib import Path
import sys

if __name__ == "__main__":
    report = Path(sys.argv[2]) if len(sys.argv) == 3 and sys.argv[1] == "--startup-check-report" else None
    try:
        from datefix.gui import main
        result = main(startup_report=report)
    except Exception as exc:
        if report is None:
            raise
        report.write_text(json.dumps({"ready": False, "error": str(exc)}), encoding="utf-8")
        result = 1
    raise SystemExit(result)
