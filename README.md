# Monitor-de-procedimientos
Monitoreamos procedimientos de ComprasMX de claves especificas

Busca cada día procedimientos nuevos en ComprasMX con las claves de partida **32401** y **53101**,
los compara contra el historial de lo que hemos ofertado, los guarda en un Excel y avisa por Telegram
al grupo del equipo. **No usa IA ni Ollama**: solo reglas de texto y claves.

Es un repo independiente de `vigilante-comprasmx`.

## Qué hace en cada revisión

1. Calcula el rango de fechas de publicación:
   - Revisión de la mañana (antes de las 10:00): procedimientos publicados **ayer**.
   - Revisiones de las 13:00, 18:00 y 21:00: solo los publicados **hoy**.
   - Si una revisión anterior falló o el equipo estuvo apagado, el rango se extiende hacia atrás
     (máximo 7 días) para no perder nada.
2. Busca cada clave en el filtro *"Clave - Descripción de partida específica"* y une los resultados.
3. Ignora los procedimientos que ya están en `data/estado.json` (por número de procedimiento).
4. Para los nuevos abre el detalle y lee cronograma (junta de aclaraciones, presentación y apertura, fallo) y partidas.
5. Compara cada partida con el historial:
   - **Alta**: la clave coincide, o la descripción es casi igual a algo ya ofertado.
   - **Media**: descripción parecida.
   - **Ninguna**: sin parecido claro.
6. Guarda todo en `data/Registro_oportunidades.xlsx` y envía a Telegram solo lo nuevo.

Nunca se vuelve a avisar un procedimiento ya reportado. Si Telegram falla, se reintenta en la
siguiente revisión sin duplicar los que sí llegaron.

## Excel

Hoja **Procedimientos** (una fila por procedimiento) y hoja **Partidas** (una fila por partida, con la
partida similar del historial, marca/modelo ofertado y cuántas veces).

Columnas que llena el equipo: **Probabilidad de ofertar (manual)**, **Revisado** (Sí/No, empieza en No) y **Notas**.
El script solo agrega filas nuevas; nunca sobrescribe lo que marques. Las filas sin revisar se ven en amarillo.
Si el Excel está abierto en Windows al momento de la revisión, se completa en la siguiente.

## Instalación

```bash
git clone https://github.com/brayanitniel-a11y/Monitor-de-procedimientos && cd Monitor-de-procedimientos
python -m venv .venv && source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium
cp .env.example .env                                      # llena TELEGRAM_BOT_TOKEN y TELEGRAM_CHAT_ID
```

El ID del grupo es un número negativo. Para obtenerlo: agrega el bot al grupo, escribe un mensaje y abre
`https://api.telegram.org/bot<TOKEN>/getUpdates`; busca `"chat":{"id":-100...}`.

## Primera puesta en marcha (importante)

El portal es una aplicación Angular y los localizadores del scraper se basan en el texto de las etiquetas.
Hay que validarlos una vez con el portal real:

```bash
python -m monitor.main --descubrir            # guarda captura y lista de campos en data/descubrir/
python -m monitor.main --dry-run --desde 01/11/2026 --hasta 05/11/2026   # busca y muestra, sin enviar ni guardar
```

Si falla con "No encontré el filtro…", manda `data/descubrir/campos.json` y `inicio.png` y se ajusta
en `monitor/scraper.py`. Con `HEADLESS=0` en `.env` puedes ver el navegador.

Prueba sin portal ni Telegram: `python -m monitor.main --demo` (usa `data_demo/`).

## Instalación en el servidor Linux (recomendada)

```bash
git clone https://github.com/brayanitniel-a11y/Monitor-de-procedimientos.git ~/Monitor-de-procedimientos
cd ~/Monitor-de-procedimientos
bash instalar_linux.sh          # como tu usuario normal; pide sudo solo para systemd y librerías
```

El instalador (se puede repetir sin problema): crea el entorno, instala Chromium, te pide token e ID del grupo
(se guardan en `.env` con permisos 600), prueba Telegram, **registra sin avisar** lo publicado en los últimos 4 días
(para no repetir avisos en el grupo) y deja programados los horarios con **systemd**:

- Lunes a viernes 8:00, 13:00, 18:00 y 21:00 · sábado y domingo 19:30, siempre en hora de CDMX aunque el servidor tenga otra zona.
- Si el servidor estuvo apagado, corre al encender y la ventana de fechas recupera lo perdido.
- Baja prioridad (`Nice=10`), tope de memoria (1.5 GB) y sin solaparse consigo mismo. No toca el otro bot ni la zona horaria del equipo.

Para copiar el historial completo (con precios) al servidor, déjalo en `data/historial/` con su nombre original; si no está,
se usa la copia sin precios que viene en el repo.

## Comandos de Telegram (revisión manual)

El bot sigue siendo de **envío**: no lee las conversaciones del grupo. Con la privacidad de grupos activada (déjala así),
Telegram solo le entrega los comandos dirigidos a él. Hay tres, con nombres propios para no cruzarse con otros bots del grupo:

| Comando | Qué hace |
|---|---|
| `/revisar_partidas` | Revisión manual ahora, además de las programadas. Responde aunque no haya nada nuevo. |
| `/excel_partidas` | Envía el Excel actualizado. |
| `/estado_partidas` | Última revisión correcta, total registrado y si hay una revisión en curso. |

- Escribe el comando desde el menú `/` del grupo (Telegram le agrega `@NombreDelBot`). Si escribes `/revisar_partidas` a mano,
  con otros bots en el grupo conviene añadir `@NombreDelBot`; si no, Telegram puede no entregárselo.
- Solo atiende mensajes del grupo configurado en `TELEGRAM_CHAT_ID`; ignora cualquier otro chat y los comandos con más de 2 minutos.
- No se empalma con las programadas: si ya hay una revisión corriendo, responde que está en curso.
- Lo ejecuta el servicio `monitor-compras-escucha` (lo instala `instalar_linux.sh`): una sola petición larga cada ~50 s, consumo mínimo.
- Los avisos de procedimientos nuevos y el Excel salen igual que en una revisión programada; no repite lo ya avisado.
- Si el registro dice "otro programa está leyendo los mensajes de este bot", el token está compartido con otro proyecto: usa uno distinto.

## Programación

(Si prefieres cron en lugar de systemd, usa estas líneas; `instalar_linux.sh` ya hace lo equivalente con systemd.)

| Días | Horas (CDMX) |
|---|---|
| Lunes a viernes | 8:00, 13:00, 18:00 y 21:00 |
| Sábado y domingo | solo 19:30 |

No quedan huecos: cada revisión consulta desde la última revisión exitosa. El sábado a las 19:30 revisa
desde el viernes (cubre el viernes después de las 21:00), el domingo desde el sábado, y el lunes a las 8:00
revisa el domingo. Lo ya avisado nunca se repite.

**Linux / Mac** (`crontab -e`):

```
CRON_TZ=America/Mexico_City
0 8,13,18,21 * * 1-5 cd /ruta/Monitor-de-procedimientos && nice -n 10 .venv/bin/python -m monitor.main >> data/logs/cron.log 2>&1
30 19 * * 6,0       cd /ruta/Monitor-de-procedimientos && nice -n 10 .venv/bin/python -m monitor.main >> data/logs/cron.log 2>&1
```

**Windows** (Programador de tareas, PowerShell). "Iniciar en" debe ser la carpeta del repo para que encuentre `.env`:

```powershell
$py  = "C:\ruta\Monitor-de-procedimientos\.venv\Scripts\python.exe"
$dir = "C:\ruta\Monitor-de-procedimientos"
function Programar($nombre, $dias, $hora) {
  $a = New-ScheduledTaskAction -Execute $py -Argument "-m monitor.main" -WorkingDirectory $dir
  $t = New-ScheduledTaskTrigger -Weekly -DaysOfWeek $dias -At $hora
  $s = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 30)
  Register-ScheduledTask -TaskName $nombre -Action $a -Trigger $t -Settings $s -Force | Out-Null
}
foreach ($h in "08:00","13:00","18:00","21:00") { Programar "MonitorCompras_SEM_$($h.Replace(':',''))" Monday,Tuesday,Wednesday,Thursday,Friday $h }
Programar "MonitorCompras_FINDE_1930" Saturday,Sunday "19:30"
```

`StartWhenAvailable` hace que, si la computadora estaba apagada a esa hora, la revisión corra al encenderse.

## Convivencia con el otro bot (vigilante-comprasmx)

Los dos proyectos son independientes y **pueden correr al mismo tiempo**; no hay que coordinar horarios:

- Cada uno tiene su carpeta, su entorno virtual, su `.env`, su estado y su candado. El candado
  (`data/monitor.lock`) es solo de este repo: evita que dos revisiones *de este mismo monitor* se pisen
  (por ejemplo, si una tarda más de lo normal), y no bloquea al otro bot.
- Cada revisión abre su propio navegador, aislado. No comparten perfil, cookies ni puertos.
- **Consumo:** cada revisión abre un solo Chromium sin ventana, unos 2–4 minutos, y lo cierra al terminar. Dos revisiones a la vez
  usan algo menos de 1 GB de RAM en total. (No se bloquean imágenes ni fuentes para ahorrar: se probó y el portal deja de aplicar el filtro de fecha.)
- En Linux/Mac, `nice -n 10` le da prioridad baja a este monitor frente a otras tareas del equipo.
- **Reintento automático:** si una revisión falla (equipo ocupado, portal lento), espera 90 s y lo intenta una vez más
  antes de avisar por Telegram. Se ajusta con `REINTENTO_SEG`.
- Ambos bots pueden escribir al mismo grupo de Telegram sin problema; los mensajes de este monitor llevan su propio
  encabezado (🟢/🟡/⚪ NUEVO · número) y usan un token distinto si creaste otro bot.
- Lo único compartido es la salida a internet y el portal. Con dos revisiones a la vez el portal ve dos navegadores
  desde la misma IP, igual que dos personas en la misma oficina.

## Historial

`data/historial/Historico_cotizaciones_2025_2026_preliminar.csv` (no versionado). Para actualizarlo, reemplaza el archivo
con el mismo formato de columnas. Notas sobre el historial:

- Las claves vienen en varios formatos (SAI, CUCOP, cuadro básico). La clave se compara sin puntos ni guiones y
  aceptando prefijos; la descripción es la comparación principal.
- Los renglones con precio 0 o marcados como investigación de mercado se señalan como "solo cotización".

## Pruebas

```bash
python -m unittest discover -s tests -t .
```

> El historial (`data/historial/*.csv`) contiene precios y marcas ofertadas, por eso **no se sube al repo** (es público). Cópialo a esa carpeta en el servidor.
