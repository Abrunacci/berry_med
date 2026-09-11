"""Contexto SSL de las conexiones salientes: la API y el websocket de Pusher.

Valida contra el almacén de certificados del sistema operativo (truststore) y
no contra OpenSSL. En Windows eso es la misma validación que hacen el navegador
e `Invoke-WebRequest`.

Por qué no el default de Python: `ssl.create_default_context()` le carga a
OpenSSL los almacenes ROOT *y CA* de Windows, más el bundle de `SSL_CERT_FILE`.
Si la PC tiene intermedios viejos en el almacén CA, OpenSSL puede armar la
cadena con uno vencido y fallar con `certificate has expired`, aunque el
certificado del servidor esté bien y Windows confíe en él. Pasó con la
jerarquía nueva de Let's Encrypt (intermedio YE1). El motor de cadenas de
Windows sí sabe descartar ese intermedio y elegir otro camino.

Se pasa explícito a cada cliente en vez de usar `truststore.inject_into_ssl()`:
ese parche sólo alcanza a lo que arme su contexto después, y aiohttp arma el
suyo al importarse.
"""

import ssl

import truststore


def get_ssl_context() -> ssl.SSLContext:
    """Contexto nuevo que valida certificado y hostname contra el sistema.

    `PROTOCOL_TLS_CLIENT` exige las dos verificaciones por defecto, y acá no se
    tocan.

    Devuelve uno nuevo en cada llamada a propósito: hay que usar uno por hilo.
    truststore apaga la verificación de OpenSSL durante el handshake (la hace
    el sistema después) y la restaura al terminar, y en `wrap_bio()` —el camino
    de asyncio— lo hace sin lock. Si el hilo de pysher y el event loop
    compartieran el contexto, dos handshakes cruzados podrían dejarlo con la
    verificación apagada para siempre.
    """
    return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
