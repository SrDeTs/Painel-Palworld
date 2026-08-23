#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Painel Palworld — site de administração para servidores dedicados de Palworld.

Servidor web leve usando SOMENTE a biblioteca padrão do Python
(sem pip, sem dependências, sem build).

Como funciona:
  - Serve a interface do painel (pasta static/)
  - Recebe as ações do navegador e repassa para a REST API oficial do jogo
    (http://<servidor>:8212/v1/api), injetando a autenticação Basic no
    lado do servidor — a senha de admin nunca chega ao navegador.
  - Controla acesso próprio com senha + token de sessão.

Configuração (nesta ordem de prioridade):
  1. Variáveis de ambiente do processo (ex.: usadas pelo Docker Compose)
  2. Arquivo config.env na mesma pasta deste script
  3. Valores padrão

  PANEL_PORT      porta do painel            (padrão 8080)
  PANEL_PASSWORD  senha de acesso ao painel  (padrão "123")
  PALWORLD_API    endereço da API do jogo    (padrão http://127.0.0.1:8212)
  ADMIN_PASSWORD  AdminPassword do servidor  (padrão "123")
  API_TIMEOUT     timeout das chamadas em s  (padrão 4)

Rodar:  python3 painel.py
"""

import base64
import hmac
import json
import os
import secrets
import threading
import time
import urllib.error
import urllib.request
from urllib.parse import urlparse, parse_qs, urlsplit, quote
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# ----------------------------------------------------------------- configuração

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


def _load_env_file(path: str) -> dict:
    """Lê um arquivo simples KEY=VALUE (ignora linhas vazias e comentários #)."""
    valores = {}
    try:
        with open(path, encoding="utf-8") as fh:
            for linha in fh:
                linha = linha.strip()
                if not linha or linha.startswith("#") or "=" not in linha:
                    continue
                chave, _, valor = linha.partition("=")
                valores[chave.strip()] = valor.strip().strip("'\"")
    except OSError:
        pass
    return valores


_env_file = _load_env_file(os.path.join(_SCRIPT_DIR, "config.env"))
_fonte = {}  # de onde veio cada valor, só p/ exibir no banner


def _cfg(nome: str, padrao: str) -> str:
    if os.environ.get(nome):            # 1. ambiente do processo (Docker)
        _fonte[nome] = "variável de ambiente"
        return os.environ[nome]
    if nome in _env_file:               # 2. arquivo config.env
        _fonte[nome] = "config.env"
        return _env_file[nome]
    _fonte[nome] = "padrão"             # 3. padrão do código
    return padrao


PANEL_PORT = int(_cfg("PANEL_PORT", "8080"))
PANEL_PASSWORD = _cfg("PANEL_PASSWORD", "123")
PALWORLD_API = _cfg("PALWORLD_API", "http://127.0.0.1:8212").rstrip("/")
ADMIN_PASSWORD = _cfg("ADMIN_PASSWORD", "123")
API_TIMEOUT = float(_cfg("API_TIMEOUT", "4"))
MAX_API_BODY = 5_000_000  # teto de leitura da resposta da API do jogo (bytes)
GAME_PORT = _cfg("GAME_PORT", "8211")  # porta UDP do jogo, p/ exibir o endereço certo no painel
BACKUP_DIR = _cfg("BACKUP_DIR", "")  # opcional: pasta de backups p/ listar/baixar (ex.: /backups)

STATIC_DIR = os.path.join(_SCRIPT_DIR, "static")
SESSION_TTL = 12 * 3600  # 12 horas

MIME = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
    ".json": "application/json; charset=utf-8",
    ".woff2": "font/woff2",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
}

# ----------------------------------------------------------------- sessões

_sessions = {}
_sessions_lock = threading.Lock()

# rate-limit simples de login por IP
_login_fails = {}
_login_lock = threading.Lock()
LOGIN_MAX_FAILS = 5
LOGIN_LOCKOUT_S = 60.0


def _login_bloqueado(ip: str) -> float:
    """Retorna segundos restantes de bloqueio (0 = liberado)."""
    with _login_lock:
        reg = _login_fails.get(ip)
        if not reg:
            return 0.0
        until, count = reg
        if count < LOGIN_MAX_FAILS:
            return 0.0
        restante = until - time.time()
        if restante <= 0:
            _login_fails.pop(ip, None)
            return 0.0
        return restante


def _registrar_falha(ip: str) -> None:
    with _login_lock:
        # poda oportunista: o mapa de IPs não cresce sem limite
        if len(_login_fails) > 1024:
            agora = time.time()
            for k in [k for k, (ate, _) in _login_fails.items() if ate < agora]:
                _login_fails.pop(k, None)
        reg = _login_fails.get(ip, [0.0, 0])
        if time.time() > reg[0]:
            reg = [time.time() + LOGIN_LOCKOUT_S, 0]
        reg[1] += 1
        _login_fails[ip] = reg


def _limpar_falhas(ip: str) -> None:
    with _login_lock:
        _login_fails.pop(ip, None)


def _create_session() -> str:
    token = secrets.token_urlsafe(32)
    now = time.time()
    with _sessions_lock:
        for t in [t for t, exp in _sessions.items() if exp < now]:
            _sessions.pop(t, None)
        _sessions[token] = now + SESSION_TTL
    return token


def _valid_session(token: str) -> bool:
    if not token:
        return False
    with _sessions_lock:
        exp = _sessions.get(token)
        if exp is None:
            return False
        if exp < time.time():
            _sessions.pop(token, None)
            return False
        _sessions[token] = time.time() + SESSION_TTL  # renova a sessão
        return True


def _destroy_session(token: str) -> None:
    with _sessions_lock:
        _sessions.pop(token, None)


# ----------------------------------------------------------------- cliente da API do jogo

class ApiError(Exception):
    """Falha ao falar com a REST API do jogo."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


_basic_cache = ""
_basic_lock = threading.Lock()


def _basic_header() -> str:
    global _basic_cache
    with _basic_lock:
        if not _basic_cache:
            raw = f"admin:{ADMIN_PASSWORD}".encode("utf-8")
            _basic_cache = "Basic " + base64.b64encode(raw).decode("ascii")
        return _basic_cache


def api(method: str, path: str, payload=None):
    """Chama a REST API do Palworld. Levanta ApiError em qualquer falha."""
    data = None
    headers = {"Authorization": _basic_header()}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = urllib.request.Request(PALWORLD_API + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=API_TIMEOUT) as resp:
            body = resp.read(MAX_API_BODY).decode("utf-8", "replace")
            return json.loads(body) if body.strip() else {}
    except urllib.error.HTTPError as e:
        try:
            detail = e.read().decode("utf-8", "replace")[:300]
        except Exception:
            detail = ""
        if e.code == 401:
            raise ApiError(502, "O jogo rejeitou a senha de admin (401). "
                                "Confira se ADMIN_PASSWORD do painel é igual à do servidor.")
        raise ApiError(502, f"A API do jogo respondeu {e.code}: {detail or e.reason}")
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise ApiError(504, f"Servidor de Palworld inacessível em {PALWORLD_API} ({e})")


# ----------------------------------------------------------------- ações agregadas

def status_combinado():
    """Info + métricas numa chamada só. Se o jogo estiver offline, devolve online:false."""
    srv = {"host": urlparse(PALWORLD_API).hostname or "", "porta_jogo": GAME_PORT}
    try:
        info = api("GET", "/v1/api/info")
        metrics = api("GET", "/v1/api/metrics")
        return {"online": True, "info": info, "metrics": metrics, "servidor": srv}
    except ApiError as e:
        return {"online": False, "error": e.message, "servidor": srv}


def _waittime_seguro(payload) -> int:
    try:
        return max(5, min(int(payload.get("waittime", 30)), 600))
    except (TypeError, ValueError):
        return 30


def _sequencia_desligar(msg: str, waittime: int) -> dict:
    """Anuncia → salva → desliga com contagem. Devolve o resultado por passo."""
    passos = {}
    for nome, metodo, caminho, corpo in (
        ("anuncio", "POST", "/v1/api/announce", {"message": msg}),
        ("save", "POST", "/v1/api/save", {}),
        ("shutdown", "POST", "/v1/api/shutdown", {"waittime": waittime, "message": msg}),
    ):
        try:
            api(metodo, caminho, corpo)
            passos[nome] = "ok"
        except ApiError as e:
            passos[nome] = e.message
    return passos


def reiniciar(payload):
    """Anuncia → salva → desliga com contagem. O Docker (restart unless-stopped)
    religa o container sozinho logo depois — é isso que faz o 'reiniciar' funcionar."""
    waittime = _waittime_seguro(payload)
    msg = str(payload.get("message") or "").strip() or \
        f"O servidor sera reiniciado em {waittime} segundos!"

    passos = _sequencia_desligar(msg, waittime)
    falhou_algum = any(v != "ok" for v in passos.values())
    resposta = {
        "ok": not falhou_algum,
        "passos": passos,
        "nota": f"Desligamento em {waittime}s solicitado. O container será religado "
                "automaticamente pelo Docker (restart: unless-stopped).",
    }
    return resposta, (200 if not falhou_algum else 502)


# ----------------------------------------------------------------- backups

def _raiz_backups() -> str:
    """Caminho real da pasta de backups ("" se não configurada)."""
    return os.path.realpath(BACKUP_DIR) if BACKUP_DIR else ""


def listar_backups() -> dict:
    """Lista os arquivos de backup do servidor, mais recentes primeiro."""
    raiz = _raiz_backups()
    if not raiz:
        return {"configurado": False, "backups": []}
    if not os.path.isdir(raiz):
        return {"configurado": True, "raiz": raiz, "backups": [],
                "nota": f"A pasta {raiz} ainda não existe (sem backups gerados)."}
    itens = []
    for nome in os.listdir(raiz):
        caminho = os.path.join(raiz, nome)
        try:
            if os.path.isfile(caminho):
                st = os.stat(caminho)
                itens.append({"nome": nome, "bytes": st.st_size,
                              "modificado": int(st.st_mtime)})
        except OSError:
            continue  # arquivo sumiu/permission — ignora
    itens.sort(key=lambda i: i["modificado"], reverse=True)
    return {"configurado": True, "raiz": raiz, "backups": itens}


# ----------------------------------------------------------------- handler HTTP

class PanelHandler(BaseHTTPRequestHandler):
    server_version = "PainelPalworld/1.0"
    protocol_version = "HTTP/1.1"

    # ---------- helpers de resposta

    def log_message(self, fmt, *args):  # log compacto no stdout
        print(f"[{self.log_date_time_string()}] {self.command} {self.path} "
              f"-> {args[0] if args else ''}")

    def end_headers(self):
        # cabeçalhos de segurança em toda resposta
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        # CSP: só recursos locais; scripts externos/inline ficam bloqueados
        self.send_header("Content-Security-Policy",
                         "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
                         "img-src 'self' data:; connect-src 'self'; font-src 'self'; "
                         "frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        super().end_headers()

    def _send_json(self, status: int, obj) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self):
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0:
            return {}
        raw = self.rfile.read(min(length, 1_000_000))
        try:
            obj = json.loads(raw.decode("utf-8"))
            return obj if isinstance(obj, dict) else {}
        except json.JSONDecodeError:
            return {}

    def _token(self) -> str:
        return self.headers.get("X-Panel-Token", "")

    def _autenticado(self) -> bool:
        if _valid_session(self._token()):
            return True
        self._send_json(401, {"error": "Não autenticado. Faça login novamente."})
        return False

    # ---------- arquivos estáticos

    def _serve_static(self, rel_path: str, head_only: bool = False) -> None:
        if rel_path in ("", "/", "index.html"):
            rel_path = "index.html"
        rel_path = rel_path.lstrip("/")
        full = os.path.realpath(os.path.join(STATIC_DIR, rel_path))
        static_root = os.path.realpath(STATIC_DIR)

        if not full.startswith(static_root + os.sep) and full != static_root:
            self._send_json(404, {"error": "não encontrado"})
            return
        if not os.path.isfile(full):
            self._send_json(404, {"error": "não encontrado"})
            return

        ext = os.path.splitext(full)[1].lower()
        with open(full, "rb") as fh:
            body = fh.read()
        self.send_response(200)
        self.send_header("Content-Type", MIME.get(ext, "application/octet-stream"))
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        if not head_only:
            self.wfile.write(body)

    # ---------- GET / HEAD

    def do_GET(self):
        partes = urlsplit(self.path)
        path = partes.path
        try:
            if path.startswith("/api/"):
                self._handle_api_get(path, parse_qs(partes.query))
            else:
                self._serve_static(path)
        except BrokenPipeError:
            pass
        except Exception as e:  # nunca derruba o servidor
            print(f"[ERRO] GET {path}: {e!r}")
            self._send_json(500, {"error": "erro interno do painel"})

    def do_HEAD(self):
        # cabe apenas em estáticos — monitoramento/health check
        path = self.path.split("?", 1)[0]
        try:
            if not path.startswith("/api/"):
                self._serve_static(path, head_only=True)
            else:
                self._send_json(405, {"error": "método não permitido"})
        except Exception as e:
            print(f"[ERRO] HEAD {path}: {e!r}")
            self._send_json(500, {"error": "erro interno do painel"})

    def _handle_api_get(self, path: str, qs: dict = None) -> None:
        if path == "/api/backups":
            if not self._autenticado():
                return
            try:
                self._send_json(200, listar_backups())
            except ApiError as e:
                self._send_json(e.status, {"error": e.message})
            return

        if path == "/api/backups/download":
            if not self._autenticado():
                return
            self._baixar_backup(qs or {})
            return

        rotas = {
            "/api/status": lambda: status_combinado(),
            "/api/players": lambda: api("GET", "/v1/api/players"),
            "/api/settings": lambda: api("GET", "/v1/api/settings"),
        }
        rota = rotas.get(path)
        if rota is None:
            self._send_json(404, {"error": "rota não encontrada"})
            return
        if not self._autenticado():
            return
        try:
            self._send_json(200, rota())
        except ApiError as e:
            self._send_json(e.status, {"error": e.message})

    def _baixar_backup(self, qs: dict) -> None:
        """Manda o arquivo de backup como download (à prova de traversal)."""
        nome = str((qs.get("name") or [""])[0]).strip()
        raiz = _raiz_backups()
        if not raiz:
            return self._send_json(400,
                {"error": "BACKUP_DIR não configurado no painel."})
        if not nome:
            return self._send_json(400, {"error": "name obrigatório."})

        # basename derruba qualquer pasta no caminho; realpath+prefix fecha o resto
        alvo = os.path.realpath(os.path.join(raiz, os.path.basename(nome)))
        if not alvo.startswith(raiz + os.sep) or not os.path.isfile(alvo):
            return self._send_json(404, {"error": "Backup não encontrado."})

        tamanho = os.path.getsize(alvo)
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(tamanho))
        self.send_header("Content-Disposition",
                         "attachment; filename=\"%s\"; filename*=UTF-8''%s"
                         % (nome.encode("ascii", "replace").decode(),
                            quote(nome)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        with open(alvo, "rb") as fh:
            while True:
                pedaco = fh.read(256 * 1024)
                if not pedaco:
                    break
                self.wfile.write(pedaco)

    # ---------- POST

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        payload = self._read_json()
        try:
            if path == "/api/login":
                self._handle_login(payload)
            elif path == "/api/logout":
                _destroy_session(self._token())
                self._send_json(200, {"ok": True})
            elif path.startswith("/api/"):
                if not self._autenticado():
                    return
                self._handle_api_post(path, payload)
            else:
                self._send_json(404, {"error": "rota não encontrada"})
        except BrokenPipeError:
            pass
        except Exception as e:
            print(f"[ERRO] POST {path}: {e!r}")
            self._send_json(500, {"error": "erro interno do painel"})

    def _handle_login(self, payload) -> None:
        ip = self.client_address[0]
        restante = _login_bloqueado(ip)
        if restante > 0:
            return self._send_json(429, {
                "error": f"Muitas tentativas. Tente de novo em {int(restante) + 1}s."
            })
        senha = str(payload.get("password", ""))
        # compara em bytes para aceitar qualquer caractere sem TypeError
        if PANEL_PASSWORD and hmac.compare_digest(
                senha.encode("utf-8"), PANEL_PASSWORD.encode("utf-8")):
            _limpar_falhas(ip)
            self._send_json(200, {"ok": True, "token": _create_session()})
        else:
            _registrar_falha(ip)
            time.sleep(0.5)  # freia força bruta
            self._send_json(401, {"error": "Senha incorreta."})

    def _handle_api_post(self, path: str, payload: dict) -> None:
        try:
            if path == "/api/announce":
                msg = str(payload.get("message", "")).strip()
                if not msg:
                    return self._send_json(400, {"error": "Mensagem vazia."})
                resp = api("POST", "/v1/api/announce", {"message": msg[:500]})
                return self._send_json(200, {"ok": True, "resposta": resp})

            if path == "/api/save":
                resp = api("POST", "/v1/api/save", {})
                return self._send_json(200, {"ok": True, "resposta": resp})

            if path == "/api/restart":
                resultado, code = reiniciar(payload)
                return self._send_json(code, resultado)

            if path == "/api/shutdown":
                waittime = _waittime_seguro(payload)
                msg = str(payload.get("message") or "").strip() or \
                    f"O servidor sera desligado em {waittime} segundos!"
                passos = _sequencia_desligar(msg, waittime)
                falhou_algum = any(v != "ok" for v in passos.values())
                return self._send_json(200 if not falhou_algum else 502,
                                       {"ok": not falhou_algum, "passos": passos})

            if path in ("/api/kick", "/api/ban"):
                userid = str(payload.get("userid", "")).strip()
                if not userid:
                    return self._send_json(400, {"error": "userid obrigatório."})
                motivo = str(payload.get("message", "")).strip() or \
                    "Acao administrativa via painel."
                resp = api("POST", f"/v1/api/{path.split('/')[-1]}",
                           {"userid": userid, "message": motivo[:200]})
                return self._send_json(200, {"ok": True, "resposta": resp})

            if path == "/api/unban":
                userid = str(payload.get("userid", "")).strip()
                if not userid:
                    return self._send_json(400, {"error": "userid obrigatório."})
                resp = api("POST", "/v1/api/unban", {"userid": userid})
                return self._send_json(200, {"ok": True, "resposta": resp})

            self._send_json(404, {"error": "rota não encontrada"})
        except ApiError as e:
            self._send_json(e.status, {"error": e.message})


# ----------------------------------------------------------------- main

def main() -> None:
    try:
        httpd = ThreadingHTTPServer(("0.0.0.0", PANEL_PORT), PanelHandler)
    except OSError as e:
        if e.errno == 98:  # EADDRINUSE — porta já ocupada
            print(f"  ❌ A porta {PANEL_PORT} JÁ ESTÁ EM USO — o painel não subiu.")
            print("     No servidor (Docker), cheque e limpe sobras:")
            print("       docker ps -a | grep -i painel")
            print("       docker rm -f Palworld-Painel")
            print("       sudo ss -ltnp | grep :%s   # quem segura a porta no host" % PANEL_PORT)
            print("       pkill -f painel.py        # painel rodando na mão, se houver")
            print("     Ou escolha outra porta: troque 'published:' no Palworld Server.yml")
            print("     (e PANEL_PORT no config.env, se rodar sem Docker).")
            raise SystemExit(1)
        raise
    httpd.daemon_threads = True
    linha = "=" * 60
    print(linha)
    print(f"  🐑 Painel Palworld rodando em  http://0.0.0.0:{PANEL_PORT}")
    print(linha)
    print(f"  Senha de acesso ao painel : {PANEL_PASSWORD}   [{_fonte.get('PANEL_PASSWORD', '?')}]")
    print(f"  ADMIN_PASSWORD (do jogo)  : {ADMIN_PASSWORD}   [{_fonte.get('ADMIN_PASSWORD', '?')}]")
    print(f"  API do jogo               : {PALWORLD_API}")
    print(linha)
    if not PANEL_PASSWORD or not ADMIN_PASSWORD:
        print("  ⚠️  Há uma senha VAZIA na configuração — o acesso correspondente fica BLOQUEADO.")
        print("     Defina PANEL_PASSWORD e ADMIN_PASSWORD em painel/config.env ou no YAML.")
        print(linha)
    elif PANEL_PASSWORD == "123" or ADMIN_PASSWORD == "123":
        print("  ⚠️  Usando senha padrão '123' — troque antes de abrir pra galera!")
        print("     Edite painel/config.env ou as variáveis no Palworld Server.yml.")
        print(linha)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nEncerrando o painel...")


if __name__ == "__main__":
    main()
