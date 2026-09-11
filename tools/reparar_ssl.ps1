# Reparacion de errores de certificado de berry-monitor.exe
#
# Los berry-monitor sin truststore validan TLS con OpenSSL. Como anclas de
# confianza usan una copia de los almacenes ROOT y CA de Windows mas el ARCHIVO
# de SSL_CERT_FILE_PATH (credentials.json). Fallan de varias formas, aunque el
# navegador y Invoke-WebRequest conecten bien en la misma PC:
#   - "certificate has expired": en el almacen hay un certificado vencido con el
#     sujeto de un emisor de la cadena. Por ejemplo, el cruce viejo
#     "ISRG Root X2" emitido por "ISRG Root X1", vencido el 2025-09-15.
#   - "unable to get local issuer certificate": ni el almacen ni el archivo
#     tienen la raiz de la cadena del servidor.
#   - "Missing Authority Key Identifier", "Basic Constraints of CA cert not
#     marked critical" y similares: los exes con Python 3.13 validan en modo
#     estricto y rechazan certificados que no cumplen RFC 5280. Pasa sobre todo
#     cuando un firewall o antivirus inspecciona el HTTPS y firma los
#     certificados con su propia raiz. Los exes con Python 3.11 no hacen esos
#     chequeos, y la version con truststore valida con Windows.
#
# SSL_CERT_FILE_PATH tiene que ser la ruta de un ARCHIVO .pem: ninguna version
# la usa como carpeta. Hasta el build del 2025-07-17 el exe usaba el bundle de
# certifi que trae adentro.
#
# En un solo paso:
#   1. Analiza el equipo: los berry-monitor que hay (version de Python, si traen
#      truststore, cual arranca solo), la configuracion, las variables de
#      entorno y el proxy.
#   2. Analiza los dos servidores con los que habla berry-monitor: la API
#      (API_URL) y Pusher (las ordenes). De cada uno toma la cadena que llega a
#      este equipo, la cruza con el almacen de Windows y con el archivo de
#      SSL_CERT_FILE_PATH, revisa el modo estricto, el nombre del certificado y
#      el reloj, y dice que va a pasar con cada berry-monitor encontrado.
#   3. Si hay certificados vencidos de esas cadenas en el almacen, pide
#      confirmacion, los respalda en el Escritorio, cierra berry-monitor, los
#      borra (si hay copia a nivel maquina pide permiso de administrador) y
#      vuelve a abrir berry-monitor. Despues verifica en el log que conecte.
#   Para lo demas (falta la raiz, firewall que inspecciona) dice que hacer.
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
# Productos que inspeccionan HTTPS firmando con su propia raiz. Se buscan en el
# sujeto y el emisor de la cadena que llega a este equipo.
$pistasInspeccion = [ordered]@{
    'Fortinet|FortiGate|CN=F[GW][0-9A-Z]{8,}' = 'FortiGate (Fortinet)'
    'Zscaler'                   = 'Zscaler'
    'Netskope'                  = 'Netskope'
    'Palo Alto|PAN-OS'          = 'Palo Alto'
    'Sophos'                    = 'Sophos'
    'Cisco Umbrella|OpenDNS'    = 'Cisco Umbrella'
    'Check ?Point'              = 'Check Point'
    'WatchGuard'                = 'WatchGuard'
    'SonicWall'                 = 'SonicWall'
    'Barracuda'                 = 'Barracuda'
    'Kaspersky'                 = 'Kaspersky'
    '\bESET\b'                  = 'ESET'
    'Avast|\bAVG\b'             = 'Avast/AVG'
    'Bitdefender'               = 'Bitdefender'
    'McAfee|Skyhigh'            = 'McAfee/Skyhigh'
    'Blue ?Coat|Symantec Web'   = 'Symantec/Blue Coat'
    'Untangle|pfSense|OPNsense' = 'firewall de codigo abierto'
}

# Las tablas se pasan a texto con un ancho grande: si no, el transcript las
# corta al ancho de la consola y se pierden columnas.
function Show-Tabla($filas) {
    if ($filas) { Write-Host (($filas | Format-Table -AutoSize | Out-String -Width 400).TrimEnd()) }
}

function Get-Nombre($cert) { $cert.GetNameInfo('SimpleName', $false) }
function Get-NombreEmisor($cert) { $cert.GetNameInfo('SimpleName', $true) }
function Test-Vigente($cert) { $cert.NotBefore -le $ahora -and $cert.NotAfter -ge $ahora }
function Get-Ext($cert, $oid) { @($cert.Extensions | Where-Object { $_.Oid.Value -eq $oid })[0] }

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

# De donde salio un certificado del almacen, segun donde lo guarda el registro.
# Las raices publicas llegan por Windows Update; una raiz "agregada" la puso
# alguien: una persona, un programa o el area de sistemas.
function Get-Origen($huella) {
    $mapa = [ordered]@{
        'HKLM:\SOFTWARE\Microsoft\SystemCertificates\AuthRoot\Certificates'      = 'Windows Update'
        'HKLM:\SOFTWARE\Policies\Microsoft\SystemCertificates\Root\Certificates' = 'politica de grupo'
        'HKLM:\SOFTWARE\Microsoft\EnterpriseCertificates\Root\Certificates'      = 'dominio'
        'HKLM:\SOFTWARE\Microsoft\SystemCertificates\ROOT\Certificates'          = 'agregada en la maquina'
        'HKCU:\Software\Microsoft\SystemCertificates\Root\Certificates'          = 'agregada por el usuario'
        'HKLM:\SOFTWARE\Policies\Microsoft\SystemCertificates\CA\Certificates'   = 'intermedio por politica'
        'HKLM:\SOFTWARE\Microsoft\SystemCertificates\CA\Certificates'            = 'intermedio de la maquina'
        'HKCU:\Software\Microsoft\SystemCertificates\CA\Certificates'            = 'intermedio del usuario'
    }
    $origenes = @(foreach ($k in $mapa.Keys) { if (Test-Path "$k\$huella") { $mapa[$k] } })
    if ($origenes) { $origenes -join ', ' } else { '?' }
}

function Get-PistaInspeccion($certs) {
    $texto = ($certs | ForEach-Object { "$($_.Subject) $($_.Issuer)" }) -join ' '
    foreach ($patron in $pistasInspeccion.Keys) {
        if ($texto -match $patron) { return $pistasInspeccion[$patron] }
    }
    $null
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

# Cadena del servidor tal como la arma Windows con lo que llega a este equipo.
# Solo se mira la cadena: la validacion se acepta siempre y no se envia nada.
function Get-CadenaServidor([string]$servidor, [int]$puerto) {
    $captura = @{ Cadena = @(); Errores = $null; Ip = $null }
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
        $captura.Ip = $tcp.Client.RemoteEndPoint.Address.ToString()
        $tcp.ReceiveTimeout = 10000
        $tcp.SendTimeout = 10000
        $tls = New-Object System.Net.Security.SslStream($tcp.GetStream(), $false,
            [System.Net.Security.RemoteCertificateValidationCallback]$alValidar)
        $tls.AuthenticateAsClient($servidor, $null, [System.Security.Authentication.SslProtocols]::Tls12, $false)
        $tls.Dispose()
    } finally { $tcp.Close() }
    $captura
}

function Get-TextoValidacionWindows($errores) {
    $t = "$errores"
    if ($t -eq 'None') { return 'sin errores' }
    $partes = @()
    if ($t -match 'RemoteCertificateChainErrors') { $partes += 'cadena no confiable para Windows' }
    if ($t -match 'RemoteCertificateNameMismatch') { $partes += 'el nombre no coincide' }
    if ($t -match 'RemoteCertificateNotAvailable') { $partes += 'el servidor no mando certificado' }
    if ($partes) { $partes -join ', ' } else { $t }
}

# Nombres DNS del Subject Alternative Name. Python valida el nombre del
# servidor SOLO contra esta lista (no mira el CN).
function Get-SanDns($cert) {
    $ext = Get-Ext $cert '2.5.29.17'
    if (-not $ext) { return $null }
    $b = $ext.RawData
    $nombres = New-Object System.Collections.Generic.List[string]
    $i = 1
    $largo = [int]$b[$i]; $i++
    if ($largo -band 0x80) { $n = $largo -band 0x7F; $largo = 0; for ($k = 0; $k -lt $n; $k++) { $largo = $largo * 256 + $b[$i]; $i++ } }
    $fin = $i + $largo
    while ($i -lt $fin) {
        $tag = $b[$i]; $i++
        $l = [int]$b[$i]; $i++
        if ($l -band 0x80) { $n = $l -band 0x7F; $l = 0; for ($k = 0; $k -lt $n; $k++) { $l = $l * 256 + $b[$i]; $i++ } }
        if ($tag -eq 0x82) { $nombres.Add([Text.Encoding]::ASCII.GetString($b, $i, $l)) }   # dNSName
        $i += $l
    }
    ,$nombres.ToArray()
}

function Test-NombreServidor($servidor, $nombres) {
    foreach ($n in @($nombres)) {
        if ($n -ieq $servidor) { return $true }
        if ($n.StartsWith('*.') -and $servidor.EndsWith($n.Substring(1), 'OrdinalIgnoreCase') -and
            $servidor.Split('.').Count -eq $n.Split('.').Count) { return $true }
    }
    $false
}

# Imita la busqueda de OpenSSL: sube desde la hoja y, en cada paso, busca
# primero al emisor entre las anclas. Si solo lo encuentra vencido, falla con
# "certificate has expired"; si no lo encuentra en ningun paso, con
# "unable to get local issuer certificate". Es una aproximacion: la cadena es
# la que arma Windows, que puede no ser identica a la que manda el servidor.
function Test-Cadena($cadena, $anclas) {
    $faltan = @()
    for ($i = 0; $i -lt $cadena.Count; $i++) {
        $c = $cadena[$i]
        $emisor = Get-Clave $c.IssuerName
        $cands = @($anclas | Where-Object { $_.Clave -eq $emisor })
        $validos = @($cands | Where-Object { Test-Vigente $_.Cert })
        if ($validos) { return [pscustomobject]@{ Resultado = 'OK'; Ancla = $validos[0]; Faltan = @(); Indice = $i } }
        if ($cands) { return [pscustomobject]@{ Resultado = 'VENCIDO'; Ancla = $cands[0]; Faltan = @(); Indice = $i } }
        $faltan += Get-NombreEmisor $c
        if ($emisor -eq (Get-Clave $c.SubjectName)) { break }   # raiz autofirmada
    }
    [pscustomobject]@{ Resultado = 'FALTA'; Ancla = $null; Faltan = @($faltan | Select-Object -Unique); Indice = -1 }
}

# Los certificados que OpenSSL tendria en la cadena: desde la hoja hasta el
# ancla que encontro.
function Get-Camino($cadena, $v) {
    if ($v -and $v.Ancla) { @($cadena[0..$v.Indice]) + @($v.Ancla.Cert) } else { @($cadena) }
}

# Los chequeos de RFC 5280 que OpenSSL hace en modo estricto (X509_STRICT), que
# Python 3.13 activa por defecto. Los mensajes son los de OpenSSL, para que se
# puedan comparar con el error que muestra berry-monitor.
function Get-ProblemasEstricto($camino) {
    $problemas = @()
    for ($i = 0; $i -lt $camino.Count; $i++) {
        $c = $camino[$i]
        $n = Get-Nombre $c
        $bc = Get-Ext $c '2.5.29.19'
        $ku = Get-Ext $c '2.5.29.15'
        $aki = Get-Ext $c '2.5.29.35'
        $ski = Get-Ext $c '2.5.29.14'
        $san = Get-Ext $c '2.5.29.17'
        $esCA = $false
        if ($bc) {
            $bcT = New-Object System.Security.Cryptography.X509Certificates.X509BasicConstraintsExtension -ArgumentList $bc, $bc.Critical
            $esCA = $bcT.CertificateAuthority
        }
        if ($c.Version -lt 3) {
            if ($c.Extensions.Count -gt 0) { $problemas += "'$n': Extensions require version 3" }
            continue
        }
        if ($esCA -and -not $bc.Critical) { $problemas += "'$n': Basic Constraints of CA cert not marked critical" }
        if ($esCA -and -not $ku) { $problemas += "'$n': CA cert does not include key usage extension" }
        if ($i -lt $camino.Count - 1 -and -not $aki) { $problemas += "'$n': Missing Authority Key Identifier" }
        if ($esCA -and -not $ski) { $problemas += "'$n': Missing Subject Key Identifier" }
        if ($aki -and $aki.Critical) { $problemas += "'$n': Authority Key Identifier marked critical" }
        if ($ski -and $ski.Critical) { $problemas += "'$n': Subject Key Identifier marked critical" }
        if (-not $c.Subject -and ($esCA -or -not $san)) { $problemas += "'$n': Subject name empty" }
        if (-not $c.Issuer) { $problemas += "'$n': Issuer name empty" }
    }
    $problemas
}

function Get-TextoVeredicto($v) {
    switch ($v.Resultado) {
        'OK'      { "OK: se ancla en '$(Get-Nombre $v.Ancla.Cert)' ($($v.Ancla.Fuentes -join ', '), vence $($v.Ancla.Cert.NotAfter.ToString('yyyy-MM-dd')))." }
        'VENCIDO' { "FALLA 'certificate has expired': toma '$(Get-Nombre $v.Ancla.Cert)' emitido por '$(Get-NombreEmisor $v.Ancla.Cert)', vencido el $($v.Ancla.Cert.NotAfter.ToString('yyyy-MM-dd')) ($($v.Ancla.Fuentes -join ', '))." }
        default   { "FALLA 'unable to get local issuer certificate': no hay ninguna de estas en el almacen ni en el archivo: $($v.Faltan -join ', ')." }
    }
}

# Que le pasa a un berry-monitor segun con que valida. $estricto solo cuenta
# para la API: el websocket de Pusher usa un contexto sin modo estricto.
function Get-Esperado($valida, $s, $conEstricto) {
    if (-not $s -or -not $s.Cadena) { return '?' }
    if ($valida -eq 'Windows (truststore)') {
        if ("$($s.Errores)" -eq 'None') { return 'OK' }
        return "FALLA ($(Get-TextoValidacionWindows $s.Errores))"
    }
    if (-not $s.NombreOk) { return 'FALLA: Hostname mismatch' }
    if ($s.Veredicto.Resultado -eq 'VENCIDO') { return 'FALLA: certificate has expired' }
    if ($s.Veredicto.Resultado -eq 'FALTA') { return 'FALLA: unable to get local issuer certificate' }
    if ($conEstricto -and $s.Estricto) {
        $motivo = ($s.Estricto[0] -split ': ', 2)[1]
        if ($valida -eq 'OpenSSL estricto') { return "FALLA: $motivo" }
        if ($valida -eq '?') { return "FALLA con Python 3.13 ($motivo); OK con 3.11" }
    }
    'OK'
}

# Version de Python y truststore, leidas de los bytes del exe: PyInstaller deja
# el nombre de la DLL de Python y los nombres de los modulos sin comprimir.
function Get-InfoExe($ruta, $origen) {
    $info = [pscustomobject]@{ Ruta = $ruta; Origen = $origen; Modificado = ''; Version = ''; Build = '?'; Python = '?'; Truststore = '?'; Valida = '?' }
    try {
        $item = Get-Item -LiteralPath $ruta
        $info.Modificado = $item.LastWriteTime.ToString('yyyy-MM-dd HH:mm')
        # Los exe generados con build.ps1 traen la version en las propiedades del archivo.
        $info.Version = "$($item.VersionInfo.ProductVersion)"
        if ($item.Attributes -band 0x401000) { $info.Build = 'en la nube (no descargado)'; return $info }
        $hash = (Get-FileHash -LiteralPath $ruta -Algorithm SHA256).Hash
        $info.Build = if ($buildsConocidos.ContainsKey($hash)) { $buildsConocidos[$hash] } else { "desconocido, SHA-256 $hash" }
        $texto = [Text.Encoding]::GetEncoding(28591).GetString([IO.File]::ReadAllBytes($ruta))
        $m = [regex]::Match($texto, 'python3(\d{1,2})\.dll')
        if ($m.Success) { $info.Python = "3.$($m.Groups[1].Value)" }
        $conTruststore = $texto.Contains('truststore._windows')
        $info.Truststore = if ($conTruststore) { 'si' } else { 'no' }
        $info.Valida = if ($conTruststore) { 'Windows (truststore)' }
                       elseif ($m.Success -and [int]$m.Groups[1].Value -ge 13) { 'OpenSSL estricto' }
                       elseif ($m.Success) { 'OpenSSL' }
                       else { '?' }
    } catch { }
    $info
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

# Diferencia entre el reloj de este equipo y el del servidor (encabezado Date).
# Un reloj muy corrido hace que certificados vigentes parezcan vencidos.
function Get-DesfaseReloj($servidor) {
    try { [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12 } catch { }
    # HttpWebRequest y no Invoke-WebRequest: que el servidor conteste 404 o 405
    # no es un error aca, y el cmdlet lo dejaria anotado en el reporte.
    $fecha = $null
    $resp = $null
    try {
        $req = [Net.HttpWebRequest]::Create("https://$servidor/")
        $req.Method = 'HEAD'
        $req.Timeout = 10000
        try { $resp = $req.GetResponse() } catch [Net.WebException] { $resp = $_.Exception.Response }
        if ($resp) { $fecha = $resp.Headers['Date']; $resp.Close() }
    } catch { }
    if (-not $fecha) { return $null }
    $delServidor = [DateTime]::Parse($fecha, [Globalization.CultureInfo]::InvariantCulture).ToUniversalTime()
    ((Get-Date).ToUniversalTime() - $delServidor).TotalSeconds
}

# Todo lo que se sabe de un servidor visto desde este equipo.
function Get-AnalisisServidor($servidor, $puerto, $anclasArchivo) {
    $s = [pscustomobject]@{
        Servidor = $servidor; Puerto = $puerto; Ips = @(); Ip = $null; Cadena = @(); Errores = $null; Error = $null
        VeredictoAntes = $null; Veredicto = $null; Estricto = @(); NombreOk = $true; San = $null
        Pista = $null; Nuevos = @()
    }
    try { $s.Ips = @([System.Net.Dns]::GetHostAddresses($servidor) | ForEach-Object { $_.IPAddressToString }) } catch { }
    # La foto del almacen va ANTES de conectarse: al validar, Windows puede bajar
    # por su cuenta una raiz que faltaba, y eso cambiaria lo que se quiere medir.
    $antes = @(Get-Almacen)
    try {
        $captura = Get-CadenaServidor $servidor $puerto
        $s.Cadena = @($captura.Cadena)
        $s.Errores = $captura.Errores
        $s.Ip = $captura.Ip
        if (-not $s.Cadena) { $s.Error = 'el servidor no devolvio certificados' }
    } catch { $s.Error = "$($_.Exception.Message)" }
    $despues = @(Get-Almacen)
    $huellasAntes = @($antes | ForEach-Object { $_.Cert.Thumbprint })
    $s.Nuevos = @($despues | Where-Object { $huellasAntes -notcontains $_.Cert.Thumbprint })
    if ($s.Cadena) {
        $s.VeredictoAntes = Test-Cadena $s.Cadena ($antes + $anclasArchivo)
        $s.Veredicto = Test-Cadena $s.Cadena ($despues + $anclasArchivo)
        $s.Estricto = @(Get-ProblemasEstricto (Get-Camino $s.Cadena $s.Veredicto))
        $s.San = Get-SanDns $s.Cadena[0]
        $s.NombreOk = Test-NombreServidor $servidor $s.San
        $s.Pista = Get-PistaInspeccion $s.Cadena
    }
    $s
}

function Show-Servidor($s, $conEstricto) {
    $ipsTexto = $s.Ips -join ', '
    $privada = @($s.Ips | Where-Object { $_ -match '^(10\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.|127\.|f[cd])' })
    Write-Host "Servidor: $($s.Servidor):$($s.Puerto)   DNS: $ipsTexto   Conectado a: $($s.Ip)"
    if ($privada) { Write-Host "  El nombre resuelve a una IP de la red local: hay un DNS interno o un equipo intermedio." }
    if ($s.Error) { Write-Host "  No se pudo conectar: $($s.Error)"; return }
    Write-Host "  Validacion de Windows: $(Get-TextoValidacionWindows $s.Errores)"
    Show-Tabla ($s.Cadena | ForEach-Object {
        [pscustomobject]@{
            Sujeto = Get-Nombre $_
            Emisor = Get-NombreEmisor $_
            Vence  = $_.NotAfter.ToString('yyyy-MM-dd')
            Estado = if (Test-Vigente $_) { 'ok' } else { 'VENCIDO' }
            AKI    = if (Get-Ext $_ '2.5.29.35') { 'si' } else { 'no' }
            Huella = $_.Thumbprint
        }
    })
    if ($null -eq $s.San) { Write-Host "  El certificado no tiene nombres alternativos (SAN): Python no lo acepta para ningun servidor." }
    elseif (-not $s.NombreOk) { Write-Host "  El certificado no es para $($s.Servidor): nombres $($s.San -join ', ')." }
    foreach ($n in $s.Nuevos) {
        Write-Host "  Windows agrego al almacen durante la revision: '$(Get-Nombre $n.Cert)' ($($n.Fuentes -join ', '))"
    }
    $raiz = $s.Cadena[-1]
    $origenRaiz = Get-Origen $raiz.Thumbprint
    if ((Get-Clave $raiz.SubjectName) -ne (Get-Clave $raiz.IssuerName)) {
        $origenRaiz = '?'
        Write-Host "  Windows no encontro en este equipo la raiz que firma a '$(Get-Nombre $raiz)' (emisor: '$(Get-NombreEmisor $raiz)')."
    } else {
        Write-Host "  Raiz de la cadena: '$(Get-Nombre $raiz)', origen en este equipo: $origenRaiz"
    }
    if ($s.Pista) {
        Write-Host "  INSPECCION HTTPS: la cadena la firma $($s.Pista). Un equipo de la red intercepta la conexion."
    } elseif ($origenRaiz -ne '?' -and $origenRaiz -notmatch 'Windows Update') {
        Write-Host "  La raiz no vino de Windows Update: si no es una raiz publica conocida, suele ser un firewall o antivirus que inspecciona el HTTPS."
    }
    Write-Host "  Simulacion OpenSSL (almacen + archivo): $(Get-TextoVeredicto $s.VeredictoAntes)"
    if ($s.Nuevos -and $s.Veredicto.Resultado -ne $s.VeredictoAntes.Resultado) {
        Write-Host "  Con lo que agrego Windows recien: $(Get-TextoVeredicto $s.Veredicto)"
    }
    if ($conEstricto) {
        if ($s.Estricto) {
            Write-Host "  Modo estricto de Python 3.13: NO lo cumple:"
            foreach ($p in $s.Estricto) { Write-Host "    $p" }
        } else {
            Write-Host "  Modo estricto de Python 3.13: lo cumple."
        }
    }
    try {
        $desfase = Get-DesfaseReloj "$($s.Servidor):$($s.Puerto)"
        if ($null -eq $desfase) { Write-Host "  Reloj: no se pudo comparar con el servidor." }
        elseif ([Math]::Abs($desfase) -gt 300) { Write-Host "  RELOJ CORRIDO: este equipo esta $([Math]::Round($desfase / 60)) minutos $(if ($desfase -gt 0) { 'adelantado' } else { 'atrasado' }) respecto del servidor." }
        else { Write-Host "  Reloj: ok (diferencia $([Math]::Round($desfase)) s)." }
    } catch { Write-Host "  Reloj: no se pudo comparar ($($_.Exception.Message))." }
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
        Write-Host "TOTEM_ID: $($cred.TOTEM_ID)   API_URL: $($cred.API_URL)   PUSHER_CLUSTER: $($cred.PUSHER_CLUSTER)"
        Write-Host "credentials.json modificado: $((Get-Item $credFile).LastWriteTime)"
    } else {
        Write-Host "No existe $credFile (este usuario no tiene berry-monitor configurado)"
    }
} catch { Write-Host "ERROR leyendo credentials.json: $($_.Exception.Message)" }

# Variables y proxy que pueden cambiar como se conecta. berry-monitor (aiohttp)
# no usa el proxy de Windows; el websocket de Pusher si respeta HTTPS_PROXY.
try {
    $variables = @(foreach ($v in 'SSL_CERT_FILE', 'SSL_CERT_DIR', 'REQUESTS_CA_BUNDLE', 'CURL_CA_BUNDLE',
                                  'WEBSOCKET_CLIENT_CA_BUNDLE', 'HTTPS_PROXY', 'HTTP_PROXY', 'ALL_PROXY', 'NO_PROXY') {
        foreach ($ambito in 'User', 'Machine') {
            $val = [Environment]::GetEnvironmentVariable($v, $ambito)
            if ($val) { "$v ($ambito) = $val" }
        }
    })
    if ($variables) { Write-Host "Variables de entorno:"; $variables | ForEach-Object { Write-Host "  $_" } }
    else { Write-Host "Variables de entorno de certificados o proxy: ninguna." }
    $ie = Get-ItemProperty 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet Settings' -ErrorAction SilentlyContinue
    if ($ie -and $ie.ProxyEnable -eq 1) { Write-Host "Proxy de Windows: $($ie.ProxyServer) (berry-monitor no lo usa)" }
    if ($ie -and $ie.AutoConfigURL) { Write-Host "Configuracion automatica de proxy: $($ie.AutoConfigURL) (berry-monitor no la usa)" }
} catch { Write-Host "ERROR leyendo variables y proxy: $($_.Exception.Message)" }

Write-Host "`n== 1. berry-monitor en este equipo =="
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
    foreach ($k in 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run',
                   'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Run',
                   'HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Run') {
        $p = Get-ItemProperty $k -ErrorAction SilentlyContinue
        if (-not $p) { continue }
        foreach ($prop in $p.PSObject.Properties) {
            if ("$($prop.Value)" -match '"?([^"]*berry-monitor[^"]*?\.exe)') {
                $exes += [pscustomobject]@{ Ruta = [Environment]::ExpandEnvironmentVariables($Matches[1]); Origen = 'RUN' }
            }
        }
    }
} catch { }
try {
    Get-ScheduledTask -ErrorAction SilentlyContinue | ForEach-Object {
        foreach ($a in $_.Actions) {
            if ("$($a.Execute)" -match 'berry-monitor') {
                $exes += [pscustomobject]@{ Ruta = [Environment]::ExpandEnvironmentVariables("$($a.Execute)".Trim('"')); Origen = 'TAREA' }
            }
        }
    }
} catch { }
try {
    $noBuscar = 'AppData', 'Windows', 'Program Files', 'Program Files (x86)', 'ProgramData', 'Users', 'PerfLogs',
                'Recovery', '$Recycle.Bin', 'System Volume Information', '$WinREAgent', 'OneDriveTemp'
    $carpetas = @(Get-ChildItem $env:USERPROFILE -Directory -ErrorAction SilentlyContinue) +
                @(Get-ChildItem 'C:\' -Directory -ErrorAction SilentlyContinue) +
                @(Get-ChildItem 'C:\Program Files', 'C:\Program Files (x86)' -Directory -Filter '*berry*' -ErrorAction SilentlyContinue)
    foreach ($c in $carpetas) {
        if ($noBuscar -contains $c.Name -or ($c.Attributes -band [IO.FileAttributes]::ReparsePoint)) { continue }
        Get-ChildItem -LiteralPath $c.FullName -Filter 'berry-monitor*.exe' -File -Recurse -Depth 5 -ErrorAction SilentlyContinue |
            ForEach-Object { $exes += [pscustomobject]@{ Ruta = $_.FullName; Origen = 'disco' } }
    }
} catch { Write-Host "(No se pudo buscar el exe en el disco: $($_.Exception.Message))" }
$infoExes = @()
try {
    $grupos = @($exes | Where-Object { $_.Ruta -and $_.Ruta -match 'berry-monitor' -and (Test-Path -LiteralPath $_.Ruta) } | Group-Object Ruta)
    foreach ($g in $grupos) {
        $origen = ($g.Group | ForEach-Object { $_.Origen } | Where-Object { $_ -ne 'disco' } | Sort-Object -Unique) -join '+'
        if (-not $origen) { $origen = 'disco' }
        $infoExes += Get-InfoExe $g.Name $origen
    }
} catch { Write-Host "ERROR identificando los exes: $($_.Exception.Message)" }
if (-not $infoExes) { Write-Host "No se encontro berry-monitor.exe." }
else {
    Write-Host "(ABIERTO = corriendo ahora; INICIO/RUN/TAREA = arranca solo con Windows; disco = solo esta guardado)"
    Show-Tabla ($infoExes | Select-Object Origen, Ruta, Modificado, Version, Python, Truststore, Valida, Build)
}

$archivoConfigurado = $null
$carpetaCandidatos = $null
$pem = if ($cred) { "$($cred.SSL_CERT_FILE_PATH)".Trim() } else { '' }
try {
    if ($pem -and (Test-Path -LiteralPath $pem -PathType Leaf)) {
        $archivoConfigurado = Read-ArchivoCert $pem
        $carpetaCandidatos = Split-Path $pem -Parent
    } elseif ($pem -and (Test-Path -LiteralPath $pem -PathType Container)) {
        $carpetaCandidatos = $pem
    } elseif ($pem) {
        $padre = Split-Path $pem -Parent
        if ($padre -and (Test-Path -LiteralPath $padre -PathType Container)) { $carpetaCandidatos = $padre }
    }
} catch { }
$anclasArchivo = Get-AnclasArchivo $archivoConfigurado

# Si WEBSOCKET_CLIENT_CA_BUNDLE esta definida, el websocket de Pusher usa solo
# ese archivo y no mira el almacen de Windows.
$bundleWebsocket = [Environment]::GetEnvironmentVariable('WEBSOCKET_CLIENT_CA_BUNDLE', 'User')
if (-not $bundleWebsocket) { $bundleWebsocket = [Environment]::GetEnvironmentVariable('WEBSOCKET_CLIENT_CA_BUNDLE', 'Machine') }

Write-Host "`n== 2. Servidor de la API (metricas y health) =="
$api = $null
$errorServidor = $null
try {
    $uri = [uri]"$($cred.API_URL)"
    if (-not $cred -or $uri.Scheme -ne 'https') {
        $errorServidor = 'la API no es https: no hay cadena que revisar'
    } else {
        $api = Get-AnalisisServidor $uri.Host $(if ($uri.IsDefaultPort) { 443 } else { $uri.Port }) $anclasArchivo
        Show-Servidor $api $true
        if ($api.Error) { $errorServidor = $api.Error }
    }
} catch { $errorServidor = "$($_.Exception.Message)" }
if ($errorServidor -and -not $api) { Write-Host "No se pudo revisar: $errorServidor" }
$cadena = if ($api) { @($api.Cadena) } else { @() }

Write-Host "`n== 3. Servidor de Pusher (ordenes de arranque y parada) =="
$pusher = $null
try {
    $cluster = if ($cred) { "$($cred.PUSHER_CLUSTER)".Trim() } else { '' }
    $hostPusher = if ($cluster) { "ws-$cluster.pusher.com" } else { 'ws.pusherapp.com' }
    $anclasPusher = if ($bundleWebsocket) { Get-AnclasArchivo (Read-ArchivoCert $bundleWebsocket) } else { $anclasArchivo }
    $pusher = Get-AnalisisServidor $hostPusher 443 $anclasPusher
    if ($bundleWebsocket) {
        # Con esa variable, el almacen no cuenta: se vuelve a simular solo con el archivo.
        if ($pusher.Cadena) { $pusher.VeredictoAntes = $pusher.Veredicto = Test-Cadena $pusher.Cadena $anclasPusher }
        Write-Host "(WEBSOCKET_CLIENT_CA_BUNDLE definida: el websocket usa solo $bundleWebsocket)"
    }
    Show-Servidor $pusher $false
} catch { Write-Host "ERROR revisando Pusher: $($_.Exception.Message)" }

# Nombres que importan: sujetos y emisores de las dos cadenas.
$todasLasCadenas = @($cadena) + @(if ($pusher) { $pusher.Cadena })
$clavesCadena = @($todasLasCadenas | ForEach-Object { Get-Clave $_.SubjectName; Get-Clave $_.IssuerName } | Select-Object -Unique)
$clavesEmisores = @($todasLasCadenas | ForEach-Object { Get-Clave $_.IssuerName } | Select-Object -Unique)

Write-Host "`n== 4. Certificados de esas cadenas en el almacen de Windows =="
Write-Host "(Registrado = cuando se escribio el certificado en el registro)"
try {
    $relacionados = @(Get-Almacen | Where-Object { $clavesCadena -contains $_.Clave -or $_.Cert.Thumbprint -eq $huellaConocida })
    if (-not $relacionados) { Write-Host "Ninguno." }
    Show-Tabla ($relacionados | ForEach-Object {
        [pscustomobject]@{
            Almacen    = $_.Fuentes -join ', '
            Sujeto     = Get-Nombre $_.Cert
            Emisor     = Get-NombreEmisor $_.Cert
            Vence      = $_.Cert.NotAfter.ToString('yyyy-MM-dd')
            Estado     = if (Test-Vigente $_.Cert) { 'ok' } else { 'VENCIDO' }
            Origen     = Get-Origen $_.Cert.Thumbprint
            Registrado = Get-FechaRegistro $_.Fuentes $_.Cert.Thumbprint
            Huella     = $_.Cert.Thumbprint
        }
    })
} catch { Write-Host "ERROR en esta seccion: $($_.Exception.Message)" }

Write-Host "`n== 5. Archivo de SSL_CERT_FILE_PATH =="
try {
    Write-Host "SSL_CERT_FILE_PATH = $pem"
    if (-not $pem) {
        Write-Host "Vacio: berry-monitor usa solo el almacen de Windows."
    } elseif (Test-Path -LiteralPath $pem -PathType Container) {
        Write-Host "Es una CARPETA: berry-monitor necesita la ruta de un ARCHIVO; con una carpeta no carga nada."
    } elseif (-not $archivoConfigurado) {
        Write-Host "No existe: berry-monitor no carga nada y usa solo el almacen de Windows."
    } else {
        Write-Host "Archivo modificado: $((Get-Item -LiteralPath $pem).LastWriteTime)   Formato: $($archivoConfigurado.Formato)   Certificados: $($archivoConfigurado.Certs.Count)"
        if ($archivoConfigurado.Formato -ne 'PEM') {
            Write-Host "No es PEM: berry-monitor no carga nada de este archivo."
        } else {
            $certsArchivo = @($archivoConfigurado.Certs)
            $mostrar = if ($certsArchivo.Count -le 15) { $certsArchivo }
                       else { @($certsArchivo | Where-Object { $clavesCadena -contains (Get-Clave $_.SubjectName) -or -not (Test-Vigente $_) }) }
            if ($certsArchivo.Count -gt 15) {
                Write-Host "(Es un bundle grande: se listan solo los de estas cadenas y los vencidos.)"
            }
            Show-Tabla ($mostrar | ForEach-Object {
                [pscustomobject]@{
                    Sujeto       = Get-Nombre $_
                    Emisor       = Get-NombreEmisor $_
                    Vence        = $_.NotAfter.ToString('yyyy-MM-dd')
                    Estado       = if (Test-Vigente $_) { 'ok' } else { 'VENCIDO' }
                    'De la cadena' = if ($clavesCadena -contains (Get-Clave $_.SubjectName)) { 'si' } else { '' }
                }
            })
        }
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
            Write-Host "(Aporta la raiz = el archivo solo alcanza para validar la API, sin contar el almacen)"
            Show-Tabla ($candidatos | ForEach-Object {
                $aporta = if ($_.Formato -ne 'PEM') { 'no (no es PEM)' }
                          elseif (-not $cadena) { '?' }
                          elseif ((Test-Cadena $cadena (Get-AnclasArchivo $_)).Resultado -eq 'OK') { 'SI' }
                          else { 'no' }
                [pscustomobject]@{
                    Archivo          = $_.Ruta
                    Formato          = $_.Formato
                    Certificados     = $_.Certs.Count
                    'Aporta la raiz' = $aporta
                }
            })
        } else {
            Write-Host "No hay archivos .pem, .crt ni .cer en $carpetaCandidatos."
        }
    }
} catch { Write-Host "ERROR buscando archivos de certificados: $($_.Exception.Message)" }

$recomendado = $null
if ($api -and $api.Cadena -and $api.Veredicto.Resultado -ne 'OK') {
    $almacenAhora = @(Get-Almacen)
    $recomendado = $candidatos |
        Where-Object { $_.Formato -eq 'PEM' -and (Test-Cadena $cadena ($almacenAhora + (Get-AnclasArchivo $_))).Resultado -eq 'OK' } |
        Select-Object -First 1
}

Write-Host "`n== 6. Que va a pasar con cada berry-monitor =="
if ($infoExes -and $api -and $api.Cadena) {
    Show-Tabla ($infoExes | ForEach-Object {
        [pscustomobject]@{
            Origen = $_.Origen
            Ruta   = $_.Ruta
            Valida = $_.Valida
            API    = Get-Esperado $_.Valida $api $true
            Pusher = Get-Esperado $_.Valida $pusher $false
        }
    })
} elseif (-not $infoExes) {
    Write-Host "No hay berry-monitor para evaluar."
} else {
    Write-Host "Sin la cadena de la API no se puede evaluar."
}
Write-Host "Referencia por tipo de exe:"
Write-Host "  OpenSSL (Python 3.11) ......... API: $(Get-Esperado 'OpenSSL' $api $true)   Pusher: $(Get-Esperado 'OpenSSL' $pusher $false)"
Write-Host "  OpenSSL estricto (Python 3.13)  API: $(Get-Esperado 'OpenSSL estricto' $api $true)   Pusher: $(Get-Esperado 'OpenSSL estricto' $pusher $false)"
Write-Host "  Windows (version con truststore) API: $(Get-Esperado 'Windows (truststore)' $api $true)   Pusher: $(Get-Esperado 'Windows (truststore)' $pusher $false)"

Write-Host "`n== 7. Actualizaciones de Windows de los ultimos 7 dias =="
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
$patronErrorTls = 'CERTIFICATE_VERIFY_FAILED|SSLCertVerificationError|certificate verify failed'

Write-Host "`n== 8. Log de berry-monitor =="
try {
    $archivosLog = @(Get-ChildItem "$logBerry*" -ErrorAction SilentlyContinue | Sort-Object LastWriteTime)
    if (-not $archivosLog) {
        Write-Host "No hay log: este berry-monitor no lo escribe (el log a disco esta desde el build del 2026-09-04)."
    } else {
        $lineas = @($archivosLog | ForEach-Object { Get-Content $_.FullName -Encoding UTF8 -ErrorAction SilentlyContinue })
        $errTls = @($lineas | Where-Object { $_ -match $patronErrorTls })
        $oks = @($lineas | Where-Object { Test-LineaOk $_ })
        Write-Host "Lineas: $($lineas.Count)   Errores de certificado: $($errTls.Count)"
        $corte = { param($l) $l.Substring(0, [Math]::Min(220, $l.Length)) }
        if ($errTls) {
            Write-Host "Primer error:  $(& $corte $errTls[0])"
            Write-Host "Ultimo error:  $(& $corte $errTls[-1])"
        }
        if ($oks) { Write-Host "Ultima conexion OK: $(& $corte $oks[-1])" }
        $builds = @($lineas | Where-Object { $_ -match '\[BUILD\]' })
        if ($builds) { Write-Host "Build segun el log: $(& $corte $builds[-1])" }
    }
} catch { Write-Host "ERROR en esta seccion: $($_.Exception.Message)" }

# ============================================================== reparacion ===

# Vencidos del almacen que pueden romper la validacion: los que tienen el
# sujeto de un emisor de las cadenas, y siempre el cruce conocido.
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
        if ($nuevas | Where-Object { $_ -match $patronErrorTls }) { return 'SSL' }
        if ($nuevas | Where-Object { Test-LineaOk $_ }) { return 'OK' }
    }
    return 'SIN DATOS'
}

Write-Host "`n== 9. Reparacion =="
$resultado = 'NADA QUE BORRAR'
$rutas = @()
$vencidos = Get-Vencidos
if (-not $vencidos) {
    Write-Host "No hay certificados vencidos de estas cadenas en el almacen: no hay nada que borrar."
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

        $desde = if (Test-Path $logBerry) { @(Get-Content $logBerry -Encoding UTF8 -ErrorAction SilentlyContinue).Count } else { 0 }
        foreach ($ruta in $rutas) {
            try {
                Start-Process -FilePath $ruta -WorkingDirectory (Split-Path $ruta) -ErrorAction Stop
                Write-Host "berry-monitor abierto de nuevo: $ruta"
            } catch { Write-Host "(No se pudo abrir $ruta : $($_.Exception.Message))" }
        }
    }
}

# Lo que queda despues de reparar: borrar puede no alcanzar.
if ($api -and $api.Cadena -and $resultado -eq 'LISTO') {
    try {
        $almacenFinal = @(Get-Almacen)
        $api.Veredicto = Test-Cadena $api.Cadena ($almacenFinal + $anclasArchivo)
        $api.VeredictoAntes = $api.Veredicto
        $api.Estricto = @(Get-ProblemasEstricto (Get-Camino $api.Cadena $api.Veredicto))
    } catch { }
}
$consejoRaiz = if ($recomendado) {
    "Configurar SSL_CERT_FILE_PATH = $($recomendado.Ruta) (con berry-configure, o en credentials.json escribiendo las barras dobles) y volver a abrir berry-monitor."
} elseif ($api -and $api.Veredicto) {
    "Conseguir un archivo .pem que incluya alguna de: $($api.Veredicto.Faltan -join ', ') (por ejemplo, el cacert.pem de certifi), configurarlo en SSL_CERT_FILE_PATH y volver a abrir berry-monitor."
}

# Conclusion sobre las conexiones, aparte de la reparacion. Tipo 'OK' = no se
# encontro nada que explique un error de certificado.
function Get-Conclusion {
    if (-not $api -or -not $api.Cadena) {
        return [pscustomobject]@{ Tipo = 'SIN DATOS'; Lineas = @("No se pudo revisar el servidor de la API: $errorServidor", "Mandar este reporte a soporte.") }
    }
    $l = @()
    $h = $api.Servidor
    if ($api.Veredicto.Resultado -eq 'FALTA') {
        $l += "FALTA LA RAIZ del servidor en este equipo."
        $l += $consejoRaiz
    } elseif ($api.Veredicto.Resultado -eq 'VENCIDO') {
        $l += "El archivo de SSL_CERT_FILE_PATH tiene un certificado vencido de la cadena."
        $l += "Configurar otro archivo que no lo tenga y volver a abrir berry-monitor."
    } elseif ($api.VeredictoAntes.Resultado -ne 'OK') {
        $l += "Windows agrego durante la revision la raiz que faltaba."
        $l += "Cerrar y volver a abrir berry-monitor (sin reiniciar la PC)."
    } elseif (-not $api.NombreOk) {
        $l += "El certificado que recibe este equipo no corresponde a $h."
        $l += "Mandar este reporte a soporte."
    } elseif ($api.Estricto) {
        $motivo = ($api.Estricto[0] -split ': ', 2)[1]
        $l += "El certificado que recibe este equipo para $h no cumple el modo estricto de Python 3.13: $motivo."
        if ($api.Pista) { $l += "Lo genera $($api.Pista), que inspecciona el HTTPS de esta red." }
        else { $l += "Suele pasar cuando un firewall o antivirus inspecciona el HTTPS." }
        if ((Get-Esperado 'Windows (truststore)' $api $true) -eq 'OK') {
            $l += "Fallan los berry-monitor con Python 3.13; funcionan los de Python 3.11 y la version con truststore."
            $l += "Solucion: pedir a sistemas que excluyan $h de la inspeccion SSL, o instalar la version con truststore."
        } else {
            $l += "Fallan los berry-monitor con Python 3.13; funcionan los de Python 3.11. La version con truststore tambien fallaria: Windows no confia en la raiz de esa cadena."
            $l += "Solucion: pedir a sistemas que excluyan $h de la inspeccion SSL (o que instalen su raiz en Windows y usar la version con truststore)."
        }
        $sirven = @($infoExes | Where-Object { (Get-Esperado $_.Valida $api $true) -eq 'OK' })
        foreach ($e in $sirven) { $l += "En este equipo funcionaria: $($e.Ruta) (Python $($e.Python))" }
    }
    if (-not $l -and $pusher -and $pusher.Cadena -and (Get-Esperado 'OpenSSL' $pusher $false) -ne 'OK') {
        $l += "La API valida bien, pero la conexion a Pusher (ordenes) fallaria: $(Get-Esperado 'OpenSSL' $pusher $false)."
        $l += "Mandar este reporte a soporte."
    }
    if (-not $l) {
        return [pscustomobject]@{ Tipo = 'OK'; Lineas = @("El almacen y la configuracion alcanzan para validar la cadena: el problema es otro.", "Mandar este reporte a soporte.") }
    }
    [pscustomobject]@{ Tipo = 'PROBLEMA'; Lineas = $l }
}
$conclusion = Get-Conclusion

Write-Host "`n== 10. Verificacion =="
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
            if ($conclusion.Tipo -eq 'PROBLEMA') { $conclusion.Lineas | ForEach-Object { Write-Host " $_" } }
            else { Write-Host " Mandar este reporte a soporte." }
        } elseif ($conclusion.Tipo -eq 'PROBLEMA') {
            Write-Host " Se borraron los certificados vencidos, pero ademas:"
            $conclusion.Lineas | ForEach-Object { Write-Host " $_" }
        } else {
            Write-Host " LISTO. El totem quedo corregido. NO reiniciar la PC."
        }
    }
    'FALTA' {
        Write-Host " FALTA: hace falta un usuario administrador para terminar."
        Write-Host " Repetir con clic derecho en Windows PowerShell > Ejecutar como administrador."
    }
    'CANCELADO' { Write-Host " Cancelado: no se cambio nada." }
    default { $conclusion.Lineas | ForEach-Object { Write-Host " $_" } }
}
Write-Host " Reporte: $reporte"
Write-Host "================================================================"
try { Stop-Transcript | Out-Null } catch { }
Start-Process notepad.exe $reporte
