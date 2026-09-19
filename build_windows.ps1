$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$projectPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $projectPython)) {
    throw 'Create .venv and install requirements-dev.txt first. See README.md.'
}
& $projectPython -m PyInstaller --clean --noconfirm datefix.spec
if ($LASTEXITCODE -ne 0) { throw 'Build failed.' }
Copy-Item -LiteralPath README.md,LICENSE,THIRD_PARTY_NOTICES.md -Destination dist\DateFix
Write-Output 'Ready: dist\DateFix\DateFix.exe (keep the whole DateFix folder together).'
