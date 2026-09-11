"""Que el `.exe` empaquete todo lo que la app importa.

`src/` no tiene `__init__.py`: es un namespace package, y PyInstaller no
resuelve sus submódulos por análisis. Por eso los `.spec` los listan a mano — y
por eso agregar un módulo a `src/` sin tocar el spec produce un exe que compila
bien, se distribuye bien, y revienta al arrancar en la máquina del cliente con
`ModuleNotFoundError`. Es el peor momento posible para enterarse.

Estos tests recorren la cadena real de imports y la comparan contra el spec, así
que el olvido se ve acá y no allá. Pasó de verdad: `src.health` y
`src.pm6750_protocol` se agregaron en esta rama y ninguno de los dos estaba en
`berry-monitor.spec`.
"""

import ast
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent


def modulos_src_que_importa(entrada: str) -> set:
    """Todos los `src.*` alcanzables desde un archivo, siguiendo la cadena."""
    visto, pendiente, encontrados = set(), [REPO / entrada], set()
    while pendiente:
        archivo = pendiente.pop()
        if archivo in visto or not archivo.exists():
            continue
        visto.add(archivo)
        for nodo in ast.walk(ast.parse(archivo.read_text(encoding="utf-8"))):
            modulos = []
            if isinstance(nodo, ast.ImportFrom) and nodo.module:
                if nodo.module.startswith("src."):
                    modulos.append(nodo.module)
                elif nodo.module == "src":
                    # `from src import config_schema`
                    modulos += [f"src.{a.name}" for a in nodo.names]
            elif isinstance(nodo, ast.Import):
                modulos += [a.name for a in nodo.names if a.name.startswith("src.")]
            for m in modulos:
                encontrados.add(m)
                pendiente.append(REPO / (m.replace(".", "/") + ".py"))
    return encontrados


def hiddenimports(spec: str) -> set:
    texto = (REPO / spec).read_text(encoding="utf-8")
    return set(re.findall(r"'(src\.[a-z0-9_]+)'", texto))


@pytest.mark.parametrize("spec,entrada", [
    ("berry-monitor.spec", "app.py"),
    ("berry-configure.spec", "configure.py"),
])
def test_el_spec_lista_todo_lo_que_la_app_importa(spec, entrada):
    necesarios = modulos_src_que_importa(entrada)
    faltantes = necesarios - hiddenimports(spec)
    assert not faltantes, (
        f"{spec} no empaqueta {sorted(faltantes)}. El exe va a arrancar con "
        f"ModuleNotFoundError en la máquina del cliente. Agregalos a "
        f"hiddenimports."
    )


@pytest.mark.parametrize("spec", ["berry-monitor.spec", "berry-configure.spec"])
def test_el_spec_no_lista_modulos_que_ya_no_existen(spec):
    """Un nombre viejo en hiddenimports hace fallar el build entero."""
    inexistentes = [
        m for m in hiddenimports(spec)
        if not (REPO / (m.replace(".", "/") + ".py")).exists()
    ]
    assert not inexistentes


def test_las_dos_listas_de_sensores_vigilables_no_divergieron():
    """La tupla está escrita dos veces —en `config.py` y en `src/health.py`—
    a propósito: `config.py` lo usan los dos ejecutables y se lo mantiene sin
    dependencias de `src/`, para no arrastrar el protocolo al configurador. El
    precio es que pueden separarse, y esto es lo que lo impide."""
    from config import SENSORES_VIGILABLES as en_config
    from src.health import SENSORES_VIGILABLES as en_health
    assert tuple(en_config) == tuple(en_health)


def test_el_pyproject_pide_el_mismo_python_que_el_build():
    """Si divergieran, `poetry install` aceptaría un Python con el que después
    el build corta, o al revés."""
    from tools.build_meta import PYTHON_REQUERIDO
    a, b = PYTHON_REQUERIDO
    texto = (REPO / "pyproject.toml").read_text(encoding="utf-8")
    assert re.search(r'^python = "(.+)"', texto, re.M).group(1) == f">={a}.{b},<{a}.{b + 1}"


@pytest.mark.parametrize("spec", ["berry-monitor.spec", "berry-configure.spec"])
def test_el_spec_verifica_python_y_sella_la_version(spec):
    """Es lo que hace que el build no dependa de cómo se lo corra: con otro
    Python corta, y el exe siempre sale con su versión adentro."""
    texto = (REPO / spec).read_text(encoding="utf-8")
    assert "preparar(" in texto
    assert "meta['json']" in texto
    assert "version=meta['version']" in texto
