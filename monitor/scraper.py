"""Lectura de ComprasMX con Playwright.

IMPORTANTE: los localizadores se basan en el TEXTO visible de las etiquetas del portal
(p. ej. "Clave - Descripción de partida específica"), no en clases CSS. Aun asi, el portal
es una aplicacion Angular y pueden requerir ajuste en la primera corrida real.
Para eso existe `python -m monitor.main --descubrir`, que guarda captura y lista de campos.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from datetime import date
from pathlib import Path

from .models import Partida, Procedimiento, parse_fecha

log = logging.getLogger("monitor")

ETIQUETA_CLAVE = "Clave - Descripción de partida específica"
ETIQUETA_FECHA = "Fecha de publicación"
MAX_PAGINAS = 30
PASADAS = max(1, int(os.getenv("PASADAS_BUSQUEDA", "2")))
ORIGEN_PROYECTO = "PROYECTO DE CONVOCATORIA"

# Encabezados de la tabla de resultados -> campo (se compara sin acentos, en minusculas)
ALIAS_LISTA = {
    "numero": ["numero de identificacion", "numero de procedimiento", "no. de procedimiento", "numero procedimiento"],
    "descripcion": ["nombre", "nombre del procedimiento", "descripcion", "objeto"],
    "entidad": ["siglas dependencia o entidad", "unidad compradora", "dependencia", "entidad"],
    "tipo": ["tipo de publicacion", "tipo de procedimiento", "caracter"],
    "estatus": ["estatus", "estado", "situacion"],
    "f_publicacion": ["fecha de publicacion", "publicacion"],
    "f_junta": ["fecha junta de aclaraciones", "junta de aclaraciones"],
    "f_apertura": ["fecha de presentacion y apertura de proposiciones", "presentacion y apertura", "fecha de apertura"],
}
ALIAS_PARTIDAS = {
    "numero": ["num.", "no.", "partida"],
    "clave": ["clave cucop+", "clave cucop", "cucop"],
    "descripcion": ["descripcion detallada"],
    "descripcion_cucop": ["descripcion cucop+", "descripcion cucop"],
    "cantidad": ["cantidad solicitada", "cantidad"],
}

JS_TABLAS = """
() => {
  const out = [];
  const limpia = s => (s || '').replace(/\\s+/g, ' ').trim();
  document.querySelectorAll('table').forEach(t => {
    const headers = Array.from(t.querySelectorAll('thead th, tr:first-child th')).map(x => limpia(x.innerText));
    const rows = Array.from(t.querySelectorAll('tbody tr')).map(tr => ({
      cells: Array.from(tr.querySelectorAll('td')).map(td => limpia(td.innerText)),
      href: (tr.querySelector('a[href]') || {}).href || ''
    }));
    out.push({headers, rows});
  });
  document.querySelectorAll('[role=table], [role=grid]').forEach(t => {
    if (t.tagName === 'TABLE') return;
    const headers = Array.from(t.querySelectorAll('[role=columnheader]')).map(x => limpia(x.innerText));
    const rows = Array.from(t.querySelectorAll('[role=row]')).filter(r => r.querySelector('[role=cell], [role=gridcell]')).map(r => ({
      cells: Array.from(r.querySelectorAll('[role=cell], [role=gridcell]')).map(c => limpia(c.innerText)),
      href: (r.querySelector('a[href]') || {}).href || ''
    }));
    out.push({headers, rows});
  });
  return out;
}
"""


def _n(t: str) -> str:
    import unicodedata
    t = unicodedata.normalize("NFKD", t or "")
    return "".join(c for c in t if not unicodedata.combining(c)).lower().strip()


def _mapear(headers: list[str], alias: dict[str, list[str]]) -> dict[str, int]:
    res: dict[str, int] = {}
    hs = [_n(h) for h in headers]
    for campo, nombres in alias.items():
        for nombre in nombres:  # primero coincidencia exacta, luego "contiene"
            if nombre in hs:
                res[campo] = hs.index(nombre)
                break
        else:
            for i, h in enumerate(hs):
                if any(nombre in h for nombre in nombres) and i not in res.values():
                    res[campo] = i
                    break
    return res


class PortalError(RuntimeError):
    pass


class Portal:
    def __init__(self, cfg, debug_dir: Path | None = None):
        self.cfg = cfg
        self.debug_dir = debug_dir or (cfg.data_dir / "descubrir")
        self._pw = self._browser = self.page = None
        self._proyecto = False  # True mientras se recorre "PROYECTO DE CONVOCATORIA"

    def __enter__(self):
        from playwright.sync_api import sync_playwright
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(
            headless=self.cfg.headless, args=["--disable-dev-shm-usage", "--disable-gpu", "--no-sandbox"])
        ctx = self._browser.new_context(locale="es-MX", viewport={"width": 1500, "height": 1000})
        ctx.set_default_timeout(45000)
        # No se interceptan peticiones (ctx.route): al bloquear imágenes/fuentes el portal ignora el filtro de fecha.
        self.page = ctx.new_page()
        return self

    def __exit__(self, *exc):
        try:
            if self._browser:
                self._browser.close()
        finally:
            if self._pw:
                self._pw.stop()

    # ---------- utilidades ----------
    def _captura(self, nombre: str) -> None:
        try:
            self.debug_dir.mkdir(parents=True, exist_ok=True)
            self.page.screenshot(path=str(self.debug_dir / f"{nombre}.png"), full_page=True)
        except Exception:
            pass

    def _campo(self, etiqueta: str, indice: int = 0):
        """Localiza un input por su etiqueta visible (label, placeholder o texto cercano)."""
        p = self.page
        intentos = [
            lambda: p.get_by_label(etiqueta, exact=False),
            lambda: p.get_by_placeholder(etiqueta, exact=False),
            lambda: p.locator(f"xpath=//*[self::label or self::span or self::mat-label][contains(normalize-space(.), '{etiqueta}')]/following::input[{indice + 1}]"),
        ]
        for f in intentos:
            try:
                loc = f()
                if loc.count() > indice and loc.nth(indice).is_visible():
                    return loc.nth(indice)
            except Exception:
                continue
        return None

    def _escribir(self, loc, valor: str) -> None:
        try:
            loc.click()
            loc.fill(valor)
        except Exception:
            loc.evaluate(
                "(el, v) => { el.removeAttribute('readonly'); el.value = v;"
                " el.dispatchEvent(new Event('input', {bubbles:true}));"
                " el.dispatchEvent(new Event('change', {bubbles:true})); }", valor)
        loc.press("Tab")

    # ---------- navegacion ----------
    LEY = re.compile(r"ADQUISICIONES, ARRENDAMIENTOS Y SERVICIOS", re.I)

    def _abrir_filtros(self) -> None:
        p = self.page
        p.goto("about:blank")  # estado limpio: evita que el panel quede abierto/cerrado de la busqueda anterior
        p.goto(self.cfg.url, wait_until="domcontentloaded")
        p.wait_for_load_state("networkidle")
        time.sleep(1.5)
        if not p.locator("input[name='fechaDesdeP']").first.is_visible():  # filtros avanzados cerrados
            try:
                p.get_by_text(re.compile(r"^\s*Filtros\s*$")).first.click(timeout=8000)
            except Exception:
                pass
            time.sleep(1.5)
        if p.locator("p-dropdown[name='ley']").count() == 0:
            self._captura("no_encontre_filtros")
            raise PortalError("No encontré el panel de filtros (p-dropdown 'ley'). Ejecuta: python -m monitor.main --descubrir")

    def _abrir_lista(self, nombre: str, tipo: str = "dropdown") -> None:
        sel = f"p-{tipo}[name='{nombre}']"
        self.page.locator(sel).first.wait_for(state="visible", timeout=20000)  # algunos filtros aparecen tras elegir otros
        self.page.locator(sel).locator(f".p-{tipo}").first.click()
        time.sleep(0.8)

    def _opciones_dropdown(self, nombre: str, intentos: int = 6) -> list[str]:
        """Las listas dependientes cargan despues de elegir la ley, asi que se reintenta."""
        for _ in range(intentos):
            self.page.wait_for_load_state("networkidle")
            self._abrir_lista(nombre)
            ops = [t.strip() for t in self.page.locator("li[role=option]").all_inner_texts() if t.strip()]
            self.page.keyboard.press("Escape")
            time.sleep(0.4)
            ops = [o for o in ops if "no se encontraron" not in o.lower()]
            if ops:
                return ops
            time.sleep(2)
        return []

    def _elegir_dropdown(self, nombre: str, texto) -> None:
        self._abrir_lista(nombre)
        pat = texto if isinstance(texto, re.Pattern) else re.compile(re.escape(texto), re.I)
        self.page.locator("li[role=option]").filter(has_text=pat).first.click()
        self.page.wait_for_load_state("networkidle")
        time.sleep(1.2)

    def _elegir_claves(self, claves: list[str]) -> None:
        p = self.page
        for clave in claves:
            self._abrir_lista("claves", "multiselect")
            p.keyboard.press("Control+A")
            p.keyboard.type(clave, delay=70)
            time.sleep(2.0)
            op = p.locator("li[role=option]").filter(has_text=re.compile(rf"^\s*{re.escape(clave)}\b")).first
            if op.count() == 0:
                raise PortalError(f"La clave {clave} no aparece en el filtro de partidas")
            op.click()
            time.sleep(0.5)
            p.keyboard.press("Escape")
            time.sleep(0.4)

    def _escribir_fecha(self, loc, d: date) -> None:
        """Calendario de PrimeNG: se escribe como una persona y se confirma con Enter."""
        loc.click()
        loc.press("Control+A")
        loc.press("Backspace")
        loc.press_sequentially(d.strftime("%d/%m/%Y"), delay=60)
        loc.press("Enter")
        time.sleep(0.5)
        self.page.keyboard.press("Escape")
        self.page.mouse.click(5, 5)
        time.sleep(0.4)

    def _pulsar_buscar(self) -> None:
        p = self.page
        for loc in (p.get_by_text(re.compile(r"^\s*Buscar\s*$")),
                    p.locator("button:has-text('Buscar'), a:has-text('Buscar'), p-button:has-text('Buscar')")):
            try:
                if loc.count():
                    loc.last.click(timeout=8000)
                    p.wait_for_load_state("networkidle")
                    return
            except Exception:
                continue
        self._captura("no_encontre_buscar")
        raise PortalError("No encontré el botón Buscar")

    def _filtrar_y_buscar(self, tipo: str, claves: list[str], desde: date, hasta: date) -> str:
        self._abrir_filtros()
        if self._proyecto:  # por omisión el portal busca "PROCEDIMIENTO DE CONTRATACIÓN"
            self._elegir_dropdown("proceso", re.compile("convocatoria", re.I))
        self._elegir_dropdown("ley", self.LEY)
        self._opciones_dropdown("contratacion")  # espera a que carguen las opciones dependientes de la ley
        self._elegir_dropdown("contratacion", re.compile(rf"^\s*{re.escape(tipo)}\s*$", re.I))
        self._elegir_claves(claves)
        self._fijar_fecha("fechaDesdeP", desde)
        self._fijar_fecha("fechaHastaP", hasta)
        self._pulsar_buscar()
        time.sleep(2.5)
        total = re.search(r"Total:\s*(\d+)", self.page.inner_text("body"))
        total = total.group(1) if total else "?"
        if total.isdigit() and int(total) > self.cfg.max_resultados:
            self._captura("filtro_fecha_no_aplicado")
            raise PortalError(
                f"El portal reporta {total} procedimientos para {desde}→{hasta} (máximo esperado "
                f"{self.cfg.max_resultados}): el filtro de fecha no se aplicó. Se cancela para no recorrer ni avisar de más.")
        return total

    def _fijar_fecha(self, nombre: str, d: date) -> None:
        """Escribe la fecha y verifica leyendo el campo; reintenta si el calendario no la conservó."""
        esperado = d.strftime("%d/%m/%Y")
        loc = self.page.locator(f"input[name='{nombre}']").first
        leido = ""
        for intento in range(1, 4):
            self._escribir_fecha(loc, d)
            leido = (loc.input_value() or "").strip()
            if leido == esperado:
                return
            log.warning("Fecha %s: se escribió %s pero el campo muestra %r (intento %s/3)", nombre, esperado, leido, intento)
            time.sleep(1.0)
        raise PortalError(f"No se pudo fijar {nombre}={esperado}; el campo muestra {leido!r}")

    # ---------- resultados ----------
    def _filas(self):
        return self.page.locator("table tbody tr")

    def _mapa_tabla(self) -> dict[str, int]:
        for tabla in self.page.evaluate(JS_TABLAS):
            mapa = _mapear(tabla["headers"], ALIAS_LISTA)
            if "numero" in mapa:
                return mapa
        return {}

    @staticmethod
    def _proc_desde_celdas(c: list[str], mapa: dict[str, int], claves: list[str]) -> Procedimiento | None:
        get = lambda campo: " ".join(c[mapa[campo]].split()) if campo in mapa and mapa[campo] < len(c) else ""
        numero = get("numero")
        if not numero:
            return None
        return Procedimiento(
            numero=numero, entidad=get("entidad"), descripcion=get("descripcion"), tipo=get("tipo"),
            estatus=get("estatus"), f_publicacion=parse_fecha(get("f_publicacion")),
            f_junta=parse_fecha(get("f_junta")), f_apertura=parse_fecha(get("f_apertura")),
            claves_filtro=list(claves))

    def _pagina_actual(self) -> int:
        t = self.page.locator(".p-paginator-page.p-highlight").first
        try:
            return int(t.inner_text()) if t.count() else 1
        except Exception:
            return 1

    def _siguiente_pagina(self) -> bool:
        p = self.page
        sig = p.locator("button.p-paginator-next:not(.p-disabled):not([disabled])")
        try:
            if sig.count() and sig.first.is_visible():
                sig.first.click()
                p.wait_for_load_state("networkidle")
                time.sleep(1)
                return True
        except Exception:
            pass
        return False

    def buscar_todo(self, claves: list[str], desde: date, hasta: date, conocidos: set[str] | None = None) -> list[Procedimiento]:
        """Por cada tipo de contratacion (campo obligatorio) busca las claves con la fecha de publicacion.
        Solo abre el detalle de los procedimientos que no estan en `conocidos`."""
        """Revisa 'Procedimiento de contratación' y 'Proyecto de convocatoria' con los mismos filtros."""
        conocidos = conocidos or set()
        resultados: dict[str, Procedimiento] = {}
        for proyecto in (False, True):
            self._proyecto = proyecto
            log.info("=== Proceso: %s ===", "PROYECTO DE CONVOCATORIA" if proyecto else "PROCEDIMIENTO DE CONTRATACIÓN")
            for proc in self._buscar_proceso(claves, desde, hasta, conocidos):
                resultados.setdefault(proc.numero, proc)
        self._proyecto = False
        log.info("Total encontrados %s→%s: %s", desde, hasta, len(resultados))
        return list(resultados.values())

    def _buscar_proceso(self, claves: list[str], desde: date, hasta: date, conocidos: set[str]) -> list[Procedimiento]:
        self._abrir_filtros()
        if self._proyecto:
            self._elegir_dropdown("proceso", re.compile("convocatoria", re.I))
        self._elegir_dropdown("ley", self.LEY)
        tipos = self._opciones_dropdown("contratacion")
        log.info("Tipos de contratación: %s", tipos)
        if not tipos:
            self._captura("sin_tipos")
            raise PortalError("No aparecieron tipos de contratación tras elegir la ley")
        resultados: dict[str, Procedimiento] = {}
        # El portal a veces devuelve resultados distintos para la misma consulta (visto en pruebas reales):
        # se hacen PASADAS búsquedas por tipo y se unen; los ya vistos no se vuelven a abrir.
        for tipo in [t for _ in range(PASADAS) for t in tipos]:
            total = self._filtrar_y_buscar(tipo, claves, desde, hasta)
            log.info("Tipo '%s': el portal reporta %s procedimiento(s)", tipo, total)
            self._captura(f"resultados_{tipo}")
            if total in ("0", ""):
                continue
            pagina = 1
            for _ in range(MAX_PAGINAS):
                mapa = self._mapa_tabla()
                n_filas = self._filas().count()
                log.info("Página %s: %s fila(s)", pagina, n_filas)
                for k in range(n_filas):
                    celdas = self._celdas(k)
                    proc = self._proc_desde_celdas(celdas, mapa, claves)
                    log.info("  fila %s: %s", k + 1, proc.numero if proc else f"(sin número) {celdas[:3]}")
                    if proc is None or proc.numero in resultados:
                        continue
                    if self._proyecto:
                        proc.origen = ORIGEN_PROYECTO
                    resultados[proc.numero] = proc
                    if " ".join(proc.numero.upper().split()) in conocidos:
                        continue
                    try:
                        self._abrir_detalle(k, proc)
                    except Exception as e:
                        log.warning("Detalle de %s falló: %s", proc.numero, e)
                        proc.detalle_ok = False
                    self._regresar(tipo, claves, desde, hasta, pagina)
                if not self._siguiente_pagina():
                    break
                pagina += 1
        log.info("Encontrados %s→%s: %s", desde, hasta, len(resultados))
        return list(resultados.values())

    def _celdas(self, k: int, intentos: int = 12) -> list[str]:
        """Texto de las celdas de la fila k; tras volver del detalle la tabla tarda en repintarse."""
        for _ in range(intentos):
            celdas = [" ".join(t.split()) for t in self._filas().nth(k).locator("td").all_inner_texts()]
            if any(celdas):
                return celdas
            time.sleep(1)
        return []

    def _regresar(self, tipo, claves, desde, hasta, pagina: int) -> None:
        p = self.page
        p.go_back()
        p.wait_for_load_state("networkidle")
        try:
            p.locator("table tbody tr").first.wait_for(state="visible", timeout=10000)
        except Exception:
            pass
        time.sleep(1.0)
        if self._filas().count() == 0:  # el portal perdio los filtros: se repite la busqueda
            self._filtrar_y_buscar(tipo, claves, desde, hasta)
        for _ in range(pagina - 1):
            if self._pagina_actual() >= pagina or not self._siguiente_pagina():
                break

    # ---------- detalle ----------
    def _abrir_detalle(self, k: int, proc: Procedimiento) -> None:
        p = self.page
        self._filas().nth(k).locator("td").nth(1).click()
        p.wait_for_url(re.compile(r"/detalle/"), timeout=15000)
        p.wait_for_load_state("networkidle")
        try:  # el detalle carga por partes: primero el cronograma, luego la tabla de renglones
            p.get_by_text("CRONOGRAMA DE EVENTOS").first.wait_for(state="visible", timeout=20000)
        except Exception:
            log.warning("No apareció el cronograma en el detalle de %s", proc.numero)
        if self._proyecto:  # los proyectos no traen tabla de renglones
            time.sleep(2.0)
            proc.url = p.url
            self._leer_proyecto(proc)
            log.info("Detalle proyecto %s: publicación=%s comentarios=%s", proc.numero, proc.f_publicacion or "—",
                     proc.f_comentarios or "—")
            return
        try:
            p.locator("th, [role=columnheader]").filter(has_text=re.compile(r"^\s*Descripción detallada\s*$")).first.wait_for(
                state="visible", timeout=25000)
        except Exception:
            log.warning("No apareció la tabla de renglones en el detalle de %s", proc.numero)
        time.sleep(1.5)
        proc.url = p.url
        self._leer_detalle(proc)
        log.info("Detalle %s: publicación=%s apertura=%s partidas=%s", proc.numero, proc.f_publicacion or "—",
                 proc.f_apertura or "—", len(proc.partidas))

    def _leer_proyecto(self, proc: Procedimiento) -> None:
        """Detalle de un proyecto de convocatoria: sin renglones; se compara su nombre/descripción."""
        texto = ""
        for _ in range(20):  # el detalle del proyecto carga por partes: se espera a que aparezca el cronograma
            texto = self.page.inner_text("body")
            if _fecha_cerca(texto, r"fecha y hora de publicaci[oó]n") and _valor_tras(texto, r"nombre del proyecto de convocatoria"):
                break
            time.sleep(1.0)
        time.sleep(1.0)
        texto = self.page.inner_text("body")
        proc.f_publicacion = _fecha_cerca(texto, r"fecha y hora de publicaci[oó]n") or proc.f_publicacion
        proc.f_comentarios = _fecha_cerca(texto, r"l[ií]mite para recepci[oó]n de comentarios")
        nombre = _valor_tras(texto, r"nombre del proyecto de convocatoria")
        detalle = _valor_tras(texto, r"descripci[oó]n detallada del proyecto de convocatoria")
        textos = [t for t in (nombre, detalle) if t]
        if len(textos) == 2 and (textos[1].upper() in textos[0].upper() or textos[0].upper() in textos[1].upper()):
            textos = [max(textos, key=len)]
        desc = " — ".join(textos)
        if desc:
            proc.descripcion = proc.descripcion if len(proc.descripcion) > len(nombre) else (nombre or desc)
            proc.partidas = [Partida(numero="1", descripcion=desc)]
        codigos = []
        for tabla in self.page.evaluate(JS_TABLAS):
            hs = [_n(h) for h in tabla["headers"]]
            if hs == ["clave", "descripcion"]:
                codigos += [f["cells"][0] for f in tabla["rows"] if f["cells"]]
        if codigos:
            proc.claves_filtro = sorted(set(codigos))
        proc.detalle_ok = bool(proc.partidas)

    def _firma_renglones(self) -> str:
        """Huella de TODAS las tablas de renglones visibles. Algunos procedimientos traen una tabla
        por renglón (p. ej. 19 tablas de 1 fila) que la página dibuja poco a poco, así que se vigila
        el conjunto completo y no solo la primera tabla."""
        partes = []
        for tabla in self.page.evaluate(JS_TABLAS):
            if any("descripcion detallada" in _n(h) for h in tabla["headers"]):
                partes.append(f"{len(tabla['rows'])}:" + "|".join(" ".join(f["cells"]) for f in tabla["rows"]))
        return f"{len(partes)}#" + "||".join(partes)[:30000] if partes else ""

    def _esperar_estable(self, max_intentos: int = 25, iguales: int = 3) -> None:
        """Espera a que el conjunto de renglones deje de cambiar (varias lecturas iguales seguidas)."""
        previa, seguidas = None, 0
        for _ in range(max_intentos):
            actual = self._firma_renglones()
            seguidas = seguidas + 1 if (actual and actual == previa) else 0
            if seguidas >= iguales - 1:
                return
            previa = actual
            time.sleep(1.0)
        log.warning("Los renglones no se estabilizaron; se usa la última lectura")

    def _leer_detalle(self, proc: Procedimiento) -> None:
        texto = self.page.inner_text("body")
        proc.f_publicacion = _fecha_cerca(texto, r"fecha y hora de publicaci[oó]n") or proc.f_publicacion
        proc.f_junta = _fecha_cerca(texto, r"fecha y hora de junta de aclaraciones") or proc.f_junta
        proc.f_apertura = _fecha_cerca(texto, r"fecha y hora de presentaci[oó]n y apertura") or proc.f_apertura
        proc.f_fallo = _fecha_cerca(texto, r"fecha y hora del acto del fallo") or proc.f_fallo
        partidas, codigos, vistos = [], [], set()
        for _pag in range(40):  # los renglones vienen paginados
            self._esperar_estable()
            nuevos = 0
            for tabla in self.page.evaluate(JS_TABLAS):
                hs = [_n(h) for h in tabla["headers"]]
                if "clave" in hs and "descripcion" in hs and len(hs) == 2:  # partidas presupuestales (53101, ...)
                    codigos += [f["cells"][0] for f in tabla["rows"] if f["cells"]]
                elif any("descripcion detallada" in h for h in hs):  # renglones del procedimiento
                    mapa = _mapear(tabla["headers"], ALIAS_PARTIDAS)
                    for fila in tabla["rows"]:
                        c = fila["cells"]
                        get = lambda campo: " ".join(c[mapa[campo]].split()) if campo in mapa and mapa[campo] < len(c) else ""
                        desc = get("descripcion") or get("descripcion_cucop")
                        clave_fila = (get("numero"), desc)
                        if desc and clave_fila not in vistos:
                            vistos.add(clave_fila)
                            nuevos += 1
                            partidas.append(Partida(numero=get("numero"), clave=get("clave"), descripcion=desc,
                                                    cantidad=get("cantidad")))
            sig = self.page.locator("p-table", has=self.page.locator("th", has_text="Descripción detallada")) \
                .locator("button.p-paginator-next:not(.p-disabled):not([disabled])")
            if os.getenv("DIAG") == "1":
                pag = self.page.locator("p-table", has=self.page.locator("th", has_text="Descripción detallada"))
                log.info("DIAG %s pág %s: filas_nuevas=%s acumuladas=%s p-table=%s siguiente=%s texto_paginador=%r rpp=%r",
                         proc.numero, _pag + 1, nuevos, len(partidas), pag.count(), sig.count(),
                         (pag.locator(".p-paginator").first.inner_text()[:80] if pag.locator(".p-paginator").count() else None),
                         (pag.locator(".p-paginator-rpp-options").first.inner_text()[:40] if pag.locator(".p-paginator-rpp-options").count() else None))
            if not nuevos or not sig.count() or not sig.first.is_visible():
                break
            antes = self._firma_renglones()
            sig.first.click()
            for _ in range(15):  # esperar a que la tabla muestre otra pagina
                time.sleep(0.7)
                if self._firma_renglones() != antes:
                    break
        codigos = sorted(set(codigos))
        proc.partidas = partidas
        if codigos:
            proc.claves_filtro = codigos
        proc.detalle_ok = bool(partidas)

    # ---------- depuracion ----------
    def descubrir(self) -> Path:
        """Guarda captura + inventario de campos/botones para ajustar los localizadores."""
        self.debug_dir.mkdir(parents=True, exist_ok=True)
        p = self.page
        p.goto(self.cfg.url, wait_until="domcontentloaded")
        p.wait_for_load_state("networkidle")
        time.sleep(2)
        self._captura("inicio")
        info = p.evaluate("""() => ({
          url: location.href,
          titulo: document.title,
          inputs: Array.from(document.querySelectorAll('input, select, textarea')).map(e => ({
            tag: e.tagName, type: e.type, id: e.id, name: e.name, placeholder: e.placeholder,
            aria: e.getAttribute('aria-label'), visible: !!(e.offsetWidth || e.offsetHeight)})),
          etiquetas: Array.from(document.querySelectorAll('label, mat-label')).map(e => e.innerText.trim()).filter(Boolean),
          botones: Array.from(document.querySelectorAll('button, [role=button], a')).map(e => (e.innerText || '').trim()).filter(Boolean).slice(0, 80),
          enlaces: Array.from(document.querySelectorAll('a[href]')).map(e => ({t: (e.innerText || '').trim().slice(0, 60), h: e.getAttribute('href')})).slice(0, 80),
          texto: (document.body.innerText || '').slice(0, 3000),
        })""")
        ruta = self.debug_dir / "campos.json"
        ruta.write_text(json.dumps(info, ensure_ascii=False, indent=1), encoding="utf-8")
        (self.debug_dir / "inicio.html").write_text(p.content(), encoding="utf-8")
        try:
            p.get_by_text(re.compile(r"^\s*Filtros\s*$")).first.click(timeout=5000)
            time.sleep(2)
            self._captura("filtros_abiertos")
            info2 = p.evaluate("""() => ({
              inputs: Array.from(document.querySelectorAll('input, select, textarea, mat-select, [role=combobox], [role=listbox]')).map(e => ({
                tag: e.tagName, type: e.type || '', id: e.id, name: e.name || e.getAttribute('formcontrolname') || '',
                placeholder: e.placeholder || '', aria: e.getAttribute('aria-label'),
                visible: !!(e.offsetWidth || e.offsetHeight)})),
              etiquetas: Array.from(document.querySelectorAll('label, mat-label, span.label, legend')).map(e => e.innerText.trim()).filter(Boolean),
              texto: (document.body.innerText || '').slice(0, 2500),
            })""")
            (self.debug_dir / "campos_filtros.json").write_text(json.dumps(info2, ensure_ascii=False, indent=1), encoding="utf-8")
            (self.debug_dir / "desplegables.json").write_text(
                json.dumps(self._explorar_desplegables(), ensure_ascii=False, indent=1), encoding="utf-8")
        except Exception as e:
            (self.debug_dir / "campos_filtros.json").write_text(json.dumps({"error": str(e)}), encoding="utf-8")
        return ruta

    def _explorar_desplegables(self) -> dict:
        """Para cada filtro con lista, abre el desplegable y anota sus opciones y el HTML del widget."""
        p, res = self.page, {}
        js_opciones = """() => Array.from(document.querySelectorAll(
            '[role=option], li[class*=item], .p-dropdown-item, .p-multiselect-item, mat-option, .ng-option, .dropdown-item'))
            .filter(e => e.offsetWidth || e.offsetHeight).map(e => (e.innerText || '').trim()).filter(Boolean).slice(0, 40)"""
        for etiqueta in ("Ley / Soporte normativo", "Tipo de contratación", "Tipo de procedimiento de contratación",
                         "Clave - Descripción de partida específica", "Estatus"):
            item = {}
            try:
                lab = p.locator(f"xpath=//*[contains(normalize-space(text()), '{etiqueta}')]").first
                cont = lab.locator("xpath=ancestor::*[self::div or self::td or self::li][1]")
                item["html"] = cont.evaluate("e => e.outerHTML")[:1800]
                gatillo = cont.locator("[class*=dropdown], [class*=select], [class*=multiselect], [role=combobox], input:not([type=checkbox])").first
                if etiqueta.startswith("Clave"):
                    gatillo.click(timeout=4000)
                    p.keyboard.type("32401", delay=80)
                    time.sleep(2.5)
                else:
                    gatillo.click(timeout=4000)
                    time.sleep(1.2)
                item["opciones"] = p.evaluate(js_opciones)
                self._captura("lista_" + etiqueta[:12].replace(" ", "_").replace("/", ""))
                p.keyboard.press("Escape")
                p.mouse.click(5, 5)
            except Exception as e:
                item["error"] = str(e)[:300]
            res[etiqueta] = item
        return res


def _valor_tras(texto: str, etiqueta: str) -> str:
    """Primer renglón no vacío después de la línea que contiene la etiqueta ('Etiqueta:' y valor abajo)."""
    lineas = [ln.strip() for ln in texto.splitlines()]
    for i, ln in enumerate(lineas):
        if re.search(etiqueta, ln, re.I):
            resto = re.sub(rf".*?{etiqueta}\s*:?", "", ln, flags=re.I).strip()
            if resto:
                return resto
            for sig in lineas[i + 1:i + 4]:
                if sig:
                    return sig
    return ""


def _fecha_cerca(texto: str, patron: str) -> str:
    """Busca la primera fecha dd/mm/yyyy en la linea de la etiqueta o en las 2 siguientes."""
    lineas = texto.splitlines()
    for i, ln in enumerate(lineas):
        if re.search(patron, ln, re.I):
            for j in range(i, min(i + 3, len(lineas))):
                f = parse_fecha(lineas[j])
                if f:
                    return f
    return ""
