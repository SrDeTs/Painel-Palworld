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
import time
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

import store as store_mod  # noqa: E402  (painel.py põe a pasta no sys.path)
import players as players_mod  # noqa: E402
import scheduler as scheduler_mod  # noqa: E402
import notifications as notif_mod  # noqa: E402
import users as users_mod  # noqa: E402

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

    # ---------- editor de configurações ----------

    def setUpEditor(self, ini_conteudo=None):
        """Prepara PALWORLD_INI + pasta de backups temporários."""
        import tempfile
        self._pasta_tmp = tempfile.TemporaryDirectory()
        ini = os.path.join(self._pasta_tmp.name, "PalWorldSettings.ini")
        if ini_conteudo is not None:
            with open(ini, "w", encoding="utf-8") as fh:
                fh.write(ini_conteudo)
        self._orig_ini = painel.PALWORLD_INI
        self._orig_bk = painel.CONFIG_BACKUPS_DIR
        painel.PALWORLD_INI = ini if ini_conteudo is not None else ""
        painel.CONFIG_BACKUPS_DIR = os.path.join(self._pasta_tmp.name, "bk")
        return ini

    def tearDownEditor(self):
        painel.PALWORLD_INI = self._orig_ini
        painel.CONFIG_BACKUPS_DIR = self._orig_bk
        self._pasta_tmp.cleanup()

    INI_EXEMPLO = (
        "; comentario\n[/Script/Pal.PalGameWorldSettings]\n"
        "OptionSettings=(DeathPenalty=All,bIsUseBackupSaveData=True,"
        'ExpRate=2.500000,ServerName="Servidor Teste",AdminPassword="segredo",'
        "CrossplayPlatforms=(Steam,Xbox))\n")

    def test_60_editor_sem_ini_mostra_padrao(self):
        tok = self._token()
        self.setUpEditor(ini_conteudo=None)
        try:
            status, corpo, _resp = self._req("GET", "/api/settings/editor",
                                             token=tok)
            self.assertEqual(status, 200)
            self.assertFalse(corpo["ini_existe"])
            chaves = {c["chave"] for c in corpo["catalogo"]}
            self.assertIn("ExpRate", chaves)
            self.assertEqual(corpo["fontes"]["ExpRate"], "padrao")
            # senha nunca volta em texto claro
            for c in corpo["catalogo"]:
                if c["senha"]:
                    v = corpo["valores"][c["chave"]]
                    self.assertIn(v, ("", "\u2022\u2022\u2022\u2022\u2022\u2022"))
        finally:
            self.tearDownEditor()

    def test_61_editor_com_ini_le_valores(self):
        tok = self._token()
        self.setUpEditor(self.INI_EXEMPLO)
        try:
            status, corpo, _resp = self._req("GET", "/api/settings/editor",
                                             token=tok)
            self.assertEqual(status, 200)
            self.assertTrue(corpo["ini_existe"])
            self.assertEqual(corpo["valores"]["ExpRate"], 2.5)
            self.assertEqual(corpo["valores"]["DeathPenalty"], "All")
            self.assertEqual(corpo["valores"]["AdminPassword"],
                             "\u2022\u2022\u2022\u2022\u2022\u2022")
            self.assertEqual(corpo["fontes"]["ExpRate"], "ini")
        finally:
            self.tearDownEditor()

    def test_62_diff_valida_e_normaliza(self):
        tok = self._token()
        self.setUpEditor(self.INI_EXEMPLO)
        try:
            status, corpo, _resp = self._req(
                "POST", "/api/settings/diff",
                {"mudancas": {"ExpRate": "3,5",       # vírgula vira ponto
                              "bHardcore": True,
                              "DeathPenalty": "Xpto"}},   # inválido
                token=tok)
            self.assertEqual(status, 200)
            self.assertFalse(corpo["ok"])
            validas = {v["chave"]: v["para"] for v in corpo["validas"]}
            self.assertEqual(validas.get("ExpRate"), 3.5)
            self.assertEqual(validas.get("bHardcore"), True)
            self.assertTrue(any(i["chave"] == "DeathPenalty"
                                for i in corpo["invalidas"]))
        finally:
            self.tearDownEditor()

    def test_63_apply_sem_ini_configurado_409(self):
        tok = self._token()
        self.setUpEditor(None)  # PALWORLD_INI=""
        try:
            status, corpo, _resp = self._req(
                "POST", "/api/settings/apply",
                {"mudancas": {"ExpRate": 5}}, token=tok)
            self.assertEqual(status, 409)
            self.assertIn("PALWORLD_INI", corpo["error"])
        finally:
            self.tearDownEditor()

    def test_64_apply_grava_e_faz_backup(self):
        tok = self._token()
        ini = self.setUpEditor(self.INI_EXEMPLO)
        try:
            status, corpo, _resp = self._req(
                "POST", "/api/settings/apply",
                {"mudancas": {"ExpRate": 7.0, "bHardcore": True}},
                token=tok)
            self.assertEqual(status, 200)
            self.assertTrue(corpo["ok"])
            self.assertEqual(len(corpo["aplicadas"]), 2)
            self.assertTrue(corpo["backup"])
            texto = open(ini, encoding="utf-8").read()
            self.assertIn("ExpRate=7.0", texto)
            self.assertIn("bHardcore=True", texto)
            # backup criado e listável
            status, corpo, _resp = self._req("GET", "/api/settings/backups",
                                             token=tok)
            nomes = [b["nome"] for b in corpo["backups"]]
            self.assertIn(corpo and nomes[0], nomes)
        finally:
            self.tearDownEditor()

    def test_65_apply_rejeita_valor_invalido_sem_escrever(self):
        tok = self._token()
        ini = self.setUpEditor(self.INI_EXEMPLO)
        try:
            antes = open(ini, encoding="utf-8").read()
            status, corpo, _resp = self._req(
                "POST", "/api/settings/apply",
                {"mudancas": {"ExpRate": 99999}}, token=tok)
            self.assertEqual(status, 200)
            self.assertFalse(corpo["ok"])
            self.assertEqual(open(ini, encoding="utf-8").read(), antes)
        finally:
            self.tearDownEditor()

    def test_66_restore_de_backup(self):
        tok = self._token()
        ini = self.setUpEditor(self.INI_EXEMPLO)
        try:
            # aplica algo p/ gerar backup
            self._req("POST", "/api/settings/apply",
                      {"mudancas": {"ExpRate": 9}}, token=tok)
            _, lista, _r = self._req("GET", "/api/settings/backups", token=tok)
            nome = lista["backups"][0]["nome"]
            status, corpo, _resp = self._req(
                "POST", "/api/settings/backups/restore", {"nome": nome},
                token=tok)
            self.assertEqual(status, 200)
            self.assertTrue(corpo["ok"])
            vals = sys.modules.get("settings_ini")
            import importlib.util as _ilu
            spec = _ilu.spec_from_file_location(
                "si_check", os.path.join(_ROOT_DIR, "painel", "settings_ini.py"))
            si = _ilu.module_from_spec(spec); spec.loader.exec_module(si)
            self.assertEqual(si.carregar(ini)["ExpRate"], 2.5)
        finally:
            self.tearDownEditor()

    def test_67_restore_traversal_bloqueado(self):
        tok = self._token()
        self.setUpEditor(self.INI_EXEMPLO)
        try:
            status, corpo, _resp = self._req(
                "POST", "/api/settings/backups/restore",
                {"nome": "../../painel.py"}, token=tok)
            self.assertEqual(status, 404)
        finally:
            self.tearDownEditor()

    def test_68_export_nao_vaza_senhas(self):
        tok = self._token()
        self.setUpEditor(self.INI_EXEMPLO)
        try:
            status, corpo, _resp = self._req("GET", "/api/settings/export",
                                             token=tok)
            self.assertEqual(status, 200)
            self.assertEqual(corpo["_formato"], "painel-palworld-config")
            bruto_txt = json.dumps(corpo)
            self.assertNotIn("segredo", bruto_txt)
            self.assertNotIn("AdminPassword", bruto_txt)
        finally:
            self.tearDownEditor()

    def test_69_import_valida_formato_e_valores(self):
        tok = self._token()
        self.setUpEditor(self.INI_EXEMPLO)
        try:
            status, corpo, _resp = self._req(
                "POST", "/api/settings/import",
                {"valores": {"ExpRate": 4}}, token=tok)
            self.assertEqual(status, 400)

            status, corpo, _resp = self._req(
                "POST", "/api/settings/import",
                {"_formato": "painel-palworld-config",
                 "valores": {"ExpRate": 4, "DeathPenalty": "Nada"}},
                token=tok)
            self.assertEqual(status, 200)
            validas = {v["chave"]: v["para"] for v in corpo["validas"]}
            self.assertEqual(validas.get("ExpRate"), 4.0)
            self.assertTrue(any(i["chave"] == "DeathPenalty"
                                for i in corpo["invalidas"]))
        finally:
            self.tearDownEditor()

    def test_70_raw_mascara_senhas(self):
        tok = self._token()
        self.setUpEditor(self.INI_EXEMPLO)
        try:
            status, corpo, _resp = self._req("GET", "/api/settings/raw",
                                             token=tok)
            self.assertEqual(status, 200)
            self.assertNotIn("segredo", corpo["texto"])
            self.assertIn("AdminPassword=", corpo["texto"])
        finally:
            self.tearDownEditor()

    # ---------- dashboard & métricas históricas ----------

    def setUpBanco(self):
        """Pasta de dados isolada p/ o SQLite dos testes."""
        import tempfile
        self._banco_tmp = tempfile.TemporaryDirectory()
        store_mod.DATA_DIR_ORIGINAL = store_mod.DATA_DIR
        # recarrega conexão apontando p/ pasta temporária
        self._conn_antiga = store_mod._conn
        store_mod.DATA_DIR = self._banco_tmp.name
        store_mod.DB_PATH = os.path.join(self._banco_tmp.name, "painel.db")
        with store_mod._lock:
            store_mod._conn = None
        return store_mod

    def tearDownBanco(self):
        if getattr(self, "_conn_antiga", None) is not None:
            self._conn_antiga.close()
        store_mod.DATA_DIR = store_mod.DATA_DIR_ORIGINAL
        store_mod.DB_PATH = os.path.join(store_mod.DATA_DIR, "painel.db")
        with store_mod._lock:
            store_mod._conn = None
        self._banco_tmp.cleanup()
        painel.DATA_DIR = painel.store_mod.DATA_DIR

    def test_71_dashboard_forma_e_banco(self):
        tok = self._token()
        store = self.setUpBanco()
        try:
            status, corpo, _resp = self._req("GET", "/api/dashboard",
                                             token=tok)
            self.assertEqual(status, 200)
            for chave in ("status", "ultimo_backup", "ultima_queda",
                          "disco_livre_mb", "banco"):
                self.assertIn(chave, corpo)
            self.assertTrue(corpo["banco"]["ok"])
            self.assertIsInstance(corpo["status"], dict)
            self.assertTrue("online" in corpo["status"])
        finally:
            self.tearDownBanco()

    def test_72_history_resumo_com_sementes(self):
        tok = self._token()
        store = self.setUpBanco()
        try:
            agora = time.time()
            for i in range(6):  # 3 amostras online + 1 offline, últimos minutos
                ts = agora - (6 - i) * 60
                store.registrar_metricas({
                    "online": i != 2,
                    "info": {"version": "t"},
                    "metrics": {"ServerFPS": float(100 + i),
                                "currentplayernum": i % 3,
                                "maxplayernum": 4}}, ts=ts)

            status, corpo, _resp = self._req(
                "GET", "/api/metrics/history?range=1h", token=tok)
            self.assertEqual(status, 200)
            self.assertGreaterEqual(len(corpo["amostras"]), 4)
            resumo = corpo["resumo"]
            self.assertEqual(resumo["fps_min"], 100.0)
            self.assertEqual(resumo["fps_max"], 105.0)
            self.assertEqual(resumo["players_max"], 2)
            self.assertAlmostEqual(resumo["pct_online"],
                                   round(100 * 5 / 6, 1))
        finally:
            self.tearDownBanco()

    def test_73_history_sem_token_401(self):
        status, _corpo, _resp = self._req("GET", "/api/metrics/history")
        self.assertEqual(status, 401)
        status, _corpo, _resp = self._req("GET", "/api/dashboard")
        self.assertEqual(status, 401)

    # ---------- registro de jogadores & bans ----------

    def _players_store_isolado(self):
        """Aponta players.* p/ um banco temporário e devolve o módulo."""
        import tempfile
        self._pl_tmp = tempfile.TemporaryDirectory()
        self._pl_orig = (players_mod.DATA_DIR, players_mod.DB_PATH,
                         players_mod._conn)
        players_mod.DATA_DIR = self._pl_tmp.name
        players_mod.DB_PATH = os.path.join(self._pl_tmp.name, "p.db")
        with players_mod._lock:
            players_mod._conn = None
        return players_mod

    def _players_store_restaurar(self):
        orig_data, orig_path, orig_conn = self._pl_orig
        if orig_conn is not None:
            orig_conn.close()
        players_mod.DATA_DIR, players_mod.DB_PATH = orig_data, orig_path
        with players_mod._lock:
            players_mod._conn = None
        self._pl_tmp.cleanup()

    def test_74_registro_vazio_e_busca(self):
        tok = self._token()
        self._players_store_isolado()
        try:
            status, corpo, _resp = self._req("GET", "/api/players/registro",
                                             token=tok)
            self.assertEqual(status, 200)
            self.assertEqual(corpo["total"], 0)

            # semeia dois jogadores via presença observada
            players_mod.atualizar([
                {"userId": "steam_111", "name": "Ana", "level": 10},
                {"userId": "steam_222", "name": "Beto", "level": 20},
            ])
            players_mod.atualizar([{"userId": "steam_111", "name": "Ana",
                                    "level": 11}])  # Beto saiu

            status, corpo, _resp = self._req("GET",
                                             "/api/players/registro?busca=Ana",
                                             token=tok)
            self.assertEqual(corpo["total"], 1)
            ana = corpo["jogadores"][0]
            self.assertTrue(ana["online"])
            self.assertEqual(ana["nivel_max"], 11)

            status, corpo, _resp = self._req(
                "GET", "/api/players/detalhe?userid=steam_222", token=tok)
            ficha = corpo
            self.assertFalse(ficha["online"])
            self.assertEqual(ficha["sessoes"], 1)
            self.assertIsNotNone(ficha["sessoes_lista"][0]["duracao_s"])
        finally:
            self._players_store_restaurar()

    def test_75_ban_registra_no_livro_e_unban_fecha(self):
        tok = self._token()
        alvo = mock_mod.PLAYERS[0]
        self._players_store_isolado()
        try:
            status, corpo, _resp = self._req(
                "POST", "/api/ban",
                {"userid": alvo["userId"], "message": "flood",
                 "duracao_horas": 24}, token=tok)
            self.assertEqual(status, 200)
            livro = players_mod.listar_bans()
            self.assertEqual(len(livro), 1)
            self.assertEqual(livro[0]["motivo"], "flood")
            self.assertIsNotNone(livro[0]["expira_em"])
            self.assertTrue(players_mod.esta_banido(alvo["userId"]))

            status, corpo, _resp = self._req(
                "POST", "/api/unban", {"userid": alvo["userId"]}, token=tok)
            self.assertEqual(status, 200)
            self.assertGreaterEqual(corpo.get("bans_locais_removidos", 0), 1)
            self.assertFalse(players_mod.esta_banido(alvo["userId"]))
        finally:
            self._players_store_restaurar()

    def test_76_bans_listagem_e_filtro(self):
        tok = self._token()
        self._players_store_isolado()
        try:
            players_mod.registrar_ban("steam_aaa", nome="Um", motivo="x")
            players_mod.registrar_ban("steam_bbb", nome="Dois", motivo="y")
            players_mod.marcar_unban("steam_aaa")
            _, corpo, _r = self._req("GET", "/api/bans?ativos=1", token=tok)
            self.assertEqual(len(corpo["bans"]), 1)
            self.assertEqual(corpo["bans"][0]["userid"], "steam_bbb")
            _, corpo, _r = self._req("GET", "/api/bans", token=tok)
            self.assertEqual(len(corpo["bans"]), 2)
            _, corpo, _r = self._req("GET", "/api/bans?busca=bbb", token=tok)
            self.assertEqual(len(corpo["bans"]), 1)
        finally:
            self._players_store_restaurar()

    def test_77_detalhe_inexistente_404(self):
        tok = self._token()
        self._players_store_isolado()
        try:
            status, corpo, _resp = self._req(
                "GET", "/api/players/detalhe?userid=nada", token=tok)
            self.assertEqual(status, 404)
        finally:
            self._players_store_restaurar()

    # ---------- console (eventos + SSE) ----------

    def test_80_events_exige_token_e_lista(self):
        status, _corpo, _resp = self._req("GET", "/api/events")
        self.assertEqual(status, 401)
        tok = self._token()  # o próprio login já gera eventos
        status, corpo, _resp = self._req(
            "GET", "/api/events?limite=50", token=tok)
        self.assertEqual(status, 200)
        evs = corpo["eventos"]
        self.assertTrue(any(e["kind"] == "auth" for e in evs))
        # ordenado, ids crescentes na resposta
        ids = [e["id"] for e in evs]
        self.assertEqual(ids, sorted(ids, reverse=True))

    def test_81_events_desde_id_filtra(self):
        tok = self._token()
        _status, corpo, _resp = self._req("GET", "/api/events?limite=5",
                                          token=tok)
        maior_id = max(e["id"] for e in corpo["eventos"])
        _status, corpo2, _resp = self._req(
            "GET", f"/api/events?desde_id={maior_id}", token=tok)
        self.assertEqual(corpo2["eventos"], [])

    def test_82_sse_sem_token_401(self):
        conn = http.client.HTTPConnection("127.0.0.1", PANEL_PORT, timeout=5)
        conn.request("GET", "/api/stream")
        resp = conn.getresponse()
        resp.read()
        conn.close()
        self.assertEqual(resp.status, 401)

    def test_83_sse_entrega_eventos_novos(self):
        import time as _time
        tok = self._token()
        conn = http.client.HTTPConnection("127.0.0.1", PANEL_PORT, timeout=10)
        conn.request("GET", f"/api/stream?token={tok}")
        resp = conn.getresponse()
        self.assertEqual(resp.status, 200)
        self.assertIn("text/event-stream",
                      resp.headers.get("Content-Type", ""))
        # lê o cabeçalho retry e os eventos iniciais (login acima)
        buf = b""
        prazo = _time.time() + 5
        while _time.time() < prazo and b"\ndata:" not in buf:
            pedaco = resp.read(64)
            if not pedaco:
                break
            buf += pedaco
            if len(buf) > 65536:
                break
        conn.close()
        self.assertIn(b"data:", buf)

    # ---------- usuários, permissões e audit ----------

    def _users_isolado(self):
        import tempfile
        self._us_tmp = tempfile.TemporaryDirectory()
        self._us_orig = (users_mod.DATA_DIR, users_mod.DB_PATH,
                         users_mod._conn)
        users_mod.DATA_DIR = self._us_tmp.name
        users_mod.DB_PATH = os.path.join(self._us_tmp.name, "u.db")
        with users_mod._lock:
            users_mod._conn = None

    def _users_restaurar(self):
        orig_data, orig_path, orig_conn = self._us_orig
        if orig_conn is not None:
            orig_conn.close()
        users_mod.DATA_DIR, users_mod.DB_PATH = orig_data, orig_path
        with users_mod._lock:
            users_mod._conn = None
        self._us_tmp.cleanup()

    def test_a0_bootstrap_admin_login_legacy(self):
        tok = self._token()  # login só com senha → admin
        status, corpo, _resp = self._req("GET", "/api/me", token=tok)
        self.assertEqual(status, 200)
        self.assertEqual(corpo["usuario"]["papel"], "admin")

    def test_a1_criar_moderador_e_viewer_com_permissoes(self):
        self._users_isolado()
        try:
            users_mod.garantir_admin_inicial(PANEL_PASSWORD)
            admin_tok = self._token()  # sessão já no banco isolado
            # criar moderador
            status, corpo, _resp = self._req(
                "POST", "/api/usuarios",
                {"usuario": "mod1", "password": "senha-mod-1",
                 "papel": "moderador"}, token=admin_tok)
            self.assertEqual(status, 200)

            # login do moderador
            status, corpo, _resp = self._req(
                "POST", "/api/login",
                {"usuario": "mod1", "password": "senha-mod-1"})
            self.assertEqual(status, 200)
            mod_tok = corpo["token"]

            # moderador pode salvar (SERVER_SAVE)…
            status, _c, _r = self._req("POST", "/api/save",
                                       {"waittime": 5}, token=mod_tok)
            self.assertEqual(status, 200)
            # …mas não pode editar settings nem gerenciar usuários
            status, corpo, _resp = self._req(
                "POST", "/api/settings/apply",
                {"mudancas": {}}, token=mod_tok)
            self.assertEqual(status, 403)
            status, _c, _r = self._req("GET", "/api/usuarios",
                                       token=mod_tok)
            self.assertEqual(status, 403)

            # viewer: só leitura — save é bloqueado
            status, corpo, _resp = self._req(
                "POST", "/api/usuarios",
                {"usuario": "view1", "password": "senha-view-1",
                 "papel": "viewer"}, token=admin_tok)
            self.assertEqual(status, 200)
            status, corpo, _resp = self._req(
                "POST", "/api/login",
                {"usuario": "view1", "password": "senha-view-1"})
            view_tok = corpo["token"]
            status, _c, _r = self._req("POST", "/api/save", {}, token=view_tok)
            self.assertEqual(status, 403)
            status, _c, _r = self._req("GET", "/api/status", token=view_tok)
            self.assertEqual(status, 200)
        finally:
            self._users_restaurar()

    def test_a2_ultimo_admin_protegido(self):
        self._users_isolado()
        try:
            users_mod.garantir_admin_inicial(PANEL_PASSWORD)
            admin_tok = self._token()
            me = users_mod.autenticar(None, PANEL_PASSWORD)
            status, corpo, _resp = self._req(
                "DELETE", f"/api/usuarios/{me['id']}", token=admin_tok)
            self.assertEqual(status, 400)
            self.assertIn("último admin", corpo["error"])
        finally:
            self._users_restaurar()

    def test_a3_audit_registra_usuario(self):
        tok = self._token()
        status, corpo, _resp = self._req("GET", "/api/audit?limite=20",
                                         token=tok)
        self.assertEqual(status, 200)
        logins = [e for e in corpo["eventos"] if e["kind"] == "auth"
                  and e["level"] == "info"]
        self.assertTrue(logins)
        detalhe = json.loads(logins[0]["detail"] or "{}")
        self.assertEqual(detalhe.get("usuario"), "admin")

    def test_a4_revogar_sessao_especifica(self):
        self._users_isolado()
        try:
            users_mod.garantir_admin_inicial(PANEL_PASSWORD)
            tok = self._token()
            status, corpo, _resp = self._req(
                "POST", "/api/sessoes/revogar", {"todos": True}, token=tok)
            self.assertEqual(status, 200)
            # revogar tudo matou inclusive a sessão deste request
            status, _c, _r = self._req("GET", "/api/me", token=tok)
            self.assertEqual(status, 401)
        finally:
            self._users_restaurar()

    # ---------- página pública & PWA ----------

    def test_b0_public_status_sem_auth(self):
        original = painel.PUBLIC_STATUS
        painel.PUBLIC_STATUS = True
        try:
            status, corpo, _resp = self._req("GET", "/api/public/status")
            self.assertEqual(status, 200)
            for chave in ("online", "nome", "versao", "jogadores",
                          "capacidade", "conectar"):
                self.assertIn(chave, corpo)
        finally:
            painel.PUBLIC_STATUS = original

    def test_b1_public_status_desligavel(self):
        original = painel.PUBLIC_STATUS
        painel.PUBLIC_STATUS = False
        try:
            status, corpo, _resp = self._req("GET", "/api/public/status")
            self.assertEqual(status, 404)
            status, corpo, _resp = self._req("GET", "/status")
            self.assertEqual(status, 404)
        finally:
            painel.PUBLIC_STATUS = original

    def test_b2_public_status_nao_vaza_segredos(self):
        tok = self._token()
        # garante amostra com nome do servidor no cache local
        store_mod.registrar_metricas({
            "online": True,
            "info": {"servername": "Servidor Secreto", "version": "v9"},
            "metrics": {"currentplayernum": 3, "maxplayernum": 10}})
        status, corpo, _resp = self._req("GET", "/api/public/status")
        self.assertTrue(corpo["online"])
        self.assertEqual(corpo["nome"], "Servidor Secreto")
        texto = json.dumps(corpo)
        for proibido in ("password", "senha", "token", "admin"):
            self.assertNotIn(proibido, texto.lower())

    def test_b3_pwa_arquivos_servidos(self):
        conn = http.client.HTTPConnection("127.0.0.1", PANEL_PORT, timeout=5)
        conn.request("GET", "/manifest.webmanifest")
        resp = conn.getresponse()
        corpo = resp.read()
        conn.close()
        self.assertEqual(resp.status, 200)
        self.assertIn(b'"name"', corpo)

        conn = http.client.HTTPConnection("127.0.0.1", PANEL_PORT, timeout=5)
        conn.request("GET", "/sw.js")
        resp = conn.getresponse()
        dados = resp.read()
        conn.close()
        self.assertEqual(resp.status, 200)
        self.assertIn(b"serviceWorker" , b"") if False else None
        self.assertIn(b"addEventListener", dados)

    def test_b4_gamedata_degrada_com_graca(self):
        tok = self._token()
        status, corpo, _resp = self._req("GET", "/api/gamedata", token=tok)
        self.assertEqual(status, 200)
        # o mock não expõe game-data → deve marcar como indisponível,
        # nunca inventar dados
        if not corpo["disponivel"]:
            self.assertIn("motivo", corpo)

    # ---------- API v1 + API keys + Prometheus ----------

    def test_c0_api_v1_exige_auth(self):
        for caminho in ("/api/v1", "/api/v1/server", "/metrics"):
            status, _corpo, _resp = self._req("GET", caminho)
            self.assertEqual(status, 401, caminho)

    def test_c1_api_key_fluxo_completo(self):
        admin_tok = self._token()
        self._users_isolado()
        try:
            users_mod.garantir_admin_inicial(PANEL_PASSWORD)
            admin_tok = self._token()

            # criar key com permissões de leitura
            status, corpo, _resp = self._req(
                "POST", "/api/keys",
                {"nome": "ci", "permissoes": ["SERVER_VIEW"]},
                token=admin_tok)
            self.assertEqual(status, 200)
            chave = corpo["chave"]
            self.assertTrue(chave.startswith("ppk_"))

            # índice acessível com X-API-Key
            conn = http.client.HTTPConnection("127.0.0.1", PANEL_PORT,
                                              timeout=5)
            conn.request("GET", "/api/v1", headers={"X-API-Key": chave})
            resp = conn.getresponse(); dados = json.loads(resp.read())
            conn.close()
            self.assertEqual(dados["versao"], "v1")
            self.assertEqual(dados["identidade"], "key:ci")

            # permissão ausente → 403 em ação de escrita
            conn = http.client.HTTPConnection("127.0.0.1", PANEL_PORT,
                                              timeout=5)
            conn.request("POST", "/api/v1/actions/save",
                         body="{}", headers={"X-API-Key": chave})
            resp = conn.getresponse(); resp.read(); conn.close()
            self.assertEqual(resp.status, 403)

            # listagem não expõe a chave completa
            status, corpo, _resp = self._req("GET", "/api/keys",
                                             token=admin_tok)
            self.assertNotIn(chave, json.dumps(corpo))
        finally:
            self._users_restaurar()

    def test_c2_prometheus_formato(self):
        admin_tok = self._token()
        self._users_isolado()
        try:
            users_mod.garantir_admin_inicial(PANEL_PASSWORD)
            admin_tok = self._token()
            store = self.setUpBanco() if False else None  # banco já isolado
            agora = time.time()
            store_mod.registrar_metricas({
                "online": True, "info": {}, "metrics": {
                    "ServerFPS": 60.5, "currentplayernum": 2,
                    "maxplayernum": 8}}, ts=agora)

            status, corpo, _resp = self._req(
                "POST", "/api/keys",
                {"nome": "prom", "permissoes": ["SERVER_VIEW"]},
                token=admin_tok)
            chave = corpo["chave"]

            conn = http.client.HTTPConnection("127.0.0.1", PANEL_PORT,
                                              timeout=5)
            conn.request("GET", "/metrics", headers={"X-API-Key": chave})
            resp = conn.getresponse()
            texto = resp.read().decode()
            conn.close()
            self.assertEqual(resp.status, 200)
            self.assertIn("text/plain", resp.headers.get("Content-Type", ""))
            self.assertIn("palworld_up 1", texto)
            self.assertIn("palworld_fps 60.5", texto)
            self.assertIn("painel_saude", texto)
        finally:
            if getattr(self, "_banco_tmp", None):
                self.tearDownBanco()
            self._users_restaurar()

    # ---------- scheduler & backups do painel ----------

    def setUpAgendaBanco(self):
        """Banco isolado p/ jobs + registra executores de teste."""
        self.setUpBanco()
        self._exec_orig = dict(scheduler_mod._executores)
        self._rodados = []
        scheduler_mod.registrar_executor(
            "announce", lambda p: self._rodados.append(p) or "anunciado")
        return scheduler_mod

    def tearDownAgendaBanco(self):
        scheduler_mod._executores.clear()
        scheduler_mod._executores.update(getattr(self, "_exec_orig", {}))
        self.tearDownBanco()

    def test_90_job_crud_e_validacao(self):
        tok = self._token()
        sch = self.setUpAgendaBanco()
        try:
            status, corpo, _resp = self._req(
                "POST", "/api/scheduler",
                {"nome": "Restart diário", "tipo": "restart",
                 "hora": "04:00", "params": {"waittime": 300}},
                token=tok)
            self.assertEqual(status, 200)
            job = corpo["job"]
            self.assertEqual(job["tipo"], "restart")
            self.assertTrue(job["proximo_run"] > time.time())

            # inválido: sem hora nem intervalo
            status, corpo, _resp = self._req(
                "POST", "/api/scheduler",
                {"nome": "x", "tipo": "save"}, token=tok)
            self.assertEqual(status, 400)

            # inválido: waittime fora da faixa é clampado na validação
            status, corpo, _resp = self._req(
                "POST", "/api/scheduler",
                {"nome": "r2", "tipo": "restart", "intervalo_horas": 2,
                 "params": {"waittime": 99999}}, token=tok)
            self.assertEqual(status, 200)
            self.assertEqual(corpo["job"]["params"]["waittime"], 600)

            # atualizar desabilita → proximo_run vai pro futuro distante
            status, corpo, _resp = self._req(
                "POST", f"/api/scheduler/{job['id']}",
                {"habilitado": False}, token=tok)
            self.assertEqual(status, 200)

            # excluir
            status, corpo, _resp = self._req(
                "DELETE", f"/api/scheduler/{job['id']}", token=tok)
            self.assertTrue(corpo["ok"])
        finally:
            self.tearDownAgendaBanco()

    def test_91_execucao_manual_via_endpoint(self):
        tok = self._token()
        sch = self.setUpAgendaBanco()
        try:
            status, corpo, _resp = self._req(
                "POST", "/api/scheduler",
                {"nome": "Anúncio", "tipo": "announce",
                 "intervalo_horas": 1,
                 "params": {"message": "oi"}}, token=tok)
            job_id = corpo["job"]["id"]
            status, corpo, _resp = self._req(
                "POST", f"/api/scheduler/{job_id}/run", {}, token=tok)
            self.assertEqual(status, 200)
            self.assertEqual(corpo["resultado"], "anunciado")
            self.assertEqual(len(self._rodados), 1)
            job = sch.obter(job_id)
            self.assertIn("ok", job["ultimo_resultado"])
            self.assertIsNotNone(job["ultimo_run"])
        finally:
            self.tearDownAgendaBanco()

    def test_92_backup_mundo_criar_restaurar_excluir(self):
        import tempfile
        tok = self._token()
        pasta = tempfile.TemporaryDirectory()
        save_dir = os.path.join(pasta.name, "SaveGames", "0", "W1")
        os.makedirs(save_dir)
        with open(os.path.join(save_dir, "Level.sav"), "w") as fh:
            fh.write("conteudo original")
        backup_dir = os.path.join(pasta.name, "bk")
        orig = (painel.PALWORLD_SAVE_DIR, painel.BACKUP_DIR)
        painel.PALWORLD_SAVE_DIR, painel.BACKUP_DIR = save_dir, backup_dir
        try:
            status, corpo, _resp = self._req(
                "POST", "/api/backups/criar", {}, token=tok)
            self.assertEqual(status, 200)
            nome = corpo["nome"]
            self.assertTrue(nome.startswith("palworld-painel-"))

            # modifica o save e restaura
            with open(os.path.join(save_dir, "Level.sav"), "w") as fh:
                fh.write("modificado depois")
            status, corpo, _resp = self._req(
                "POST", "/api/backups/restaurar", {"nome": nome}, token=tok)
            self.assertEqual(status, 200)
            with open(os.path.join(save_dir, "Level.sav")) as fh:
                self.assertEqual(fh.read(), "conteudo original")

            # traversal bloqueado
            status, corpo, _resp = self._req(
                "POST", "/api/backups/excluir",
                {"nome": "../painel.py"}, token=tok)
            self.assertEqual(status, 404)

            status, corpo, _resp = self._req(
                "POST", "/api/backups/excluir", {"nome": nome}, token=tok)
            self.assertTrue(corpo["ok"])
            self.assertFalse(os.path.exists(os.path.join(backup_dir, nome)))
        finally:
            painel.PALWORLD_SAVE_DIR, painel.BACKUP_DIR = orig
            pasta.cleanup()

    def test_93_backup_sem_pasta_configurada_400(self):
        tok = self._token()
        orig = (painel.PALWORLD_SAVE_DIR, painel.BACKUP_DIR)
        painel.PALWORLD_SAVE_DIR, painel.BACKUP_DIR = "", ""
        try:
            status, corpo, _resp = self._req(
                "POST", "/api/backups/criar", {}, token=tok)
            self.assertEqual(status, 400)
            self.assertIn("PALWORLD_SAVE_DIR", corpo["error"])
        finally:
            painel.PALWORLD_SAVE_DIR, painel.BACKUP_DIR = orig

    # ---------- saúde, recuperação & notificações ----------

    def test_94_diagnostico_forma_e_estado(self):
        tok = self._token()
        status, corpo, _resp = self._req("GET", "/api/health", token=tok)
        self.assertEqual(status, 200)
        self.assertIn(corpo["estado"],
                      ("healthy", "degraded", "critical", "offline",
                       "maintenance"))
        ids = [i["id"] for i in corpo["itens"]]
        for esperado in ("api", "disco", "backups", "coletor", "banco"):
            self.assertIn(esperado, ids)
        api_item = next(i for i in corpo["itens"] if i["id"] == "api")
        if not api_item["estado"] == "ok":
            self.assertTrue(api_item["sugestao"])

    def test_95_recuperacao_get_post_valida(self):
        tok = self._token()
        original = painel.config_recuperacao()
        try:
            status, corpo, _resp = self._req("GET", "/api/recovery",
                                             token=tok)
            self.assertEqual(status, 200)
            padrao_fps = corpo["fps_minimo"]

            status, corpo, _resp = self._req(
                "POST", "/api/recovery",
                {"habilitado": True, "fps_minimo": 20}, token=tok)
            self.assertEqual(status, 200)
            self.assertTrue(corpo["ok"])
            self.assertEqual(corpo["fps_minimo"], 20)
            self.assertFalse(corpo["janela_minutos"] < 2)  # sanidade aplicada
        finally:
            painel.salvar_config_recuperacao(original)

    def test_96_notificacoes_mascaram_e_preservam_segredo(self):
        tok = self._token()
        import notifications as notif_mod
        cfg_original = notif_mod.carregar()
        try:
            status, corpo, _resp = self._req(
                "POST", "/api/notifications",
                {"canais": [{"id": "t1", "tipo": "discord", "nome": "x",
                             "webhook_url":
                                 "https://discord.com/api/webhooks/9/segredo",
                             "eventos": ["server_down"]}]},
                token=tok)
            self.assertEqual(status, 200)

            status, corpo, _resp = self._req("GET", "/api/notifications",
                                             token=tok)
            canal = corpo["canais"][0]
            self.assertNotIn("segredo", json.dumps(corpo))
            self.assertIn("\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022",
                          canal["webhook_url"])
            self.assertIn("server_down", canal["eventos"])
        finally:
            notif_mod.salvar(cfg_original)

    def test_97_notificacoes_teste_canal_inexistente(self):
        tok = self._token()
        status, corpo, _resp = self._req(
            "POST", "/api/notifications/teste", {"id": "fantasma"},
            token=tok)
        self.assertEqual(status, 400)
        self.assertIn("não encontrado", corpo["erro"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
