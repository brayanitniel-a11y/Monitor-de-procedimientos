import json
import os
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from monitor import escucha


class _Falso(BaseHTTPRequestHandler):
    llamadas: list = []

    def do_POST(self):
        cuerpo = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        _Falso.llamadas.append((self.path, cuerpo))
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"ok": true, "result": {}}')

    def log_message(self, *a):
        pass


def _textos():
    salida = []
    for ruta, cuerpo in _Falso.llamadas:
        if ruta.endswith("/sendMessage"):
            salida.append(json.loads(cuerpo)["text"])
    return salida


def _upd(texto, chat="-100", edad=5, nombre="Ana"):
    return {"update_id": 1, "message": {"chat": {"id": int(chat)}, "date": int(time.time()) - edad,
                                        "text": texto, "from": {"first_name": nombre}}}


class Comandos(unittest.TestCase):
    def test_reconoce_solo_los_propios(self):
        c = escucha.comando_de
        self.assertEqual(c("/revisar_partidas", "MiBot"), "revisar_partidas")
        self.assertEqual(c("/revisar_partidas@MiBot", "MiBot"), "revisar_partidas")
        self.assertEqual(c("/REVISAR_PARTIDAS@mibot arg", "MiBot"), "revisar_partidas")
        self.assertIsNone(c("/revisar_partidas@OtroBot", "MiBot"))   # dirigido a otro bot
        self.assertIsNone(c("/start@MiBot", "MiBot"))                # comando que no es nuestro
        self.assertIsNone(c("hola /revisar_partidas", "MiBot"))
        self.assertIsNone(c(None, "MiBot"))


class Escucha(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = HTTPServer(("127.0.0.1", 0), _Falso)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        os.environ["TELEGRAM_API_BASE"] = f"http://127.0.0.1:{cls.srv.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        os.environ.pop("TELEGRAM_API_BASE", None)

    def setUp(self):
        _Falso.llamadas.clear()
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        self.cfg = SimpleNamespace(token="TOK", chat_id="-100", data_dir=d, excel=d / "r.xlsx",
                                   tz=ZoneInfo("America/Mexico_City"), claves=["32401", "53101"])
        self.e = escucha.Escucha(self.cfg, comando_revision=[sys.executable, "-c", "print('ok')"])
        self.e.usuario = "MiBot"

    def tearDown(self):
        self.tmp.cleanup()

    def test_ignora_otro_chat_y_comandos_de_otro_bot(self):
        self.e.atender(_upd("/estado_partidas@MiBot", chat="-999"))
        self.e.atender(_upd("/estado_partidas@OtroBot"))
        self.e.atender(_upd("hola a todos"))
        self.assertEqual(_Falso.llamadas, [])

    def test_no_depende_del_reloj_del_servidor(self):
        self.e.atender(_upd("/estado_partidas@MiBot", edad=7200))          # fecha "vieja": igual se atiende
        self.assertEqual(len(_textos()), 1)

    def test_getupdates_nunca_lleva_offset_nulo(self):
        self.e._descartar_pendientes()
        cuerpos = [json.loads(c) for r, c in _Falso.llamadas if r.endswith("/getUpdates")]
        self.assertEqual(cuerpos, [{"offset": -1, "timeout": 0, "allowed_updates": ["message"]}])

    def test_estado(self):
        self.e.atender(_upd("/estado_partidas@MiBot"))
        t = _textos()
        self.assertEqual(len(t), 1)
        self.assertIn("Procedimientos registrados: 0", t[0])
        self.assertIn("32401, 53101", t[0])

    def test_excel_sin_archivo_y_con_archivo(self):
        self.e.atender(_upd("/excel_partidas"))
        self.assertIn("Todavía no hay un Excel", _textos()[0])
        self.cfg.excel.write_bytes(b"PK-falso")
        _Falso.llamadas.clear()
        self.e.atender(_upd("/excel_partidas"))
        self.assertTrue(any(r.endswith("/sendDocument") for r, _ in _Falso.llamadas))

    def test_revisar_lanza_el_proceso_una_sola_vez(self):
        self.e.atender(_upd("/revisar_partidas@MiBot", nombre="Luis"))
        self.assertIn("Revisión manual iniciada por Luis", _textos()[0])
        self.e.atender(_upd("/revisar_partidas@MiBot"))          # segunda petición mientras corre
        if self.e._hilo.is_alive():
            self.assertIn("Ya hay una revisión en curso", _textos()[-1])
        self.e._hilo.join(10)
        self.assertFalse(self.e._hilo.is_alive())

    def test_no_arranca_si_hay_una_revision_programada(self):
        (Path(self.tmp.name) / "monitor.lock").write_text("1")
        self.e.atender(_upd("/revisar_partidas@MiBot"))
        self.assertIn("Ya hay una revisión en curso", _textos()[0])
        self.assertIsNone(self.e._hilo)

    def test_revision_con_error_grave_avisa(self):
        self.e.comando_revision = [sys.executable, "-c", "import sys; sys.exit(7)"]
        self.e.atender(_upd("/revisar_partidas@MiBot"))
        self.e._hilo.join(10)
        self.assertTrue(any("terminó con error (código 7)" in t for t in _textos()))


if __name__ == "__main__":
    unittest.main()
