"""Registro en Excel. Solo AGREGA filas nuevas: lo que el equipo marque (Revisado,
probabilidad, notas) nunca se sobrescribe."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from openpyxl import Workbook, load_workbook
from openpyxl.formatting.rule import FormulaRule
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

from .estado import Estado
from .models import Procedimiento

HOJA_PROC = "Procedimientos"
HOJA_PART = "Partidas"
MAX_FILAS = 5000

COLS_PROC = [
    ("Detectado", 17), ("Número de procedimiento", 34), ("Entidad / Dependencia", 34),
    ("Descripción", 50), ("Tipo", 16), ("Estatus", 14), ("Publicación", 16),
    ("Junta de aclaraciones", 18), ("Presentación y apertura", 18), ("Fallo", 16),
    ("Partidas", 9), ("Coincidencia historial", 14), ("% similitud", 10),
    ("Partidas con coincidencia", 70), ("Enlace", 10),
    ("Probabilidad de ofertar (manual)", 20), ("Revisado", 11), ("Notas", 40),
]
COLS_PART = [
    ("Número de procedimiento", 34), ("Partida", 9), ("Clave", 16), ("Descripción", 55),
    ("Cantidad", 10), ("Coincidencia", 12), ("% similitud", 10),
    ("Partida similar en historial", 55), ("Clave(s) historial", 22),
    ("Marca / modelo ofertado", 34), ("Veces ofertada", 10), ("Último año", 10),
    ("Procedimientos donde se ofertó", 45), ("Solo cotización / inv. mercado", 14),
]
IDX_REVISADO = [c[0] for c in COLS_PROC].index("Revisado") + 1
IDX_PROB = [c[0] for c in COLS_PROC].index("Probabilidad de ofertar (manual)") + 1
IDX_NIVEL = [c[0] for c in COLS_PROC].index("Coincidencia historial") + 1


def _dt(iso: str):
    return datetime.strptime(iso, "%Y-%m-%dT%H:%M") if iso else None


def _crear(ruta: Path) -> Workbook:
    wb = Workbook()
    ws = wb.active
    ws.title = HOJA_PROC
    wp = wb.create_sheet(HOJA_PART)
    cab = PatternFill("solid", fgColor="1F3864")
    for hoja, cols in ((ws, COLS_PROC), (wp, COLS_PART)):
        for i, (nombre, ancho) in enumerate(cols, 1):
            c = hoja.cell(row=1, column=i, value=nombre)
            c.font = Font(bold=True, color="FFFFFF")
            c.fill = cab
            c.alignment = Alignment(wrap_text=True, vertical="center")
            hoja.column_dimensions[get_column_letter(i)].width = ancho
        hoja.freeze_panes = "C2"
        hoja.auto_filter.ref = f"A1:{get_column_letter(len(cols))}1"
        hoja.row_dimensions[1].height = 32

    dv_rev = DataValidation(type="list", formula1='"Sí,No"', allow_blank=False)
    dv_prob = DataValidation(type="list", formula1='"Alta,Media,Baja,No ofertar"', allow_blank=True)
    ws.add_data_validation(dv_rev)
    ws.add_data_validation(dv_prob)
    dv_rev.add(f"{get_column_letter(IDX_REVISADO)}2:{get_column_letter(IDX_REVISADO)}{MAX_FILAS}")
    dv_prob.add(f"{get_column_letter(IDX_PROB)}2:{get_column_letter(IDX_PROB)}{MAX_FILAS}")

    rev = get_column_letter(IDX_REVISADO)
    nivel = get_column_letter(IDX_NIVEL)
    rango = f"A2:{get_column_letter(len(COLS_PROC))}{MAX_FILAS}"
    ws.conditional_formatting.add(rango, FormulaRule(formula=[f'${rev}2="No"'], fill=PatternFill("solid", bgColor="FFF2CC")))
    ws.conditional_formatting.add(rango, FormulaRule(formula=[f'${rev}2="Sí"'], fill=PatternFill("solid", bgColor="E2EFDA")))
    ws.conditional_formatting.add(f"{nivel}2:{nivel}{MAX_FILAS}", FormulaRule(formula=[f'${nivel}2="Alta"'], font=Font(bold=True, color="006100")))
    ws.conditional_formatting.add(f"{nivel}2:{nivel}{MAX_FILAS}", FormulaRule(formula=[f'${nivel}2="Media"'], font=Font(bold=True, color="9C5700")))
    return wb


def _resumen_partidas(p: Procedimiento) -> str:
    lineas = []
    con = [x for x in p.partidas if x.nivel != "Ninguna"]
    for x in sorted(con, key=lambda x: -x.porcentaje)[:8]:
        lineas.append(f"[{x.nivel} {x.porcentaje}%] {x.numero or '-'} {x.descripcion[:60]} → {x.similar_desc[:50]}")
    return "\n".join(lineas)


def sincronizar(estado: Estado, ruta: Path) -> int:
    """Agrega al Excel los procedimientos que aun no estan. Devuelve cuantas filas agrego."""
    wb = load_workbook(ruta) if ruta.exists() else _crear(ruta)
    ws, wp = wb[HOJA_PROC], wb[HOJA_PART]
    existentes = {" ".join(str(ws.cell(row=r, column=2).value or "").upper().split()) for r in range(2, ws.max_row + 1)}
    nuevos = [p for k, p in estado.procedimientos.items() if k not in existentes]
    nuevos.sort(key=lambda p: p.detectado)
    for p in nuevos:
        fila = ws.max_row + 1 if ws.max_row > 1 or ws.cell(row=2, column=2).value else 2
        valores = [
            _dt(p.detectado), p.numero, p.entidad, p.descripcion, (f"{p.origen} · {p.tipo}" if p.origen else p.tipo), p.estatus,
            _dt(p.f_publicacion), _dt(p.f_junta), _dt(p.f_comentarios if p.origen else p.f_apertura), _dt(p.f_fallo),
            len(p.partidas), p.nivel, p.porcentaje, _resumen_partidas(p), None, None, "No", None,
        ]
        for i, v in enumerate(valores, 1):
            c = ws.cell(row=fila, column=i, value=v)
            c.alignment = Alignment(wrap_text=True, vertical="top")
            if isinstance(v, datetime):
                c.number_format = "dd/mm/yyyy hh:mm"
        if p.url:
            c = ws.cell(row=fila, column=len(COLS_PROC) - 3, value="Abrir")
            c.hyperlink = p.url
            c.font = Font(color="0563C1", underline="single")
        for x in p.partidas:
            wp.append([
                p.numero, x.numero, x.clave, x.descripcion, x.cantidad, x.nivel, x.porcentaje,
                x.similar_desc, x.similar_clave, x.similar_marca_modelo, x.similar_veces or None,
                x.similar_ultimo_anio, x.similar_procedimientos, "Sí" if x.similar_dudoso else "",
            ])
    wb.save(ruta)
    return len(nuevos)
