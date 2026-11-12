"""Formato de los mensajes de Telegram (HTML)."""
from __future__ import annotations

from html import escape

from .models import Procedimiento, fmt_fecha

ICONO = {"Alta": "🟢", "Media": "🟡", "Ninguna": "⚪"}
LIMITE = 3900  # Telegram admite 4096 caracteres


def _e(t: str, n: int = 160) -> str:
    t = " ".join((t or "").split())
    return escape(t if len(t) <= n else t[: n - 1] + "…")


NIVEL_TXT = {"Alta": "ALTA", "Media": "MEDIA", "Ninguna": "NULA"}


def _campo(etiqueta: str, valor: str) -> list[str]:
    return [f"<b>{etiqueta}</b>", _e(valor, 300) if valor else "—"]


def formatear(p: Procedimiento) -> str:
    """Solo lo que el equipo necesita, con los mismos datos del detalle de ComprasMX.
    La coincidencia con el historial va al principio."""
    proyecto = bool(p.origen)
    pct = f" ({p.porcentaje}%)" if p.nivel != "Ninguna" else ""
    L = [f"{ICONO[p.nivel]} <b>Coincidencia {NIVEL_TXT[p.nivel]}</b>{pct}"]
    if proyecto:
        L.append("📄 <b>PROYECTO DE CONVOCATORIA</b>")
    L.append("")
    L += _campo("Código del expediente:", p.expediente)
    L += _campo("Código del proyecto de convocatoria:" if proyecto else "Número de procedimiento de contratación:", p.numero)
    L += _campo("Dependencia o Entidad:", p.entidad_completa or p.entidad)
    L += _campo("Nombre del proyecto de convocatoria:" if proyecto else "Nombre del procedimiento de contratación:", p.descripcion)
    L += _campo("Tipo de procedimiento de contratación:", p.tipo)
    L += _campo("Fecha y hora de publicación:", fmt_fecha(p.f_publicacion) if p.f_publicacion else "")
    if proyecto:
        L += _campo("Fecha y hora límite para recepción de comentarios:", fmt_fecha(p.f_comentarios) if p.f_comentarios else "")
    else:
        L += _campo("Fecha y hora de presentación y apertura de proposiciones:", fmt_fecha(p.f_apertura) if p.f_apertura else "")
        L += _campo("Fecha y hora de junta de aclaraciones:", fmt_fecha(p.f_junta) if p.f_junta else "")
        L += _campo("Fecha y hora límite para envío de aclaraciones a través de Compras MX:",
                    fmt_fecha(p.f_aclaraciones) if p.f_aclaraciones else "")
        L += _campo("Fecha y hora del acto del Fallo:", fmt_fecha(p.f_fallo) if p.f_fallo else "")
    L += _campo("Fecha estimada del inicio del contrato:", fmt_fecha(p.f_inicio_contrato) if p.f_inicio_contrato else "")
    L += _campo("Criterio de evaluación:", p.criterio)
    L += _campo("Carácter:", p.caracter)
    if not p.detalle_ok:
        L.append("⚠️ No se pudo leer el detalle completo; revisar en el portal.")
    if p.url:
        L.append(f'🔗 <a href="{escape(p.url, quote=True)}">Abrir en ComprasMX</a>')
    texto = "\n".join(L)
    return texto if len(texto) <= LIMITE else texto[: LIMITE - 1] + "…"


def sin_novedad(ahora) -> str:
    return f"✅ <b>Sin novedad en ComprasMX para EM</b>\n🕒 Revisión: {ahora:%d/%m/%Y %H:%M}"


def resumen(etiqueta: str, nuevos: list[Procedimiento]) -> str:
    altas = sum(1 for p in nuevos if p.nivel == "Alta")
    medias = sum(1 for p in nuevos if p.nivel == "Media")
    return (
        f"📋 <b>Revisión {escape(etiqueta)}</b>: {len(nuevos)} procedimiento(s) nuevo(s)\n"
        f"🟢 Coincidencia alta: {altas} · 🟡 Media: {medias}"
    )


def alerta_error(etiqueta: str, error: str) -> str:
    return f"⚠️ <b>Monitor de compras</b>: falló la revisión de las {escape(etiqueta)}.\n<code>{escape(error[:300])}</code>"
