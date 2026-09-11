"""Cómo el exe sabe qué build es (src/build_info.py)."""

import json
import sys

from src.build_info import obtener, texto

SELLO = {"name": "berry-monitor", "version": "1.0.9", "commit": "abc1234",
         "dirty": False, "builtAt": "2026-09-11T12:00:00Z", "python": "3.13.5",
         "builder": "github-actions"}


def test_desde_el_exe_lee_el_sello_empaquetado(tmp_path, monkeypatch):
    (tmp_path / "build_info.json").write_text(json.dumps(SELLO), encoding="utf-8")
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    info = obtener()
    assert info["version"] == "1.0.9"
    assert info["commit"] == "abc1234"
    assert info["runtimePython"]


def test_un_sello_roto_no_impide_arrancar(tmp_path, monkeypatch):
    (tmp_path / "build_info.json").write_text("esto no es json", encoding="utf-8")
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    info = obtener()
    assert info["version"] == "desconocida"
    assert "error" in info


def test_desde_el_codigo_fuente_lo_dice(monkeypatch):
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    info = obtener("berry-monitor")
    assert info["version"] == "codigo fuente"
    assert info["name"] == "berry-monitor"


def test_la_linea_del_log_dice_todo_lo_necesario():
    linea = texto(SELLO)
    for dato in ("berry-monitor 1.0.9", "commit abc1234", "Python 3.13.5",
                 "generado 2026-09-11T12:00:00Z", "github-actions"):
        assert dato in linea
    assert "sin commitear" not in linea
    assert "con cambios sin commitear" in texto(dict(SELLO, dirty=True))
