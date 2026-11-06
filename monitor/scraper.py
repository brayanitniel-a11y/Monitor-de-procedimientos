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
    "numero": ["partida", "no. partida", "numero de partida", "no."],
    "clave": ["clave", "clave cucop", "cucop"],
    "descripcion": ["descripcion", "descripcion de la partida", "nombre"],
    "cantidad": ["cantidad", "cantidad minima", "cant."],
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

    def __enter__(self):
        from playwright.sync_api import sync_playwright
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(headless=self.cfg.headless)
        ctx = self._browser.new_context(locale="es-MX", viewport={"width": 1500, "height": 1000})
        ctx.set_default_timeout(30000)
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
        p.goto(self.cfg.url, wait_until="domcontentloaded")
        p.wait_for_load_state("networkidle")
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
        self.page.locator(sel).locator(f".p-{tipo}").first.click()
        time.sleep(0.8)

    def _opciones_dropdown(self, nombre: str, intentos: int = 6) -> list[str]:
        """Lee las opciones; las listas dependientes cargan despues de elegir la ley, asi que reintenta."""
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

    def _elegir_dropdown(self, nombre: str, texto: re.Pattern | str) -> None:
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

    def buscar_todo(self, claves: list[str], desde: date, hasta: date) -> list[Procedimiento]:
        """Por cada tipo de contratación (campo obligatorio) busca las claves con la fecha de publicación."""
        self._abrir_filtros()
        self._elegir_dropdown("ley", self.LEY)
        tipos = self._opciones_dropdown("contratacion")
        log.info("Tipos de contratación disponibles: %s", tipos)
        if not tipos:
            self._captura("sin_tipos")
            raise PortalError("No aparecieron tipos de contratación tras elegir la ley")
        resultados: dict[str, Procedimiento] = {}
        for i, tipo in enumerate(tipos):
            if i:
                self._abrir_filtros()
                self._elegir_dropdown("ley", self.LEY)
            self._elegir_dropdown("contratacion", re.compile(rf"^\s*{re.escape(tipo)}\s*$", re.I))
            self._elegir_claves(claves)
            ini, fin = self.page.locator("input[name='fechaDesdeP']"), self.page.locator("input[name='fechaHastaP']")
            self._escribir_fecha(ini.first, desde)
            self._escribir_fecha(fin.first, hasta)
            log.info("Fechas en pantalla: desde=%r hasta=%r", ini.first.input_value(), fin.first.input_value())
            self._captura(f"filtros_llenos_{i}")
            self._pulsar_buscar()
            self.page.wait_for_load_state("networkidle")
            time.sleep(2.5)
            self._captura(f"resultados_{i}")
            total = re.search(r"Total:\s*(\d+)", self.page.inner_text("body"))
            log.info("Tipo '%s': total reportado por el portal = %s", tipo, total.group(1) if total else "?")
            if os.getenv("EXPLORAR") == "1" and i == 0:
                self._explorar_fila()
            for _ in range(MAX_PAGINAS):
                for proc in self._leer_pagina_resultados(claves):
                    resultados.setdefault(proc.numero, proc)
                if not self._siguiente_pagina():
                    break
        log.info("Procedimientos encontrados %s→%s: %s", desde, hasta, len(resultados))
        return list(resultados.values())

    def _escribir_fecha(self, loc, d: date) -> None:
        """Calendario de PrimeNG: se escribe como una persona (tecla por tecla) y se confirma con Enter."""
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
        for loc in (p.get_by_text(re.compile(r"^\s*Buscar\s*$")), p.locator("button:has-text('Buscar'), a:has-text('Buscar'), p-button:has-text('Buscar')")):
            try:
                if loc.count():
                    loc.last.click(timeout=8000)
                    p.wait_for_load_state("networkidle")
                    return
            except Exception:
                continue
        self._captura("no_encontre_buscar")
        raise PortalError("No encontré el botón Buscar")

    def _explorar_fila(self) -> None:
        """Depuracion: que pasa al abrir el primer resultado (URL y texto del detalle)."""
        p = self.page
        try:
            fila = p.locator("table tbody tr").first
            log.info("FILA1: %s", " | ".join(fila.locator("td").all_inner_texts())[:300])
            antes = p.url
            fila.locator("td").nth(1).click()
            time.sleep(3)
            log.info("DETALLE url antes=%s despues=%s", antes, p.url)
            self._captura("detalle_explorado")
            texto = p.inner_text("body")
            log.info("DETALLE texto (resto):\n%s", texto[3500:7500])
            log.info("DETALLE botones/pestañas: %s", p.evaluate("() => Array.from(document.querySelectorAll('button, a, [role=tab], .p-tabview-nav li')).map(e => (e.innerText||'').trim()).filter(Boolean).slice(0, 60)"))
            log.info("DETALLE tablas: %s", json.dumps(p.evaluate(JS_TABLAS), ensure_ascii=False)[:2500])
            p.go_back()
            time.sleep(2)
        except Exception as e:
            log.info("DETALLE exploración falló: %s", e)

    def _leer_pagina_resultados(self, claves: list[str]) -> list[Procedimiento]:
        salida = []
        for tabla in self.page.evaluate(JS_TABLAS):
            mapa = _mapear(tabla["headers"], ALIAS_LISTA)
            if "numero" not in mapa:
                continue
            for fila in tabla["rows"]:
                c = fila["cells"]
                get = lambda campo: c[mapa[campo]] if campo in mapa and mapa[campo] < len(c) else ""
                numero = get("numero").strip()
                if not numero:
                    continue
                salida.append(Procedimiento(
                    numero=numero, entidad=get("entidad"), descripcion=get("descripcion"),
                    tipo=get("tipo"), estatus=get("estatus"),
                    f_publicacion=parse_fecha(get("f_publicacion")), f_junta=parse_fecha(get("f_junta")),
                    f_apertura=parse_fecha(get("f_apertura")),
                    url=fila.get("href", ""), claves_filtro=list(claves)))
        return salida

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

    # ---------- detalle ----------
    def completar_detalle(self, proc: Procedimiento) -> None:
        """Lee cronograma de eventos y partidas desde la pagina del procedimiento."""
        if not proc.url:
            proc.detalle_ok = False
            return
        try:
            self.page.goto(proc.url, wait_until="domcontentloaded")
            self.page.wait_for_load_state("networkidle")
            time.sleep(1.5)
            texto = self.page.inner_text("body")
            proc.f_junta = proc.f_junta or _fecha_cerca(texto, r"junta de aclaraciones")
            proc.f_apertura = _fecha_cerca(texto, r"presentaci[oó]n y apertura de proposiciones") or proc.f_apertura
            proc.f_fallo = proc.f_fallo or _fecha_cerca(texto, r"\bfallo\b")
            proc.f_publicacion = proc.f_publicacion or _fecha_cerca(texto, r"publicaci[oó]n")
            proc.partidas = self._leer_partidas()
            proc.detalle_ok = bool(proc.partidas)
        except Exception as e:
            log.warning("Detalle de %s falló: %s", proc.numero, e)
            proc.detalle_ok = False

    def _leer_partidas(self) -> list[Partida]:
        partidas: list[Partida] = []
        for tabla in self.page.evaluate(JS_TABLAS):
            hs = [_n(h) for h in tabla["headers"]]
            if not any("descripcion" in h for h in hs) or not any("partida" in h or "clave" in h for h in hs):
                continue
            mapa = _mapear(tabla["headers"], ALIAS_PARTIDAS)
            for fila in tabla["rows"]:
                c = fila["cells"]
                get = lambda campo: c[mapa[campo]] if campo in mapa and mapa[campo] < len(c) else ""
                if get("descripcion"):
                    partidas.append(Partida(numero=get("numero"), clave=get("clave"),
                                            descripcion=get("descripcion"), cantidad=get("cantidad")))
        return partidas

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
