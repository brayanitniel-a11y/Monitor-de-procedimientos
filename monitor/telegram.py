"""Envio de mensajes al grupo de Telegram (solo libreria estandar)."""
from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request

log = logging.getLogger("monitor")


def enviar(token: str, chat_id: str, texto: str, reintentos: int = 3) -> bool:
    if not token or not chat_id:
        log.error("Falta TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID en .env")
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    cuerpo = json.dumps({
        "chat_id": chat_id, "text": texto, "parse_mode": "HTML", "disable_web_page_preview": True,
    }).encode("utf-8")
    for intento in range(1, reintentos + 1):
        req = urllib.request.Request(url, data=cuerpo, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return bool(json.loads(r.read()).get("ok"))
        except urllib.error.HTTPError as e:
            detalle = e.read().decode("utf-8", "ignore")
            if e.code == 429:
                try:
                    espera = int(json.loads(detalle)["parameters"]["retry_after"]) + 1
                except Exception:
                    espera = 10
                log.warning("Telegram pide esperar %ss", espera)
                time.sleep(espera)
                continue
            log.error("Telegram HTTP %s: %s", e.code, detalle[:200])
            return False
        except Exception as e:  # red caida, DNS, etc.
            log.warning("Telegram intento %s fallo: %s", intento, e)
            time.sleep(3 * intento)
    return False
