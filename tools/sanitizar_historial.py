"""Genera una copia del historial SIN precios, licitantes ni observaciones, apta para repo publico.

Uso: python tools/sanitizar_historial.py <historial_completo.csv> [salida.csv]
Conserva solo lo necesario para comparar (clave, descripcion, marca/modelo, procedimiento, anio) y una
bandera `solo_cotizacion` (1 si el renglon tenia precio 0 o era investigacion de mercado).
"""
import csv
import sys
from pathlib import Path

DUDOSAS = ("investigación de mercado", "investigacion de mercado", "no equivale", "solicitud de cotización")
COLS = ["numero_procedimiento", "numero_partida", "clave_cucop_o_sai", "descripcion_detallada",
        "marca", "modelo", "anio", "solo_cotizacion"]

src = Path(sys.argv[1])
dst = Path(sys.argv[2]) if len(sys.argv) > 2 else Path(__file__).resolve().parent.parent / "data" / "historial" / "historial_sin_precios.csv"
with open(src, encoding="utf-8-sig", newline="") as f, open(dst, "w", encoding="utf-8", newline="") as g:
    w = csv.DictWriter(g, fieldnames=COLS)
    w.writeheader()
    for fila in csv.DictReader(f):
        try:
            precio = float(fila.get("precio_unitario_sin_iva") or 0)
        except ValueError:
            precio = 0
        obs = (fila.get("observaciones") or "").lower()
        fila["solo_cotizacion"] = "0" if precio > 0 and not any(m in obs for m in DUDOSAS) else "1"
        fila["anio"] = (fila.get("anio") or "").split(".")[0]
        w.writerow({k: fila.get(k, "") for k in COLS})
print("Listo:", dst)
