#!/usr/bin/env python3
"""Convierte un Markdown de docs/ a PDF, manteniendo el formato.

No requiere pandoc ni instalar nada: arma un HTML con estilos de impresión y lo
imprime con el Chrome/Edge que ya está en Windows, en modo headless.

Uso:
    python tools/md_to_pdf.py docs/protocolo_berry.md
    python tools/md_to_pdf.py docs/protocolo_berry.md -o /tmp/salida.pdf
"""

import argparse
import html
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# ---------------------------------------------------------------- Markdown ---

def _inline(text: str) -> str:
    """Convierte el marcado inline. Los `code` se protegen primero para que su
    contenido no se reinterprete (ej. `**` dentro de un span de código)."""
    spans = []

    def stash(m):
        spans.append(m.group(1))
        return f"\x00{len(spans) - 1}\x00"

    text = re.sub(r"`([^`]+)`", stash, text)
    text = html.escape(text, quote=False)

    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"(?<![\w*])\*([^*\n]+)\*(?![\w*])", r"<em>\1</em>", text)
    text = re.sub(r"~~([^~]+)~~", r"<del>\1</del>", text)

    return re.sub(
        r"\x00(\d+)\x00",
        lambda m: f"<code>{html.escape(spans[int(m.group(1))], quote=False)}</code>",
        text,
    )


def _row(line: str):
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _slug(text: str) -> str:
    s = re.sub(r"<[^>]+>", "", text).lower()
    s = re.sub(r"[^\w\s-]", "", s, flags=re.UNICODE)
    return re.sub(r"[\s]+", "-", s).strip("-")


def markdown_to_html(md: str):
    lines = md.split("\n")
    out, toc = [], []
    list_stack = []          # [(indent, tag)]
    i = 0

    def close_lists(to_indent=-1):
        while list_stack and list_stack[-1][0] > to_indent:
            out.append(f"</{list_stack.pop()[1]}>")

    while i < len(lines):
        line = lines[i]

        # --- bloque de código ---
        if line.lstrip().startswith("```"):
            close_lists()
            lang = line.lstrip()[3:].strip()
            i += 1
            buf = []
            while i < len(lines) and not lines[i].lstrip().startswith("```"):
                buf.append(lines[i])
                i += 1
            i += 1
            cls = f' class="lang-{lang}"' if lang else ""
            out.append(
                f"<pre{cls}><code>{html.escape(chr(10).join(buf), quote=False)}</code></pre>"
            )
            continue

        # --- tabla ---
        if (
            "|" in line
            and i + 1 < len(lines)
            and re.fullmatch(r"\s*\|?[\s:|-]+\|[\s:|-]*", lines[i + 1] or "")
            and "-" in lines[i + 1]
        ):
            close_lists()
            headers = _row(line)
            aligns = []
            for spec in _row(lines[i + 1]):
                if spec.startswith(":") and spec.endswith(":"):
                    aligns.append("center")
                elif spec.endswith(":"):
                    aligns.append("right")
                else:
                    aligns.append("left")
            i += 2
            body = []
            while i < len(lines) and "|" in lines[i] and lines[i].strip():
                body.append(_row(lines[i]))
                i += 1
            th = "".join(
                f'<th style="text-align:{aligns[n] if n < len(aligns) else "left"}">'
                f"{_inline(c)}</th>"
                for n, c in enumerate(headers)
            )
            trs = []
            for cells in body:
                tds = "".join(
                    f'<td style="text-align:{aligns[n] if n < len(aligns) else "left"}">'
                    f"{_inline(c)}</td>"
                    for n, c in enumerate(cells)
                )
                trs.append(f"<tr>{tds}</tr>")
            out.append(
                '<div class="table-wrap"><table><thead><tr>'
                + th
                + "</tr></thead><tbody>"
                + "".join(trs)
                + "</tbody></table></div>"
            )
            continue

        # --- cita ---
        if line.lstrip().startswith(">"):
            close_lists()
            buf = []
            while i < len(lines) and lines[i].lstrip().startswith(">"):
                buf.append(lines[i].lstrip()[1:].lstrip())
                i += 1
            paras = "</p><p>".join(
                p.strip() for p in "\n".join(buf).split("\n\n") if p.strip()
            )
            out.append(f"<blockquote><p>{_inline(paras)}</p></blockquote>")
            continue

        # --- título ---
        m = re.match(r"^(#{1,6})\s+(.*)$", line)
        if m:
            close_lists()
            level, text = len(m.group(1)), _inline(m.group(2).strip())
            anchor = _slug(text)
            if level in (2, 3):
                toc.append((level, text, anchor))
            out.append(f'<h{level} id="{anchor}">{text}</h{level}>')
            i += 1
            continue

        # --- regla horizontal ---
        if re.fullmatch(r"\s*([-*_])\1{2,}\s*", line):
            close_lists()
            out.append("<hr>")
            i += 1
            continue

        # --- lista ---
        m = re.match(r"^(\s*)([-*+]|\d+\.)\s+(.*)$", line)
        if m:
            indent = len(m.group(1))
            tag = "ul" if m.group(2) in "-*+" else "ol"
            if not list_stack or indent > list_stack[-1][0]:
                list_stack.append((indent, tag))
                out.append(f"<{tag}>")
            else:
                close_lists(indent)
                if not list_stack:
                    list_stack.append((indent, tag))
                    out.append(f"<{tag}>")
            item = [m.group(3)]
            i += 1
            # líneas de continuación (indentadas y sin marcador propio)
            while i < len(lines):
                nxt = lines[i]
                if not nxt.strip():
                    break
                if re.match(r"^(\s*)([-*+]|\d+\.)\s+", nxt):
                    break
                if len(nxt) - len(nxt.lstrip()) <= indent:
                    break
                item.append(nxt.strip())
                i += 1
            out.append(f"<li>{_inline(' '.join(item))}</li>")
            continue

        # --- párrafo / vacío ---
        if not line.strip():
            close_lists()
            i += 1
            continue

        close_lists()
        buf = []
        while i < len(lines) and lines[i].strip():
            nxt = lines[i]
            if re.match(r"^(#{1,6})\s|^\s*```|^\s*>", nxt):
                break
            if re.match(r"^(\s*)([-*+]|\d+\.)\s+", nxt):
                break
            if re.fullmatch(r"\s*([-*_])\1{2,}\s*", nxt):
                break
            buf.append(nxt.strip())
            i += 1
        if buf:
            out.append(f"<p>{_inline(' '.join(buf))}</p>")

    close_lists()
    return "\n".join(out), toc


# -------------------------------------------------------------------- CSS ---

CSS = """
@page { size: A4; margin: 16mm 14mm 18mm 14mm; }
* { box-sizing: border-box; }
body {
  font-family: "Segoe UI", "Helvetica Neue", Arial, sans-serif;
  font-size: 9.6pt; line-height: 1.5; color: #1a1d21; margin: 0;
  -webkit-print-color-adjust: exact; print-color-adjust: exact;
}
h1, h2, h3, h4 { line-height: 1.25; margin: 0 0 .4em; font-weight: 600; }
h1 { font-size: 21pt; letter-spacing: -.01em; }
h2 {
  font-size: 14pt; margin-top: 1.5em; padding-bottom: .22em;
  border-bottom: 2px solid #d7dbe0; break-after: avoid;
}
h3 { font-size: 11pt; margin-top: 1.25em; color: #2f3640; break-after: avoid; }
p { margin: .5em 0; orphans: 2; widows: 2; }
a { color: #1257a8; text-decoration: none; }
hr { border: 0; border-top: 1px solid #e2e5e9; margin: 1.5em 0; }
strong { font-weight: 600; color: #10131a; }

code {
  font-family: "Cascadia Mono", Consolas, "Courier New", monospace;
  font-size: .87em; background: #f1f3f5; color: #1c2430;
  padding: .1em .34em; border-radius: 3px; border: 1px solid #e4e8ec;
  white-space: nowrap;
}
pre {
  background: #f7f8fa; border: 1px solid #e2e6ea; border-left: 3px solid #9aa5b1;
  border-radius: 4px; padding: .7em .85em; margin: .8em 0;
  overflow-x: auto; break-inside: avoid;
}
pre code {
  background: none; border: 0; padding: 0; font-size: 8.3pt;
  line-height: 1.45; white-space: pre; color: #232a33;
}

.table-wrap { margin: .85em 0; break-inside: avoid; }
table { border-collapse: collapse; width: 100%; font-size: 8.6pt; }
thead { display: table-header-group; }
th, td {
  border: 1px solid #dce0e5; padding: .4em .55em; vertical-align: top;
}
th { background: #eef1f4; font-weight: 600; text-align: left; }
tbody tr:nth-child(even) { background: #fafbfc; }
td code, th code { font-size: .92em; white-space: nowrap; }

blockquote {
  margin: .85em 0; padding: .55em .9em; background: #fff8e6;
  border-left: 3px solid #e0a93b; border-radius: 0 3px 3px 0;
  break-inside: avoid;
}
blockquote p { margin: .3em 0; }

ul, ol { margin: .5em 0; padding-left: 1.5em; }
li { margin: .25em 0; }
li > ul, li > ol { margin: .2em 0; }

/* portada e índice */
.cover { break-after: page; padding-top: 22mm; }
.cover .subtitle { font-size: 11pt; color: #59616b; margin-top: .2em; }
.cover .meta {
  margin-top: 2.5em; font-size: 9pt; color: #59616b;
  border-top: 1px solid #e2e5e9; padding-top: .9em;
}
.toc { break-after: page; }
.toc h2 { border: 0; margin-top: 0; }
.toc ol { list-style: none; padding-left: 0; margin: 0; }
.toc li { margin: .2em 0; }
.toc .lvl3 { padding-left: 1.4em; font-size: .93em; color: #4a525c; }
.toc a { color: #1a1d21; }
"""


# ------------------------------------------------------------------- main ---

def find_browser():
    for path in (
        "/mnt/c/Program Files/Google/Chrome/Application/chrome.exe",
        "/mnt/c/Program Files (x86)/Google/Chrome/Application/chrome.exe",
        "/mnt/c/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
        "/mnt/c/Program Files/Microsoft/Edge/Application/msedge.exe",
    ):
        if os.path.exists(path):
            return path
    for name in ("chromium", "chromium-browser", "google-chrome"):
        found = shutil.which(name)
        if found:
            return found
    return None


def to_windows_path(p: Path) -> str:
    out = subprocess.run(
        ["wslpath", "-w", str(p)], capture_output=True, text=True, check=True
    )
    return out.stdout.strip()


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("source", help="archivo .md de entrada")
    ap.add_argument("-o", "--output", help="PDF de salida (default: junto al .md)")
    ap.add_argument("--keep-html", action="store_true", help="conservar el HTML intermedio")
    args = ap.parse_args()

    src = Path(args.source).resolve()
    if not src.exists():
        sys.exit(f"No existe: {src}")
    dst = Path(args.output).resolve() if args.output else src.with_suffix(".pdf")

    md = src.read_text(encoding="utf-8")
    body, toc = markdown_to_html(md)

    m = re.search(r"^#\s+(.*)$", md, re.M)
    title = re.sub(r"<[^>]+>", "", _inline(m.group(1))) if m else src.stem
    doc_title, _, subtitle = title.partition("—")

    toc_html = "".join(
        f'<li class="lvl{lvl}"><a href="#{anchor}">{text}</a></li>'
        for lvl, text, anchor in toc
    )

    page = f"""<!doctype html>
<html lang="es"><head><meta charset="utf-8">
<title>{html.escape(title)}</title><style>{CSS}</style></head><body>
<div class="cover">
  <h1>{html.escape(doc_title.strip())}</h1>
  {f'<div class="subtitle">{html.escape(subtitle.strip())}</div>' if subtitle.strip() else ''}
  <div class="meta">
    Documentación técnica · generado desde <code>{html.escape(src.name)}</code>
  </div>
</div>
<div class="toc"><h2>Contenido</h2><ol>{toc_html}</ol></div>
{body}
</body></html>"""

    browser = find_browser()
    if not browser:
        sys.exit("No se encontró Chrome/Edge/Chromium para imprimir el PDF.")

    windows_browser = browser.startswith("/mnt/")
    # Chrome de Windows no puede leer rutas de WSL: se trabaja en un temporal de C:
    tmp_root = Path("/mnt/c/Windows/Temp") if windows_browser else Path(tempfile.gettempdir())
    with tempfile.TemporaryDirectory(dir=str(tmp_root)) as tmp:
        tmp = Path(tmp)
        html_path, pdf_path = tmp / "doc.html", tmp / "doc.pdf"
        html_path.write_text(page, encoding="utf-8")

        if windows_browser:
            src_arg = "file:///" + to_windows_path(html_path).replace("\\", "/")
            out_arg = to_windows_path(pdf_path)
        else:
            src_arg, out_arg = html_path.as_uri(), str(pdf_path)

        proc = subprocess.run(
            [
                browser, "--headless=new", "--disable-gpu", "--no-sandbox",
                "--no-pdf-header-footer",
                "--run-all-compositor-stages-before-draw",
                "--virtual-time-budget=10000",
                f"--print-to-pdf={out_arg}", src_arg,
            ],
            capture_output=True, text=True, timeout=180,
        )
        if not pdf_path.exists():
            sys.exit(f"Falló la impresión.\n{proc.stdout}\n{proc.stderr}")

        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(pdf_path, dst)
        if args.keep_html:
            shutil.copyfile(html_path, dst.with_suffix(".html"))

    print(f"OK  {dst}  ({dst.stat().st_size / 1024:.0f} KB)")


if __name__ == "__main__":
    main()
