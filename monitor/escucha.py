"""Escucha de comandos de Telegram: el bot ENVÍA avisos y solo atiende tres comandos.

No lee conversaciones: con el modo de privacidad de grupos activado (el predeterminado), Telegram solo le entrega
los comandos dirigidos a él (/comando@NombreDelBot). Consume casi nada: una petición larga cada ~50 s.

  /revisar_partidas   revisión manual ahora (además de las programadas)
  /excel_partidas     envía el Excel de oportunidades
  /estado_partidas    última revisión y cuántos procedimientos hay registrados

Solo responde a mensajes del grupo configurado en TELEGRAM_CHAT_ID.
"""
from __future__ import annotations

import logging
import subprocess
import sys
import threading
import time
from datetime import datetime
from html import escape
from pathlib import Path

from . import settings, telegram
from .estado import Estado

log = logging.getLogger("monitor")

COMANDOS = {
    "revisar_partidas": "Revisión manual ahora (busca procedimientos nuevos)",
    "excel_partidas": "Enviar el Excel de oportunidades",
    "estado_partidas": "Última revisión y total registrado",
}
LIMITE_REVISION_SEG = 25 * 60


def comando_de(texto: str | None, mi_usuario: str) -> str | None:
    """Devuelve el nombre del comando si es nuestro; None si no es un comando o va dirigido a otro bot."""
    if not texto or not texto.startswith("/"):
        return None
    nombre, _, destino = texto.split()[0][1:].partition("@")
    if destino and destino.lower() != mi_usuario.lower():
        return None
    nombre = nombre.lower()
    return nombre if nombre in COMANDOS else None


class Escucha:
    def __init__(self, cfg, comando_revision: list[str] | None = None):
        self.cfg = cfg
        self.usuario = ""
        self.comando_revision = comando_revision or [sys.executable, "-m", "monitor.main", "--manual"]
        self._hilo: threading.Thread | None = None
        self._chats_ignorados: set[str] = set()

    # ---------- envío ----------
    def _decir(self, texto: str) -> None:
        telegram.enviar(self.cfg.token, self.cfg.chat_id, texto)

    def _ocupado(self) -> bool:
        if self._hilo is not None and self._hilo.is_alive():
            return True
        candado = self.cfg.data_dir / "monitor.lock"   # también cuenta una revisión programada en curso
        return candado.exists() and time.time() - candado.stat().st_mtime < 30 * 60

    # ---------- comandos ----------
    def revisar(self, quien: str) -> None:
        if self._ocupado():
            self._decir("⏳ Ya hay una revisión en curso. Cuando termine verás sus avisos aquí.")
            return
        self._decir(f"🔎 Revisión manual iniciada por {escape(quien)}. Tarda unos minutos…")
        self._hilo = threading.Thread(target=self._correr_revision, daemon=True)
        self._hilo.start()

    def _correr_revision(self) -> None:
        try:
            r = subprocess.run(self.comando_revision, cwd=str(settings.BASE), timeout=LIMITE_REVISION_SEG,
                               capture_output=True, text=True)
            log.info("Revisión manual terminó con código %s", r.returncode)
            if r.returncode not in (0, 1):  # 1 = fallo ya reportado por la propia revisión
                self._decir(f"⚠️ La revisión manual terminó con error (código {r.returncode}). Revisa el registro del servidor.")
        except subprocess.TimeoutExpired:
            self._decir("⚠️ La revisión manual tardó demasiado y se canceló.")
        except Exception as e:
            log.exception("No se pudo lanzar la revisión manual")
            self._decir(f"⚠️ No se pudo iniciar la revisión manual: {escape(str(e)[:150])}")

    def excel(self) -> None:
        if not self.cfg.excel.exists():
            self._decir("Todavía no hay un Excel: aún no se ha registrado ningún procedimiento.")
            return
        hora = datetime.now(self.cfg.tz).strftime("%d/%m/%Y %H:%M")
        if not telegram.enviar_documento(self.cfg.token, self.cfg.chat_id, self.cfg.excel,
                                         f"📎 <b>Registro de oportunidades</b> al {hora}"):
            self._decir("⚠️ No pude enviar el Excel.")

    def estado(self) -> None:
        est = Estado.cargar(self.cfg.data_dir / "estado.json")
        ultimo = est.ultimo_ok.strftime("%d/%m/%Y") if est.ultimo_ok else "ninguna todavía"
        self._decir("ℹ️ <b>Monitor de partidas</b>\n"
                    f"Última revisión correcta: {ultimo}\n"
                    f"Procedimientos registrados: {len(est.procedimientos)}\n"
                    f"Claves vigiladas: {escape(', '.join(self.cfg.claves))}\n"
                    f"{'🔎 Hay una revisión en curso.' if self._ocupado() else 'Sin revisión en curso.'}")

    # ---------- ciclo ----------
    def _descartar_pendientes(self) -> int | None:
        """Al arrancar se olvidan los comandos que quedaron en espera mientras el servicio estaba caído, para no
        ejecutarlos tarde. No depende del reloj del servidor (usa los identificadores de Telegram)."""
        try:
            r = telegram.llamar(self.cfg.token, "getUpdates", {"offset": -1, "timeout": 0, "allowed_updates": ["message"]})
            res = r.get("result") or []
            if isinstance(res, list) and res:
                return res[-1]["update_id"] + 1
        except Exception as e:
            log.warning("No se pudieron descartar los comandos pendientes: %s", e)
        return None

    def atender(self, update: dict) -> None:
        msg = update.get("message")
        if not msg:
            return
        chat = str(msg.get("chat", {}).get("id"))
        if chat != str(self.cfg.chat_id):             # otro chat (o privado): se ignora, pero se deja constancia
            if chat not in self._chats_ignorados and (msg.get("text") or "").startswith("/"):
                self._chats_ignorados.add(chat)
                log.warning("Comando recibido de un chat distinto al configurado (%s ≠ %s); se ignora. "
                            "Si es el grupo correcto, corrige TELEGRAM_CHAT_ID en .env.", chat, self.cfg.chat_id)
            return
        cmd = comando_de(msg.get("text"), self.usuario)
        if cmd is None:
            return
        quien = (msg.get("from") or {}).get("first_name", "alguien")
        log.info("Comando /%s de %s", cmd, quien)
        if cmd == "revisar_partidas":
            self.revisar(quien)
        elif cmd == "excel_partidas":
            self.excel()
        elif cmd == "estado_partidas":
            self.estado()

    def run(self) -> None:
        me = telegram.llamar(self.cfg.token, "getMe")
        self.usuario = me["result"]["username"]
        telegram.llamar(self.cfg.token, "setMyCommands", {
            "commands": [{"command": c, "description": d} for c, d in COMANDOS.items()],
            "scope": {"type": "all_group_chats"}})
        log.info("Escuchando comandos como @%s en el grupo %s", self.usuario, self.cfg.chat_id)
        offset = self._descartar_pendientes()
        while True:
            try:
                datos = {"timeout": 50, "allowed_updates": ["message"]}
                if offset is not None:               # Telegram no recibe "offset": null
                    datos["offset"] = offset
                r = telegram.llamar(self.cfg.token, "getUpdates", datos, timeout=70)
            except telegram.ConflictoDeLectura as e:
                log.error("Otro programa está leyendo los mensajes de este bot (¿mismo token que el otro bot?): %s", e)
                time.sleep(60)
                continue
            except Exception as e:
                log.warning("getUpdates falló (%s); reintento en 10 s", e)
                time.sleep(10)
                continue
            resultado = r.get("result") or []
            for u in resultado if isinstance(resultado, list) else []:
                offset = u["update_id"] + 1
                try:
                    self.atender(u)
                except Exception:
                    log.exception("Error atendiendo un comando")


def main() -> None:
    from .main import _configurar_log
    cfg = settings.cargar()
    _configurar_log(cfg.data_dir, "escucha.log")
    if not cfg.token or not cfg.chat_id:
        raise SystemExit("Falta TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID en .env")
    Escucha(cfg).run()


if __name__ == "__main__":
    main()
