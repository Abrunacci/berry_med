# Build de berry-monitor.exe y berry-configure.exe. Siempre los mismos pasos,
# se lo corra desde donde se lo corra:
#
#     powershell -NoProfile -ExecutionPolicy Bypass -File build.ps1
#
# 1. Busca Python 3.13. Con otra version los exe validan TLS distinto (ver
#    docs/certificado_vencido.md, seccion 2.3); si no esta, corta.
# 2. Arma .venv con las versiones exactas de poetry.lock ("poetry sync").
#    Poetry vive aparte, en .tools\poetry, para que "poetry sync" no lo borre.
# 3. Corre los tests (-SinTests para saltearlos).
# 4. Genera los dos exe con los .spec. Los .spec vuelven a verificar Python y
#    sellan la version adentro del exe (tools/build_meta.py).
# 5. Verifica cada exe (trae Python 3.13; el monitor trae truststore) y deja en
#    dist\build-info.txt la version y el SHA-256 de cada uno.
#
# Lo mismo corre en GitHub Actions (.github/workflows/build.yml).
param([switch]$SinTests)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$versionPoetry = '2.4.1'

function Write-Paso($texto) { Write-Host "`n=== $texto" -ForegroundColor Cyan }

# Corre un programa, muestra su salida y corta si falla. Con la preferencia en
# 'Continue' mientras corre: Windows PowerShell convierte en error cualquier
# linea que el programa escriba en stderr (PyInstaller escribe todo ahi) cuando
# la salida esta redirigida, como en GitHub Actions.
function Invoke-Programa([string]$programa, [string[]]$argumentos) {
    $anterior = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try { & $programa @argumentos 2>&1 | ForEach-Object { Write-Host "$_" } }
    finally { $ErrorActionPreference = $anterior }
    if ($LASTEXITCODE -ne 0) { throw "Fallo (codigo $LASTEXITCODE): $programa $($argumentos -join ' ')" }
}

# Corre un programa y devuelve su salida, sin cortar si falla.
function Get-Salida([string]$programa, [string[]]$argumentos) {
    $anterior = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try { $salida = @(& $programa @argumentos 2>$null) } catch { $salida = @() }
    finally { $ErrorActionPreference = $anterior }
    if ($LASTEXITCODE -ne 0) { return @() }
    $salida
}

Write-Paso '1. Python 3.13'
$python = $null
$probar = "import sys; print('%d.%d' % sys.version_info[:2]); print(sys.executable)"
foreach ($candidato in @(@('py', '-3.13'), @('python3.13'), @('python'), @('python3'))) {
    if (-not (Get-Command $candidato[0] -ErrorAction SilentlyContinue)) { continue }
    $extra = @($candidato | Select-Object -Skip 1)
    $salida = Get-Salida $candidato[0] ($extra + @('-c', $probar))
    if ($salida.Count -ge 2 -and $salida[0] -eq '3.13') { $python = $salida[1]; break }
}
if (-not $python) {
    throw 'No se encontro Python 3.13. Instalarlo (python.org o Microsoft Store) y volver a correr build.ps1.'
}
Write-Host "Python: $python"

Write-Paso '2. Entorno con las versiones exactas de poetry.lock'
$poetryDir = Join-Path $PSScriptRoot '.tools\poetry'
$poetry = Join-Path $poetryDir 'Scripts\poetry.exe'
$versionActual = if (Test-Path $poetry) { (Get-Salida $poetry @('--version')) -join ' ' } else { '' }
if ($versionActual -notmatch [regex]::Escape($versionPoetry)) {
    Write-Host "Instalando Poetry $versionPoetry en .tools\poetry"
    if (Test-Path $poetryDir) { Remove-Item $poetryDir -Recurse -Force }
    Invoke-Programa $python @('-m', 'venv', $poetryDir)
    Invoke-Programa (Join-Path $poetryDir 'Scripts\python.exe') @('-m', 'pip', 'install', '--quiet', "poetry==$versionPoetry")
}
# .venv es el entorno del proyecto. Si existe con otra version de Python, se rehace.
$venvPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (Test-Path $venvPython) {
    $v = (Get-Salida $venvPython @('-c', "import sys; print('%d.%d' % sys.version_info[:2])")) -join ''
    if ($v -ne '3.13') {
        Write-Host ".venv es de Python '$v': se rehace."
        Remove-Item (Join-Path $PSScriptRoot '.venv') -Recurse -Force
    }
}
if (-not (Test-Path $venvPython)) { Invoke-Programa $python @('-m', 'venv', '.venv') }
$env:POETRY_VIRTUALENVS_IN_PROJECT = 'true'
Invoke-Programa $poetry @('sync', '--no-interaction')

if ($SinTests) {
    Write-Paso '3. Tests: salteados (-SinTests)'
} else {
    Write-Paso '3. Tests'
    Invoke-Programa $venvPython @('-m', 'pytest', '-q')
}

Write-Paso '4. Exe'
foreach ($spec in 'berry-monitor.spec', 'berry-configure.spec') {
    Invoke-Programa $venvPython @('-m', 'PyInstaller', $spec, '--clean', '--noconfirm')
}

Write-Paso '5. Verificacion'
$lineas = @("berry_med build $(Get-Date -Format 'yyyy-MM-dd HH:mm')")
foreach ($nombre in 'berry-monitor', 'berry-configure') {
    $exe = Join-Path $PSScriptRoot "dist\$nombre.exe"
    if (-not (Test-Path $exe)) { throw "No se genero $exe" }
    $bytes = [Text.Encoding]::GetEncoding(28591).GetString([IO.File]::ReadAllBytes($exe))
    if (-not $bytes.Contains('python313.dll')) { throw "$nombre.exe no trae Python 3.13" }
    if ($nombre -eq 'berry-monitor' -and -not $bytes.Contains('truststore._windows')) {
        throw 'berry-monitor.exe no trae truststore'
    }
    $version = (Get-Item $exe).VersionInfo.ProductVersion
    $hash = (Get-FileHash $exe -Algorithm SHA256).Hash
    $lineas += "$nombre.exe  version $version  SHA-256 $hash"
}
$lineas | Set-Content (Join-Path $PSScriptRoot 'dist\build-info.txt') -Encoding UTF8
Write-Paso 'Listo'
$lineas | ForEach-Object { Write-Host $_ }
