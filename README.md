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

## Programación (8:00, 13:00, 18:00 y 21:00, hora CDMX)

Linux / Mac (`crontab -e`):

```
CRON_TZ=America/Mexico_City
0 8,13,18,21 * * * cd /ruta/Monitor-de-procedimientos && .venv/bin/python -m monitor.main >> data/logs/cron.log 2>&1
```

Windows (Programador de tareas, en PowerShell):

```powershell
foreach ($h in "08:00","13:00","18:00","21:00") {
  schtasks /Create /TN "MonitorCompras_$($h.Replace(':',''))" /SC DAILY /ST $h `
    /TR "C:\ruta\Monitor-de-procedimientos\.venv\Scripts\python.exe -m monitor.main" /F
}
```
(En Windows agrega `/SD` o usa "Iniciar en" = la carpeta del repo para que encuentre `.env`.)

Si el otro bot corre a alguna de estas horas, desplaza este unos minutos (por ejemplo `5 8,13,18,21`)
para que no abran dos navegadores a la vez. Un candado (`data/monitor.lock`) evita que dos revisiones se pisen.

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
