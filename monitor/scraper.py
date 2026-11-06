"""Lectura de ComprasMX con Playwright.

IMPORTANTE: los localizadores se basan en el TEXTO visible de las etiquetas del portal
(p. ej. "Clave - Descripción de partida específica"), no en clases CSS. Aun asi, el portal
es una aplicacion Angular y pueden requerir ajuste en la primera corrida real.
Para eso existe `python -m monitor.main --descubrir`, que guarda captura y lista de campos.
"""
from __future__ import annotations

import json
import logging
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
    "numero": ["numero de procedimiento", "no. de procedimiento", "numero procedimiento", "no. procedimiento", "procedimiento de contratacion"],
    "descripcion": ["nombre del procedimiento", "descripcion", "nombre procedimiento", "objeto"],
    "entidad": ["unidad compradora", "dependencia", "entidad", "institucion"],
    "tipo": ["tipo de procedimiento", "tipo procedimiento"],
    "estatus": ["estatus", "estado", "situacion"],
    "f_publicacion": ["fecha de publicacion", "publicacion"],
    "f_apertura": ["fecha de apertura", "apertura de proposiciones", "presentacion y apertura"],
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
    def abrir_busqueda(self) -> None:
        p = self.page
        p.goto(self.cfg.url, wait_until="domcontentloaded")
        p.wait_for_load_state("networkidle")
        if self._campo(ETIQUETA_CLAVE) is None:
            for texto in (r"Procedimientos? de contrataci[oó]n", r"Buscar procedimientos?", r"Filtros"):
                try:
                    p.get_by_text(re.compile(texto, re.I)).first.click(timeout=4000)
                    p.wait_for_load_state("networkidle")
                    if self._campo(ETIQUETA_CLAVE) is not None:
                        break
                except Exception:
                    continue
        if self._campo(ETIQUETA_CLAVE) is None:
            self._captura("no_encontre_filtros")
            raise PortalError(
                f"No encontré el filtro '{ETIQUETA_CLAVE}'. Ejecuta: python -m monitor.main --descubrir")

    def buscar(self, clave: str, desde: date, hasta: date) -> list[Procedimiento]:
        self.abrir_busqueda()
        self._escribir(self._campo(ETIQUETA_CLAVE), clave)
        f_ini, f_fin = self._campo(ETIQUETA_FECHA, 0), self._campo(ETIQUETA_FECHA, 1)
        if f_ini is None or f_fin is None:
            self._captura("no_encontre_fechas")
            raise PortalError(f"No encontré el rango '{ETIQUETA_FECHA}'. Ejecuta: python -m monitor.main --descubrir")
        self._escribir(f_ini, desde.strftime("%d/%m/%Y"))
        self._escribir(f_fin, hasta.strftime("%d/%m/%Y"))
        self.page.get_by_role("button", name=re.compile(r"Buscar|Consultar", re.I)).first.click()
        self.page.wait_for_load_state("networkidle")
        time.sleep(1.5)
        self._captura(f"resultados_{clave}")

        resultados: dict[str, Procedimiento] = {}
        for pagina in range(MAX_PAGINAS):
            for proc in self._leer_pagina_resultados(clave):
                resultados.setdefault(proc.numero, proc)
            if not self._siguiente_pagina():
                break
        log.info("Clave %s %s→%s: %s procedimiento(s)", clave, desde, hasta, len(resultados))
        return list(resultados.values())

    def _leer_pagina_resultados(self, clave: str) -> list[Procedimiento]:
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
                    f_publicacion=parse_fecha(get("f_publicacion")), f_apertura=parse_fecha(get("f_apertura")),
                    url=fila.get("href", ""), claves_filtro=[clave]))
        return salida

    def _siguiente_pagina(self) -> bool:
        p = self.page
        for loc in (
            p.get_by_role("button", name=re.compile(r"siguiente|next", re.I)),
            p.locator("[aria-label*='iguiente'], [aria-label*='Next']"),
        ):
            try:
                b = loc.first
                if b.count() and b.is_visible() and b.is_enabled() and b.get_attribute("disabled") is None \
                        and b.get_attribute("aria-disabled") != "true":
                    b.click()
                    p.wait_for_load_state("networkidle")
                    time.sleep(1)
                    return True
            except Exception:
                continue
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
        return ruta


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
