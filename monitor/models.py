"""Modelos de datos y utilidades de fechas."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime

_RE_FECHA = re.compile(r"(\d{1,2})/(\d{1,2})/(\d{4})(?:\s+(\d{1,2}):(\d{2}))?")
_RE_ISO = re.compile(r"(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2}))?")


def parse_fecha(texto: str) -> str:
    """Devuelve la fecha en ISO (YYYY-MM-DDTHH:MM) o '' si no se reconoce."""
    if not texto:
        return ""
    m = _RE_FECHA.search(texto)
    if m:
        d, mo, y, h, mi = m.groups()
        try:
            return datetime(int(y), int(mo), int(d), int(h or 0), int(mi or 0)).strftime("%Y-%m-%dT%H:%M")
        except ValueError:
            return ""
    m = _RE_ISO.search(texto)
    if m:
        y, mo, d, h, mi = m.groups()
        try:
            return datetime(int(y), int(mo), int(d), int(h or 0), int(mi or 0)).strftime("%Y-%m-%dT%H:%M")
        except ValueError:
            return ""
    return ""


def fmt_fecha(iso: str) -> str:
    """ISO -> 'dd/mm/yyyy HH:MM' (sin hora si es medianoche)."""
    if not iso:
        return "—"
    dt = datetime.strptime(iso, "%Y-%m-%dT%H:%M")
    if dt.hour == 0 and dt.minute == 0:
        return dt.strftime("%d/%m/%Y")
    return dt.strftime("%d/%m/%Y %H:%M")


@dataclass
class Partida:
    numero: str = ""
    clave: str = ""
    descripcion: str = ""
    cantidad: str = ""
    # Resultado de la comparacion con el historial
    nivel: str = "Ninguna"
    porcentaje: int = 0
    por_clave: bool = False
    similar_desc: str = ""
    similar_clave: str = ""
    similar_marca_modelo: str = ""
    similar_veces: int = 0
    similar_ultimo_anio: str = ""
    similar_procedimientos: str = ""
    similar_dudoso: bool = False


@dataclass
class Procedimiento:
    numero: str
    entidad: str = ""
    descripcion: str = ""
    tipo: str = ""
    estatus: str = ""
    f_publicacion: str = ""
    f_junta: str = ""
    f_apertura: str = ""
    f_fallo: str = ""
    url: str = ""
    claves_filtro: list[str] = field(default_factory=list)
    partidas: list[Partida] = field(default_factory=list)
    nivel: str = "Ninguna"
    porcentaje: int = 0
    detalle_ok: bool = True
    origen: str = ""          # "" = procedimiento de contratación; "PROYECTO DE CONVOCATORIA"
    expediente: str = ""
    entidad_completa: str = ""
    f_aclaraciones: str = ""    # límite para enviar aclaraciones por ComprasMX
    f_inicio_contrato: str = ""
    criterio: str = ""
    caracter: str = ""
    f_comentarios: str = ""   # solo proyectos: fecha límite para recibir comentarios
    detectado: str = ""
    notificado: bool = False
