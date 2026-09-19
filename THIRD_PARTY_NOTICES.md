# Bundled components

DateFix's Python source is distributed under the MIT license in `LICENSE`.
Third-party components retain their own licenses.

- Python: Python Software Foundation license. https://www.python.org/psf/license/
- PySide6 / Qt for Python: version 6.11.2 in this Windows build. License texts
  are included in `_internal/licenses`; the dynamically loaded Qt libraries
  remain separate files and may be replaced with compatible builds.
  Source: https://download.qt.io/official_releases/QtForPython/pyside6/
  Qt sources: https://download.qt.io/official_releases/qt/
- ExifTool: Phil Harvey, version 13.59, under the same terms as Perl
  (Artistic License or GPL). https://exiftool.org/
  Sources: https://github.com/exiftool/exiftool
- Windows ExifTool distribution: PhotoStructure `exiftool-vendored.exe` 13.59.2.
  The distribution includes the portable Perl launcher/runtime and a downstream
  stdin EOF patch. Vendor manifest, patch and licenses are included in
  `_internal/tools/exiftool`; Perl notices are in its `exiftool_files` directory.
  Source: https://github.com/photostructure/exiftool-vendored.exe
- PyInstaller: bootloader distribution exception applies.
  https://pyinstaller.org/en/stable/license.html

The build uses an unmodified installed PySide6 wheel and the vendored ExifTool
package. All application source and build instructions are provided alongside
this build in the Project directory. DateFix does not upload media files.
