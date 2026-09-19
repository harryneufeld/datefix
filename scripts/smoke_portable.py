"""Verify the built Windows launchers using disposable fixtures only."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "dist" / "DateFix" / "datefix-cli.exe"
GUI = CLI.with_name("DateFix.exe")
ENV = {key: value for key, value in os.environ.items() if key not in {"PYTHONPATH", "DATEFIX_EXIFTOOL"}}


def run(*args):
    command = subprocess.run([str(CLI), *map(str, args)], capture_output=True,
                             encoding="utf-8", env=ENV, timeout=30)
    if command.returncode:
        raise RuntimeError(command.stdout + command.stderr)
    return json.loads(command.stdout)


def main():
    doctor = run("doctor")
    engine = Path(doctor["exiftool"])
    assert engine.is_relative_to(CLI.parent), doctor
    with tempfile.TemporaryDirectory(prefix="datefix-portable-", dir=ROOT / "artifacts") as temporary:
        folder = Path(temporary)
        fixture = folder / "test.avi"
        fixture.write_bytes(b"DateFix disposable filesystem fixture")
        before = fixture.stat()
        original = fixture.read_bytes()
        preview = run("shift", fixture, "--hours", "6", "--json")
        assert preview["preview"] and not preview["files"][0]["errors"]
        assert fixture.stat().st_mtime_ns == before.st_mtime_ns
        result = run("shift", fixture, "--hours", "6", "--fields", "modified,created",
                     "--apply", "--journal-dir", folder / "history", "--json")
        assert result["files"][0]["status"] == "applied", result
        assert fixture.read_bytes() == original
        assert fixture.stat().st_mtime_ns == before.st_mtime_ns + 21600 * 1_000_000_000
        restored = run("undo", result["journal_path"], "--json")
        assert restored["files"][0]["status"] == "undone", restored
        assert fixture.stat().st_mtime_ns == before.st_mtime_ns
        assert fixture.stat().st_birthtime_ns == before.st_birthtime_ns

        from PIL import Image
        photo = folder / "portable.jpg"
        Image.new("RGB", (24, 24), "teal").save(photo)
        subprocess.run([str(engine), "-config", "", "-overwrite_original",
                        "-EXIF:DateTimeOriginal=2024:04:05 05:27:50", str(photo)],
                       check=True, capture_output=True)
        original_hash = hashlib.sha256(photo.read_bytes()).hexdigest()
        result = run("shift", photo, "--years", "2", "--days", "16", "--hours", "6",
                     "--fields", "captured", "--apply", "--journal-dir", folder / "history", "--json")
        assert result["files"][0]["status"] == "applied", result
        assert hashlib.sha256(photo.read_bytes()).hexdigest() != original_hash
        assert run("undo", result["journal_path"], "--json")["files"][0]["status"] == "undone"
        assert hashlib.sha256(photo.read_bytes()).hexdigest() == original_hash

    process = subprocess.Popen([str(GUI)], env={**ENV, "QT_QPA_PLATFORM": "offscreen"},
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    try:
        time.sleep(3)
        assert process.poll() is None, "Portable GUI exited at startup: " + str(process.communicate())
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=10)
    report = {"portable_cli": "passed", "filesystem_apply_undo": "passed",
              "bundled_metadata_apply_undo": "passed", "portable_gui_offscreen_startup": "passed",
              "exiftool": str(engine)}
    (ROOT / "artifacts" / "portable-verification.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
