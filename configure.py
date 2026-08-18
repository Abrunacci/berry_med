"""Asistente de configuración de BerryMed Monitor.

Por defecto abre una interfaz gráfica. Si no hay entorno gráfico disponible
(tkinter ausente, sesión sin display, SSH), cae solo al asistente de texto.

    python configure.py            # gráfico, con fallback automático
    python configure.py --cli      # fuerza el asistente de texto

Los campos salen de `src/config_schema.py`, que es la única fuente de verdad:
las dos interfaces se arman de ahí y no pueden desincronizarse.
"""

import argparse
import getpass
import os
import sys

from src import config_schema as schema

# ¿Se puede escribir/leer por consola? En el .exe compilado con console=False no
# hay consola propia: sys.stdout es None y cualquier print() o input() rompe.
_HAS_CONSOLE = sys.stdout is not None and sys.stdin is not None


def _attach_parent_console() -> bool:
    """Se engancha a la consola del proceso que lanzó el exe, si la hay.

    Con `console=False` el ejecutable no abre consola propia, pero si alguien lo
    corrió desde una terminal (`berry-configure.exe --cli`) se puede reusar esa.
    Devuelve False si no había ninguna, que es el caso normal al abrirlo con
    doble clic.
    """
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        if not ctypes.windll.kernel32.AttachConsole(-1):  # -1 = proceso padre
            return False
        sys.stdout = open("CONOUT$", "w", buffering=1, encoding="utf-8",
                          errors="replace")
        sys.stderr = open("CONOUT$", "w", buffering=1, encoding="utf-8",
                          errors="replace")
        sys.stdin = open("CONIN$", "r", encoding="utf-8", errors="replace")
        return True
    except Exception:
        return False


def _silence_output():
    """Manda stdout/stderr a un agujero negro, para que nada rompa por imprimir."""
    devnull = open(os.devnull, "w")
    if sys.stdout is None:
        sys.stdout = devnull
    if sys.stderr is None:
        sys.stderr = devnull


def _fatal(message: str):
    """Avisa de un error sin depender de la consola ni de tkinter."""
    if _HAS_CONSOLE:
        sys.exit(message)
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(
            None, message, "BerryMed Monitor — Configuración", 0x10
        )
    except Exception:
        pass
    sys.exit(1)


def _prompt(field, current):
    """Pide un campo por consola, reintentando hasta que valide."""
    if field.help:
        print(f"  ({field.help})")

    while True:
        if field.kind == "choice":
            suffix = f"[{'/'.join(field.options)}] (default '{current}')"
        elif field.kind == "bool":
            suffix = f"[true/false] (default '{current}')"
        elif current:
            suffix = f"(default '{current}')"
        else:
            suffix = "(requerido)" if field.required else "(opcional)"

        prompt = f"{field.label} {suffix}: "
        raw = (getpass.getpass(prompt) if field.kind == "password"
               else input(prompt)).strip()

        if not raw:
            raw = current

        errors = schema.validate({**{f.key: f.default for f in schema.FIELDS},
                                  field.key: raw})
        if field.key not in errors:
            return raw
        print(f"  -> {errors[field.key]}")


def run_cli():
    print("BerryMed Monitor — Configuración")
    print("--------------------------------")

    existing = schema.load_existing()
    values = schema.initial_values(existing)
    if existing:
        print(f"Editando la configuración existente: {schema.config_path()}")
    print("Enter para dejar el valor entre paréntesis.\n")

    current_section = None
    for field in schema.FIELDS:
        if field.section != current_section:
            current_section = field.section
            print(f"\n-- {current_section} --")
        values[field.key] = _prompt(field, values[field.key])

        # Las tres partes de la URL se piden seguidas: después de cada una se
        # muestra cómo va quedando, para poder validarla a ojo antes de seguir.
        if field.key in schema.URL_PARTS:
            print(f"  URL de métricas: {schema.metrics_url(values) or '…'}")

    try:
        path = schema.save(values, existing)
    except (OSError, ValueError) as exc:
        sys.exit(f"\nError al guardar: {exc}")

    print(f"\nConfiguración guardada en: {path}")
    conservadas = [k for k in existing if k not in schema.BY_KEY]
    if conservadas:
        print(f"Se conservaron sin cambios: {', '.join(conservadas)}")


def main():
    global _HAS_CONSOLE

    # Si el exe se lanzó desde una terminal, reusarla; si no, blindar los prints.
    if not _HAS_CONSOLE and _attach_parent_console():
        _HAS_CONSOLE = True
    _silence_output()

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cli", action="store_true",
                        help="usar el asistente de texto en vez del gráfico")
    args = parser.parse_args()

    if not args.cli:
        motivo = None
        try:
            from src.configure_gui import run
        except ImportError:
            motivo = "tkinter no está disponible"
        else:
            if run():
                return
            motivo = "no hay entorno gráfico"

        if not _HAS_CONSOLE:
            _fatal(
                "No se pudo abrir la ventana de configuración "
                f"({motivo}).\n\n"
                "Ejecutá el configurador desde una terminal para usar el "
                "asistente de texto:\n\n    berry-configure.exe --cli"
            )
        print(f"[INFO] {motivo}; se usa el asistente de texto.\n")

    if not _HAS_CONSOLE:
        _fatal("El asistente de texto necesita una terminal.\n\n"
               "Abrí una consola y ejecutá:\n\n    berry-configure.exe --cli")

    run_cli()


if __name__ == "__main__":
    main()
