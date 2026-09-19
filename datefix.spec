# Build both launchers with one shared Python/Qt runtime.
from importlib.metadata import distribution
from pathlib import Path

root = Path(SPECPATH)
data = [(str(root / name), ".") for name in ("README.md", "LICENSE", "THIRD_PARTY_NOTICES.md")]
if (root / "licenses").is_dir():
    data.append((str(root / "licenses"), "licenses"))
vendor = root / "tools" / "exiftool"
if (vendor / "bin" / "exiftool.exe").is_file():
    data.append((str(vendor / "bin"), "tools/exiftool"))
    for name in ("LICENSE", "README.md", "vendor-manifest.json", "patches"):
        if (vendor / name).exists():
            data.append((str(vendor / name), "tools/exiftool" if name != "patches" else "tools/exiftool/patches"))
for dependency in ("PySide6-Essentials", "shiboken6"):
    dist = distribution(dependency)
    for item in dist.files or []:
        if "/licenses/" in str(item).replace("\\", "/"):
            data.append((str(dist.locate_file(item)), "licenses/" + dependency))

a = Analysis(
    [str(root / "launch_gui.py"), str(root / "launch_cli.py")],
    pathex=[str(root / "src")], binaries=[], datas=data,
    hiddenimports=[], hookspath=[], hooksconfig={}, runtime_hooks=[],
    excludes=["pytest", "PIL", "imageio_ffmpeg", "tkinter"], noarchive=False,
)
pyz = PYZ(a.pure)
gui = EXE(pyz, [entry for entry in a.scripts if entry[0] != "launch_cli"], [],
          exclude_binaries=True, name="DateFix", debug=False, strip=False, upx=False, console=False)
cli = EXE(pyz, [entry for entry in a.scripts if entry[0] != "launch_gui"], [],
          exclude_binaries=True, name="datefix-cli", debug=False, strip=False, upx=False, console=True)
coll = COLLECT(gui, cli, a.binaries, a.datas, strip=False, upx=False, name="DateFix")
