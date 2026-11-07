"""Valida la lectura real de partidas de un detalle (usa el mismo codigo que el monitor). Uso: diag_leer.py URL [esperadas]"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from monitor import settings  # noqa: E402
from monitor.models import Procedimiento  # noqa: E402
from monitor.scraper import Portal  # noqa: E402

url = sys.argv[1]
esperadas = int(sys.argv[2]) if len(sys.argv) > 2 else None
cfg = settings.cargar()
resultados = []
with Portal(cfg) as portal:
    for intento in range(1, 4):
        portal.page.goto(url, wait_until="domcontentloaded")
        portal.page.wait_for_load_state("networkidle")
        proc = Procedimiento(numero=f"DIAG-{intento}", url=url)
        portal._leer_detalle(proc)
        resultados.append(len(proc.partidas))
        print(f"Lectura {intento}: {len(proc.partidas)} partidas; claves={proc.claves_filtro}", flush=True)
print("RESULTADOS:", resultados)
ok = len(set(resultados)) == 1 and (esperadas is None or resultados[0] == esperadas)
print("ESTABLE Y CORRECTO" if ok else "INESTABLE O DISTINTO DE LO ESPERADO")
sys.exit(0 if ok else 1)
