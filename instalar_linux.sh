#!/usr/bin/env bash
# Instalador para Linux (Debian/Ubuntu/Raspberry Pi OS). Ejecutar como TU usuario normal, no como root:
#   cd ~/Monitor-de-procedimientos && bash instalar_linux.sh
# Es seguro repetirlo. No toca el otro bot ni cambia la zona horaria del servidor.
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
USUARIO="$(id -un)"
cd "$DIR"
[ "$(id -u)" -eq 0 ] && { echo "No lo ejecutes como root; usa tu usuario normal (pedirá sudo solo cuando haga falta)."; exit 1; }

echo "== 1/6 Python y entorno virtual"
command -v python3 >/dev/null || { echo "Falta python3 (sudo apt install python3 python3-venv)"; exit 1; }
python3 - <<'PY' || { echo "Se necesita Python 3.9 o superior"; exit 1; }
import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)
PY
[ -d .venv ] || python3 -m venv .venv || { echo "Falta python3-venv: sudo apt install python3-venv"; exit 1; }
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements.txt

echo "== 2/6 Navegador (Chromium de Playwright)"
.venv/bin/python -m playwright install chromium
if ! .venv/bin/python - <<'PY'
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    b = p.chromium.launch(); b.close()
PY
then
  echo "Faltan librerías del sistema para Chromium; se instalan con sudo (las mismas que usa el otro bot)."
  sudo "$DIR/.venv/bin/python" -m playwright install-deps chromium
fi

echo "== 3/6 Configuración (.env)"
if [ ! -f .env ]; then
  cp .env.example .env
  chmod 600 .env
  read -r -p "Token del bot de Telegram: " TOKEN
  read -r -p "ID del grupo (número negativo, p. ej. -1001234567890): " CHAT
  sed -i "s|^TELEGRAM_BOT_TOKEN=.*|TELEGRAM_BOT_TOKEN=${TOKEN}|; s|^TELEGRAM_CHAT_ID=.*|TELEGRAM_CHAT_ID=${CHAT}|" .env
else
  echo ".env ya existe; no se modifica."
fi
chmod 600 .env
mkdir -p data/logs

echo "== 4/6 Prueba de Telegram"
.venv/bin/python -m monitor.main --probar-telegram || { echo "Telegram falló: revisa token e ID en .env y vuelve a ejecutar."; exit 1; }

echo "== 5/6 Primera vez: registrar lo ya publicado SIN avisar (evita repetir avisos en el grupo)"
if [ ! -f data/estado.json ]; then
  # Fechas en hora de CDMX aunque el servidor esté en otra zona; rango completo, de hace 4 días hasta hoy
  DESDE="$(TZ=America/Mexico_City date -d '4 days ago' +%d/%m/%Y)"
  HASTA="$(TZ=America/Mexico_City date +%d/%m/%Y)"
  .venv/bin/python -m monitor.main --registrar-sin-avisar --desde "$DESDE" --hasta "$HASTA"
else
  echo "Ya hay estado guardado; se omite."
fi

echo "== 6/6 Programación con systemd (hora CDMX) y servicio de comandos de Telegram"
TMP="$(mktemp -d)"
sed "s|@USUARIO@|$USUARIO|g; s|@DIR@|$DIR|g" deploy/monitor-compras.service.in > "$TMP/monitor-compras.service"
sudo install -m 644 "$TMP/monitor-compras.service" /etc/systemd/system/monitor-compras.service
sudo install -m 644 deploy/monitor-compras-semana.timer /etc/systemd/system/monitor-compras-semana.timer
sudo install -m 644 deploy/monitor-compras-finde.timer /etc/systemd/system/monitor-compras-finde.timer
sed "s|@USUARIO@|$USUARIO|g; s|@DIR@|$DIR|g" deploy/monitor-compras-escucha.service.in > "$TMP/monitor-compras-escucha.service"
sudo install -m 644 "$TMP/monitor-compras-escucha.service" /etc/systemd/system/monitor-compras-escucha.service
rm -rf "$TMP"
sudo systemctl daemon-reload
sudo systemctl enable --now monitor-compras-semana.timer monitor-compras-finde.timer monitor-compras-escucha.service

echo
echo "Listo. Próximas ejecuciones:"
systemctl list-timers 'monitor-compras-*' --no-pager || true
cat <<TXT

Comandos útiles:
  journalctl -u monitor-compras -n 50 --no-pager     # ver la última revisión
  tail -f $DIR/data/logs/monitor.log                 # registro del monitor
  sudo systemctl start monitor-compras               # forzar una revisión ahora (o /revisar_partidas en el grupo)
  journalctl -u monitor-compras-escucha -n 30 --no-pager   # servicio de comandos de Telegram
  sudo systemctl disable --now monitor-compras-semana.timer monitor-compras-finde.timer monitor-compras-escucha.service   # pausar
El Excel está en: $DIR/data/Registro_oportunidades.xlsx
TXT
