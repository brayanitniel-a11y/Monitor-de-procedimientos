"""Formato de los mensajes de Telegram (HTML)."""
from __future__ import annotations

from html import escape

from .models import Procedimiento, fmt_fecha

ICONO = {"Alta": "🟢", "Media": "🟡", "Ninguna": "⚪"}
LIMITE = 3900  # Telegram admite 4096 caracteres


def _e(t: str, n: int = 160) -> str:
    t = " ".join((t or "").split())
    return escape(t if len(t) <= n else t[: n - 1] + "…")


def formatear(p: Procedimiento) -> str:
    proyecto = bool(p.origen)
    titulo = "NUEVO PROYECTO DE CONVOCATORIA" if proyecto else "NUEVO"
    L = [f"{ICONO[p.nivel]} <b>{titulo} · {_e(p.numero, 80)}</b>"]
    if p.entidad:
        L.append(f"🏛 {_e(p.entidad)}")
    if p.descripcion:
        L.append(f"📝 {_e(p.descripcion, 220)}")
    L.append("")
    L.append(f"📅 Publicación: {fmt_fecha(p.f_publicacion)}")
    if proyecto:
        L.append(f"💬 Límite para comentarios: <b>{fmt_fecha(p.f_comentarios)}</b>")
        L.append("ℹ️ Es un proyecto: aún no es el procedimiento; sirve para anticipar y comentar.")
    else:
        L.append(f"❓ Junta de aclaraciones: {fmt_fecha(p.f_junta)}")
        L.append(f"📨 Presentación y apertura: <b>{fmt_fecha(p.f_apertura)}</b>")
        L.append(f"⚖️ Fallo: {fmt_fecha(p.f_fallo)}")
    if p.claves_filtro:
        L.append(f"🔑 Clave(s): {_e(', '.join(p.claves_filtro))}")
    L.append("")
    if p.nivel == "Ninguna":
        L.append("📚 Historial: sin coincidencia clara con lo que hemos ofertado")
    else:
        L.append(f"📚 Historial: <b>coincidencia {p.nivel.upper()}</b> ({p.porcentaje}%)")
    con = sorted((x for x in p.partidas if x.nivel != "Ninguna"), key=lambda x: -x.porcentaje)
    for x in con[:5]:
        marca = f" · {_e(x.similar_marca_modelo, 60)}" if x.similar_marca_modelo else ""
        veces = f" · {x.similar_veces}x" if x.similar_veces else ""
        aviso = " (solo cotización)" if x.similar_dudoso else ""
        L.append(f"• Partida {_e(x.numero or '-', 10)}: {_e(x.descripcion, 90)}\n   ↳ posible partida de interés: {_e(x.similar_desc, 80)}{marca}{veces}{aviso}")
    if len(con) > 5:
        L.append(f"… y {len(con) - 5} partidas más con coincidencia (ver Excel)")
    sin = len(p.partidas) - len(con)
    if con and sin > 0:
        L.append(f"({sin} partida(s) sin coincidencia)")
    if not p.detalle_ok:
        L.append("⚠️ No se pudo leer el detalle completo; revisar en el portal.")
    if p.url:
        L.append(f'🔗 <a href="{escape(p.url, quote=True)}">Abrir en ComprasMX</a>')
    texto = "\n".join(L)
    return texto if len(texto) <= LIMITE else texto[: LIMITE - 1] + "…"


def resumen(etiqueta: str, nuevos: list[Procedimiento]) -> str:
    altas = sum(1 for p in nuevos if p.nivel == "Alta")
    medias = sum(1 for p in nuevos if p.nivel == "Media")
    return (
        f"📋 <b>Revisión {escape(etiqueta)}</b>: {len(nuevos)} procedimiento(s) nuevo(s)\n"
        f"🟢 Coincidencia alta: {altas} · 🟡 Media: {medias}"
    )


def alerta_error(etiqueta: str, error: str) -> str:
    return f"⚠️ <b>Monitor de compras</b>: falló la revisión de las {escape(etiqueta)}.\n<code>{escape(error[:300])}</code>"
