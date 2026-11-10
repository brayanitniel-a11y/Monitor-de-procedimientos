"""Envio de mensajes al grupo de Telegram (solo libreria estandar)."""
from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request

log = logging.getLogger("monitor")


def _base() -> str:
    import os
    return os.getenv("TELEGRAM_API_BASE", "https://api.telegram.org").rstrip("/")


class ConflictoDeLectura(RuntimeError):
    """Otro programa está leyendo los mensajes de este mismo bot (409 de Telegram)."""


def llamar(token: str, metodo: str, datos: dict | None = None, timeout: int = 30) -> dict:
    """Llamada genérica a la API de Telegram (getMe, getUpdates, setMyCommands...)."""
    req = urllib.request.Request(
        f"{_base()}/bot{token}/{metodo}", data=json.dumps(datos or {}).encode("utf-8"),
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        if e.code == 409:
            raise ConflictoDeLectura(e.read().decode("utf-8", "ignore")[:200]) from e
        raise


def enviar(token: str, chat_id: str, texto: str, reintentos: int = 3) -> bool:
    if not token or not chat_id:
        log.error("Falta TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID en .env")
        return False
    url = f"{_base()}/bot{token}/sendMessage"
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


def enviar_documento(token: str, chat_id: str, ruta, caption: str = "", reintentos: int = 3) -> bool:
    """Envia un archivo (p. ej. el Excel) al grupo. Limite de Telegram: 50 MB."""
    import os
    import uuid
    from pathlib import Path

    ruta = Path(ruta)
    if not token or not chat_id or not ruta.exists():
        log.error("No se puede enviar el documento (falta token, grupo o archivo)")
        return False
    if ruta.stat().st_size > 49 * 1024 * 1024:
        log.error("El archivo pesa más de 49 MB; Telegram no lo acepta")
        return False
    limite = "----monitor" + uuid.uuid4().hex
    campos = {"chat_id": str(chat_id), "caption": caption[:1000], "parse_mode": "HTML"}
    partes = []
    for k, v in campos.items():
        partes.append(f'--{limite}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode("utf-8"))
    partes.append((f'--{limite}\r\nContent-Disposition: form-data; name="document"; filename="{ruta.name}"\r\n'
                   "Content-Type: application/vnd.openxmlformats-officedocument.spreadsheetml.sheet\r\n\r\n").encode("utf-8"))
    partes.append(ruta.read_bytes())
    partes.append(f"\r\n--{limite}--\r\n".encode("utf-8"))
    cuerpo = b"".join(partes)
    url = f"{_base()}/bot{token}/sendDocument"
    for intento in range(1, reintentos + 1):
        req = urllib.request.Request(url, data=cuerpo, headers={"Content-Type": f"multipart/form-data; boundary={limite}"})
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                return bool(json.loads(r.read()).get("ok"))
        except urllib.error.HTTPError as e:
            detalle = e.read().decode("utf-8", "ignore")
            if e.code == 429:
                time.sleep(10)
                continue
            log.error("Telegram HTTP %s al enviar el documento: %s", e.code, detalle[:200])
            return False
        except Exception as e:
            log.warning("Envío de documento, intento %s falló: %s", intento, e)
            time.sleep(3 * intento)
    return False
