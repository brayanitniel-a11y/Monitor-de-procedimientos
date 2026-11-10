"""Carga del historial de partidas ofertadas (CSV) agrupado por descripcion."""
from __future__ import annotations

import csv
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

STOP = {"de", "del", "la", "las", "el", "los", "para", "con", "y", "o", "en", "a", "un", "una", "por", "al", "se"}

_MARCAS_DUDOSAS = ("investigación de mercado", "investigacion de mercado", "no equivale", "solicitud de cotización")


def normalizar(texto: str) -> str:
    t = unicodedata.normalize("NFKD", texto or "")
    t = "".join(c for c in t if not unicodedata.combining(c)).lower()
    return re.sub(r"[^a-z0-9]+", " ", t).strip()


def _raiz(tok: str) -> str:
    if len(tok) > 5 and tok.endswith("es"):
        return tok[:-2]
    if len(tok) > 3 and tok.endswith("s"):
        return tok[:-1]
    return tok


def tokens(texto: str) -> list[str]:
    return [_raiz(t) for t in normalizar(texto).split() if t not in STOP]


def solo_digitos(clave: str) -> str:
    return re.sub(r"\D", "", clave or "")


@dataclass
class ItemHistorial:
    descripcion: str
    claves: set[str] = field(default_factory=set)
    marcas: list[str] = field(default_factory=list)
    veces: int = 0
    anios: set[str] = field(default_factory=set)
    procs: list[str] = field(default_factory=list)
    reales: int = 0  # renglones que NO son investigacion de mercado ni precio 0
    toks: list[str] = field(default_factory=list)
    norm: str = ""

    @property
    def dudoso(self) -> bool:
        return self.reales == 0

    @property
    def marca_modelo(self) -> str:
        return " | ".join(self.marcas[:3])

    @property
    def ultimo_anio(self) -> str:
        return max(self.anios) if self.anios else ""


BASE_SIN_PRECIOS = "historial_sin_precios.csv"


def archivos_historial(carpeta: Path) -> list[Path]:
    """Todos los .csv de la carpeta. Si existe el historial completo (con precios) del que se derivó la copia
    sin precios que viene en el repo, esa copia se carga al final y sus renglones repetidos se ignoran."""
    todos = sorted(p for p in Path(carpeta).glob("*.csv"))
    base = [p for p in todos if p.name == BASE_SIN_PRECIOS]
    return [p for p in todos if p.name != BASE_SIN_PRECIOS] + base


def _llave(fila: dict) -> tuple:
    return tuple(normalizar(fila.get(k) or "") for k in (
        "numero_procedimiento", "numero_partida", "descripcion_detallada", "marca", "modelo")) + (
        (fila.get("anio") or "").strip().split(".")[0],)


def cargar(origen) -> list[ItemHistorial]:
    """Carga uno o varios CSV (un archivo, una lista de archivos o una carpeta) y los junta.
    Un renglón que ya vino en un archivo anterior (mismo procedimiento, partida, descripción, marca, modelo
    y año) no se cuenta dos veces. Solo es obligatoria la columna `descripcion_detallada`."""
    if isinstance(origen, (str, Path)):
        origen = Path(origen)
        rutas = archivos_historial(origen) if origen.is_dir() else [origen]
    else:
        rutas = [Path(r) for r in origen]
    grupos: dict[str, ItemHistorial] = {}
    vistas: set[tuple] = set()
    for ruta in rutas:
        de_este_archivo: set[tuple] = set()
        with open(ruta, encoding="utf-8-sig", newline="") as f:
            for fila in csv.DictReader(f):
                llave = _llave(fila)
                if llave in vistas:      # ya venía en un archivo anterior
                    continue
                de_este_archivo.add(llave)
                _sumar(grupos, fila)
        vistas |= de_este_archivo
    return list(grupos.values())


def _sumar(grupos: dict[str, ItemHistorial], fila: dict) -> None:
    desc = (fila.get("descripcion_detallada") or "").strip()
    if not desc:
        return
    clave_g = normalizar(desc)
    it = grupos.get(clave_g)
    if it is None:
        it = ItemHistorial(descripcion=desc, toks=tokens(desc), norm=" ".join(tokens(desc)))
        grupos[clave_g] = it
    c = (fila.get("clave_cucop_o_sai") or "").strip()
    if c:
        it.claves.add(c)
    mm = f"{(fila.get('marca') or '').strip()} {(fila.get('modelo') or '').strip()}".strip()
    if mm and mm not in it.marcas:
        it.marcas.append(mm)
    it.veces += 1
    anio = (fila.get("anio") or "").strip().split(".")[0]
    if anio:
        it.anios.add(anio)
    proc = (fila.get("numero_procedimiento") or "").strip()
    if proc and proc not in it.procs:
        it.procs.append(proc)
    if "solo_cotizacion" in fila:  # copia sin precios (tools/sanitizar_historial.py)
        if (fila["solo_cotizacion"] or "").strip() == "0":
            it.reales += 1
        return
    obs = (fila.get("observaciones") or "").lower()
    try:
        precio = float(fila.get("precio_unitario_sin_iva") or 0)
    except ValueError:
        precio = 0
    # Sin columna de precio (archivo mínimo) no se puede saber: se toma como oferta real
    if "precio_unitario_sin_iva" not in fila or (precio > 0 and not any(m in obs for m in _MARCAS_DUDOSAS)):
        it.reales += 1
