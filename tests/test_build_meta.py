"""El sello de versión y la guarda de Python del build (tools/build_meta.py).

Es lo que hace que un exe no pueda salir con otro Python sin que nadie se
entere, como pasó con los de 3.11 y 3.13 mezclados en los tótems.
"""

import ast
import json
import subprocess
from pathlib import Path

import pytest

import tools.build_meta as bm

REPO = Path(__file__).resolve().parent.parent

INFO = {"version": "1.0.9-3-gabc1234", "commit": "abc1234", "dirty": False,
        "builtAt": "2026-09-11T12:00:00Z", "python": "3.13.5", "builder": "prueba"}


def test_el_build_exige_python_3_13():
    bm.verificar_python((3, 13, 5))
    with pytest.raises(SystemExit, match="3.13"):
        bm.verificar_python((3, 11, 9))


def test_falta_un_modulo_y_el_build_corta():
    with pytest.raises(SystemExit, match="no_existe_este_modulo"):
        bm.verificar_modulos(["json", "no_existe_este_modulo"])


def test_la_version_de_git_pasa_a_numeros_para_windows():
    assert bm.numeros_version("1.0.9") == (1, 0, 9, 0)
    assert bm.numeros_version("1.0.9-3-gabc1234") == (1, 0, 9, 3)
    assert bm.numeros_version("1.0.9-3-gabc1234-dirty") == (1, 0, 9, 3)
    assert bm.numeros_version("1.0") == (1, 0, 0, 0)
    assert bm.numeros_version("abc1234") == (0, 0, 0, 0)


def test_el_recurso_de_version_es_una_expresion_valida():
    """PyInstaller lo evalúa como Python: tiene que ser una expresión válida,
    armada sólo con sus clases y con literales. Si no, el build cortaría
    recién al armar el exe."""
    texto = bm.texto_recurso_version("berry-monitor", INFO)
    arbol = ast.parse(texto, mode="eval")
    llamadas = {n.func.id for n in ast.walk(arbol) if isinstance(n, ast.Call)}
    assert llamadas == {"VSVersionInfo", "FixedFileInfo", "StringFileInfo", "StringTable",
                        "StringStruct", "VarFileInfo", "VarStruct"}
    assert "filevers=(1, 0, 9, 3)" in texto
    assert "1.0.9-3-gabc1234 (abc1234)" in texto


def test_el_recurso_de_version_lo_acepta_pyinstaller(tmp_path):
    """La prueba completa, con el cargador de PyInstaller. Necesita pefile, que
    PyInstaller sólo instala en Windows: en Linux se saltea, y en Windows la
    hace además el build de GitHub Actions."""
    versioninfo = pytest.importorskip("PyInstaller.utils.win32.versioninfo")
    f = tmp_path / "version_info.txt"
    f.write_text(bm.texto_recurso_version("berry-monitor", INFO), encoding="utf-8")
    recurso = str(versioninfo.load_version_info_from_text_file(str(f)))
    assert "1.0.9-3-gabc1234 (abc1234)" in recurso
    assert "Python 3.13.5" in recurso
    assert "(1, 0, 9, 3)" in recurso


def test_en_el_repo_la_version_y_el_commit_salen_de_git():
    info = bm.leer_info(REPO)
    commit = subprocess.run(["git", "rev-parse", "--short=7", "HEAD"], cwd=REPO,
                            capture_output=True, text=True).stdout.strip()
    assert info["commit"] == commit
    assert info["version"] != "desconocida"


def test_un_build_con_cambios_dice_exactamente_que_archivos(tmp_path):
    """Un exe "-dirty" tiene que poder explicarse: el sello lista los archivos
    versionados modificados, con el nombre completo."""
    def git(*args):
        subprocess.run(["git", "-c", "commit.gpgsign=false", *args], cwd=tmp_path,
                       check=True, capture_output=True)
    git("init", "-q")
    git("config", "user.email", "prueba@example.com")
    git("config", "user.name", "prueba")
    (tmp_path / "dist").mkdir()
    (tmp_path / "dist" / "berry-monitor.exe").write_bytes(b"viejo")
    git("add", ".")
    git("commit", "-qm", "inicial")
    (tmp_path / "dist" / "berry-monitor.exe").write_bytes(b"nuevo")

    info = bm.leer_info(tmp_path)
    assert info["dirty"] is True
    assert info["dirtyFiles"] == ["dist/berry-monitor.exe"]
    assert info["version"].endswith("-dirty")


def test_preparar_deja_el_json_y_el_recurso(tmp_path):
    rutas = bm.preparar("berry-monitor", tmp_path)
    info = json.loads(Path(rutas["json"]).read_text(encoding="utf-8"))
    assert info["name"] == "berry-monitor"
    assert info["python"].startswith("3.13")
    assert Path(rutas["version"]).exists()
    assert Path(rutas["json"]).parent == tmp_path / "build" / "_meta" / "berry-monitor"
