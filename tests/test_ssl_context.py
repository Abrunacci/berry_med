"""El contexto SSL valida siempre, y contra el almacén del sistema.

No abre conexiones: el handshake real se prueba con `tools/check_ssl.py`.
"""

import ssl

import truststore

from src.ssl_context import get_ssl_context


def test_usa_el_almacen_del_sistema():
    assert isinstance(get_ssl_context(), truststore.SSLContext)


def test_verifica_certificado_y_hostname():
    ctx = get_ssl_context()
    assert ctx.verify_mode == ssl.CERT_REQUIRED
    assert ctx.check_hostname is True


def test_cada_llamada_da_un_contexto_propio():
    """Compartirlo entre el hilo de pysher y el event loop puede dejarlo con
    la verificación apagada. Ver el docstring de `get_ssl_context()`."""
    assert get_ssl_context() is not get_ssl_context()
