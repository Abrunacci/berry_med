# Diagnostico del error "certificate has expired" de berry-monitor.exe (hasta 1.0.6)
#
# Esas versiones validan TLS con OpenSSL, usando como anclas de confianza una
# copia de los almacenes ROOT y CA de Windows mas el archivo de
# SSL_CERT_FILE_PATH. Si ahi hay un certificado vencido con el sujeto de un
# emisor de la cadena -por ejemplo el cruce viejo "ISRG Root X2" firmado por
# "ISRG Root X1", vencido el 2025-09-15- y no esta el "ISRG Root X2"
# autofirmado, falla con "certificate has expired" aunque el navegador e
# Invoke-WebRequest conecten bien.
#
# Todo lo que informa sale del propio equipo (nombre, exe instalado, fechas de
# alta de los certificados, de los archivos y de las actualizaciones), asi no
# depende de lo que recuerde quien lo usa.
#
# Solo lee: no modifica nada. Correrlo con el MISMO usuario que ejecuta berry-monitor:
# clic derecho > "Ejecutar con PowerShell", o
#     powershell -NoProfile -ExecutionPolicy Bypass -File diagnostico_ssl.ps1
# Deja el reporte en el Escritorio (diagnostico_ssl_<EQUIPO>.txt) y lo abre en
# el Bloc de notas: la ventana de PowerShell se cierra sola al terminar.
$reporte = Join-Path ([Environment]::GetFolderPath('Desktop')) "diagnostico_ssl_$env:COMPUTERNAME.txt"
Start-Transcript -Path $reporte -Force | Out-Null
$ahora = Get-Date
$sujetos = 'ISRG Root X1', 'ISRG Root X2', 'Root YE', 'YE1', 'YE2'
$buildsConocidos = @{
    '217EE7B97A3629C2F7ED9F0C3E664555B2951B6020C55A80C3A6EE60F8240BE0' = '1.0.5 / 1.0.6 (mismo build, 2026-05-26)'
}

Add-Type -Namespace Diag -Name Reg -MemberDefinition @'
[DllImport("advapi32.dll")]
public static extern int RegQueryInfoKey(Microsoft.Win32.SafeHandles.SafeRegistryHandle hKey,
    IntPtr lpClass, IntPtr lpcchClass, IntPtr lpReserved, IntPtr lpcSubKeys,
    IntPtr lpcbMaxSubKeyLen, IntPtr lpcbMaxClassLen, IntPtr lpcValues,
    IntPtr lpcbMaxValueNameLen, IntPtr lpcbMaxValueLen, IntPtr lpcbSecurityDescriptor,
    out long lpftLastWriteTime);
'@

# Cuando se escribio el certificado en el registro: su alta en el almacen (o la
# ultima vez que Windows le cambio alguna propiedad). Busca en todos los
# lugares de donde Windows arma el almacen logico.
function Get-FechaRegistro($alm, $huella) {
    $raices = 'HKCU:\Software\Microsoft\SystemCertificates',
              'HKCU:\Software\Policies\Microsoft\SystemCertificates',
              'HKLM:\SOFTWARE\Microsoft\SystemCertificates',
              'HKLM:\SOFTWARE\Policies\Microsoft\SystemCertificates',
              'HKLM:\SOFTWARE\Microsoft\EnterpriseCertificates'
    $z = [IntPtr]::Zero
    foreach ($r in $raices) {
        $k = Get-Item "$r\$alm\Certificates\$huella" -ErrorAction SilentlyContinue
        if (-not $k) { continue }
        $ft = 0L
        if ([Diag.Reg]::RegQueryInfoKey($k.Handle, $z, $z, $z, $z, $z, $z, $z, $z, $z, $z, [ref]$ft) -eq 0) {
            return [DateTime]::FromFileTime($ft).ToString('yyyy-MM-dd HH:mm')
        }
    }
    return '?'
}

Write-Host "== 0. Equipo =="
Write-Host "Equipo: $env:COMPUTERNAME   Usuario: $env:USERNAME   Fecha del sistema: $ahora"
$credFile = Join-Path $env:APPDATA 'BerryMed Monitor\credentials.json'
$cred = $null
if (Test-Path $credFile) {
    $cred = Get-Content $credFile -Raw | ConvertFrom-Json
    Write-Host "TOTEM_ID: $($cred.TOTEM_ID)   API_URL: $($cred.API_URL)"
} else {
    Write-Host "No existe $credFile (este usuario no tiene berry-monitor configurado)"
}
$shell = New-Object -ComObject WScript.Shell
$exes = @(Get-Process -Name 'berry-monitor' -ErrorAction SilentlyContinue | ForEach-Object { $_.Path })
$exes += Get-ChildItem "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Startup",
                      "$env:ProgramData\Microsoft\Windows\Start Menu\Programs\StartUp" -Filter *.lnk -ErrorAction SilentlyContinue |
    ForEach-Object { $shell.CreateShortcut($_.FullName).TargetPath }
$exes += Get-ChildItem $env:USERPROFILE -Filter 'berry-monitor*.exe' -Recurse -Depth 3 -ErrorAction SilentlyContinue |
    ForEach-Object { $_.FullName }
$exes = $exes | Where-Object { $_ -and $_ -match 'berry-monitor' -and (Test-Path $_) } | Sort-Object -Unique
if (-not $exes) { Write-Host "No se encontro berry-monitor.exe (ni corriendo, ni en Inicio, ni en el perfil)." }
foreach ($e in $exes) {
    $h = (Get-FileHash $e -Algorithm SHA256).Hash
    $build = if ($buildsConocidos.ContainsKey($h)) { $buildsConocidos[$h] } else { "build desconocido, SHA-256 $h" }
    Write-Host "exe: $e  (modificado $((Get-Item $e).LastWriteTime.ToString('yyyy-MM-dd HH:mm'))) -> $build"
}

Write-Host "`n== 1. Cadena de Let's Encrypt en los almacenes de Windows =="
Write-Host "(Registrado = cuando se escribio el certificado en el registro)"
$filas = foreach ($ubic in 'LocalMachine', 'CurrentUser') {
    foreach ($alm in 'Root', 'AuthRoot', 'CA') {
        Get-ChildItem "Cert:\$ubic\$alm" -ErrorAction SilentlyContinue |
            Where-Object { $sujetos -contains $_.GetNameInfo('SimpleName', $false) } |
            ForEach-Object {
                [pscustomobject]@{
                    Almacen    = "$ubic\$alm"
                    Sujeto     = $_.GetNameInfo('SimpleName', $false)
                    Emisor     = $_.GetNameInfo('SimpleName', $true)
                    Vence      = $_.NotAfter.ToString('yyyy-MM-dd')
                    Estado     = if ($_.NotAfter -lt $ahora) { 'VENCIDO' } else { 'ok' }
                    Registrado = Get-FechaRegistro $alm $_.Thumbprint
                    Huella     = $_.Thumbprint
                }
            }
    }
}
$filas | Format-Table -AutoSize

Write-Host "== 2. Archivo configurado en SSL_CERT_FILE_PATH =="
if (-not $cred) {
    Write-Host "Sin credentials.json: nada que revisar."
} else {
    Write-Host "credentials.json modificado: $((Get-Item $credFile).LastWriteTime)"
    $pem = $cred.SSL_CERT_FILE_PATH
    Write-Host "SSL_CERT_FILE_PATH = $pem"
    if (-not $pem -or -not (Test-Path $pem)) {
        Write-Host "El archivo no existe (OpenSSL lo ignora en silencio)."
    } else {
        Write-Host "Archivo modificado: $((Get-Item $pem).LastWriteTime)"
        $certs = New-Object System.Collections.Generic.List[object]
        $bloques = [regex]::Matches((Get-Content $pem -Raw),
            '-----BEGIN CERTIFICATE-----[\s\S]+?-----END CERTIFICATE-----')
        foreach ($b in $bloques) {
            $b64 = ($b.Value -replace '-----(BEGIN|END) CERTIFICATE-----', '') -replace '\s', ''
            $certs.Add([System.Security.Cryptography.X509Certificates.X509Certificate2]::new(
                [Convert]::FromBase64String($b64)))
        }
        if ($certs.Count -eq 0) {
            try {
                $certs.Add([System.Security.Cryptography.X509Certificates.X509Certificate2]::new($pem))
                Write-Host "Formato DER (binario): OpenSSL solo lee PEM en SSL_CERT_FILE, este archivo no aporta nada."
            } catch {
                Write-Host "No se pudo leer ningun certificado del archivo."
            }
        }
        Write-Host "Certificados en el archivo: $($certs.Count) (se listan los vencidos y los de Let's Encrypt)"
        $filas = foreach ($c in $certs) {
            $cn = $c.GetNameInfo('SimpleName', $false)
            if ($c.NotAfter -lt $ahora -or $sujetos -contains $cn) {
                [pscustomobject]@{
                    Sujeto = $cn
                    Emisor = $c.GetNameInfo('SimpleName', $true)
                    Vence  = $c.NotAfter.ToString('yyyy-MM-dd')
                    Estado = if ($c.NotAfter -lt $ahora) { 'VENCIDO' } else { 'ok' }
                }
            }
        }
        $filas | Format-Table -AutoSize
    }
}

Write-Host "== 3. Actualizaciones de Windows de los ultimos 7 dias =="
try {
    $buscador = (New-Object -ComObject Microsoft.Update.Session).CreateUpdateSearcher()
    $buscador.QueryHistory(0, [Math]::Min($buscador.GetTotalHistoryCount(), 100)) |
        Where-Object { $_.Date -gt $ahora.AddDays(-7) -and
                       $_.Title -notmatch 'Security Intelligence Update|^9N' } |
        Sort-Object Date |
        ForEach-Object { '{0:yyyy-MM-dd HH:mm}  {1}' -f $_.Date.ToLocalTime(), $_.Title }
} catch {
    Get-HotFix | Where-Object { $_.InstalledOn -and $_.InstalledOn -gt $ahora.AddDays(-7) } |
        Sort-Object InstalledOn | Format-Table HotFixID, Description, InstalledOn -AutoSize
}

Stop-Transcript | Out-Null
Start-Process notepad.exe $reporte
