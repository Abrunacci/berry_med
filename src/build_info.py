"""Qué exe es este: versión, commit y Python con que se generó.

El build (tools/build_meta.py) mete `build_info.json` adentro del exe.
berry-monitor lo escribe en su log al arrancar, y lo repite al principio de
cada archivo cuando el log rota: así, el build que corre en un tótem se ve
abriendo el log, sin depender de que alguien revise el /health. También viaja
en el /health, en el campo `build`.

Corriendo desde el código fuente no hay build: se informa "codigo fuente" con
el commit de git, si lo hay.
"""

import json
import os
import platform
import subprocess
import sys


def _commit_local():
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    try:
        r = subprocess.run(["git", "rev-parse", "--short=7", "HEAD"], cwd=repo,
                           capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout.strip() or None if r.returncode == 0 else None


def obtener(nombre="berry-monitor") -> dict:
    """La info del build. Nunca lanza: un sello ilegible no impide arrancar."""
    base = getattr(sys, "_MEIPASS", None)
    if base:
        try:
            with open(os.path.join(base, "build_info.json"), encoding="utf-8") as f:
                info = json.load(f)
        except (OSError, ValueError) as e:
            info = {"version": "desconocida", "error": f"{type(e).__name__}: {e}"}
    else:
        info = {"version": "codigo fuente", "commit": _commit_local()}
    info.setdefault("name", nombre)
    info["runtimePython"] = platform.python_version()
    return info


def texto(info: dict) -> str:
    """Una línea para el log: nombre, versión, commit, Python y cuándo se generó."""
    partes = [f"{info.get('name', 'berry-monitor')} {info.get('version', '?')}"]
    if info.get("commit"):
        partes.append(f"commit {info['commit']}"
                      + (" con cambios sin commitear" if info.get("dirty") else ""))
    partes.append(f"Python {info.get('python') or info.get('runtimePython')}")
    if info.get("builtAt"):
        partes.append(f"generado {info['builtAt']}")
    if info.get("builder"):
        partes.append(f"en {info['builder']}")
    return " | ".join(partes)
