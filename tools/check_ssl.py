#!/usr/bin/env python3
"""Prueba de TLS: conecta al portal con el mismo contexto SSL que berry-monitor.

Usa `get_ssl_context()` de src/ssl_context.py, así que valida exactamente como
la app. Cualquier respuesta HTTP —incluido el 404 de la raíz— es éxito: el
certificado validó. Lo que no tiene que aparecer es un error SSL.

    python tools\\check_ssl.py
    python tools\\check_ssl.py https://otro.host/ruta

Sale con 0 si validó, 1 si hubo error de certificado/SSL y 2 si falló otra
cosa (red, DNS, timeout): en ese caso el TLS ni llegó a probarse.
"""

import asyncio
import os
import sys

import aiohttp

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

from src.ssl_context import get_ssl_context  # noqa: E402

URL = "https://portal.ondoctor365.com"


async def probar(url: str) -> int:
    async with aiohttp.ClientSession(
        connector=aiohttp.TCPConnector(ssl=get_ssl_context()),
        timeout=aiohttp.ClientTimeout(total=15),
    ) as session:
        async with session.get(url) as resp:
            return resp.status


def main() -> int:
    url = sys.argv[1] if len(sys.argv) > 1 else URL
    print(f"[SSL] Validación de certificados nativa del sistema (truststore): {url}")
    try:
        status = asyncio.run(probar(url))
    except aiohttp.ClientSSLError as e:
        print(f"[FALLA] Error SSL: {type(e).__name__}: {e}")
        return 1
    except Exception as e:
        print(f"[FALLA] No se pudo probar el TLS: {type(e).__name__}: {e}")
        return 2
    print(f"[OK] HTTP {status}: el certificado validó contra el almacén del sistema.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
