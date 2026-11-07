"""Diagnostico rapido: abre la pagina de detalle de un procedimiento y vuelca sus tablas varias veces."""
import json
import sys
import time

sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.abspath(__file__))))
from playwright.sync_api import sync_playwright

from monitor.scraper import JS_TABLAS, _n

url = sys.argv[1]
with sync_playwright() as pw:
    b = pw.chromium.launch(headless=True)
    pg = b.new_context(locale="es-MX", viewport={"width": 1500, "height": 1000}).new_page()
    pg.goto(url, wait_until="domcontentloaded")
    pg.wait_for_load_state("networkidle")
    for lectura in range(1, 6):
        time.sleep(3 if lectura > 1 else 6)
        tablas = pg.evaluate(JS_TABLAS)
        print(f"\n===== LECTURA {lectura}: {len(tablas)} tabla(s) =====")
        for i, t in enumerate(tablas):
            hs = [_n(h) for h in t["headers"]]
            print(f"[{i}] headers={hs} filas={len(t['rows'])}")
            if any("descripcion detallada" in h for h in hs):
                for f in t["rows"]:
                    print("     ", " | ".join(x[:34] for x in f["cells"][:6]))
        pag = pg.locator(".p-paginator")
        print("paginadores:", pag.count(), [pag.nth(k).inner_text()[:60].replace("\n", " ") for k in range(min(pag.count(), 4))])
        print("p-table con 'Descripción detallada':", pg.locator("p-table", has=pg.locator("th", has_text="Descripción detallada")).count())
    b.close()
