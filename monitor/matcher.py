"""Comparacion de partidas nuevas contra el historial. Sin IA: reglas fijas y texto."""
from __future__ import annotations

from difflib import SequenceMatcher

from .historial import ItemHistorial, solo_digitos, tokens
from .models import Partida, Procedimiento

UMBRAL_ALTA = 0.80
UMBRAL_MEDIA = 0.55
_ORDEN = {"Alta": 2, "Media": 1, "Ninguna": 0}


def clave_coincide(a: str, b: str) -> bool:
    """Coincidencia de claves ignorando puntos/guiones; acepta que una sea prefijo de la otra
    (p. ej. 531.168.0069 vs 531.168.0069.02.01). Se exigen 8+ digitos para evitar falsos positivos."""
    da, db = solo_digitos(a), solo_digitos(b)
    if len(da) < 8 or len(db) < 8:
        return False
    return da == db or da.startswith(db) or db.startswith(da)


def similitud(toks_a: list[str], toks_b: list[str]) -> float:
    sa, sb = set(toks_a), set(toks_b)
    if not sa or not sb:
        return 0.0
    inter = len(sa & sb)
    jacc = inter / len(sa | sb)
    seq = SequenceMatcher(None, " ".join(toks_a), " ".join(toks_b)).ratio()
    score = 0.5 * jacc + 0.5 * seq
    if inter == min(len(sa), len(sb)) and jacc >= 0.5:  # una descripcion contenida en la otra
        score += 0.10
    return min(score, 1.0)


def _nivel(score: float) -> str:
    return "Alta" if score >= UMBRAL_ALTA else "Media" if score >= UMBRAL_MEDIA else "Ninguna"


def evaluar_partida(p: Partida, historial: list[ItemHistorial]) -> Partida:
    toks_p = tokens(p.descripcion)
    mejor: ItemHistorial | None = None
    mejor_score = 0.0
    por_clave = False

    # 1) Clave: si alguna clave del historial coincide, es coincidencia fuerte.
    if p.clave:
        candidatos = [it for it in historial if any(clave_coincide(p.clave, c) for c in it.claves)]
        if candidatos:
            por_clave = True
            mejor = max(candidatos, key=lambda it: similitud(toks_p, it.toks))
            mejor_score = 1.0
    # 2) Descripcion.
    if mejor is None:
        for it in historial:
            s = similitud(toks_p, it.toks)
            if s > mejor_score:
                mejor, mejor_score = it, s

    if mejor is None or (mejor_score < UMBRAL_MEDIA and not por_clave):
        p.nivel, p.porcentaje, p.por_clave = "Ninguna", int(round(mejor_score * 100)), False
        if mejor is not None and mejor_score > 0:
            _copiar(p, mejor)  # se deja como referencia aunque no alcance el umbral
        return p

    p.nivel = "Alta" if por_clave else _nivel(mejor_score)
    p.porcentaje = 100 if por_clave else int(round(mejor_score * 100))
    p.por_clave = por_clave
    _copiar(p, mejor)
    return p


def _copiar(p: Partida, it: ItemHistorial) -> None:
    p.similar_desc = it.descripcion
    p.similar_clave = ", ".join(sorted(it.claves)[:3])
    p.similar_marca_modelo = it.marca_modelo
    p.similar_veces = it.veces
    p.similar_ultimo_anio = it.ultimo_anio
    p.similar_procedimientos = ", ".join(it.procs[:3])
    p.similar_dudoso = it.dudoso


def evaluar_procedimiento(proc: Procedimiento, historial: list[ItemHistorial]) -> Procedimiento:
    if not proc.partidas and proc.descripcion:
        # Sin detalle de partidas: se compara el objeto del procedimiento como respaldo.
        proc.partidas = [Partida(descripcion=proc.descripcion)]
    for p in proc.partidas:
        evaluar_partida(p, historial)
    if proc.partidas:
        mejor = max(proc.partidas, key=lambda x: (_ORDEN[x.nivel], x.porcentaje))
        proc.nivel, proc.porcentaje = mejor.nivel, mejor.porcentaje
    return proc
