import json
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from openpyxl import load_workbook

from monitor import historial, matcher, registro
from monitor.estado import Estado
from monitor.models import Partida, Procedimiento, fmt_fecha, parse_fecha
from monitor.ventana import calcular_ventana

CSV = Path(__file__).resolve().parent.parent / "data" / "historial" / "historial_sin_precios.csv"  # un archivo; también se acepta una carpeta
TZ = ZoneInfo("America/Mexico_City")


def hora(h, d=6):
    return datetime(2026, 11, d, h, 0, tzinfo=TZ)


class Ventana(unittest.TestCase):
    def test_manana_busca_ayer_y_hoy(self):
        self.assertEqual(calcular_ventana(hora(8), date(2026, 11, 5)), (date(2026, 11, 5), date(2026, 11, 6)))

    def test_dia_y_noche_solo_hoy(self):
        for h in (13, 18, 21):
            self.assertEqual(calcular_ventana(hora(h), date(2026, 11, 6)), (date(2026, 11, 6), date(2026, 11, 6)))

    def test_recupera_dias_perdidos(self):
        self.assertEqual(calcular_ventana(hora(13), date(2026, 11, 3)), (date(2026, 11, 3), date(2026, 11, 6)))

    def test_tope_de_dias(self):
        self.assertEqual(calcular_ventana(hora(13), date(2026, 9, 1))[0], date(2026, 10, 30))


class VentanaFinDeSemana(unittest.TestCase):
    """Entre semana 8/13/18/21; sabado y domingo solo 19:30. Nada debe quedar sin cubrir."""

    def test_sabado_cubre_desde_el_viernes_noche(self):
        sab = datetime(2026, 11, 7, 19, 30, tzinfo=TZ)           # sabado 19:30
        self.assertEqual(calcular_ventana(sab, date(2026, 11, 6)), (date(2026, 11, 6), date(2026, 11, 7)))

    def test_domingo_cubre_sabado_noche(self):
        dom = datetime(2026, 11, 8, 19, 30, tzinfo=TZ)
        self.assertEqual(calcular_ventana(dom, date(2026, 11, 7)), (date(2026, 11, 7), date(2026, 11, 8)))

    def test_lunes_manana_cubre_domingo_noche(self):
        lun = datetime(2026, 11, 9, 8, 0, tzinfo=TZ)
        self.assertEqual(calcular_ventana(lun, date(2026, 11, 8)), (date(2026, 11, 8), date(2026, 11, 9)))

    def test_fin_de_semana_sin_revisiones_se_recupera(self):
        lun = datetime(2026, 11, 9, 8, 0, tzinfo=TZ)             # el equipo estuvo apagado desde el viernes
        self.assertEqual(calcular_ventana(lun, date(2026, 11, 6)), (date(2026, 11, 6), date(2026, 11, 9)))


class Fechas(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(parse_fecha("19/11/2026 10:00 hrs"), "2026-11-19T10:00")
        self.assertEqual(parse_fecha("sin fecha"), "")
        self.assertEqual(fmt_fecha("2026-11-19T00:00"), "19/11/2026")


class Matcher(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.items = historial.cargar(CSV)

    def ev(self, desc, clave=""):
        return matcher.evaluar_partida(Partida(descripcion=desc, clave=clave), self.items)

    def test_historial_cargado(self):
        self.assertGreater(len(self.items), 50)

    def test_descripcion_casi_igual_es_alta(self):
        r = self.ev("MONITOR DE SIGNOS VITALES BÁSICO")
        self.assertEqual(r.nivel, "Alta", (r.porcentaje, r.similar_desc))

    def test_plural_y_acentos(self):
        self.assertEqual(self.ev("Oxímetros de pulso portátiles").nivel, "Alta")

    def test_clave_con_formato_distinto(self):
        r = self.ev("COSA RARA", clave="5311680069")
        self.assertTrue(r.por_clave)
        self.assertEqual(r.nivel, "Alta")
        self.assertIn("ELECTROCARDI", r.similar_desc)

    def test_producto_ajeno_sin_coincidencia(self):
        self.assertEqual(self.ev("ESCRITORIO EJECUTIVO DE MADERA").nivel, "Ninguna")
        self.assertEqual(self.ev("SILLA DE RUEDAS PLEGABLE").nivel, "Ninguna")

    def test_claves_cortas_no_coinciden(self):
        self.assertFalse(matcher.clave_coincide("500.531", "500.531.0059"))

    def test_central_no_es_alta_de_monitor(self):
        r = self.ev("CENTRAL DE MONITOREO PARA MONITORES DE SIGNOS VITALES")
        self.assertIn("CENTRAL", r.similar_desc)  # debe parecerse a la central, no al monitor suelto


class HistorialVariosArchivos(unittest.TestCase):
    ENC = "numero_procedimiento,numero_partida,clave_cucop_o_sai,descripcion_detallada,marca,modelo,anio\n"

    def _carpeta(self, d, archivos):
        for nombre, filas in archivos.items():
            (Path(d) / nombre).write_text(self.ENC + "".join(filas), encoding="utf-8")

    def test_suma_archivos_y_no_duplica_renglones_repetidos(self):
        with tempfile.TemporaryDirectory() as d:
            self._carpeta(d, {
                "a_base.csv": ["LA-1,1,531.1,TERMOMETRO INFRARROJO,MARCA X,M1,2025\n"],
                "b_nuevo.csv": ["LA-1,1,531.1,TERMOMETRO INFRARROJO,MARCA X,M1,2025\n",     # repetido de a_base
                                "LA-9,3,531.2,BASCULA DIGITAL PEDIATRICA,MARCA Y,M2,2026\n"],
            })
            items = {it.descripcion: it for it in historial.cargar(Path(d))}
            self.assertEqual(set(items), {"TERMOMETRO INFRARROJO", "BASCULA DIGITAL PEDIATRICA"})
            self.assertEqual(items["TERMOMETRO INFRARROJO"].veces, 1)       # no se contó dos veces
            self.assertEqual(items["BASCULA DIGITAL PEDIATRICA"].ultimo_anio, "2026")

    def test_archivo_minimo_solo_con_descripcion(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "minimo.csv").write_text("descripcion_detallada\nOXIMETRO DE PULSO\n", encoding="utf-8")
            it = historial.cargar(Path(d))[0]
            self.assertFalse(it.dudoso)                                     # sin precio: cuenta como oferta real
            e = matcher.evaluar_partida(Partida(descripcion="Oximetros de pulso portatiles"), [it])
            self.assertEqual(e.nivel, "Alta")

    def test_la_copia_sin_precios_va_al_final(self):
        with tempfile.TemporaryDirectory() as d:
            for n in ("historial_sin_precios.csv", "z_otro.csv", "a_otro.csv"):
                (Path(d) / n).write_text("descripcion_detallada\nX\n")
            self.assertEqual([p.name for p in historial.archivos_historial(Path(d))],
                             ["a_otro.csv", "z_otro.csv", "historial_sin_precios.csv"])


class Registro(unittest.TestCase):
    def _proc(self, n):
        p = Procedimiento(numero=n, entidad="X", descripcion="D", f_publicacion="2026-11-06T00:00",
                          f_apertura="2026-11-19T10:00", detectado="2026-11-06T13:00", url="https://e.com",
                          partidas=[Partida(numero="1", descripcion="MONITOR DE SIGNOS VITALES")])
        matcher.evaluar_procedimiento(p, historial.cargar(CSV))
        return p

    def test_excel_no_duplica_y_conserva_ediciones(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            est = Estado(d / "estado.json")
            est.agregar(self._proc("LA-1"))
            self.assertEqual(registro.sincronizar(est, d / "r.xlsx"), 1)
            wb = load_workbook(d / "r.xlsx")
            ws = wb["Procedimientos"]
            col = registro.IDX_REVISADO
            self.assertEqual(ws.cell(row=2, column=col).value, "No")
            ws.cell(row=2, column=col).value = "Sí"   # el equipo marca revisado
            ws.cell(row=2, column=registro.IDX_PROB).value = "Alta"
            wb.save(d / "r.xlsx")
            est.agregar(self._proc("LA-2"))
            self.assertEqual(registro.sincronizar(est, d / "r.xlsx"), 1)
            self.assertEqual(registro.sincronizar(est, d / "r.xlsx"), 0)
            ws = load_workbook(d / "r.xlsx")["Procedimientos"]
            self.assertEqual(ws.max_row, 3)
            self.assertEqual(ws.cell(row=2, column=col).value, "Sí")
            self.assertEqual(ws.cell(row=2, column=registro.IDX_PROB).value, "Alta")
            self.assertEqual(ws.cell(row=3, column=col).value, "No")

    def test_estado_roundtrip_y_dedupe(self):
        with tempfile.TemporaryDirectory() as d:
            est = Estado(Path(d) / "e.json")
            est.agregar(self._proc("LA-07-110-1-T-5-2026"))
            est.guardar()
            otro = Estado.cargar(Path(d) / "e.json")
            self.assertTrue(otro.tiene("la-07-110-1-t-5-2026 "))
            self.assertEqual(len(otro.pendientes()), 1)



class Mensajes(unittest.TestCase):
    def test_coincidencia_al_inicio_y_sin_detalle_de_partidas(self):
        from monitor import mensajes
        p = Procedimiento(numero="LA-1", partidas=[Partida(numero="1", descripcion="MONITOR DE SIGNOS VITALES")])
        matcher.evaluar_procedimiento(p, historial.cargar(CSV))
        txt = mensajes.formatear(p)
        self.assertTrue(txt.startswith("🟢 <b>Coincidencia ALTA</b>"))
        self.assertNotIn("Partida 1", txt)


class Telegram(unittest.TestCase):
    def test_documento_se_arma_y_se_envia(self):
        import io
        from unittest import mock

        from monitor import telegram
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "r.xlsx"
            f.write_bytes(b"PK-contenido-falso")
            visto = {}

            def falso(req, timeout=0):
                visto["url"], visto["cuerpo"], visto["tipo"] = req.full_url, req.data, req.headers["Content-type"]
                return io.BytesIO(b'{"ok": true}')

            with mock.patch("urllib.request.urlopen", falso):
                self.assertTrue(telegram.enviar_documento("TOK", "-100", f, "pie"))
            self.assertTrue(visto["url"].endswith("/botTOK/sendDocument"))
            self.assertIn(b'filename="r.xlsx"', visto["cuerpo"])
            self.assertIn(b"PK-contenido-falso", visto["cuerpo"])
            self.assertIn("multipart/form-data; boundary=", visto["tipo"])

    def test_documento_sin_archivo_falla_limpio(self):
        from monitor import telegram
        self.assertFalse(telegram.enviar_documento("T", "1", "/no/existe.xlsx"))


class Proyectos(unittest.TestCase):
    def _proy(self):
        p = Procedimiento(numero="PC-2026-00001941", entidad="IMSS", descripcion="SERVICIO DE DOSIMETRIA",
                          origen="PROYECTO DE CONVOCATORIA", tipo="LICITACIÓN PÚBLICA",
                          f_publicacion="2026-11-05T16:03", f_comentarios="2026-11-12T09:00",
                          detectado="2026-11-06T08:00", partidas=[Partida(numero="1", descripcion="MONITOR DE SIGNOS VITALES")])
        matcher.evaluar_procedimiento(p, historial.cargar(CSV))
        return p

    def test_mensaje_identifica_proyecto(self):
        from monitor import mensajes
        t = mensajes.formatear(self._proy())
        self.assertIn("PROYECTO DE CONVOCATORIA", t)
        self.assertIn("límite para recepción de comentarios", t)
        self.assertNotIn("junta de aclaraciones", t)

    def test_mensaje_procedimiento_formato_pedido(self):
        from monitor import mensajes
        p = self._proy()
        p.origen = ""
        p.expediente, p.caracter, p.criterio = "E-2026-00121219", "NACIONAL", "BINARIO"
        p.f_aclaraciones, p.f_fallo = "2026-11-17T10:00", "2026-11-26T13:00"
        t = mensajes.formatear(p).splitlines()
        self.assertTrue(t[0].startswith(("🟢", "🟡", "⚪")))      # coincidencia al principio
        self.assertIn("<b>Código del expediente:</b>", t)
        self.assertIn("E-2026-00121219", t)
        self.assertIn("17/11/2026 10:00", t)
        self.assertNotIn("posible partida", "\n".join(t))

    def test_sin_novedad(self):
        from monitor import mensajes
        t = mensajes.sin_novedad(datetime(2026, 11, 12, 9, 40))
        self.assertIn("Sin novedad en ComprasMX para EM", t)
        self.assertIn("12/11/2026 09:40", t)

    def test_estado_conserva_origen_y_excel_lo_marca(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            est = Estado(d / "e.json")
            est.agregar(self._proy())
            est.guardar()
            otro = Estado.cargar(d / "e.json")
            self.assertEqual(otro.procedimientos["PC-2026-00001941"].origen, "PROYECTO DE CONVOCATORIA")
            registro.sincronizar(otro, d / "r.xlsx")
            ws = load_workbook(d / "r.xlsx")["Procedimientos"]
            self.assertIn("PROYECTO DE CONVOCATORIA", ws.cell(row=2, column=5).value)

    def test_valor_tras_etiqueta(self):
        from monitor.scraper import _valor_tras
        txt = "Nombre del proyecto de convocatoria:\nSERVICIO X\nDescripción detallada del proyecto de convocatoria:\nSERVICIO X Y"
        self.assertEqual(_valor_tras(txt, r"nombre del proyecto de convocatoria"), "SERVICIO X")


if __name__ == "__main__":
    unittest.main()
