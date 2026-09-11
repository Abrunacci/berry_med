"""Certificados del tótem que intervienen en la conexión con el backend.

Para el `/health`: avisa si en el almacén de Windows del tótem hay un
certificado de la cadena del backend vencido o por vencer. Es la señal que
faltó en el incidente de septiembre de 2026 (ver `docs/certificado_vencido.md`):
el cruce viejo "ISRG Root X2" emitido por "ISRG Root X1" llevaba un año vencido
en el almacén CA de algunos tótems, y nadie lo supo hasta que dejaron de
conectar.

Qué se mira:

- Sólo lo que Windows ofrece para validar servidores: almacenes CA y ROOT, con
  propósito serverAuth. Es lo mismo que carga Python, y por lo tanto lo que
  OpenSSL podía elegir al armar la cadena.
- Y de eso, sólo los certificados con el sujeto de algún emisor de la cadena
  que manda el backend. Un almacén de Windows trae decenas de certificados
  vencidos que no tienen nada que ver (raíces viejas de Microsoft, de
  timestamping): avisar por ellos sería ruido permanente.

La cadena se toma de una conexión real al backend, así que si Let's Encrypt
cambia de jerarquía la revisión la sigue sola, sin una lista de nombres que
mantener.

Con truststore (`src/ssl_context.py`) un intermedio vencido en el almacén ya no
corta la conexión de berry-monitor; el aviso sigue sirviendo para ir a limpiar
el tótem antes de que le pase a otro programa, y porque una raíz vencida sí
cortaría.

`revisar_certificados()` nunca lanza: si algo sale mal, lo informa en el
resultado.
"""

import datetime as dt
import hashlib
import math
import socket
import ssl
import time
from urllib.parse import urlsplit

import _ssl

from src.ssl_context import get_ssl_context

# Con cuánta anticipación se avisa que un certificado vence.
AVISO_DIAS = 30

# Cada cuánto se revisa. Los certificados no cambian de un minuto al otro: una
# vez por día alcanza. Si la revisión falló (sin red, backend caído), se
# reintenta antes para no quedar un día entero sin dato.
REVISION_SEG = 24 * 3600
REINTENTO_SEG = 3600

_OID_CN = b"\x55\x04\x03"
_OID_OU = b"\x55\x04\x0b"
_OID_O = b"\x55\x04\x0a"


# ------------------------------------------------------------------- DER ---
# La biblioteca estándar no expone un parser de certificados, y para tres
# campos no vale la pena sumar `cryptography` al exe. X.509 en DER tiene una
# estructura fija; se lee lo justo: emisor, vigencia y sujeto.

def _tlv(buf: bytes, i: int):
    """Elemento DER que empieza en `buf[i]`: (tag, inicio del valor, fin)."""
    tag, largo = buf[i], buf[i + 1]
    i += 2
    if largo & 0x80:
        n = largo & 0x7F
        largo = int.from_bytes(buf[i:i + n], "big")
        i += n
    return tag, i, i + largo


def _hijos(buf: bytes, inicio: int, fin: int):
    """Elementos DER entre `inicio` y `fin`: (tag, inicio valor, fin, inicio TLV)."""
    i = inicio
    while i < fin:
        tag, v0, v1 = _tlv(buf, i)
        yield tag, v0, v1, i
        i = v1


def _tiempo(buf: bytes, tag: int, v0: int, v1: int) -> dt.datetime:
    texto = buf[v0:v1].decode("ascii")
    if tag == 0x17:  # UTCTime, dos dígitos de año: RFC 5280 los ubica en 1950-2049
        texto = ("19" if int(texto[:2]) >= 50 else "20") + texto
    return dt.datetime.strptime(texto, "%Y%m%d%H%M%SZ").replace(tzinfo=dt.timezone.utc)


def _nombre(buf: bytes, v0: int, v1: int) -> str:
    """Nombre legible de un Name DER: el CN, o la unidad u organización si no tiene."""
    encontrados = {}
    for _, s0, s1, _ in _hijos(buf, v0, v1):          # RelativeDistinguishedName
        for _, a0, a1, _ in _hijos(buf, s0, s1):      # AttributeTypeAndValue
            (_, o0, o1, _), (tag, x0, x1, _) = list(_hijos(buf, a0, a1))[:2]
            oid = buf[o0:o1]
            if oid in (_OID_CN, _OID_OU, _OID_O) and oid not in encontrados:
                codificacion = "utf-16-be" if tag == 0x1E else "utf-8"  # BMPString
                encontrados[oid] = buf[x0:x1].decode(codificacion, errors="replace")
    return next((encontrados[o] for o in (_OID_CN, _OID_OU, _OID_O) if o in encontrados), "")


def leer_certificado(der: bytes) -> dict:
    """Los campos de un certificado X.509 (DER) que hacen falta acá.

    `subject` e `issuer` van crudos, en DER: así se comparan, igual que hace
    OpenSSL para encontrar al emisor. Los nombres legibles son para el aviso.
    """
    _, c0, _ = _tlv(der, 0)                    # Certificate
    _, t0, t1 = _tlv(der, c0)                  # tbsCertificate
    campos = list(_hijos(der, t0, t1))
    if campos[0][0] == 0xA0:                   # [0] version, opcional
        campos = campos[1:]
    # serialNumber, signature, issuer, validity, subject
    emisor, vigencia, sujeto = campos[2], campos[3], campos[4]
    _, (tag, n0, n1, _) = list(_hijos(der, vigencia[1], vigencia[2]))[:2]
    return {
        "subject": der[sujeto[3]:sujeto[2]],
        "issuer": der[emisor[3]:emisor[2]],
        "subjectName": _nombre(der, sujeto[1], sujeto[2]),
        "issuerName": _nombre(der, emisor[1], emisor[2]),
        "notAfter": _tiempo(der, tag, n0, n1),
        "sha1": hashlib.sha1(der).hexdigest().upper(),
    }


# ------------------------------------------------------------ revisión ---

def revisar(cadena, almacen, ahora: dt.datetime, aviso_dias: int = AVISO_DIAS) -> dict:
    """Cruza la cadena del backend con el almacén del tótem.

    `cadena` son los certificados (DER) que manda el backend; `almacen`, pares
    (nombre del almacén, DER). No hace I/O, así que se prueba en cualquier lado.
    """
    emisores = set()
    for der in cadena:
        try:
            emisores.add(leer_certificado(der)["issuer"])
        except Exception:
            continue

    relevantes = {}
    for nombre_almacen, der in almacen:
        try:
            cert = leer_certificado(der)
        except Exception:
            continue  # uno ilegible no puede dejar sin revisión al resto
        if cert["subject"] not in emisores:
            continue
        # El mismo certificado puede estar en CA y en ROOT: se informa una vez.
        entrada = relevantes.setdefault(cert["sha1"], dict(cert, stores=set()))
        entrada["stores"].add(nombre_almacen)

    avisos = []
    for cert in relevantes.values():
        dias = (cert["notAfter"] - ahora).total_seconds() / 86400
        if dias < aviso_dias:
            avisos.append({
                "subject": cert["subjectName"],
                "issuer": cert["issuerName"],
                "stores": sorted(cert["stores"]),
                "notAfter": cert["notAfter"].strftime("%Y-%m-%dT%H:%M:%SZ"),
                "daysLeft": math.floor(dias),
                "expired": dias < 0,
                "sha1": cert["sha1"],
            })
    avisos.sort(key=lambda a: a["daysLeft"])

    return {"checked": True, "inspected": len(relevantes), "warnDays": aviso_dias,
            "warnings": avisos, "error": None}


def cadena_del_servidor(host: str, port: int = 443, timeout: float = 10) -> list:
    """Certificados (DER) que manda el backend al conectarse, hoja primero.

    La conexión se valida con el mismo contexto que el resto de la app: si no
    valida, no hay cadena en la que confiar y la revisión informa el error.
    """
    with socket.create_connection((host, port), timeout=timeout) as crudo:
        with get_ssl_context().wrap_socket(crudo, server_hostname=host) as tls:
            # Python 3.13 hizo pública get_unverified_chain(); antes vivía en
            # el objeto interno. Mismo criterio que usa truststore.
            objeto = tls
            while not hasattr(objeto, "get_unverified_chain"):
                objeto = objeto._sslobj
            certs = objeto.get_unverified_chain() or []
    return [c if isinstance(c, bytes) else c.public_bytes(_ssl.ENCODING_DER) for c in certs]


def almacen_windows():
    """(almacén, DER) de lo que Windows ofrece para validar servidores.

    Mismo filtro que `ssl.SSLContext.load_default_certs()`: almacenes CA y
    ROOT, y sólo certificados habilitados para autenticar servidores.
    """
    oid = ssl.Purpose.SERVER_AUTH.oid
    for nombre in ("CA", "ROOT"):
        for der, codificacion, confianza in ssl.enum_certificates(nombre):
            if codificacion == "x509_asn" and (confianza is True or oid in confianza):
                yield nombre, der


def revisar_certificados(url: str, aviso_dias: int = AVISO_DIAS) -> dict:
    """Revisión completa para el /health, contra el host de `url`. Nunca lanza.

    El resultado lleva `_t` (monotonic de cuándo se hizo), que el health
    convierte en antigüedad y no publica.
    """
    base = {"checked": False, "inspected": None, "warnDays": aviso_dias,
            "warnings": [], "error": None, "_t": time.monotonic()}
    try:
        if not hasattr(ssl, "enum_certificates"):
            return dict(base, error="sólo se revisa en Windows")
        partes = urlsplit(url or "")
        if partes.scheme != "https" or not partes.hostname:
            return dict(base, error="la API no es https: no hay certificados que revisar")
        cadena = cadena_del_servidor(partes.hostname, partes.port or 443)
        resultado = revisar(cadena, almacen_windows(), dt.datetime.now(dt.timezone.utc), aviso_dias)
        return dict(resultado, _t=base["_t"])
    except Exception as e:
        return dict(base, error=f"{type(e).__name__}: {e}")
