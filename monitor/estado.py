"""Estado persistente: fuente de verdad de lo ya detectado y lo ya notificado.

El Excel se alimenta de aqui. Si el Excel esta abierto o bloqueado, no se pierde nada:
el estado se conserva y el Excel se completa en la siguiente revision.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict
from datetime import date, datetime
from pathlib import Path

from .models import Partida, Procedimiento


class Estado:
    def __init__(self, ruta: Path):
        self.ruta = ruta
        self.procedimientos: dict[str, Procedimiento] = {}
        self.ultimo_ok: date | None = None
        self.ultima_alerta: str = ""

    @classmethod
    def cargar(cls, ruta: Path) -> "Estado":
        est = cls(ruta)
        if not ruta.exists():
            return est
        datos = json.loads(ruta.read_text(encoding="utf-8"))
        if datos.get("ultimo_ok"):
            est.ultimo_ok = date.fromisoformat(datos["ultimo_ok"])
        est.ultima_alerta = datos.get("ultima_alerta", "")
        for num, d in datos.get("procedimientos", {}).items():
            partidas = [Partida(**p) for p in d.pop("partidas", [])]
            est.procedimientos[num] = Procedimiento(**d, partidas=partidas)
        return est

    def guardar(self) -> None:
        datos = {
            "ultimo_ok": self.ultimo_ok.isoformat() if self.ultimo_ok else "",
            "ultima_alerta": self.ultima_alerta,
            "procedimientos": {n: asdict(p) for n, p in self.procedimientos.items()},
        }
        tmp = self.ruta.with_suffix(".tmp")
        tmp.write_text(json.dumps(datos, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, self.ruta)

    @staticmethod
    def clave(numero: str) -> str:
        return " ".join(numero.upper().split())

    def tiene(self, numero: str) -> bool:
        return self.clave(numero) in self.procedimientos

    def agregar(self, p: Procedimiento) -> None:
        p.numero = " ".join(p.numero.split())
        self.procedimientos[self.clave(p.numero)] = p

    def pendientes(self) -> list[Procedimiento]:
        """Detectados pero aun no avisados por Telegram (por ejemplo si Telegram fallo)."""
        return [p for p in self.procedimientos.values() if not p.notificado]

    def puede_alertar(self, ahora: datetime, horas: int = 3) -> bool:
        if not self.ultima_alerta:
            return True
        return (ahora - datetime.fromisoformat(self.ultima_alerta)).total_seconds() > horas * 3600
