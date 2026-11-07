"""Configuracion: lee variables de entorno y el archivo .env (si existe)."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

BASE = Path(__file__).resolve().parent.parent


def _leer_env(ruta: Path) -> None:
    if not ruta.exists():
        return
    for linea in ruta.read_text(encoding="utf-8").splitlines():
        linea = linea.strip()
        if not linea or linea.startswith("#") or "=" not in linea:
            continue
        k, v = linea.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


@dataclass
class Config:
    claves: list[str]
    token: str
    chat_id: str
    tz: ZoneInfo
    hora_corte: int
    url: str
    headless: bool
    data_dir: Path
    historial_csv: Path
    excel: Path
    modo_ligero: str = "0"
    max_resultados: int = 60
    reintento_seg: int = 90
    demo: bool = False


def _historial() -> Path:
    d = BASE / "data" / "historial"
    completo = d / "Historico_cotizaciones_2025_2026_preliminar.csv"
    return completo if completo.exists() else d / "historial_sin_precios.csv"


def cargar(demo: bool = False) -> Config:
    _leer_env(BASE / ".env")
    data_dir = BASE / ("data_demo" if demo else "data")
    data_dir.mkdir(parents=True, exist_ok=True)
    claves = [c.strip() for c in os.getenv("CLAVES_PARTIDA", "32401,53101").split(",") if c.strip()]
    return Config(
        claves=claves,
        token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
        chat_id=os.getenv("TELEGRAM_CHAT_ID", ""),
        tz=ZoneInfo(os.getenv("ZONA_HORARIA", "America/Mexico_City")),
        hora_corte=int(os.getenv("HORA_CORTE_MANANA", "10")),
        url=os.getenv("COMPRASMX_URL", "https://comprasmx.buengobierno.gob.mx/sitiopublico/#/"),
        headless=os.getenv("HEADLESS", "1") != "0",
        data_dir=data_dir,
        historial_csv=_historial(),
        excel=data_dir / "Registro_oportunidades.xlsx",
        max_resultados=int(os.getenv("MAX_RESULTADOS", "60")),
        modo_ligero=os.getenv("MODO_LIGERO", "0").strip(),
        reintento_seg=int(os.getenv("REINTENTO_SEG", "90")),
        demo=demo,
    )
