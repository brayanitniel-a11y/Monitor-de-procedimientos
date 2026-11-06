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

CSV = Path(__file__).resolve().parent.parent / "data" / "historial" / "historial_sin_precios.csv"
TZ = ZoneInfo("America/Mexico_City")


def hora(h, d=6):
    return datetime(2026, 11, d, h, 0, tzinfo=TZ)


class Ventana(unittest.TestCase):
    def test_manana_busca_ayer(self):
        self.assertEqual(calcular_ventana(hora(8), date(2026, 11, 5)), (date(2026, 11, 5), date(2026, 11, 5)))

    def test_dia_y_noche_solo_hoy(self):
        for h in (13, 18, 21):
            self.assertEqual(calcular_ventana(hora(h), date(2026, 11, 6)), (date(2026, 11, 6), date(2026, 11, 6)))

    def test_recupera_dias_perdidos(self):
        self.assertEqual(calcular_ventana(hora(13), date(2026, 11, 3)), (date(2026, 11, 3), date(2026, 11, 6)))

    def test_tope_de_dias(self):
        self.assertEqual(calcular_ventana(hora(13), date(2026, 9, 1))[0], date(2026, 10, 30))


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


if __name__ == "__main__":
    unittest.main()
