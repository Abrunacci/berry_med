# Reparacion de errores de certificado de berry-monitor.exe (versiones sin truststore)
#
# Esas versiones validan TLS con OpenSSL. Como anclas de confianza usan una
# copia de los almacenes ROOT y CA de Windows mas el ARCHIVO de
# SSL_CERT_FILE_PATH (credentials.json). Fallan de dos formas, aunque el
# navegador y Invoke-WebRequest conecten bien en la misma PC:
#   - "certificate has expired": en el almacen hay un certificado vencido con el
#     sujeto de un emisor de la cadena. Por ejemplo, el cruce viejo
#     "ISRG Root X2" emitido por "ISRG Root X1", vencido el 2025-09-15.
#   - "unable to get local issuer certificate": ni el almacen ni el archivo
#     tienen la raiz de la cadena del servidor.
#
# SSL_CERT_FILE_PATH tiene que ser la ruta de un ARCHIVO .pem: ninguna version
# la usa como carpeta, y con una carpeta no se carga nada. (Hasta el build del
# 2025-07-17 el exe usaba el bundle de certifi que trae adentro.)
#
# En un solo paso:
#   1. Analiza el equipo y el servidor de API_URL: toma la cadena que manda el
#      servidor, la cruza con el almacen de Windows y con el archivo de
#      SSL_CERT_FILE_PATH (si es una carpeta, revisa los archivos de adentro), y
#      simula si berry-monitor la puede validar.
#   2. Si hay certificados vencidos de esa cadena en el almacen, pide
#      confirmacion, los respalda en el Escritorio, cierra berry-monitor, los
#      borra (si hay copia a nivel maquina pide permiso de administrador) y
#      vuelve a abrir berry-monitor.
#   3. Verifica en el log de berry-monitor que la conexion ande.
#   Si lo que falta es la raiz, dice que archivo configurar.
# Todo queda en el Escritorio en reparacion_ssl_<EQUIPO>.txt, que se abre al final.
#
# Como correrlo, con el usuario que usa berry-monitor: guardarlo en el
# Escritorio y pegar esto en una ventana de Windows PowerShell (asi no lo frena
# la politica de ejecucion y la ventana queda abierta). Ver docs/certificado_vencido.md.
#
#   $f = [Environment]::GetFolderPath('Desktop') + '\reparar_ssl.ps1'
#   Get-Content -LiteralPath $f -Raw | Invoke-Expression
#
# Tambien sirve: clic derecho > "Ejecutar con PowerShell". Para correrlo sin la
# pregunta de confirmacion (por ejemplo, desde una herramienta remota), definir
# antes la variable de entorno REPARAR_SSL_SIN_PREGUNTAR=1.
$escritorio = $null
try { $escritorio = [Environment]::GetFolderPath('Desktop') } catch { }
if (-not $escritorio) { $escritorio = Join-Path $env:USERPROFILE 'Desktop' }
$reporte = Join-Path $escritorio "reparacion_ssl_$env:COMPUTERNAME.txt"
try {
    Start-Transcript -Path $reporte -Force -ErrorAction Stop | Out-Null
} catch {
    $reporte = Join-Path $env:TEMP "reparacion_ssl_$env:COMPUTERNAME.txt"
    Start-Transcript -Path $reporte -Force | Out-Null
}

$ahora = Get-Date
# El cruce viejo de Let's Encrypt del incidente de septiembre de 2026. Se busca
# siempre, aunque el servidor configurado use otra cadena.
$huellaConocida = '151682F5218C0A511C28F4060A73B9CA78CE9A53'
$logBerry = Join-Path $env:APPDATA 'BerryMed Monitor\logs\berry-monitor.log'
$sinPreguntar = $env:REPARAR_SSL_SIN_PREGUNTAR -eq '1'
$buildsConocidos = @{
    '217EE7B97A3629C2F7ED9F0C3E664555B2951B6020C55A80C3A6EE60F8240BE0' = '1.0.5 / 1.0.6 (build 2026-05-26)'
    '25CF67357A27687C5A5971B5809150676983339E26410E4EE34AD4BF55EDAEBE' = 'build 2026-09-04 (commit 284b8b6)'
    'B4AEF803F3D0C37BBFA1D2D9BFC65452116137A2AC9A69C48018A257CBF9CC9D' = 'build 2026-08-21 (commit 9e6add1)'
    'EE12408C136F8F62D308CF97E9FF1753B05CEA4D9A588DC233EB46B162730135' = 'build 2026-08-18 (commit c2a338c)'
    '7E68A695DD596E9E709F21F784EE791D8A7CA077418322797667C807B6D20F12' = 'build de julio 2026 (no esta en git)'
    '5E14E1D3F34C15DD1EB618F6FC842CB8C1DF3C6FFFBD04E9A72A29F1549D83B3' = 'build 2025-08-23 (commit 743e06c)'
    '725F591026176CB3573E232787BDB84E43AF06D83B35D2DB58FBBA612C7E4A51' = 'build 2025-07-18 (commit 0856133)'
    '3E65B301CCEB4BDB80CBA397731063359F06CF760FF4D6D2F461EFB659D9A08D' = 'build 2025-07-17 (commit a0b8d03, revienta al arrancar: get_config sin parentesis)'
    '67C129F583A2D7C955D4058FFB3C08AF5A0B427BA2B6CADA55DD8F620D5DDD1A' = 'build 2025-06-27 (commit 5d3ac19): usa el bundle de certifi propio, no SSL_CERT_FILE_PATH'
    'F6EA4EC1F0255D7F67A84EF4E20CEDE7193AC0BF4D6BA9D4A92442345C8B778D' = 'build 2024-12-04 (commit 67ba624): usa el bundle de certifi propio, no SSL_CERT_FILE_PATH'
}

# Las tablas se pasan a texto con un ancho grande: si no, el transcript las
# corta al ancho de la consola y se pierden columnas.
function Show-Tabla($filas) {
    if ($filas) { Write-Host (($filas | Format-Table -AutoSize | Out-String -Width 400).TrimEnd()) }
}

function Get-Nombre($cert) { $cert.GetNameInfo('SimpleName', $false) }
function Get-NombreEmisor($cert) { $cert.GetNameInfo('SimpleName', $true) }
function Test-Vigente($cert) { $cert.NotBefore -le $ahora -and $cert.NotAfter -ge $ahora }

# Los nombres se comparan en DER, como hace OpenSSL para encontrar al emisor.
function Get-Clave($nombre) { [Convert]::ToBase64String($nombre.RawData) }

function New-Ancla($cert, $fuente) {
    [pscustomobject]@{ Cert = $cert; Clave = Get-Clave $cert.SubjectName; Fuentes = @($fuente) }
}

# Todo lo que Windows tiene en los almacenes de donde Python arma las anclas.
function Get-Almacen {
    $porHuella = [ordered]@{}
    foreach ($alm in 'CurrentUser\Root', 'CurrentUser\AuthRoot', 'CurrentUser\CA',
                     'LocalMachine\Root', 'LocalMachine\AuthRoot', 'LocalMachine\CA') {
        foreach ($c in @(Get-ChildItem "Cert:\$alm" -ErrorAction SilentlyContinue)) {
            if ($porHuella.Contains($c.Thumbprint)) { $porHuella[$c.Thumbprint].Fuentes += $alm }
            else { $porHuella[$c.Thumbprint] = New-Ancla $c $alm }
        }
    }
    @($porHuella.Values)
}

# Certificados de un archivo. OpenSSL solo carga PEM en SSL_CERT_FILE: un DER
# se lee para informarlo, pero no cuenta como ancla.
function Read-ArchivoCert([string]$ruta) {
    $salida = [pscustomobject]@{ Ruta = $ruta; Formato = 'ilegible'; Certs = @() }
    try {
        if ((Get-Item -LiteralPath $ruta).Length -gt 5MB) { $salida.Formato = 'demasiado grande'; return $salida }
        $bloques = [regex]::Matches([IO.File]::ReadAllText($ruta),
            '-----BEGIN CERTIFICATE-----[\s\S]+?-----END CERTIFICATE-----')
        if ($bloques.Count) {
            $salida.Formato = 'PEM'
            $salida.Certs = @(foreach ($b in $bloques) {
                try {
                    $b64 = ($b.Value -replace '-----(BEGIN|END) CERTIFICATE-----', '') -replace '\s', ''
                    New-Object System.Security.Cryptography.X509Certificates.X509Certificate2 -ArgumentList (,[Convert]::FromBase64String($b64))
                } catch { }
            })
        } else {
            $salida.Certs = @(New-Object System.Security.Cryptography.X509Certificates.X509Certificate2 -ArgumentList $ruta)
            $salida.Formato = 'DER'
        }
    } catch { }
    $salida
}

function Get-AnclasArchivo($archivo) {
    if (-not $archivo -or $archivo.Formato -ne 'PEM') { return @() }
    @($archivo.Certs | ForEach-Object { New-Ancla $_ "archivo $(Split-Path $archivo.Ruta -Leaf)" })
}

# Cadena del servidor tal como la arma Windows con lo que manda el servidor.
# Solo se mira la cadena: la validacion se acepta siempre y no se envia nada.
function Get-CadenaServidor([string]$servidor, [int]$puerto) {
    $captura = @{ Cadena = @(); Errores = $null }
    $alValidar = {
        param($origen, $cert, $chain, $errores)
        $captura.Cadena = @($chain.ChainElements | ForEach-Object {
            New-Object System.Security.Cryptography.X509Certificates.X509Certificate2 -ArgumentList (,$_.Certificate.RawData)
        })
        $captura.Errores = $errores
        $true
    }.GetNewClosure()
    $tcp = New-Object System.Net.Sockets.TcpClient
    try {
        $intento = $tcp.BeginConnect($servidor, $puerto, $null, $null)
        if (-not $intento.AsyncWaitHandle.WaitOne(10000)) { throw "el servidor no respondio en 10 s" }
        $tcp.EndConnect($intento)
        $tcp.ReceiveTimeout = 10000
        $tcp.SendTimeout = 10000
        $tls = New-Object System.Net.Security.SslStream($tcp.GetStream(), $false,
            [System.Net.Security.RemoteCertificateValidationCallback]$alValidar)
        $tls.AuthenticateAsClient($servidor, $null, [System.Security.Authentication.SslProtocols]::Tls12, $false)
        $tls.Dispose()
    } finally { $tcp.Close() }
    $captura
}

# Imita la busqueda de OpenSSL: sube desde la hoja y, en cada paso, busca
# primero al emisor entre las anclas. Si solo lo encuentra vencido, falla con
# "certificate has expired"; si no lo encuentra en ningun paso, con
# "unable to get local issuer certificate". Es una aproximacion: la cadena es
# la que arma Windows, que puede no ser identica a la que manda el servidor.
function Test-Cadena($cadena, $anclas) {
    $faltan = @()
    foreach ($c in $cadena) {
        $emisor = Get-Clave $c.IssuerName
        $cands = @($anclas | Where-Object { $_.Clave -eq $emisor })
        $validos = @($cands | Where-Object { Test-Vigente $_.Cert })
        if ($validos) { return [pscustomobject]@{ Resultado = 'OK'; Ancla = $validos[0]; Faltan = @() } }
        if ($cands) { return [pscustomobject]@{ Resultado = 'VENCIDO'; Ancla = $cands[0]; Faltan = @() } }
        $faltan += Get-NombreEmisor $c
        if ($emisor -eq (Get-Clave $c.SubjectName)) { break }   # raiz autofirmada
    }
    [pscustomobject]@{ Resultado = 'FALTA'; Ancla = $null; Faltan = @($faltan | Select-Object -Unique) }
}

function Get-TextoVeredicto($v) {
    switch ($v.Resultado) {
        'OK'      { "OK: se ancla en '$(Get-Nombre $v.Ancla.Cert)' ($($v.Ancla.Fuentes -join ', '), vence $($v.Ancla.Cert.NotAfter.ToString('yyyy-MM-dd')))." }
        'VENCIDO' { "FALLA 'certificate has expired': toma '$(Get-Nombre $v.Ancla.Cert)' emitido por '$(Get-NombreEmisor $v.Ancla.Cert)', vencido el $($v.Ancla.Cert.NotAfter.ToString('yyyy-MM-dd')) ($($v.Ancla.Fuentes -join ', '))." }
        default   { "FALLA 'unable to get local issuer certificate': no hay ninguna de estas en el almacen ni en el archivo: $($v.Faltan -join ', ')." }
    }
}

# Fecha en que se escribio cada certificado en el registro: su alta en el
# almacen (o la ultima vez que Windows le cambio alguna propiedad).
$hayFechas = $false
try {
    if (-not ('Diag.Reg' -as [type])) {
        Add-Type -ErrorAction Stop -Namespace Diag -Name Reg -MemberDefinition @'
[DllImport("advapi32.dll")]
public static extern int RegQueryInfoKey(Microsoft.Win32.SafeHandles.SafeRegistryHandle hKey,
    IntPtr lpClass, IntPtr lpcchClass, IntPtr lpReserved, IntPtr lpcSubKeys,
    IntPtr lpcbMaxSubKeyLen, IntPtr lpcbMaxClassLen, IntPtr lpcValues,
    IntPtr lpcbMaxValueNameLen, IntPtr lpcbMaxValueLen, IntPtr lpcbSecurityDescriptor,
    out long lpftLastWriteTime);
'@
    }
    $hayFechas = $true
} catch { Write-Host "(No se pueden leer las fechas del registro: $($_.Exception.Message))" }

function Get-FechaRegistro($almacenes, $huella) {
    if (-not $hayFechas) { return '?' }
    $nombres = @()
    foreach ($a in $almacenes) {
        $n = ($a -split '\\')[-1]
        $nombres += $n
        if ($n -eq 'Root') { $nombres += 'AuthRoot' }
    }
    $raices = 'HKCU:\Software\Microsoft\SystemCertificates',
              'HKCU:\Software\Policies\Microsoft\SystemCertificates',
              'HKLM:\SOFTWARE\Microsoft\SystemCertificates',
              'HKLM:\SOFTWARE\Policies\Microsoft\SystemCertificates',
              'HKLM:\SOFTWARE\Microsoft\EnterpriseCertificates'
    $z = [IntPtr]::Zero
    foreach ($a in ($nombres | Select-Object -Unique)) {
        foreach ($r in $raices) {
            $k = Get-Item "$r\$a\Certificates\$huella" -ErrorAction SilentlyContinue
            if (-not $k) { continue }
            $ft = 0L
            if ([Diag.Reg]::RegQueryInfoKey($k.Handle, $z, $z, $z, $z, $z, $z, $z, $z, $z, $z, [ref]$ft) -eq 0) {
                return [DateTime]::FromFileTime($ft).ToString('yyyy-MM-dd HH:mm')
            }
        }
    }
    return '?'
}

# ================================================================ analisis ===

Write-Host "== 0. Equipo =="
Write-Host "Equipo: $env:COMPUTERNAME   Usuario: $env:USERNAME   Fecha del sistema: $ahora"
Write-Host "PowerShell $($PSVersionTable.PSVersion)   Modo de lenguaje: $($ExecutionContext.SessionState.LanguageMode)"
try {
    Write-Host ("Politicas de ejecucion: " + ((Get-ExecutionPolicy -List |
        ForEach-Object { "$($_.Scope)=$($_.ExecutionPolicy)" }) -join ', '))
} catch { Write-Host "ERROR leyendo las politicas de ejecucion: $($_.Exception.Message)" }

$credFile = Join-Path $env:APPDATA 'BerryMed Monitor\credentials.json'
$cred = $null
try {
    if (Test-Path $credFile) {
        $cred = Get-Content $credFile -Raw | ConvertFrom-Json
        Write-Host "TOTEM_ID: $($cred.TOTEM_ID)   API_URL: $($cred.API_URL)"
        Write-Host "credentials.json modificado: $((Get-Item $credFile).LastWriteTime)"
    } else {
        Write-Host "No existe $credFile (este usuario no tiene berry-monitor configurado)"
    }
} catch { Write-Host "ERROR leyendo credentials.json: $($_.Exception.Message)" }

$exes = @()
try {
    Get-Process -Name 'berry-monitor' -ErrorAction SilentlyContinue |
        ForEach-Object { $exes += [pscustomobject]@{ Ruta = $_.Path; Origen = 'ABIERTO' } }
} catch { }
try {
    $shell = New-Object -ComObject WScript.Shell
    Get-ChildItem "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Startup",
                  "$env:ProgramData\Microsoft\Windows\Start Menu\Programs\StartUp" -Filter *.lnk -ErrorAction SilentlyContinue |
        ForEach-Object { $exes += [pscustomobject]@{ Ruta = $shell.CreateShortcut($_.FullName).TargetPath; Origen = 'INICIO' } }
} catch { Write-Host "(No se pudieron leer los accesos de Inicio: $($_.Exception.Message))" }
try {
    Get-ChildItem $env:USERPROFILE -Filter 'berry-monitor*.exe' -Recurse -Depth 3 -ErrorAction SilentlyContinue |
        ForEach-Object { $exes += [pscustomobject]@{ Ruta = $_.FullName; Origen = 'perfil' } }
} catch { Write-Host "(No se pudo buscar el exe en el perfil: $($_.Exception.Message))" }
try {
    $grupos = @($exes | Where-Object { $_.Ruta -and $_.Ruta -match 'berry-monitor' -and (Test-Path $_.Ruta) } | Group-Object Ruta)
    if (-not $grupos) { Write-Host "No se encontro berry-monitor.exe (ni abierto, ni en Inicio, ni en el perfil)." }
    foreach ($g in $grupos) {
        $origen = ($g.Group | ForEach-Object { $_.Origen } | Sort-Object -Unique) -join '+'
        $h = (Get-FileHash $g.Name -Algorithm SHA256).Hash
        $build = if ($buildsConocidos.ContainsKey($h)) { $buildsConocidos[$h] } else { "build desconocido, SHA-256 $h" }
        Write-Host "exe [$origen]: $($g.Name)  (modificado $((Get-Item $g.Name).LastWriteTime.ToString('yyyy-MM-dd HH:mm'))) -> $build"
    }
} catch { Write-Host "ERROR identificando el exe: $($_.Exception.Message)" }

Write-Host "`n== 1. Cadena del servidor configurado =="
# La foto del almacen va ANTES de conectarse: al validar, Windows puede bajar
# por su cuenta una raiz que faltaba, y eso cambiaria lo que se quiere medir.
$almacenAntes = @()
try { $almacenAntes = Get-Almacen } catch { Write-Host "ERROR leyendo el almacen: $($_.Exception.Message)" }
$cadena = @()
$errorServidor = $null
$servidor = $null
try {
    $uri = [uri]"$($cred.API_URL)"
    if (-not $cred -or $uri.Scheme -ne 'https') {
        $errorServidor = 'la API no es https: no hay cadena que revisar'
    } else {
        $servidor = $uri.Host
        $puerto = if ($uri.IsDefaultPort) { 443 } else { $uri.Port }
        $captura = Get-CadenaServidor $servidor $puerto
        $cadena = @($captura.Cadena)
        $validacion = if ("$($captura.Errores)" -eq 'None') { 'sin errores' } else { "$($captura.Errores)" }
        Write-Host "Servidor: ${servidor}:$puerto   Validacion de Windows: $validacion"
        Show-Tabla ($cadena | ForEach-Object {
            [pscustomobject]@{
                Sujeto = Get-Nombre $_
                Emisor = Get-NombreEmisor $_
                Vence  = $_.NotAfter.ToString('yyyy-MM-dd')
                Estado = if (Test-Vigente $_) { 'ok' } else { 'VENCIDO' }
            }
        })
        if (-not $cadena) { $errorServidor = 'el servidor no devolvio certificados' }
    }
} catch {
    $errorServidor = "$($_.Exception.Message)"
}
if ($errorServidor) { Write-Host "No se pudo revisar la cadena: $errorServidor" }
$almacenDespues = @()
try { $almacenDespues = Get-Almacen } catch { }
$huellasAntes = @($almacenAntes | ForEach-Object { $_.Cert.Thumbprint })
$nuevos = @($almacenDespues | Where-Object { $huellasAntes -notcontains $_.Cert.Thumbprint })
foreach ($n in $nuevos) {
    Write-Host "Windows agrego al almacen durante la revision: '$(Get-Nombre $n.Cert)' ($($n.Fuentes -join ', '))"
}

# Nombres que importan: sujetos y emisores de la cadena.
$clavesCadena = @($cadena | ForEach-Object { Get-Clave $_.SubjectName; Get-Clave $_.IssuerName } | Select-Object -Unique)
$clavesEmisores = @($cadena | ForEach-Object { Get-Clave $_.IssuerName } | Select-Object -Unique)

Write-Host "`n== 2. Certificados de esa cadena en el almacen de Windows =="
Write-Host "(Registrado = cuando se escribio el certificado en el registro)"
try {
    $relacionados = @($almacenDespues | Where-Object { $clavesCadena -contains $_.Clave -or $_.Cert.Thumbprint -eq $huellaConocida })
    if (-not $relacionados) { Write-Host "Ninguno." }
    Show-Tabla ($relacionados | ForEach-Object {
        [pscustomobject]@{
            Almacen    = $_.Fuentes -join ', '
            Sujeto     = Get-Nombre $_.Cert
            Emisor     = Get-NombreEmisor $_.Cert
            Vence      = $_.Cert.NotAfter.ToString('yyyy-MM-dd')
            Estado     = if (Test-Vigente $_.Cert) { 'ok' } else { 'VENCIDO' }
            Registrado = Get-FechaRegistro $_.Fuentes $_.Cert.Thumbprint
            Huella     = $_.Cert.Thumbprint
        }
    })
} catch { Write-Host "ERROR en esta seccion: $($_.Exception.Message)" }

Write-Host "`n== 3. Archivo de SSL_CERT_FILE_PATH =="
$archivoConfigurado = $null
$carpetaCandidatos = $null
try {
    $pem = if ($cred) { "$($cred.SSL_CERT_FILE_PATH)".Trim() } else { '' }
    Write-Host "SSL_CERT_FILE_PATH = $pem"
    if (-not $pem) {
        Write-Host "Vacio: berry-monitor usa solo el almacen de Windows."
    } elseif (Test-Path -LiteralPath $pem -PathType Container) {
        Write-Host "Es una CARPETA: berry-monitor necesita la ruta de un ARCHIVO; con una carpeta no carga nada."
        $carpetaCandidatos = $pem
    } elseif (-not (Test-Path -LiteralPath $pem)) {
        Write-Host "No existe: berry-monitor no carga nada y usa solo el almacen de Windows."
        $padre = Split-Path $pem -Parent
        if ($padre -and (Test-Path -LiteralPath $padre -PathType Container)) { $carpetaCandidatos = $padre }
    } else {
        $archivoConfigurado = Read-ArchivoCert $pem
        Write-Host "Archivo modificado: $((Get-Item -LiteralPath $pem).LastWriteTime)   Formato: $($archivoConfigurado.Formato)   Certificados: $($archivoConfigurado.Certs.Count)"
        if ($archivoConfigurado.Formato -ne 'PEM') {
            Write-Host "No es PEM: berry-monitor no carga nada de este archivo."
        }
        $carpetaCandidatos = Split-Path $pem -Parent
    }
} catch { Write-Host "ERROR en esta seccion: $($_.Exception.Message)" }

# Archivos de certificados junto al configurado (o dentro de la carpeta).
$candidatos = @()
try {
    if ($carpetaCandidatos) {
        $candidatos = @(Get-ChildItem -LiteralPath $carpetaCandidatos -File -Recurse -Depth 1 -ErrorAction SilentlyContinue |
            Where-Object { $_.Extension -in '.pem', '.crt', '.cer' } |
            ForEach-Object { Read-ArchivoCert $_.FullName })
        if ($candidatos) {
            Write-Host "Certificados en $carpetaCandidatos :"
            Show-Tabla ($candidatos | ForEach-Object {
                $anclas = Get-AnclasArchivo $_
                $sirve = if ($_.Formato -ne 'PEM') { 'no (no es PEM)' }
                         elseif (-not $cadena) { '?' }
                         elseif ((Test-Cadena $cadena ($almacenDespues + $anclas)).Resultado -eq 'OK') { 'SI' }
                         else { 'no' }
                [pscustomobject]@{
                    Archivo      = $_.Ruta
                    Formato      = $_.Formato
                    Certificados = $_.Certs.Count
                    Sirve        = $sirve
                }
            })
        } else {
            Write-Host "No hay archivos .pem, .crt ni .cer en $carpetaCandidatos."
        }
    }
} catch { Write-Host "ERROR buscando archivos de certificados: $($_.Exception.Message)" }

Write-Host "`n== 4. Simulacion: puede berry-monitor validar la cadena? =="
Write-Host "(Para las versiones sin truststore: todas las instaladas hasta hoy.)"
$anclasArchivo = Get-AnclasArchivo $archivoConfigurado
$veredictoAntes = $null
$veredicto = $null
$recomendado = $null
if (-not $cadena) {
    Write-Host "Sin la cadena del servidor no se puede simular."
} else {
    try {
        $veredictoAntes = Test-Cadena $cadena ($almacenAntes + $anclasArchivo)
        $veredicto = Test-Cadena $cadena ($almacenDespues + $anclasArchivo)
        Write-Host "Con la configuracion actual: $(Get-TextoVeredicto $veredictoAntes)"
        if ($nuevos -and $veredicto.Resultado -ne $veredictoAntes.Resultado) {
            Write-Host "Con lo que agrego Windows recien: $(Get-TextoVeredicto $veredicto)"
        }
        if ($veredicto.Resultado -ne 'OK') {
            $recomendado = $candidatos |
                Where-Object { $_.Formato -eq 'PEM' -and (Test-Cadena $cadena ($almacenDespues + (Get-AnclasArchivo $_))).Resultado -eq 'OK' } |
                Select-Object -First 1
            if ($recomendado) { Write-Host "Con $($recomendado.Ruta) en SSL_CERT_FILE_PATH validaria." }
        }
    } catch { Write-Host "ERROR en la simulacion: $($_.Exception.Message)" }
}

Write-Host "`n== 5. Actualizaciones de Windows de los ultimos 7 dias =="
try {
    $buscador = (New-Object -ComObject Microsoft.Update.Session).CreateUpdateSearcher()
    $buscador.QueryHistory(0, [Math]::Min($buscador.GetTotalHistoryCount(), 100)) |
        Where-Object { $_.Date -gt $ahora.AddDays(-7) -and
                       $_.Title -notmatch 'Security Intelligence Update|inteligencia de seguridad|^9N' } |
        Sort-Object Date |
        ForEach-Object { Write-Host ('{0:yyyy-MM-dd HH:mm}  {1}' -f $_.Date.ToLocalTime(), $_.Title) }
} catch {
    try {
        Show-Tabla (Get-HotFix | Where-Object { $_.InstalledOn -and $_.InstalledOn -gt $ahora.AddDays(-7) } |
            Sort-Object InstalledOn | Select-Object HotFixID, Description, InstalledOn)
    } catch { Write-Host "ERROR en esta seccion: $($_.Exception.Message)" }
}

# Una linea [HEALTH] que no sea un error prueba que la conexion TLS anduvo,
# aunque el backend conteste otra cosa: el certificado ya se valido.
function Test-LineaOk($l) {
    ($l -match '\[HEALTH\]' -and $l -notmatch 'No se pudo reportar|Desactivado') -or
        $l -match 'Vital signs sent successfully'
}

Write-Host "`n== 6. Log de berry-monitor =="
try {
    $archivosLog = @(Get-ChildItem "$logBerry*" -ErrorAction SilentlyContinue | Sort-Object LastWriteTime)
    if (-not $archivosLog) {
        Write-Host "No hay log: este berry-monitor no lo escribe (el log a disco esta desde el build del 2026-09-04)."
    } else {
        $lineas = @($archivosLog | ForEach-Object { Get-Content $_.FullName -Encoding UTF8 -ErrorAction SilentlyContinue })
        $errSsl = @($lineas | Where-Object { $_ -match 'CERTIFICATE_VERIFY_FAILED|certificate has expired|unable to get local issuer' })
        $oks = @($lineas | Where-Object { Test-LineaOk $_ })
        Write-Host "Lineas: $($lineas.Count)   Errores de certificado: $($errSsl.Count)"
        $corte = { param($l) $l.Substring(0, [Math]::Min(200, $l.Length)) }
        if ($errSsl) {
            Write-Host "Primer error:  $(& $corte $errSsl[0])"
            Write-Host "Ultimo error:  $(& $corte $errSsl[-1])"
        }
        if ($oks) { Write-Host "Ultima conexion OK: $(& $corte $oks[-1])" }
    }
} catch { Write-Host "ERROR en esta seccion: $($_.Exception.Message)" }

# ============================================================== reparacion ===

# Vencidos del almacen que pueden romper la validacion: los que tienen el
# sujeto de un emisor de la cadena, y siempre el cruce conocido.
function Get-Vencidos {
    @(Get-Almacen | Where-Object {
        -not (Test-Vigente $_.Cert) -and
        ($clavesEmisores -contains $_.Clave -or $_.Cert.Thumbprint -eq $huellaConocida)
    })
}

# Espera a que berry-monitor escriba en su log una conexion buena o el error.
function Wait-Resultado($log, $desde, $segundos) {
    $limite = (Get-Date).AddSeconds($segundos)
    while ((Get-Date) -lt $limite) {
        Start-Sleep -Seconds 3
        if (-not (Test-Path $log)) { continue }
        $todas = @(Get-Content $log -Encoding UTF8 -ErrorAction SilentlyContinue)
        if ($todas.Count -lt $desde) { $desde = 0 }   # el log roto mientras tanto
        $nuevas = @($todas | Select-Object -Skip $desde)
        if ($nuevas | Where-Object { $_ -match 'CERTIFICATE_VERIFY_FAILED|certificate has expired|unable to get local issuer' }) { return 'SSL' }
        if ($nuevas | Where-Object { Test-LineaOk $_ }) { return 'OK' }
    }
    return 'SIN DATOS'
}

Write-Host "`n== 7. Reparacion =="
$resultado = 'NADA QUE BORRAR'
$rutas = @()
$vencidos = Get-Vencidos
if (-not $vencidos) {
    Write-Host "No hay certificados vencidos de la cadena en el almacen: no hay nada que borrar."
} else {
    foreach ($v in $vencidos) {
        Write-Host "Vencido: '$(Get-Nombre $v.Cert)' emitido por '$(Get-NombreEmisor $v.Cert)' ($($v.Fuentes -join ', '), vencio el $($v.Cert.NotAfter.ToString('yyyy-MM-dd')))"
    }
    $procesos = @(Get-Process -Name 'berry-monitor' -ErrorAction SilentlyContinue)
    $rutas = @($procesos | ForEach-Object { $_.Path } | Where-Object { $_ } | Sort-Object -Unique)
    Write-Host "Se va a guardar un respaldo en el Escritorio y borrar los certificados vencidos."
    if ($procesos) {
        Write-Host "berry-monitor se va a cerrar y volver a abrir: verificar que no haya una medicion en curso."
    }
    $seguir = $sinPreguntar
    if (-not $seguir) {
        $r = Read-Host "Escribir S y apretar Enter para continuar (cualquier otra cosa cancela)"
        $seguir = $r -match '^\s*[sS]'
    }
    if (-not $seguir) {
        $resultado = 'CANCELADO'
        $rutas = @()
        Write-Host "Cancelado: no se cambio nada."
    } else {
        foreach ($v in $vencidos) {
            $nombreBk = if ($v.Cert.Thumbprint -eq $huellaConocida) { 'respaldo-isrg-x2-vencido.cer' }
                        else { "respaldo-vencido-$($v.Cert.Thumbprint.Substring(0, 8)).cer" }
            $bk = Join-Path $escritorio $nombreBk
            try {
                Export-Certificate -Cert $v.Cert -FilePath $bk -ErrorAction Stop | Out-Null
                Write-Host "Respaldo guardado en: $bk"
            } catch { Write-Host "(No se pudo guardar el respaldo: $($_.Exception.Message))" }
        }

        foreach ($p in $procesos) {
            try {
                Stop-Process -Id $p.Id -Force -ErrorAction Stop
                Write-Host "berry-monitor cerrado (PID $($p.Id))."
            } catch { Write-Host "(No se pudo cerrar berry-monitor PID $($p.Id): $($_.Exception.Message))" }
        }

        # Si Windows pregunta por borrar un certificado de la raiz, hay que responder Si.
        foreach ($v in $vencidos) {
            foreach ($alm in $v.Fuentes) {
                Remove-Item "Cert:\$alm\$($v.Cert.Thumbprint)" -ErrorAction SilentlyContinue
            }
        }

        $huellas = @($vencidos | ForEach-Object { $_.Cert.Thumbprint })
        $quedan = @(Get-Almacen | Where-Object { $huellas -contains $_.Cert.Thumbprint })
        if ($quedan) {
            # Queda la copia de toda la maquina: se borra en una PowerShell
            # elevada, que abre el aviso de Windows pidiendo permiso.
            Write-Host "Queda una copia a nivel maquina: Windows va a pedir permiso de administrador."
            $lista = ($huellas | ForEach-Object { "'$_'" }) -join ','
            $cmd = "Get-ChildItem 'Cert:\LocalMachine\CA','Cert:\LocalMachine\Root','Cert:\LocalMachine\AuthRoot' | " +
                   "Where-Object { @($lista) -contains `$_.Thumbprint } | Remove-Item"
            $enc = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($cmd))
            try {
                Start-Process powershell.exe -Verb RunAs -Wait -ErrorAction Stop `
                    -ArgumentList '-NoProfile', '-EncodedCommand', $enc
            } catch { Write-Host "No se obtuvo el permiso de administrador: $($_.Exception.Message)" }
            $quedan = @(Get-Almacen | Where-Object { $huellas -contains $_.Cert.Thumbprint })
        }

        if ($quedan) {
            $resultado = 'FALTA'
            Write-Host "FALTA: siguen en el almacen: $(($quedan | ForEach-Object { Get-Nombre $_.Cert }) -join ', ')."
        } else {
            $resultado = 'LISTO'
            Write-Host "LISTO: certificados vencidos eliminados."
        }

        $desde = if (Test-Path $logBerry) { @(Get-Content $logBerry -ErrorAction SilentlyContinue).Count } else { 0 }
        foreach ($ruta in $rutas) {
            try {
                Start-Process -FilePath $ruta -WorkingDirectory (Split-Path $ruta) -ErrorAction Stop
                Write-Host "berry-monitor abierto de nuevo: $ruta"
            } catch { Write-Host "(No se pudo abrir $ruta : $($_.Exception.Message))" }
        }
    }
}

# Lo que queda despues de reparar: si todavia falta la raiz, borrar no alcanza.
$veredictoFinal = $veredicto
if ($cadena -and $resultado -eq 'LISTO') {
    try { $veredictoFinal = Test-Cadena $cadena ((Get-Almacen) + $anclasArchivo) } catch { }
}
$consejoRaiz = if ($recomendado) {
    "Configurar SSL_CERT_FILE_PATH = $($recomendado.Ruta) (con berry-configure, o en credentials.json escribiendo las barras dobles) y volver a abrir berry-monitor."
} elseif ($veredictoFinal) {
    "Conseguir un archivo .pem que incluya alguna de: $($veredictoFinal.Faltan -join ', ') (por ejemplo, el cacert.pem de certifi), configurarlo en SSL_CERT_FILE_PATH y volver a abrir berry-monitor."
}

Write-Host "`n== 8. Verificacion =="
$verificado = $null
if ($resultado -eq 'LISTO' -and $rutas) {
    Write-Host "Esperando hasta 90 segundos a que berry-monitor se conecte..."
    $verificado = Wait-Resultado $logBerry $desde 90
    switch ($verificado) {
        'OK'  { Write-Host "OK: berry-monitor se conecto sin error de certificado." }
        'SSL' { Write-Host "ATENCION: berry-monitor sigue con error de certificado." }
        default {
            Write-Host "No se pudo confirmar desde el log (este build puede no escribirlo)."
            Write-Host "Verificar a mano: iniciar una medicion y buscar 'Vital signs sent successfully'."
        }
    }
} elseif ($resultado -eq 'LISTO') {
    Write-Host "berry-monitor no estaba abierto: abrirlo como siempre y verificar que no aparezca el error."
} else {
    Write-Host "Nada que verificar."
}

Write-Host "`n================================================================"
switch ($resultado) {
    'LISTO' {
        if ($verificado -eq 'SSL') {
            Write-Host " Se borraron los certificados vencidos, pero el error sigue."
            Write-Host " Mandar este reporte a soporte."
        } elseif ($veredictoFinal -and $veredictoFinal.Resultado -eq 'FALTA') {
            Write-Host " Se borraron los certificados vencidos, pero ademas FALTA LA RAIZ del servidor."
            Write-Host " $consejoRaiz"
        } else {
            Write-Host " LISTO. El totem quedo corregido. NO reiniciar la PC."
        }
    }
    'FALTA' {
        Write-Host " FALTA: hace falta un usuario administrador para terminar."
        Write-Host " Repetir con clic derecho en Windows PowerShell > Ejecutar como administrador."
    }
    'CANCELADO' { Write-Host " Cancelado: no se cambio nada." }
    default {
        if (-not $cadena) {
            Write-Host " No se pudo revisar la cadena del servidor: $errorServidor"
            Write-Host " Mandar este reporte a soporte."
        } elseif ($veredicto.Resultado -eq 'OK' -and $veredictoAntes.Resultado -ne 'OK') {
            Write-Host " Windows agrego durante la revision la raiz que faltaba."
            Write-Host " Cerrar y volver a abrir berry-monitor (sin reiniciar la PC)."
        } elseif ($veredicto.Resultado -eq 'OK') {
            Write-Host " El almacen y la configuracion alcanzan para validar la cadena: el problema es otro."
            Write-Host " Mandar este reporte a soporte."
        } elseif ($veredicto.Resultado -eq 'FALTA') {
            Write-Host " FALTA LA RAIZ del servidor en este equipo."
            Write-Host " $consejoRaiz"
        } else {
            Write-Host " El archivo de SSL_CERT_FILE_PATH tiene un certificado vencido de la cadena."
            Write-Host " Configurar otro archivo que no lo tenga y volver a abrir berry-monitor."
        }
    }
}
Write-Host " Reporte: $reporte"
Write-Host "================================================================"
try { Stop-Transcript | Out-Null } catch { }
Start-Process notepad.exe $reporte
