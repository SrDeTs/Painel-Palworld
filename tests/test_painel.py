#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Testes automatizados do Painel Palworld — só biblioteca padrão.

Sobe o mock da API do jogo e o painel de verdade (em portas efêmeras,
na mesma máquina) e exercita login, sessão, proxy das rotas, ações
agregadas, cabeçalhos de segurança e travamento de path traversal.

Uso:  python3 tests/test_painel.py
"""

import copy
import http.client
import importlib.util
import json
import os
import sys
import threading
import unittest

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT_DIR = os.path.dirname(_TESTS_DIR)

PANEL_PASSWORD = "senha-teste-painel"
ADMIN_PASSWORD = "senha-teste-admin"


def _importar(caminho: str, nome: str):
    spec = importlib.util.spec_from_file_location(nome, caminho)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# --- sobe o mock ANTES de importar o painel (o painel lê env no import) ------

mock_mod = _importar(os.path.join(_TESTS_DIR, "mock_palworld.py"), "mock_palworld")
mock_mod.ADMIN_PASSWORD = ADMIN_PASSWORD

_mock_srv = mock_mod.ThreadingHTTPServer(("127.0.0.1", 0), mock_mod.MockHandler)
MOCK_PORT = _mock_srv.server_address[1]
threading.Thread(target=_mock_srv.serve_forever, daemon=True).start()

os.environ["PANEL_PASSWORD"] = PANEL_PASSWORD
os.environ["ADMIN_PASSWORD"] = ADMIN_PASSWORD
os.environ["PALWORLD_API"] = f"http://127.0.0.1:{MOCK_PORT}"
os.environ["API_TIMEOUT"] = "3"

painel = _importar(os.path.join(_ROOT_DIR, "painel", "painel.py"), "painel")
painel.PanelHandler.log_message = lambda self, *a: None  # silencia o log nos testes

_painel_srv = painel.ThreadingHTTPServer(("127.0.0.1", 0), painel.PanelHandler)
PANEL_PORT = _painel_srv.server_address[1]
threading.Thread(target=_painel_srv.serve_forever, daemon=True).start()

# cópia do elenco original de jogadores do mock p/ restaurar entre os testes
_PLAYERS_ORIGINAIS = copy.deepcopy(mock_mod.PLAYERS)


class TestesPainel(unittest.TestCase):
    def setUp(self):
        # estado limpo do mock e do painel a cada teste
        mock_mod.PLAYERS[:] = copy.deepcopy(_PLAYERS_ORIGINAIS)
        mock_mod.BANNED.clear()
        painel._limpar_falhas("127.0.0.1")

    # ---------- helpers ----------

    def _req(self, metodo, caminho, corpo=None, token=None):
        """Faz a requisição e devolve (status:int, corpo:dict, resposta crua)."""
        conn = http.client.HTTPConnection("127.0.0.1", PANEL_PORT, timeout=5)
        headers = {"Content-Type": "application/json"}
        if token:
            headers["X-Panel-Token"] = token
        payload = json.dumps(corpo) if corpo is not None else None
        conn.request(metodo, caminho, body=payload, headers=headers)
        resp = conn.getresponse()
        dados = resp.read()
        conn.close()
        try:
            parsed = json.loads(dados.decode("utf-8")) if dados else {}
        except json.JSONDecodeError:
            parsed = {}
        return resp.status, parsed, resp

    def _token(self) -> str:
        status, corpo, _resp = self._req("POST", "/api/login",
                                         {"password": PANEL_PASSWORD})
        assert status == 200, f"login deveria funcionar: {status} {corpo}"
        return corpo["token"]

    # ---------- estáticos e segurança ----------

    def test_01_index_served(self):
        conn = http.client.HTTPConnection("127.0.0.1", PANEL_PORT, timeout=5)
        conn.request("GET", "/")
        resp = conn.getresponse()
        dados = resp.read()
        conn.close()
        self.assertEqual(resp.status, 200)
        self.assertIn("text/html", resp.headers.get("Content-Type", ""))
        self.assertIn(b"Painel Palworld", dados)

    def test_02_cabecalhos_de_seguranca(self):
        status, _corpo, resp = self._req("GET", "/")
        self.assertEqual(resp.headers.get("X-Frame-Options"), "DENY")
        self.assertEqual(resp.headers.get("X-Content-Type-Options"), "nosniff")
        csp = resp.headers.get("Content-Security-Policy", "")
        self.assertIn("default-src 'self'", csp)
        self.assertIn("script-src 'self'", csp)
        self.assertIn("frame-ancestors 'none'", csp)

    def test_03_path_traversal_bloqueado(self):
        for caminho in ("/../painel.py", "/../../painel/painel.py",
                        "/..%2Fpainel.py", "/config.env"):
            status, _corpo, _resp = self._req("GET", caminho)
            self.assertEqual(status, 404, f"{caminho} deveria dar 404")
        status, _corpo, _resp = self._req("GET", "/app.js")
        self.assertEqual(status, 200)

    def test_04_api_sem_token_rejeitada(self):
        status, corpo, _resp = self._req("GET", "/api/status")
        self.assertEqual(status, 401)

    def test_05_head_responde(self):
        conn = http.client.HTTPConnection("127.0.0.1", PANEL_PORT, timeout=5)
        conn.request("HEAD", "/")
        resp = conn.getresponse()
        dados = resp.read()
        conn.close()
        self.assertEqual(resp.status, 200)
        self.assertIn("text/html", resp.headers.get("Content-Type", ""))
        self.assertEqual(dados, b"")  # HEAD não devolve corpo

    # ---------- login ----------

    def test_10_login_errado_com_nao_ascii(self):
        status, corpo, _resp = self._req("POST", "/api/login",
                                         {"password": "errada-ç@123"})
        self.assertEqual(status, 401)
        self.assertIn("error", corpo)

    def test_11_login_rate_limit(self):
        for _ in range(painel.LOGIN_MAX_FAILS):
            status, _corpo, _resp = self._req("POST", "/api/login",
                                              {"password": "errada"})
            self.assertEqual(status, 401)
        status, corpo, _resp = self._req("POST", "/api/login",
                                         {"password": "errada"})
        self.assertEqual(status, 429)
        self.assertIn("Tente de novo", corpo.get("error", ""))

    def test_12_login_ok_devolve_token(self):
        status, corpo, _resp = self._req("POST", "/api/login",
                                         {"password": PANEL_PASSWORD})
        self.assertEqual(status, 200)
        self.assertTrue(corpo.get("token"))

    def test_13_senha_vazia_nao_loga(self):
        original = painel.PANEL_PASSWORD
        painel.PANEL_PASSWORD = ""
        try:
            status, _corpo, _resp = self._req("POST", "/api/login",
                                              {"password": ""})
            self.assertEqual(status, 401)
        finally:
            painel.PANEL_PASSWORD = original

    # ---------- leitura via proxy ----------

    def test_20_status_online(self):
        tok = self._token()
        status, corpo, _resp = self._req("GET", "/api/status", token=tok)
        self.assertEqual(status, 200)
        self.assertTrue(corpo["online"])
        self.assertIn("mock", corpo["info"]["version"])
        self.assertIn("serverfps", {k.lower() for k in corpo["metrics"]})

    def test_21_players(self):
        tok = self._token()
        status, corpo, _resp = self._req("GET", "/api/players", token=tok)
        self.assertEqual(status, 200)
        nomes = [p["name"] for p in corpo["players"]]
        self.assertIn("Laure", nomes)

    def test_22_settings(self):
        tok = self._token()
        status, corpo, _resp = self._req("GET", "/api/settings", token=tok)
        self.assertEqual(status, 200)
        self.assertEqual(corpo.get("ServerName"), "Laure Kanda")

    def test_23_rota_inexistente(self):
        tok = self._token()
        status, _corpo, _resp = self._req("GET", "/api/nada", token=tok)
        self.assertEqual(status, 404)

    # ---------- ações ----------

    def test_30_anuncio_ok_e_vazio(self):
        tok = self._token()
        status, corpo, _resp = self._req("POST", "/api/announce",
                                         {"message": "olá galera"}, token=tok)
        self.assertEqual(status, 200)
        self.assertTrue(corpo["ok"])

        status, corpo, _resp = self._req("POST", "/api/announce",
                                         {"message": "  "}, token=tok)
        self.assertEqual(status, 400)

    def test_31_save(self):
        tok = self._token()
        status, corpo, _resp = self._req("POST", "/api/save", {}, token=tok)
        self.assertEqual(status, 200)
        self.assertTrue(corpo["ok"])

    def test_32_kick_remove_do_mock(self):
        tok = self._token()
        alvo = next(p for p in mock_mod.PLAYERS if p["name"] == "Zé dos Pals")
        status, corpo, _resp = self._req("POST", "/api/kick",
                                         {"userid": alvo["userId"],
                                          "message": "teste"}, token=tok)
        self.assertEqual(status, 200)
        self.assertNotIn(alvo, mock_mod.PLAYERS)

    def test_33_kick_sem_userid(self):
        tok = self._token()
        status, _corpo, _resp = self._req("POST", "/api/kick",
                                          {"message": "x"}, token=tok)
        self.assertEqual(status, 400)

    def test_34_ban_e_unban(self):
        tok = self._token()
        alvo = mock_mod.PLAYERS[0]
        status, _corpo, _resp = self._req("POST", "/api/ban",
                                          {"userid": alvo["userId"]}, token=tok)
        self.assertEqual(status, 200)
        self.assertIn(alvo["userId"], mock_mod.BANNED)

        status, _corpo, _resp = self._req("POST", "/api/unban",
                                          {"userid": alvo["userId"]}, token=tok)
        self.assertEqual(status, 200)
        self.assertNotIn(alvo["userId"], mock_mod.BANNED)

    def test_35_restart_agrega_passos(self):
        tok = self._token()
        status, corpo, _resp = self._req("POST", "/api/restart",
                                         {"waittime": 5}, token=tok)
        self.assertEqual(status, 200)
        self.assertTrue(corpo["ok"])
        self.assertEqual(set(corpo["passos"].values()), {"ok"})
        self.assertIn("anuncio", corpo["passos"])
        self.assertIn("shutdown", corpo["passos"])

    def test_36_shutdown_agrega_passos(self):
        tok = self._token()
        status, corpo, _resp = self._req("POST", "/api/shutdown",
                                         {"waittime": 5}, token=tok)
        self.assertEqual(status, 200)
        self.assertTrue(corpo["ok"])
        self.assertEqual(set(corpo["passos"].values()), {"ok"})

    def test_37_waittime_fora_da_faixa(self):
        tok = self._token()
        # 999999 vira 600; lixo vira 30 — nenhum dos dois vaza sem clamp
        for ruim in (999999, "abc"):
            status, corpo, _resp = self._req("POST", "/api/shutdown",
                                             {"waittime": ruim}, token=tok)
            self.assertEqual(status, 200)
            self.assertTrue(corpo["ok"])

    # ---------- backups ----------

    def test_50_backups_nao_configurado(self):
        tok = self._token()
        original = painel.BACKUP_DIR
        painel.BACKUP_DIR = ""
        try:
            status, corpo, _resp = self._req("GET", "/api/backups", token=tok)
            self.assertEqual(status, 200)
            self.assertFalse(corpo["configurado"])
        finally:
            painel.BACKUP_DIR = original

    def test_51_backups_listagem(self):
        import tempfile
        tok = self._token()
        original = painel.BACKUP_DIR
        with tempfile.TemporaryDirectory() as pasta:
            painel.BACKUP_DIR = pasta
            try:
                for nome, conteudo in (("velho.zip", b"antigo"),
                                       ("novo.zip", b"mais novo e maior")):
                    caminho = os.path.join(pasta, nome)
                    with open(caminho, "wb") as fh:
                        fh.write(conteudo)
                os.utime(os.path.join(pasta, "velho.zip"), (1_000, 1_000))
                os.utime(os.path.join(pasta, "novo.zip"), (2_000, 2_000))

                status, corpo, _resp = self._req("GET", "/api/backups", token=tok)
                self.assertEqual(status, 200)
                nomes = [b["nome"] for b in corpo["backups"]]
                self.assertEqual(nomes, ["novo.zip", "velho.zip"])  # mais novo primeiro
                self.assertEqual(corpo["backups"][0]["bytes"], len(b"mais novo e maior"))
            finally:
                painel.BACKUP_DIR = original

    def test_52_backup_download_ok(self):
        import tempfile
        tok = self._token()
        original = painel.BACKUP_DIR
        with tempfile.TemporaryDirectory() as pasta:
            painel.BACKUP_DIR = pasta
            try:
                alvo = os.path.join(pasta, "save.zip")
                with open(alvo, "wb") as fh:
                    fh.write(b"conteudo do backup")

                conn = http.client.HTTPConnection("127.0.0.1", PANEL_PORT, timeout=5)
                conn.request("GET", "/api/backups/download?name=save.zip",
                             headers={"X-Panel-Token": tok})
                resp = conn.getresponse()
                dados = resp.read()
                conn.close()
                self.assertEqual(resp.status, 200)
                self.assertIn("attachment", resp.headers.get("Content-Disposition", ""))
                self.assertEqual(dados, b"conteudo do backup")
            finally:
                painel.BACKUP_DIR = original

    def test_53_backup_download_traversal_bloqueado(self):
        import tempfile
        tok = self._token()
        original = painel.BACKUP_DIR
        with tempfile.TemporaryDirectory() as pasta:
            painel.BACKUP_DIR = pasta
            try:
                for ruim in ("..%2F..%2Fpainel.py", "..%2Fconfig.env",
                             "sub%2Fsegredo.txt", "inexistente.zip"):
                    conn = http.client.HTTPConnection("127.0.0.1", PANEL_PORT,
                                                      timeout=5)
                    conn.request(
                        "GET",
                        f"/api/backups/download?name={ruim}",
                        headers={"X-Panel-Token": tok})
                    resp = conn.getresponse()
                    resp.read()
                    conn.close()
                    self.assertEqual(resp.status, 404, f"{ruim} deveria dar 404")
            finally:
                painel.BACKUP_DIR = original

    def test_54_backup_sem_token_rejeitado(self):
        status, _corpo, _resp = self._req("GET", "/api/backups")
        self.assertEqual(status, 401)
        status, _corpo, _resp = self._req("GET", "/api/backups/download?name=x")
        self.assertEqual(status, 401)

    # ---------- sessão ----------

    def test_40_logout_invalida_token(self):
        tok = self._token()
        status, _corpo, _resp = self._req("POST", "/api/logout", {}, token=tok)
        self.assertEqual(status, 200)
        status, _corpo, _resp = self._req("GET", "/api/status", token=tok)
        self.assertEqual(status, 401)


if __name__ == "__main__":
    unittest.main(verbosity=2)
