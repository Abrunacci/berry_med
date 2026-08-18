"""Asistente gráfico de configuración (tkinter).

Se arma solo a partir de `config_schema.FIELDS`: no hay lista de campos duplicada
acá. Toda la lógica de validación y guardado vive en el schema; este módulo es
sólo la capa visual.

tkinter viene con Python en Windows, que es el destino de `berry-configure.exe`,
así que no agrega dependencias. Si no estuviera disponible, `configure.py` cae
solo al asistente de texto.
"""

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from src import config_schema as schema

PAD = 8


class ConfigApp:
    def __init__(self, root):
        self.root = root
        self.vars = {}
        self.error_labels = {}

        root.title("BerryMed Monitor — Configuración")
        # Ancho pensado para que la URL de métricas entre entera sin scrollear:
        # ~80 caracteres en Courier 9 más los padding de tab, notebook y marco.
        root.minsize(780, 520)

        self.existing = schema.load_existing()
        values = schema.initial_values(self.existing)

        self._build_header()
        self._build_tabs(values)
        self._build_footer()

        root.bind("<Return>", lambda _e: self.on_save())
        root.bind("<Escape>", lambda _e: self.on_cancel())

    # ------------------------------------------------------------ layout ---
    def _build_header(self):
        frame = ttk.Frame(self.root, padding=(PAD * 2, PAD * 1.5, PAD * 2, 0))
        frame.pack(fill="x")

        estado = "Editando la configuración existente" if self.existing \
            else "No hay configuración previa: se creará una nueva"
        ttk.Label(frame, text=estado, font=("", 10, "bold")).pack(anchor="w")
        ttk.Label(frame, text=str(schema.config_path()), foreground="#555").pack(
            anchor="w", pady=(2, 0)
        )

    def _build_tabs(self, values):
        nb = ttk.Notebook(self.root, padding=PAD)
        nb.pack(fill="both", expand=True)

        ports = schema.list_serial_ports()

        for section in schema.SECTIONS:
            tab = ttk.Frame(nb, padding=PAD * 1.5)
            nb.add(tab, text=section)
            tab.columnconfigure(1, weight=1)

            row = 0
            for f in (x for x in schema.FIELDS if x.section == section):
                label = f.label + (" *" if f.required else "")
                ttk.Label(tab, text=label).grid(
                    row=row, column=0, sticky="w", padx=(0, PAD), pady=(PAD, 0)
                )
                widget = self._make_widget(tab, f, values[f.key], ports)
                widget.grid(row=row, column=1, sticky="ew", pady=(PAD, 0))
                row += 1

                if f.help:
                    ttk.Label(
                        tab, text=f.help, foreground="#666", wraplength=430,
                        justify="left", font=("", 8),
                    ).grid(row=row, column=1, sticky="w")
                    row += 1

                err = ttk.Label(tab, text="", foreground="#b00020", font=("", 8))
                err.grid(row=row, column=1, sticky="w")
                self.error_labels[f.key] = err
                row += 1

                # Después del último tramo de la URL, mostrar cómo queda
                # armada. Se actualiza sola al tipear en cualquiera de las
                # tres partes, así se valida a ojo antes de guardar.
                if f.key == schema.URL_PARTS[-1]:
                    row = self._build_url_preview(tab, row)

            ttk.Label(tab, text="* campo requerido", foreground="#777",
                      font=("", 8)).grid(row=row, column=1, sticky="w",
                                         pady=(PAD, 0))

    def _build_url_preview(self, tab, row):
        """Recuadro con la URL de métricas ya armada, en vivo."""
        box = ttk.LabelFrame(tab, text="URL de métricas", padding=PAD)
        box.grid(row=row, column=0, columnspan=2, sticky="ew", pady=(PAD, PAD))
        box.columnconfigure(0, weight=1)

        # Entry de sólo lectura y no un Label: la URL queda siempre en una
        # línea — si no entra, scrollea al costado en vez de partirse — y de
        # paso se puede seleccionar y copiar para probarla en el navegador.
        self.url_var = tk.StringVar()
        self.url_entry = ttk.Entry(
            box, textvariable=self.url_var, state="readonly",
            font=("Courier", 9),
        )
        self.url_entry.grid(row=0, column=0, sticky="ew")

        self.url_note = ttk.Label(box, text="", font=("", 8))
        self.url_note.grid(row=1, column=0, sticky="w", pady=(4, 0))

        # Redibujar ante cualquier cambio en las partes de la URL.
        for key in schema.URL_PARTS:
            if key in self.vars:
                self.vars[key].trace_add("write", lambda *_a: self._refresh_url())
        self._refresh_url()
        return row + 1

    def _refresh_url(self):
        self.url_var.set(schema.metrics_url(self.current_values()))
        # Volver al principio: si el valor anterior estaba scrolleado, el nuevo
        # arrancaría por la mitad y no se vería el esquema ni el host.
        self.url_entry.xview_moveto(0)

        faltan = [
            schema.BY_KEY[k].label
            for k in schema.URL_PARTS
            if k in self.vars and not str(self.vars[k].get()).strip()
        ]
        if faltan:
            self.url_note.configure(
                text=f"Falta completar: {', '.join(faltan)}",
                foreground="#b07000",
            )
        else:
            self.url_note.configure(
                text="Acá se postean los datos, una vez por segundo.",
                foreground="#666",
            )

    def _make_widget(self, parent, f, value, ports):
        var = tk.StringVar(value=value)
        self.vars[f.key] = var

        if f.kind == "choice":
            w = ttk.Combobox(parent, textvariable=var, values=list(f.options),
                             state="readonly")
            if value not in f.options:
                var.set(f.default)
            return w

        if f.kind == "bool":
            holder = ttk.Frame(parent)
            ttk.Checkbutton(
                holder, variable=var, onvalue="true", offvalue="false",
                text="Sí",
            ).pack(side="left")
            if var.get().lower() not in ("true", "false"):
                var.set("false")
            return holder

        if f.kind == "port":
            return ttk.Combobox(parent, textvariable=var, values=ports)

        if f.kind == "path":
            holder = ttk.Frame(parent)
            holder.columnconfigure(0, weight=1)
            ttk.Entry(holder, textvariable=var).grid(row=0, column=0, sticky="ew")
            ttk.Button(holder, text="Buscar…", width=9,
                       command=lambda: self._pick_file(var)).grid(
                row=0, column=1, padx=(4, 0))
            return holder

        if f.kind == "password":
            holder = ttk.Frame(parent)
            holder.columnconfigure(0, weight=1)
            entry = ttk.Entry(holder, textvariable=var, show="•")
            entry.grid(row=0, column=0, sticky="ew")
            shown = tk.BooleanVar(value=False)

            def toggle():
                entry.configure(show="" if shown.get() else "•")

            ttk.Checkbutton(holder, text="Ver", variable=shown,
                            command=toggle).grid(row=0, column=1, padx=(4, 0))
            return holder

        return ttk.Entry(parent, textvariable=var)

    def _build_footer(self):
        frame = ttk.Frame(self.root, padding=(PAD * 2, 0, PAD * 2, PAD * 1.5))
        frame.pack(fill="x")

        self.status = ttk.Label(frame, text="")
        self.status.pack(side="left")

        ttk.Button(frame, text="Guardar", command=self.on_save).pack(
            side="right", padx=(PAD, 0))
        ttk.Button(frame, text="Cancelar", command=self.on_cancel).pack(
            side="right")

    # ----------------------------------------------------------- acciones ---
    def _pick_file(self, var):
        path = filedialog.askopenfilename(
            title="Seleccionar certificado",
            filetypes=[("Certificados", "*.pem *.crt *.cer"), ("Todos", "*.*")],
        )
        if path:
            var.set(path)

    def current_values(self):
        return {key: var.get() for key, var in self.vars.items()}

    def on_save(self):
        values = self.current_values()
        errors = schema.validate(values)

        for key, label in self.error_labels.items():
            label.configure(text=errors.get(key, ""))

        if errors:
            self.status.configure(
                text=f"{len(errors)} campo(s) con problemas", foreground="#b00020"
            )
            return

        try:
            path = schema.save(values, self.existing)
        except (OSError, ValueError) as exc:
            messagebox.showerror("No se pudo guardar", str(exc))
            return

        conservadas = [k for k in self.existing if k not in schema.BY_KEY]
        extra = (f"\n\nSe conservaron sin cambios: {', '.join(conservadas)}"
                 if conservadas else "")
        messagebox.showinfo(
            "Configuración guardada", f"Guardado en:\n{path}{extra}"
        )
        self.root.destroy()

    def on_cancel(self):
        if messagebox.askokcancel("Salir", "Salir sin guardar los cambios?"):
            self.root.destroy()


def run() -> bool:
    """Abre la ventana. Devuelve False si no hay entorno gráfico disponible."""
    try:
        root = tk.Tk()
    except tk.TclError:
        return False

    try:
        ttk.Style().theme_use("vista")
    except tk.TclError:
        pass

    ConfigApp(root)
    root.mainloop()
    return True
