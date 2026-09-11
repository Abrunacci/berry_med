"""Revisión de los certificados del tótem para el /health.

Con certificados reales (ver `tests/certs/README.md`): la cadena que mandaba el
portal en septiembre de 2026 y el almacén de SUBSUELO-11, el tótem donde
apareció el problema.
"""

import datetime as dt
import json
import ssl
from pathlib import Path

import pytest

import src.cert_check as cert_check
from src.cert_check import AVISO_DIAS, leer_certificado, revisar, revisar_certificados

CERTS = Path(__file__).resolve().parent / "certs"
UTC = dt.timezone.utc
HOY = dt.datetime(2026, 9, 10, tzinfo=UTC)
HUELLA_VENCIDO = "151682F5218C0A511C28F4060A73B9CA78CE9A53"


def der(nombre: str) -> bytes:
    return ssl.PEM_cert_to_DER_cert((CERTS / f"{nombre}.pem").read_text())


CADENA_PORTAL = [der("portal_hoja"), der("ye1"), der("root_ye_cruzado"),
                 der("isrg_root_x2_cruzado_2032")]
X1 = der("isrg_root_x1")
X2 = der("isrg_root_x2")
X2_VENCIDO = der("isrg_root_x2_cruzado_vencido")


# --- Lectura del certificado ------------------------------------------------

def test_lee_el_cruce_vencido():
    c = leer_certificado(X2_VENCIDO)
    assert c["subjectName"] == "ISRG Root X2"
    assert c["issuerName"] == "ISRG Root X1"
    assert c["notAfter"] == dt.datetime(2025, 9, 15, 16, 0, tzinfo=UTC)
    assert c["sha1"] == HUELLA_VENCIDO


def test_lee_la_vigencia_de_una_raiz():
    assert leer_certificado(X1)["notAfter"] == dt.datetime(2035, 6, 4, 11, 4, 38, tzinfo=UTC)


def test_el_emisor_de_un_certificado_es_el_sujeto_del_siguiente():
    """Es la comparación con la que se decide qué del almacén importa: tiene
    que dar igual byte a byte, como la hace OpenSSL."""
    hoja, ye1 = leer_certificado(CADENA_PORTAL[0]), leer_certificado(CADENA_PORTAL[1])
    assert hoja["issuer"] == ye1["subject"]
    assert ye1["subjectName"] == "YE1"


# --- El caso real -----------------------------------------------------------

def test_el_almacen_de_subsuelo_11_da_aviso():
    """ISRG Root X1 en ROOT y el cruce vencido en CA, sin el X2 autofirmado:
    el almacén que cortó la conexión."""
    r = revisar(CADENA_PORTAL, [("ROOT", X1), ("CA", X2_VENCIDO)], HOY)
    assert r["checked"] is True
    assert r["inspected"] == 2
    [aviso] = r["warnings"]
    assert aviso["subject"] == "ISRG Root X2"
    assert aviso["issuer"] == "ISRG Root X1"
    assert aviso["stores"] == ["CA"]
    assert aviso["expired"] is True
    assert aviso["daysLeft"] < 0
    assert aviso["notAfter"] == "2025-09-15T16:00:00Z"
    assert aviso["sha1"] == HUELLA_VENCIDO


def test_un_almacen_sano_no_da_avisos():
    r = revisar(CADENA_PORTAL, [("ROOT", X1), ("ROOT", X2)], HOY)
    assert r["warnings"] == []
    assert r["inspected"] == 2


def test_avisa_antes_de_que_venza():
    r = revisar(CADENA_PORTAL, [("ROOT", X1)], dt.datetime(2035, 5, 25, tzinfo=UTC))
    [aviso] = r["warnings"]
    assert aviso["expired"] is False
    assert aviso["daysLeft"] == 10


def test_fuera_del_margen_no_avisa():
    vence = leer_certificado(X1)["notAfter"]
    r = revisar(CADENA_PORTAL, [("ROOT", X1)], vence - dt.timedelta(days=AVISO_DIAS + 1))
    assert r["warnings"] == []


def test_los_certificados_ajenos_a_la_cadena_no_cuentan():
    """Un almacén de Windows trae decenas de certificados vencidos que no tienen
    nada que ver con el backend. Si contaran, el aviso sonaría siempre. La hoja
    del portal ya vencida no es emisora de nada en la cadena."""
    r = revisar(CADENA_PORTAL, [("CA", CADENA_PORTAL[0]), ("ROOT", X1)],
                dt.datetime(2027, 1, 1, tzinfo=UTC))
    assert r["warnings"] == []
    assert r["inspected"] == 1


def test_el_mismo_certificado_en_dos_almacenes_sale_una_vez():
    r = revisar(CADENA_PORTAL, [("CA", X2_VENCIDO), ("ROOT", X2_VENCIDO)], HOY)
    [aviso] = r["warnings"]
    assert aviso["stores"] == ["CA", "ROOT"]


def test_un_certificado_ilegible_no_tumba_la_revision():
    r = revisar(CADENA_PORTAL, [("CA", b"\x30\x03xyz"), ("CA", X2_VENCIDO)], HOY)
    assert len(r["warnings"]) == 1


def test_el_resultado_es_serializable_a_json():
    json.dumps(revisar(CADENA_PORTAL, [("CA", X2_VENCIDO)], HOY))


# --- La revisión completa ----------------------------------------------------

def test_fuera_de_windows_no_se_revisa(monkeypatch):
    monkeypatch.delattr(ssl, "enum_certificates", raising=False)
    r = revisar_certificados("https://portal.ondoctor365.com/api")
    assert r["checked"] is False
    assert "Windows" in r["error"]


def test_una_api_http_no_se_revisa(monkeypatch):
    """El mock local de desarrollo es http: no hay certificados que mirar."""
    monkeypatch.setattr(ssl, "enum_certificates", lambda nombre: [], raising=False)
    r = revisar_certificados("http://127.0.0.1:8080/vitals/totem/health")
    assert r["checked"] is False
    assert "https" in r["error"]


def test_si_no_puede_conectar_lo_informa_y_no_lanza(monkeypatch):
    monkeypatch.setattr(ssl, "enum_certificates", lambda nombre: [], raising=False)

    def sin_red(host, port):
        raise OSError("sin red")

    monkeypatch.setattr(cert_check, "cadena_del_servidor", sin_red)
    r = revisar_certificados("https://portal.ondoctor365.com/api")
    assert r["checked"] is False
    assert r["error"] == "OSError: sin red"
    assert r["warnings"] == []


def test_revisa_contra_el_almacen_de_windows(monkeypatch):
    """De punta a punta con el almacén de Windows simulado: el filtro de
    propósito deja afuera lo que Windows no habilita para servidores."""
    almacen = {
        "ROOT": [(X1, "x509_asn", True)],
        "CA": [(X2_VENCIDO, "x509_asn", {ssl.Purpose.SERVER_AUTH.oid}),
               (X2_VENCIDO, "x509_asn", {"1.3.6.1.5.5.7.3.3"})],  # sólo firma de código
    }
    monkeypatch.setattr(ssl, "enum_certificates", lambda nombre: almacen[nombre], raising=False)
    monkeypatch.setattr(cert_check, "cadena_del_servidor", lambda host, port: CADENA_PORTAL)
    r = revisar_certificados("https://portal.ondoctor365.com/api")
    assert r["checked"] is True
    assert [a["sha1"] for a in r["warnings"]] == [HUELLA_VENCIDO]
    assert "_t" in r
