$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$projectPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $projectPython)) {
    throw 'Create .venv and install requirements-dev.txt first. See README.md.'
}

$projectVersionMatch = [regex]::Match((Get-Content -LiteralPath (Join-Path $PSScriptRoot 'pyproject.toml') -Raw), '^version\s*=\s*"([^"]+)"', [System.Text.RegularExpressions.RegexOptions]::Multiline)
if (-not $projectVersionMatch.Success) { throw 'Project version not found in pyproject.toml.' }
$projectVersion = $projectVersionMatch.Groups[1].Value
$env:DATEFIX_BUILD_VERSION = $projectVersion

& $projectPython -m PyInstaller --clean --noconfirm datefix.spec
if ($LASTEXITCODE -ne 0) { throw 'Build failed.' }

Copy-Item -LiteralPath README.md,LICENSE,THIRD_PARTY_NOTICES.md -Destination dist\DateFix
Write-Output "Ready: dist\DateFix\DateFix.exe (version $projectVersion; keep the whole DateFix folder together)."
