"""Punto de entrada. Uso:

  python -m monitor.main                 # revision normal (la que programas 4 veces al dia)
  python -m monitor.main --dry-run       # busca y muestra mensajes, sin enviar ni guardar nada
  python -m monitor.main --demo          # prueba con datos de ejemplo (sin portal ni Telegram)
  python -m monitor.main --probar-telegram  # mensaje de prueba al grupo
  python -m monitor.main --descubrir     # guarda captura y campos del portal para ajustar el scraper
  python -m monitor.main --desde 01/11/2026 --hasta 05/11/2026   # rango manual
"""
from __future__ import annotations

import argparse
import json
import logging
import logging.handlers
import os
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

from . import historial, matcher, mensajes, registro, settings, telegram
from .estado import Estado
from .models import Partida, Procedimiento
from .ventana import calcular_ventana

log = logging.getLogger("monitor")
DEMO_JSON = Path(__file__).resolve().parent.parent / "tests" / "demo_procedimientos.json"


def _configurar_log(data_dir: Path) -> None:
    (data_dir / "logs").mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    fh = logging.handlers.RotatingFileHandler(data_dir / "logs" / "monitor.log", maxBytes=1_000_000, backupCount=5, encoding="utf-8")
    fh.setFormatter(fmt)
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    log.setLevel(logging.INFO)
    log.handlers[:] = [fh, sh]


class _Candado:
    """Evita que dos revisiones corran al mismo tiempo (mismo archivo Excel / mismo navegador)."""

    def __init__(self, ruta: Path, vigencia_min: int = 30):
        self.ruta, self.vigencia = ruta, vigencia_min * 60

    def __enter__(self):
        if self.ruta.exists() and time.time() - self.ruta.stat().st_mtime < self.vigencia:
            raise SystemExit("Ya hay una revisión en curso; se omite esta.")
        self.ruta.write_text(str(os.getpid()))
        return self

    def __exit__(self, *exc):
        self.ruta.unlink(missing_ok=True)


def _fecha_arg(txt: str) -> date:
    for f in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(txt, f).date()
        except ValueError:
            pass
    raise SystemExit(f"Fecha no válida: {txt} (usa dd/mm/aaaa)")


def _cargar_demo() -> list[Procedimiento]:
    datos = json.loads(DEMO_JSON.read_text(encoding="utf-8"))
    return [Procedimiento(**{**d, "partidas": [Partida(**x) for x in d.get("partidas", [])]}) for d in datos]


def _fusionar(lista: list[Procedimiento]) -> list[Procedimiento]:
    """Une resultados de varias claves: un procedimiento puede salir en ambas busquedas."""
    unicos: dict[str, Procedimiento] = {}
    for p in lista:
        k = Estado.clave(p.numero)
        if k in unicos:
            for c in p.claves_filtro:
                if c not in unicos[k].claves_filtro:
                    unicos[k].claves_filtro.append(c)
        else:
            unicos[k] = p
    return list(unicos.values())


def ejecutar(args) -> int:
    cfg = settings.cargar(demo=args.demo)
    _configurar_log(cfg.data_dir)

    if args.probar_telegram:
        ok = telegram.enviar(cfg.token, cfg.chat_id, "✅ <b>Monitor de procedimientos</b>: prueba de conexión con el grupo.")
        print("Telegram:", "enviado" if ok else "FALLÓ (revisa token e ID de grupo)")
        return 0 if ok else 1

    if args.descubrir:
        from .scraper import Portal
        with Portal(cfg) as portal:
            ruta = portal.descubrir()
        print(f"Listo. Revisa {ruta} y la captura inicio.png en la misma carpeta.")
        return 0

    with _Candado(cfg.data_dir / "monitor.lock"):
        return _revision(cfg, args)


def _revision(cfg, args) -> int:
    estado = Estado.cargar(cfg.data_dir / "estado.json")
    items = historial.cargar(cfg.historial_csv)
    ahora = datetime.now(cfg.tz)
    etiqueta = ahora.strftime("%H:%M")
    desde, hasta = calcular_ventana(ahora, estado.ultimo_ok, cfg.hora_corte)
    if args.desde:
        desde = _fecha_arg(args.desde)
        hasta = _fecha_arg(args.hasta) if args.hasta else desde
    log.info("Revisión %s · publicados del %s al %s · claves %s", etiqueta, desde, hasta, ",".join(cfg.claves))

    enviar_real = not (args.dry_run or cfg.demo)
    try:
        if cfg.demo:
            candidatos = _fusionar(_cargar_demo())
            nuevos = [p for p in candidatos if not estado.tiene(p.numero)]
        else:
            from .scraper import Portal
            with Portal(cfg) as portal:
                candidatos = _fusionar(portal.buscar_todo(cfg.claves, desde, hasta))
                nuevos = [p for p in candidatos if not estado.tiene(p.numero)]
                for p in nuevos:
                    portal.completar_detalle(p)
    except Exception as e:
        log.exception("La revisión falló")
        if enviar_real and estado.puede_alertar(ahora):
            if telegram.enviar(cfg.token, cfg.chat_id, mensajes.alerta_error(etiqueta, f"{type(e).__name__}: {e}")):
                estado.ultima_alerta = ahora.isoformat(timespec="seconds")
                estado.guardar()
        return 1

    for p in nuevos:
        matcher.evaluar_procedimiento(p, items)
        p.detectado = ahora.strftime("%Y-%m-%dT%H:%M")
    log.info("%s encontrados, %s nuevos", len(candidatos), len(nuevos))

    if args.dry_run:
        for p in nuevos:
            print("\n" + "=" * 60 + "\n" + mensajes.formatear(p))
        print(f"\n[dry-run] {len(nuevos)} nuevos. No se envió ni guardó nada.")
        return 0

    for p in nuevos:
        estado.agregar(p)
    estado.ultimo_ok = ahora.date()
    estado.guardar()  # primero guardar: si Telegram o Excel fallan, no se pierde ni se repite nada

    pendientes = sorted(estado.pendientes(), key=lambda p: p.detectado)
    if pendientes:
        textos = []
        if len(pendientes) > 1:
            textos.append((None, mensajes.resumen(etiqueta, pendientes)))
        textos += [(p, mensajes.formatear(p)) for p in pendientes]
        for p, texto in textos:
            if cfg.demo:
                print("\n" + "=" * 60 + "\n" + texto)
                ok = True
            else:
                ok = telegram.enviar(cfg.token, cfg.chat_id, texto)
                time.sleep(1.2)  # limite de Telegram en grupos
            if p is not None and ok:
                p.notificado = True
                estado.guardar()
            elif not ok:
                log.error("No se pudo enviar a Telegram; se reintentará en la próxima revisión")
                break

    try:
        agregadas = registro.sincronizar(estado, cfg.excel)
        log.info("Excel actualizado: %s fila(s) nuevas", agregadas)
    except PermissionError:
        log.warning("El Excel está abierto; se completará en la próxima revisión")
    return 0


def main() -> None:
    ap = argparse.ArgumentParser(description="Monitor de procedimientos ComprasMX por partida específica")
    ap.add_argument("--dry-run", action="store_true", help="no envía a Telegram ni guarda")
    ap.add_argument("--demo", action="store_true", help="usa datos de ejemplo, carpeta data_demo/")
    ap.add_argument("--descubrir", action="store_true", help="guarda captura y campos del portal")
    ap.add_argument("--probar-telegram", action="store_true", help="envía un mensaje de prueba al grupo")
    ap.add_argument("--desde", help="fecha inicial dd/mm/aaaa (rango manual)")
    ap.add_argument("--hasta", help="fecha final dd/mm/aaaa")
    sys.exit(ejecutar(ap.parse_args()))


if __name__ == "__main__":
    main()
