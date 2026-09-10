# Reparacion del error "certificate has expired" de berry-monitor.exe (hasta 1.0.6)
#
# Esas versiones validan TLS con OpenSSL, usando como anclas de confianza una
# copia de los almacenes ROOT y CA de Windows mas el archivo de
# SSL_CERT_FILE_PATH. Si ahi esta el cruce viejo "ISRG Root X2" firmado por
# "ISRG Root X1" (vencido el 2025-09-15), falla con "certificate has expired"
# aunque el navegador e Invoke-WebRequest conecten bien.
#
# En un solo paso:
#   1. Analiza el equipo: exe instalado, certificados, SSL_CERT_FILE_PATH,
#      actualizaciones y el log de berry-monitor. Todo sale del propio equipo.
#   2. Si encuentra el certificado vencido, pide confirmacion, lo respalda en el
#      Escritorio, cierra berry-monitor, lo borra (si hay una copia a nivel
#      maquina pide permiso de administrador) y vuelve a abrir berry-monitor.
#   3. Verifica en el log de berry-monitor que la conexion ande.
# Si no encuentra el certificado no cambia nada: el reporte sirve para soporte.
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
$sujetos = 'ISRG Root X1', 'ISRG Root X2', 'Root YE', 'YE1', 'YE2'
$huellaVencida = '151682F5218C0A511C28F4060A73B9CA78CE9A53'
$almacenes = 'Cert:\CurrentUser\CA', 'Cert:\CurrentUser\Root', 'Cert:\LocalMachine\CA', 'Cert:\LocalMachine\Root'
$logBerry = Join-Path $env:APPDATA 'BerryMed Monitor\logs\berry-monitor.log'
$sinPreguntar = $env:REPARAR_SSL_SIN_PREGUNTAR -eq '1'
$buildsConocidos = @{
    '217EE7B97A3629C2F7ED9F0C3E664555B2951B6020C55A80C3A6EE60F8240BE0' = '1.0.5 / 1.0.6 (mismo build, 2026-05-26)'
}

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

# Busca en todos los lugares de donde Windows arma el almacen logico. El
# almacen Root muestra tambien los de AuthRoot (raices bajadas por Windows Update).
function Get-FechaRegistro($alm, $huella) {
    if (-not $hayFechas) { return '?' }
    $nombres = @($alm)
    if ($alm -eq 'Root') { $nombres += 'AuthRoot' }
    $raices = 'HKCU:\Software\Microsoft\SystemCertificates',
              'HKCU:\Software\Policies\Microsoft\SystemCertificates',
              'HKLM:\SOFTWARE\Microsoft\SystemCertificates',
              'HKLM:\SOFTWARE\Policies\Microsoft\SystemCertificates',
              'HKLM:\SOFTWARE\Microsoft\EnterpriseCertificates'
    $z = [IntPtr]::Zero
    foreach ($a in $nombres) {
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

Write-Host "`n== 1. Cadena de Let's Encrypt en los almacenes de Windows =="
Write-Host "(Registrado = cuando se escribio el certificado en el registro)"
try {
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
} catch { Write-Host "ERROR en esta seccion: $($_.Exception.Message)" }

Write-Host "== 2. Archivo configurado en SSL_CERT_FILE_PATH =="
try {
    if (-not $cred) {
        Write-Host "Sin credentials.json: nada que revisar."
    } else {
        Write-Host "credentials.json modificado: $((Get-Item $credFile).LastWriteTime)"
        $pem = $cred.SSL_CERT_FILE_PATH
        Write-Host "SSL_CERT_FILE_PATH = $pem"
        if (-not $pem -or -not (Test-Path $pem)) {
            Write-Host "El archivo no existe (OpenSSL lo ignora en silencio)."
        } elseif (Test-Path $pem -PathType Container) {
            Write-Host "Es una CARPETA, no un archivo: OpenSSL no carga nada de ahi."
        } else {
            Write-Host "Archivo modificado: $((Get-Item $pem).LastWriteTime)"
            $certs = New-Object System.Collections.Generic.List[object]
            $bloques = [regex]::Matches((Get-Content $pem -Raw),
                '-----BEGIN CERTIFICATE-----[\s\S]+?-----END CERTIFICATE-----')
            foreach ($b in $bloques) {
                $b64 = ($b.Value -replace '-----(BEGIN|END) CERTIFICATE-----', '') -replace '\s', ''
                $certs.Add((New-Object System.Security.Cryptography.X509Certificates.X509Certificate2 -ArgumentList (,[Convert]::FromBase64String($b64))))
            }
            if ($certs.Count -eq 0) {
                try {
                    $certs.Add((New-Object System.Security.Cryptography.X509Certificates.X509Certificate2 -ArgumentList $pem))
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
} catch { Write-Host "ERROR en esta seccion: $($_.Exception.Message)" }

Write-Host "== 3. Actualizaciones de Windows de los ultimos 7 dias =="
try {
    $buscador = (New-Object -ComObject Microsoft.Update.Session).CreateUpdateSearcher()
    $buscador.QueryHistory(0, [Math]::Min($buscador.GetTotalHistoryCount(), 100)) |
        Where-Object { $_.Date -gt $ahora.AddDays(-7) -and
                       $_.Title -notmatch 'Security Intelligence Update|inteligencia de seguridad|^9N' } |
        Sort-Object Date |
        ForEach-Object { '{0:yyyy-MM-dd HH:mm}  {1}' -f $_.Date.ToLocalTime(), $_.Title }
} catch {
    try {
        Get-HotFix | Where-Object { $_.InstalledOn -and $_.InstalledOn -gt $ahora.AddDays(-7) } |
            Sort-Object InstalledOn | Format-Table HotFixID, Description, InstalledOn -AutoSize
    } catch { Write-Host "ERROR en esta seccion: $($_.Exception.Message)" }
}

# Una linea [HEALTH] que no sea un error prueba que la conexion TLS anduvo,
# aunque el backend conteste otra cosa: el certificado ya se valido.
function Test-LineaOk($l) {
    ($l -match '\[HEALTH\]' -and $l -notmatch 'No se pudo reportar|Desactivado') -or
        $l -match 'Vital signs sent successfully'
}

Write-Host "`n== 4. Log de berry-monitor =="
try {
    $archivosLog = @(Get-ChildItem "$logBerry*" -ErrorAction SilentlyContinue | Sort-Object LastWriteTime)
    if (-not $archivosLog) {
        Write-Host "No hay log (las versiones 1.0.5 / 1.0.6 no lo escriben)."
    } else {
        $lineas = @($archivosLog | ForEach-Object { Get-Content $_.FullName -ErrorAction SilentlyContinue })
        $errSsl = @($lineas | Where-Object { $_ -match 'certificate has expired' })
        $oks = @($lineas | Where-Object { Test-LineaOk $_ })
        Write-Host "Lineas: $($lineas.Count)   Errores de certificado vencido: $($errSsl.Count)"
        $corte = { param($l) $l.Substring(0, [Math]::Min(150, $l.Length)) }
        if ($errSsl) {
            Write-Host "Primer error:  $(& $corte $errSsl[0])"
            Write-Host "Ultimo error:  $(& $corte $errSsl[-1])"
        }
        if ($oks) { Write-Host "Ultima conexion OK: $(& $corte $oks[-1])" }
    }
} catch { Write-Host "ERROR en esta seccion: $($_.Exception.Message)" }

function Get-Vencidos {
    @(Get-ChildItem $almacenes -ErrorAction SilentlyContinue | Where-Object Thumbprint -eq $huellaVencida)
}

# Espera a que berry-monitor escriba en su log una conexion buena o el error.
function Wait-Resultado($log, $desde, $segundos) {
    $limite = (Get-Date).AddSeconds($segundos)
    while ((Get-Date) -lt $limite) {
        Start-Sleep -Seconds 3
        if (-not (Test-Path $log)) { continue }
        $todas = @(Get-Content $log -ErrorAction SilentlyContinue)
        if ($todas.Count -lt $desde) { $desde = 0 }   # el log roto mientras tanto
        $nuevas = @($todas | Select-Object -Skip $desde)
        if ($nuevas | Where-Object { $_ -match 'certificate has expired|CERTIFICATE_VERIFY_FAILED' }) { return 'SSL' }
        if ($nuevas | Where-Object { Test-LineaOk $_ }) { return 'OK' }
    }
    return 'SIN DATOS'
}

Write-Host "`n== 5. Reparacion =="
$resultado = 'NO ESTA'
$rutas = @()
$vencidos = Get-Vencidos
if (-not $vencidos) {
    Write-Host "Este equipo NO tiene el certificado vencido: no hay nada que borrar."
} else {
    $donde = ($vencidos | ForEach-Object { ($_.PSParentPath -split '::')[-1] } | Sort-Object -Unique) -join ', '
    Write-Host "Certificado vencido encontrado en: $donde"
    $procesos = @(Get-Process -Name 'berry-monitor' -ErrorAction SilentlyContinue)
    $rutas = @($procesos | ForEach-Object { $_.Path } | Where-Object { $_ } | Sort-Object -Unique)
    Write-Host "Se va a guardar un respaldo en el Escritorio y borrar el certificado vencido."
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
        $bk = Join-Path $escritorio 'respaldo-isrg-x2-vencido.cer'
        try {
            Export-Certificate -Cert $vencidos[0] -FilePath $bk -ErrorAction Stop | Out-Null
            Write-Host "Respaldo guardado en: $bk"
        } catch { Write-Host "(No se pudo guardar el respaldo: $($_.Exception.Message))" }

        foreach ($p in $procesos) {
            try {
                Stop-Process -Id $p.Id -Force -ErrorAction Stop
                Write-Host "berry-monitor cerrado (PID $($p.Id))."
            } catch { Write-Host "(No se pudo cerrar berry-monitor PID $($p.Id): $($_.Exception.Message))" }
        }

        # Si Windows pregunta por borrar un certificado de la raiz, hay que responder Si.
        foreach ($c in $vencidos) { Remove-Item $c.PSPath -ErrorAction SilentlyContinue }

        if (Get-Vencidos) {
            # Queda la copia de toda la maquina: se borra en una PowerShell
            # elevada, que abre el aviso de Windows pidiendo permiso.
            Write-Host "Queda una copia a nivel maquina: Windows va a pedir permiso de administrador."
            $cmd = "Get-ChildItem 'Cert:\LocalMachine\CA','Cert:\LocalMachine\Root' | " +
                   "Where-Object Thumbprint -eq '$huellaVencida' | Remove-Item"
            $enc = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($cmd))
            try {
                Start-Process powershell.exe -Verb RunAs -Wait -ErrorAction Stop `
                    -ArgumentList '-NoProfile', '-EncodedCommand', $enc
            } catch { Write-Host "No se obtuvo el permiso de administrador: $($_.Exception.Message)" }
        }

        $quedan = Get-Vencidos
        if ($quedan) {
            $resultado = 'FALTA'
            $donde = ($quedan | ForEach-Object { ($_.PSParentPath -split '::')[-1] } | Sort-Object -Unique) -join ', '
            Write-Host "FALTA: el certificado sigue en $donde."
        } else {
            $resultado = 'LISTO'
            Write-Host "LISTO: certificado vencido eliminado."
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

Write-Host "`n== 6. Verificacion =="
$verificado = $null
if ($resultado -eq 'LISTO' -and $rutas) {
    Write-Host "Esperando hasta 90 segundos a que berry-monitor se conecte..."
    $verificado = Wait-Resultado $logBerry $desde 90
    switch ($verificado) {
        'OK'  { Write-Host "OK: berry-monitor se conecto sin error de certificado." }
        'SSL' { Write-Host "ATENCION: berry-monitor sigue con error de certificado." }
        default {
            Write-Host "No se pudo confirmar desde el log (esta version puede no escribirlo)."
            Write-Host "Verificar a mano: iniciar una medicion y buscar 'Vital signs sent successfully'."
        }
    }
} elseif ($resultado -eq 'LISTO') {
    Write-Host "berry-monitor no estaba abierto: abrirlo como siempre y verificar que no aparezca 'certificate has expired'."
} else {
    Write-Host "Nada que verificar."
}

Write-Host "`n================================================================"
switch ($resultado) {
    'LISTO' {
        if ($verificado -eq 'SSL') {
            Write-Host " Se borro el certificado vencido, pero el error sigue."
            Write-Host " Mandar este reporte a soporte."
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
        Write-Host " Este totem no tiene el certificado vencido: el problema es otro."
        Write-Host " Mandar este reporte a soporte."
    }
}
Write-Host " Reporte: $reporte"
Write-Host "================================================================"
try { Stop-Transcript | Out-Null } catch { }
Start-Process notepad.exe $reporte
