"""Calculo del rango de fechas de publicacion a consultar en cada revision."""
from __future__ import annotations

from datetime import date, datetime, timedelta


def calcular_ventana(
    ahora: datetime,
    ultimo_ok: date | None,
    hora_corte: int = 10,
    max_dias: int = 7,
) -> tuple[date, date]:
    """Regla del equipo:
    - Primera revision de la manana (antes de hora_corte): de AYER a HOY (el dia anterior completo
      mas lo publicado en la madrugada).
    - Revisiones del dia y de la tarde/noche: solo el dia en curso.

    Si la ultima revision exitosa quedo antes del inicio de la ventana (equipo apagado,
    fallas del portal), se extiende hacia atras hasta ese dia (maximo max_dias) para no
    perder procedimientos. Los duplicados se evitan por numero de procedimiento.
    """
    hoy = ahora.date()
    if ahora.hour < hora_corte:
        desde, hasta = hoy - timedelta(days=1), hoy
    else:
        desde = hasta = hoy
    if ultimo_ok is not None and ultimo_ok < desde:
        desde = max(ultimo_ok, hoy - timedelta(days=max_dias))
    return desde, hasta
