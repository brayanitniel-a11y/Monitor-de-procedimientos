"""Explora el menu 'Procesos' del portal y la seccion 'Proyectos de Convocatoria' (solo lectura)."""
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from playwright.sync_api import sync_playwright  # noqa: E402

from monitor import settings  # noqa: E402
from monitor.scraper import JS_TABLAS  # noqa: E402

JS_PAGINA = """() => {
  const vis = e => !!(e.offsetWidth || e.offsetHeight);
  const t = e => (e.innerText || '').replace(/\\s+/g, ' ').trim();
  return {
    url: location.href,
    titulo: document.title,
    menu: Array.from(document.querySelectorAll('nav a, nav button, nav li, aside a, aside li, header a, header button, [role=menuitem], .p-menuitem-link, .p-menubar li, .p-panelmenu-header, .p-tabview-nav li, [role=tab]'))
            .filter(vis).map(t).filter(x => x && x.length < 70).slice(0, 80),
    dropdowns: Array.from(document.querySelectorAll('p-dropdown, p-multiselect, p-calendar, p-autocomplete')).map(e => ({tag: e.tagName, name: e.getAttribute('name') || e.getAttribute('formcontrolname') || '', visible: vis(e)})),
    inputs: Array.from(document.querySelectorAll('input, textarea')).map(e => ({name: e.name || '', id: e.id, ph: e.placeholder || '', visible: vis(e)})),
    etiquetas: Array.from(document.querySelectorAll('label, mat-label, legend, .p-float-label span')).filter(vis).map(t).filter(Boolean).slice(0, 60),
    botones: Array.from(document.querySelectorAll('button, [role=button]')).filter(vis).map(t).filter(Boolean).slice(0, 40),
    texto: (document.body.innerText || '').slice(0, 2500),
  };
}"""


def volcar(titulo, pg):
    print(f"\n######## {titulo} ########", flush=True)
    info = pg.evaluate(JS_PAGINA)
    for k, v in info.items():
        print(f"--- {k}:", json.dumps(v, ensure_ascii=False)[:2600], flush=True)
    for i, tb in enumerate(pg.evaluate(JS_TABLAS)[:4]):
        print(f"--- tabla[{i}] headers={tb['headers']} filas={len(tb['rows'])}", flush=True)
        for f in tb["rows"][:2]:
            print("      ", " | ".join(x[:40] for x in f["cells"][:9]), "| href=", f["href"][:90], flush=True)


cfg = settings.cargar()
with sync_playwright() as pw:
    b = pw.chromium.launch(headless=True)
    pg = b.new_context(locale="es-MX", viewport={"width": 1500, "height": 1000}).new_page()
    pg.goto(cfg.url, wait_until="domcontentloaded")
    pg.wait_for_load_state("networkidle")
    time.sleep(3)
    volcar("INICIO", pg)
    for patron in (r"^\s*Procesos\s*$", r"Procesos"):
        try:
            pg.get_by_text(re.compile(patron)).first.click(timeout=6000)
            time.sleep(2)
            break
        except Exception as e:
            print("no pude abrir 'Procesos' con", patron, "->", str(e)[:100])
    volcar("TRAS ABRIR 'PROCESOS'", pg)
    try:
        pg.get_by_text(re.compile(r"Proyectos? de Convocatoria", re.I)).first.click(timeout=8000)
        pg.wait_for_load_state("networkidle")
        time.sleep(3)
        volcar("PROYECTOS DE CONVOCATORIA", pg)
        try:
            pg.get_by_text(re.compile(r"^\s*Filtros\s*$")).first.click(timeout=5000)
            time.sleep(2)
            volcar("PROYECTOS DE CONVOCATORIA · FILTROS ABIERTOS", pg)
        except Exception as e:
            print("sin boton Filtros:", str(e)[:100])
    except Exception as e:
        print("No encontre 'Proyectos de Convocatoria':", str(e)[:200])
    b.close()
