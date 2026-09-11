"""Datos del build, para los dos .spec de PyInstaller.

Los .spec lo llaman antes que nada, así que vale para cualquier forma de
generar los exe (build.ps1, `poetry run pyinstaller`, `python -m PyInstaller`):

- Corta si el build no se hace con Python 3.13. La versión de Python cambia
  cómo el exe valida TLS (en 3.13 hay modo estricto; ver
  docs/certificado_vencido.md, sección 2.3), y ya hubo exes con 3.11 y con
  3.13 mezclados en los tótems sin que nada lo avisara.
- Corta si falta en el entorno un módulo que el exe necesita (truststore).
- Sella la versión: deja `build_info.json` para meter adentro del exe (lo lee
  src/build_info.py y berry-monitor lo escribe en su log) y un recurso de
  versión de Windows (clic derecho > Propiedades > Detalles).

La versión sale de git (`git describe`): en un tag queda "1.0.9"; en un commit
posterior, "1.0.9-3-gabc1234"; con cambios sin commitear termina en "-dirty".
"""

import datetime as dt
import importlib.util
import json
import os
import platform
import re
import subprocess
import sys
from pathlib import Path

PYTHON_REQUERIDO = (3, 13)


def verificar_python(version=None):
    """Corta el build si no es la versión de Python con la que se generan los exe."""
    v = tuple((version or sys.version_info)[:2])
    if v != PYTHON_REQUERIDO:
        esperado = ".".join(map(str, PYTHON_REQUERIDO))
        raise SystemExit(
            f"\n[BUILD] Los exe se generan con Python {esperado}, y este es "
            f"{'.'.join(map(str, v))}.\n"
            "        Con otra versión el exe valida TLS distinto "
            "(ver docs/certificado_vencido.md, sección 2.3).\n"
            "        Usar build.ps1, que arma el entorno correcto.\n"
        )


def verificar_modulos(nombres):
    """Corta el build si falta en el entorno algún módulo que el exe necesita."""
    faltan = [n for n in nombres if importlib.util.find_spec(n) is None]
    if faltan:
        raise SystemExit(
            f"\n[BUILD] Faltan módulos en este entorno: {', '.join(faltan)}.\n"
            "        Usar build.ps1, que instala las versiones exactas de poetry.lock.\n"
        )


def _git(repo, *args):
    try:
        r = subprocess.run(["git", *args], cwd=str(repo), capture_output=True,
                           text=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return None
    salida = r.stdout.strip()
    return salida if r.returncode == 0 and salida else None


def numeros_version(version):
    """(X, Y, Z, N) para el recurso de Windows: "1.0.9-3-gabc1234" -> (1, 0, 9, 3).

    N son los commits posteriores al tag. Sin tag reconocible, (0, 0, 0, 0).
    """
    m = re.match(r"(\d+)\.(\d+)(?:\.(\d+))?(?:-(\d+)-g[0-9a-f]+)?", version or "")
    if not m:
        return (0, 0, 0, 0)
    return tuple(int(g or 0) for g in m.groups())


def leer_info(repo):
    """Versión, commit, Python y dónde se generó.

    Si hay archivos versionados modificados, la versión termina en "-dirty" y
    `dirtyFiles` dice cuáles: un exe con cambios sin commitear tiene que poder
    explicarse.
    """
    cambios = (_git(repo, "diff", "--name-only", "HEAD") or "").splitlines()
    return {
        "version": _git(repo, "describe", "--tags", "--match", "[0-9]*",
                        "--dirty", "--always") or "desconocida",
        "commit": _git(repo, "rev-parse", "--short=7", "HEAD"),
        "dirty": bool(cambios),
        "dirtyFiles": cambios[:20],
        "builtAt": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "python": platform.python_version(),
        "builder": ("github-actions" if os.environ.get("GITHUB_ACTIONS") == "true"
                    else platform.node()),
    }


def texto_recurso_version(nombre, info):
    """Recurso de versión de Windows, en el formato de texto que lee PyInstaller."""
    n = numeros_version(info["version"])
    campos = [
        ("CompanyName", "BerryMed"),
        ("FileDescription", nombre),
        ("FileVersion", info["version"]),
        ("InternalName", nombre),
        ("OriginalFilename", f"{nombre}.exe"),
        ("ProductName", "BerryMed Monitor"),
        ("ProductVersion", f'{info["version"]} ({info.get("commit") or "sin commit"})'),
        ("Comments", f'Python {info["python"]}, generado {info["builtAt"]} en {info["builder"]}'),
    ]
    tabla = ",\n".join(f"        StringStruct({k!r}, {v!r})" for k, v in campos)
    return (
        "VSVersionInfo(\n"
        f"  ffi=FixedFileInfo(filevers={n}, prodvers={n}, mask=0x3f, flags=0x0,\n"
        "                    OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),\n"
        "  kids=[\n"
        "    StringFileInfo([\n"
        "      StringTable('040904B0', [\n"
        f"{tabla}])]),\n"
        "    VarFileInfo([VarStruct('Translation', [1033, 1200])])\n"
        "  ]\n"
        ")\n"
    )


def preparar(nombre, spec_dir, requiere=()):
    """Lo que cada .spec llama al principio. Devuelve las rutas para Analysis y EXE.

    Los archivos quedan en build/_meta/<nombre>: build/ está ignorado por git y
    `--clean` sólo borra build/<nombre del spec>.
    """
    verificar_python()
    verificar_modulos(requiere)
    info = dict(leer_info(spec_dir), name=nombre)
    destino = Path(spec_dir) / "build" / "_meta" / nombre
    destino.mkdir(parents=True, exist_ok=True)
    json_path = destino / "build_info.json"
    version_path = destino / "version_info.txt"
    json_path.write_text(json.dumps(info, indent=2), encoding="utf-8")
    version_path.write_text(texto_recurso_version(nombre, info), encoding="utf-8")
    print(f"[BUILD] {nombre} {info['version']} "
          f"(commit {info['commit']}, Python {info['python']})")
    if info["dirtyFiles"]:
        print(f"[BUILD] Archivos modificados sin commitear: {', '.join(info['dirtyFiles'])}")
    return {"info": info, "json": str(json_path), "version": str(version_path)}
