# Build both launchers with one shared Python/Qt runtime.
from importlib.metadata import distribution
from pathlib import Path
import os
import re
import sys

# Do not resolve Qt's Windows ICU/API-set dependencies from unrelated tools
# on a developer's PATH (e.g. Poppler ships an incompatible icuuc.dll).
if sys.platform == "win32":
    windows = Path(os.environ["SystemRoot"])
    os.environ["PATH"] = os.pathsep.join(map(str, [windows / "System32", windows, Path(sys.executable).parent]))

root = Path(SPECPATH)
project_toml = root / "pyproject.toml"
version_text = project_toml.read_text(encoding="utf-8")
version_match = re.search(r'^version\s*=\s*"([^"]+)"', version_text, re.MULTILINE)
APP_VERSION = os.environ.get("DATEFIX_BUILD_VERSION") or (version_match.group(1) if version_match else None)
if not APP_VERSION:
    raise RuntimeError("DateFix project version was not found in pyproject.toml.")

version_parts = APP_VERSION.split(".")
while len(version_parts) < 4:
    version_parts.append("0")
version_file = root / ".datefix-version-info.txt"
version_file.write_text(
    f"""# UTF-8
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=({version_parts[0]}, {version_parts[1]}, {version_parts[2]}, {version_parts[3]}),
    prodvers=({version_parts[0]}, {version_parts[1]}, {version_parts[2]}, {version_parts[3]}),
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)),
  kids=[
    StringFileInfo([
      StringTable(
        '040904B0',
        [
          StringStruct('CompanyName', 'DateFix'),
          StringStruct('FileDescription', 'DateFix'),
          StringStruct('FileVersion', '{APP_VERSION}'),
          StringStruct('InternalName', 'DateFix'),
          StringStruct('LegalCopyright', 'MIT'),
          StringStruct('OriginalFilename', 'DateFix.exe'),
          StringStruct('ProductName', 'DateFix'),
          StringStruct('ProductVersion', '{APP_VERSION}')
        ])
    ]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
""",
    encoding="utf-8",
)

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
          exclude_binaries=True, name="DateFix", debug=False, strip=False, upx=False, console=False,
          version=str(version_file))
cli = EXE(pyz, [entry for entry in a.scripts if entry[0] != "launch_gui"], [],
          exclude_binaries=True, name="datefix-cli", debug=False, strip=False, upx=False, console=True,
          version=str(version_file))
coll = COLLECT(gui, cli, a.binaries, a.datas, strip=False, upx=False, name="DateFix")
if version_file.exists():
    version_file.unlink()
