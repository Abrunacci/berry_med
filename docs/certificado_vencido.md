# Tótems con error de certificado — Qué hacer cuando berry-monitor no puede conectarse

Procedimiento para los tótems donde berry-monitor no envía datos y muestra un
error de certificado (`CERTIFICATE_VERIFY_FAILED`). Se hace una vez por tótem,
lleva unos 5 minutos y no hace falta instalar nada ni reiniciar la PC.

> **Resumen:** guardar `reparar_ssl.ps1` en el Escritorio, pegar en PowerShell
> las dos líneas del Paso 3 y seguir lo que diga el recuadro final. Si
> encuentra certificados vencidos, pregunta antes de borrarlos. **No reiniciar
> la PC.**

## 1. Cómo reconocer el problema

En la ventana de berry-monitor (la ventana negra), o en su registro, aparece
un error `CERTIFICATE_VERIFY_FAILED` con alguno de estos textos:

```text
certificate verify failed: certificate has expired
certificate verify failed: unable to get local issuer certificate
certificate verify failed: Missing Authority Key Identifier
certificate verify failed: Basic Constraints of CA cert not marked critical
```

Según la versión de berry-monitor, el error sale en una de estas líneas:

| Línea | Cuándo aparece |
|---|---|
| `[HEALTH] No se pudo reportar: ...` | Cada minuto, haya o no una medición en curso. Sólo en las versiones con reporte de estado. |
| `[ERROR] Failed to send data after 3 attempts: ...` | Al enviar los datos de una medición. En todas las versiones. |

Otra señal típica: en ese mismo equipo, el navegador entra al portal sin
problemas.

Si el error es otro (por ejemplo `Cannot connect to host` sin
`CERTIFICATE_VERIFY_FAILED`, o `API returned status`), igual se puede correr
el script: si no encuentra un problema de certificados no cambia nada, y deja
un reporte para soporte.

## 2. Por qué pasa

berry-monitor valida el certificado del servidor con un mecanismo propio, que
no es el del navegador. Para confiar en el servidor usa dos cosas del tótem:
los certificados guardados en Windows y el archivo configurado en
`SSL_CERT_FILE_PATH` (en `credentials.json`). Hay tres formas de que eso falle.

### 2.1 Un certificado vencido guardado en Windows

Error: `certificate has expired`. Windows guarda una copia vieja de un
certificado intermedio que ya venció. El navegador la descarta y usa la
vigente; berry-monitor, en cambio, la toma y rechaza la conexión.

El caso conocido es un certificado de Let's Encrypt, la entidad que firma los
certificados de `ondoctor365.com`, vencido el **15/09/2025**:

| Campo | Valor |
|---|---|
| Emitido para | `ISRG Root X2` |
| Emitido por | `ISRG Root X1` |
| Válido hasta | 15/09/2025 |
| Huella digital (SHA-1) | `151682F5218C0A511C28F4060A73B9CA78CE9A53` |

La solución es borrarlo. No afecta a ningún otro programa: al estar vencido,
ninguno puede usarlo. Igual se guarda un respaldo en el Escritorio.

### 2.2 Falta la raíz del servidor

Error: `unable to get local issuer certificate`. Ni Windows ni el archivo de
`SSL_CERT_FILE_PATH` tienen el certificado raíz que respalda al servidor.
Pasa sobre todo en dos situaciones:

- `SSL_CERT_FILE_PATH` apunta a una **carpeta** en vez de a un **archivo**.
  berry-monitor necesita la ruta completa de un archivo `.pem` (por ejemplo
  `C:\BerryMed\cacert.pem`). Con una carpeta no carga nada: ninguna versión la
  usa como carpeta.
- El tótem trabaja con un servidor cuya raíz no está en ese Windows. No todos
  los servidores usan la misma entidad: `ondoctor365.com` usa Let's Encrypt y
  `ondoctor365-sanmiguel.com.ar` usa Google Trust Services / GlobalSign.

La solución es configurar en `SSL_CERT_FILE_PATH` un archivo `.pem` que traiga
esa raíz. El script dice cuál (sección 5).

### 2.3 Un firewall inspecciona el HTTPS

Error: `Missing Authority Key Identifier`,
`Basic Constraints of CA cert not marked critical` u otro parecido. En algunos
sitios, un firewall o un antivirus (por ejemplo, un FortiGate) intercepta las
conexiones HTTPS y las vuelve a firmar con un certificado raíz propio, que el
área de sistemas instala en las PCs. El navegador lo acepta, y berry-monitor
también, salvo por un detalle: las versiones generadas con **Python 3.13**
(todas las de 2026, incluidas 1.0.5 y 1.0.6) validan en **modo estricto** y
rechazan los certificados que esos equipos suelen generar. Las versiones de
2025 (Python 3.11) no hacen ese chequeo y funcionan. Por eso, en esos sitios,
"una versión anda y la otra no".

Soluciones, de mejor a peor:

- Pedir al área de sistemas del sitio que **excluya el servidor de
  berry-monitor de la inspección SSL** (por ejemplo,
  `api.ondoctor365-sanmiguel.com.ar`). Es lo habitual para equipos que se
  conectan a una API, y no hay que tocar el tótem.
- Instalar la versión de berry-monitor con truststore (sección 10), que valida
  con Windows y no aplica ese modo estricto.
- Mientras tanto, usar el berry-monitor que el reporte indica que funciona
  (sección 6 del reporte).

## 3. Antes de empezar

- Tener el archivo `reparar_ssl.ps1`. Lo envía soporte.
- Hacerlo **en el tótem**, con el usuario de Windows que usa berry-monitor
  todos los días.
- Que **no haya una medición en curso**: si hay certificados para borrar, el
  script cierra berry-monitor y lo vuelve a abrir.
- **No reiniciar la PC** en ningún momento. Si por algún motivo se reinicia y
  el Berry está conectado por USB, hay que apagar y volver a prender el Berry
  (cortándole la alimentación): después de un reinicio de Windows queda sin
  responder hasta que se lo apaga.

## 4. Procedimiento

### Paso 1 — Guardar el archivo en el Escritorio

Guardar `reparar_ssl.ps1` en el Escritorio con **ese nombre exacto**. Si al
descargarlo quedó con otro nombre (por ejemplo `reparar_ssl (1).ps1`),
renombrarlo.

### Paso 2 — Abrir PowerShell

Abrir el menú Inicio, escribir **PowerShell** y abrir **Windows PowerShell**.
Se abre una ventana azul. No hace falta abrirlo como administrador.

### Paso 3 — Pegar el comando

Copiar estas dos líneas, pegarlas en la ventana azul (con `Ctrl+V` o con clic
derecho) y apretar Enter:

```powershell
$f = [Environment]::GetFolderPath('Desktop') + '\reparar_ssl.ps1'
Get-Content -LiteralPath $f -Raw | Invoke-Expression
```

El script revisa el equipo y los servidores durante unos segundos y va
mostrando lo que encuentra.

### Paso 4 — Confirmar, si lo pide

Si encuentra certificados vencidos para borrar, avisa lo que va a hacer y
pregunta:

```text
Escribir S y apretar Enter para continuar (cualquier otra cosa cancela)
```

Escribir `S` y apretar Enter. Durante la corrección pueden aparecer dos
avisos de Windows:

- Si pregunta si se quiere **eliminar un certificado de la raíz**: responder
  **Sí**.
- Si pide **permiso de administrador**: aceptar. Si el usuario del tótem no es
  administrador, Windows pide la contraseña de uno.

Después vuelve a abrir berry-monitor y espera hasta un minuto y medio a que se
conecte. Si no hay nada que borrar, no pregunta nada.

### Paso 5 — Leer el resultado

Al terminar, el script muestra el resultado en un recuadro y abre un Bloc de
notas con el reporte completo. El reporte queda en el Escritorio como
`reparacion_ssl_<nombre del equipo>.txt`.

| Mensaje | Qué significa | Qué hacer |
|---|---|---|
| "LISTO. El totem quedo corregido." | Se borraron los certificados vencidos y berry-monitor conectó (o no había forma de confirmarlo, ver sección 7). | Nada más. No reiniciar la PC. |
| "FALTA LA RAIZ del servidor en este equipo." | Caso 2.2. En la línea de abajo el script dice qué hacer, por ejemplo "Configurar SSL_CERT_FILE_PATH = C:\...\cacert.pem". | Hacerlo como explica la sección 5 y volver a abrir berry-monitor. |
| "El certificado que recibe este equipo para ... no cumple el modo estricto de Python 3.13: ..." | Caso 2.3. Debajo dice quién lo genera y qué berry-monitor de este equipo funcionaría. | Pedir a sistemas del sitio que excluyan el servidor de la inspección SSL. Mientras tanto, usar el berry-monitor que indica el reporte. |
| "Se borraron los certificados vencidos, pero ademas:" | Había más de un problema: debajo sigue el otro. | Según el mensaje que sigue. |
| "Windows agrego durante la revision la raiz que faltaba." | Al conectarse para revisar, Windows descargó solo la raíz que faltaba. | Cerrar y volver a abrir berry-monitor. |
| "FALTA: hace falta un usuario administrador para terminar." | Queda una copia a nivel máquina y no se pudo obtener el permiso. | Repetir desde el Paso 2, con clic derecho sobre Windows PowerShell > **Ejecutar como administrador**. |
| "El archivo de SSL_CERT_FILE_PATH tiene un certificado vencido de la cadena." | El archivo configurado trae un certificado vencido que berry-monitor toma. | Configurar otro archivo (sección 5). |
| "El certificado que recibe este equipo no corresponde a ..." | Llega el certificado de otro nombre (DNS o equipo intermedio mal configurado). | Mandar el reporte a soporte. |
| "La API valida bien, pero la conexion a Pusher (ordenes) fallaria: ..." | Las órdenes de arranque y parada no llegarían. | Mandar el reporte a soporte. |
| "Se borraron los certificados vencidos, pero el error sigue." | El problema tenía otra causa además de esta. | Mandar el reporte a soporte. |
| "El almacen y la configuracion alcanzan para validar la cadena: el problema es otro." | No es un problema de certificados del tótem. | Mandar el reporte a soporte. |
| "No se pudo revisar el servidor de la API: ..." | No se pudo conectar al servidor (red, dirección mal escrita). | Mandar el reporte a soporte. |
| "Cancelado: no se cambio nada." | No se escribió `S` en el Paso 4. | Repetir si se quiere corregir. |

Si en la ventana azul aparece texto en rojo, sacarle una foto a la ventana y
mandarla a soporte junto con el reporte.

## 5. Cómo cambiar SSL_CERT_FILE_PATH

Sólo cuando el script lo indica. Tiene que quedar la ruta completa de un
**archivo**, no de una carpeta.

- Con **berry-configure.exe**: en el campo del certificado, elegir el archivo
  que indicó el script y guardar.
- O a mano: tocar Windows+R, pegar `%APPDATA%\BerryMed Monitor`, abrir
  `credentials.json` con el Bloc de notas y cambiar el valor de
  `SSL_CERT_FILE_PATH`. Las barras van **dobles**:

```text
"SSL_CERT_FILE_PATH": "C:\\Users\\totem\\Downloads\\berry-final\\cacert.pem",
```

Después cerrar y volver a abrir berry-monitor, que lee la configuración al
arrancar. Para confirmar, se puede correr el script de nuevo.

## 6. Qué hace el script

Para que se sepa exactamente qué toca:

1. **Revisa el equipo**: los berry-monitor que hay (con qué versión de Python
   fueron generados, si traen truststore y cuál arranca solo con Windows), la
   configuración, las variables de entorno y el proxy.
2. **Revisa los dos servidores** con los que habla berry-monitor —la API y
   Pusher, por donde llegan las órdenes—: la cadena de certificados que llega
   a este equipo, si hay un equipo que inspecciona el HTTPS, qué certificados
   de esa cadena hay en Windows y de dónde salieron, el archivo de
   `SSL_CERT_FILE_PATH` (y, si es una carpeta, los certificados que tiene
   adentro), el modo estricto y el reloj.
3. **Dice qué va a pasar con cada berry-monitor** encontrado.
4. Anota las actualizaciones de Windows de la última semana y los errores del
   registro de berry-monitor. Todo lo anterior sólo lee.
5. Si hay **certificados vencidos** de esas cadenas guardados en Windows y se
   confirma con `S`: guarda un respaldo de cada uno en el Escritorio, cierra
   berry-monitor, borra **únicamente** esos certificados, lo vuelve a abrir y
   mira el registro hasta que aparezca una conexión buena o el error.
6. Para lo demás (falta la raíz, firewall que inspecciona), dice qué hacer.

No modifica `credentials.json` ni ningún otro certificado.

Lo más útil del reporte para soporte:

- **Sección 1:** qué berry-monitor hay, con qué Python y cuál arranca solo.
- **Secciones 2 y 3:** la cadena que llega a este equipo, si hay inspección
  HTTPS (`INSPECCION HTTPS: ...`) y si cumple el modo estricto.
- **Sección 6:** qué va a pasar con cada berry-monitor, con la API y con Pusher.

## 7. Cómo confirmar que quedó bien

El script lo confirma solo en las versiones de berry-monitor que escriben
registro. Las versiones instaladas en la mayoría de los tótems no lo escriben,
y el reporte lo avisa. En ese caso, iniciar una medición desde el portal: en
la ventana de berry-monitor tiene que aparecer
`[DATA] Vital signs sent successfully`.

En cualquier versión, el error de certificado no tiene que volver a aparecer,
y en la sección 6 del reporte el berry-monitor que se usa tiene que decir `OK`.

## 8. Si no se puede ejecutar el script

Sólo para el caso 2.1 con el certificado de Let's Encrypt de la tabla. Hay que
cerrar berry-monitor antes y volver a abrirlo después.

1. Cerrar berry-monitor.
2. Abrir **Windows PowerShell** como en el Paso 2.
3. Copiar el recuadro completo, pegarlo y apretar Enter:

```powershell
$h = '151682F5218C0A511C28F4060A73B9CA78CE9A53'
$donde = 'Cert:\CurrentUser\CA', 'Cert:\CurrentUser\Root'
$donde += 'Cert:\LocalMachine\CA', 'Cert:\LocalMachine\Root'
$hay = @(Get-ChildItem $donde | Where-Object Thumbprint -eq $h)
$bk = [Environment]::GetFolderPath('Desktop') + '\respaldo-isrg-x2-vencido.cer'
if ($hay) { Export-Certificate -Cert $hay[0] -FilePath $bk | Out-Null }
foreach ($c in $hay) { Remove-Item $c.PSPath -ErrorAction SilentlyContinue }
$quedan = @(Get-ChildItem $donde | Where-Object Thumbprint -eq $h)
if (-not $hay) { 'NO ESTA: este equipo no tiene el certificado vencido.' }
if ($hay -and $quedan) { 'FALTA: repetir abriendo PowerShell como administrador.' }
if ($hay -and -not $quedan) { 'LISTO: certificado vencido eliminado.' }
```

Si responde `LISTO`, volver a abrir berry-monitor. Si responde `FALTA`,
repetir con PowerShell abierto como administrador. Si responde `NO ESTA`, el
problema es otro: avisar a soporte.

## 9. Deshacer el cambio

Sólo si soporte lo pide. Vuelve a instalar un certificado desde su respaldo
del Escritorio (y con él, vuelve el error). El del caso conocido se llama
`respaldo-isrg-x2-vencido.cer`; los demás, `respaldo-vencido-<huella>.cer`.

```powershell
$bk = [Environment]::GetFolderPath('Desktop') + '\respaldo-isrg-x2-vencido.cer'
Import-Certificate -FilePath $bk -CertStoreLocation Cert:\CurrentUser\CA
```

## 10. Solución definitiva

La próxima versión de berry-monitor valida los certificados con el mismo
sistema que usa Windows (el del navegador), así que deja de depender de los
certificados guardados en el tótem, del archivo de `SSL_CERT_FILE_PATH` y del
modo estricto de Python. Hasta que esa versión esté instalada en todos los
tótems, este procedimiento es la forma de corregirlo.
