"""Explora el filtro 'Proceso' del portal (Procedimiento de contratación / Proyecto de convocatoria). Solo lectura."""
import json
import os
import re
import sys
import time
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from monitor import settings  # noqa: E402
from monitor.scraper import JS_TABLAS, Portal  # noqa: E402

JS_PAGINA = """() => {
  const vis = e => !!(e.offsetWidth || e.offsetHeight);
  const t = e => (e.innerText || '').replace(/\\s+/g, ' ').trim();
  return {
    url: location.href,
    dropdowns: Array.from(document.querySelectorAll('p-dropdown, p-multiselect, p-calendar, p-autocomplete')).map(e => ({tag: e.tagName, name: e.getAttribute('name') || e.getAttribute('formcontrolname') || '', visible: vis(e)})),
    inputs: Array.from(document.querySelectorAll('input')).map(e => ({name: e.name || '', ph: e.placeholder || '', visible: vis(e)})).filter(x => x.visible),
    etiquetas: Array.from(document.querySelectorAll('label, legend, .p-float-label span')).filter(vis).map(t).filter(Boolean).slice(0, 60),
    botones: Array.from(document.querySelectorAll('button, [role=button]')).filter(vis).map(t).filter(Boolean).slice(0, 40),
    texto: (document.body.innerText || '').slice(0, 1800),
  };
}"""


def volcar(titulo, pg):
    print(f"\n######## {titulo} ########", flush=True)
    for k, v in pg.evaluate(JS_PAGINA).items():
        print(f"--- {k}:", json.dumps(v, ensure_ascii=False)[:2200], flush=True)
    for i, tb in enumerate(pg.evaluate(JS_TABLAS)[:4]):
        print(f"--- tabla[{i}] headers={tb['headers']} filas={len(tb['rows'])}", flush=True)
        for f in tb["rows"][:3]:
            print("      ", " | ".join(x[:40] for x in f["cells"][:10]), "| href=", f["href"][:90], flush=True)


cfg = settings.cargar()
cfg.headless = True
hoy = date.today()
with Portal(cfg, debug_dir=None) as portal:
    portal._abrir_filtros()
    opciones = portal._opciones_dropdown("proceso")
    print("OPCIONES de 'proceso':", opciones, flush=True)
    portal._elegir_dropdown("proceso", re.compile("convocatoria", re.I))
    volcar("PROYECTO DE CONVOCATORIA · tras elegir", portal.page)
    for etapa in ("ley", "contratacion", "claves", "fechaDesdeP"):
        n = portal.page.locator(f"[name='{etapa}']").count()
        print(f"campo {etapa}: {n}", flush=True)
    try:
        portal._elegir_dropdown("ley", portal.LEY)
        print("tipos:", portal._opciones_dropdown("contratacion"), flush=True)
    except Exception as e:
        print("ley/contratacion no disponibles:", str(e)[:150], flush=True)
    for paso, f in (("claves", lambda: portal._elegir_claves(cfg.claves)),
                    ("desde", lambda: portal._fijar_fecha("fechaDesdeP", hoy - timedelta(days=30))),
                    ("hasta", lambda: portal._fijar_fecha("fechaHastaP", hoy))):
        try:
            f()
            print("ok", paso, flush=True)
        except Exception as e:
            print("falló", paso, str(e)[:150], flush=True)
    volcar("ANTES DE BUSCAR", portal.page)
    try:
        portal._pulsar_buscar()
        time.sleep(3)
        volcar("RESULTADOS PROYECTOS (30 días)", portal.page)
        print("TOTAL:", re.search(r"Total:\s*(\d+)", portal.page.inner_text("body")), flush=True)
    except Exception as e:
        print("falló buscar:", str(e)[:200], flush=True)
