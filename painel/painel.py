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
import hashlib
import hmac
import json
import os
import secrets
import shutil
import sys
import threading
import time
import urllib.error
import urllib.request
import zipfile
from urllib.parse import urlparse, parse_qs, urlsplit, quote
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# ----------------------------------------------------------------- configuração

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
# garante que os módulos irmãos (store.py etc.) importem mesmo quando este
# arquivo é carregado por caminho direto (ex.: suíte de testes via importlib)
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

import store as store_mod
import settings_catalog as settings_cat_mod
import settings_ini as settings_ini_mod
import settings_editor as settings_ed_mod
import players as players_mod
import scheduler as scheduler_mod
import backups_manager as backups_mgr
import health as health_mod
import notifications as notif_mod
import users as users_mod
import maintenance as maint_mod

CONFIG_BACKUPS_DIR = os.path.join(store_mod.DATA_DIR, "config_backups")

# ----------------------------------------------------------------- configuração


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
# Endereço de bind do painel. Dentro do Docker, 0.0.0.0 é o normal — o
# acesso externo é controlado pela publicação de porta no YAML
# (ex.: "127.0.0.1:3564:3564" restringe ao próprio NAS).
PANEL_HOST = _cfg("PANEL_HOST", "0.0.0.0")
PANEL_PASSWORD = _cfg("PANEL_PASSWORD", "123")
PALWORLD_API = _cfg("PALWORLD_API", "http://127.0.0.1:8212").rstrip("/")
ADMIN_PASSWORD = _cfg("ADMIN_PASSWORD", "123")
API_TIMEOUT = float(_cfg("API_TIMEOUT", "4"))
MAX_API_BODY = 5_000_000  # teto de leitura da resposta da API do jogo (bytes)
GAME_PORT = _cfg("GAME_PORT", "8211")  # porta UDP do jogo, p/ exibir o endereço certo no painel
BACKUP_DIR = _cfg("BACKUP_DIR", "")  # opcional: pasta de backups p/ listar/baixar (ex.: /backups)
# Caminho do PalWorldSettings.ini dentro de um volume montado no painel.
# Sem ele, o editor de configurações fica em modo somente leitura.
PALWORLD_INI = _cfg("PALWORLD_INI", "")
# Pasta de saves do jogo (para backup/restauração pelo painel). Derivada do
# PALWORLD_INI quando possível: …/Saved/Config/… → …/Saved/SaveGames
if _cfg("PALWORLD_SAVE_DIR", ""):
    PALWORLD_SAVE_DIR = _cfg("PALWORLD_SAVE_DIR", "")
elif PALWORLD_INI and os.sep + "Config" + os.sep in PALWORLD_INI:
    _raiz_saved = PALWORLD_INI.split(os.sep + "Config" + os.sep)[0]
    PALWORLD_SAVE_DIR = os.path.join(_raiz_saved, "SaveGames")
else:
    PALWORLD_SAVE_DIR = ""

# Página pública /status (sem login). PUBLIC_STATUS=0 desliga.
PUBLIC_STATUS = _cfg("PUBLIC_STATUS", "1") not in ("0", "false", "False")
# Endereço que os jogadores usam para conectar (exibido na página pública)
PUBLIC_HOST = _cfg("PUBLIC_HOST", "") or urlparse(PALWORLD_API).hostname or ""

MAX_BODY = 1_000_000  # teto do corpo de requisições ao painel
STATIC_DIR = os.path.join(_SCRIPT_DIR, "static")
DATA_DIR = store_mod.DATA_DIR
SESSION_TTL = 12 * 3600  # 12 horas

# ------------------------------------------------- política de senha do painel
# Nunca subir com senha fraca/ausente: se PANEL_PASSWORD não veio do ambiente/
# config (ou veio vazia/"123"), gera uma senha forte, guarda em data/ com
# permissão 600 e avisa no console. O admin pode trocar a qualquer momento
# definindo PANEL_PASSWORD.

_AUTH_FILE = os.path.join(DATA_DIR, "painel_auth.json")
_SENHAS_FRACAS = ("", "123", "admin", "senha", "password", "palworld")


def _senha_fraca(senha: str) -> bool:
    return not senha or senha.strip().lower() in _SENHAS_FRACAS


def _hash_senha(senha: str, salt: bytes) -> str:
    digest = hashlib.pbkdf2_hmac("sha256", senha.encode("utf-8"), salt, 200_000)
    return f"pbkdf2${salt.hex()}${digest.hex()}"


def _salvar_credencial_gerada(senha: str) -> None:
    """Guarda a credencial autogerada (plaintext, arquivo restrito ao dono)."""
    registro = {"gerada_em": int(time.time()),
                "pbkdf2": _hash_senha(senha, secrets.token_bytes(16)),
                "senha": senha}
    fd = os.open(_AUTH_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(registro, fh)


def _credencial_gerada() -> str | None:
    """Senha autogerada de execuções anteriores (None se não houver)."""
    try:
        with open(_AUTH_FILE, encoding="utf-8") as fh:
            dados = json.load(fh)
        senha = str(dados.get("senha") or "")
        return senha or None
    except (OSError, ValueError):
        return None


PANEL_PASSWORD_AUTOGERADA = False
if _senha_fraca(PANEL_PASSWORD):
    anterior = _credencial_gerada()
    if anterior and not _senha_fraca(anterior):
        PANEL_PASSWORD = anterior  # reusa a senha gerada em outra execução
        PANEL_PASSWORD_AUTOGERADA = True
    else:
        PANEL_PASSWORD = secrets.token_urlsafe(12)  # ~72 bits de entropia
        PANEL_PASSWORD_AUTOGERADA = True
        try:
            _salvar_credencial_gerada(PANEL_PASSWORD)
            os.chmod(_AUTH_FILE, 0o600)
        except OSError:
            pass  # sem permissão de escrita — segue com a senha só nesta execução


def _verificar_senha_panel(senha: str) -> bool:
    """Comparação em tempo constante; aceita a senha configurada ou a gerada."""
    if not PANEL_PASSWORD:
        return False
    if hmac.compare_digest(senha.encode("utf-8"),
                           PANEL_PASSWORD.encode("utf-8")):
        return True
    if PANEL_PASSWORD_AUTOGERADA:
        salva = _credencial_gerada()
        return bool(salva) and hmac.compare_digest(
            senha.encode("utf-8"), salva.encode("utf-8"))
    return False

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
# (as sessões vivem no SQLite via módulo users — revogáveis e multiusuário)

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


def _create_session(usuario_id: int = None) -> str:
    """Cria sessão no SQLite, vinculada ao usuário (admin por padrão)."""
    if usuario_id is None:
        users_mod.garantir_admin_inicial(PANEL_PASSWORD)
        admin = users_mod.autenticar(None, PANEL_PASSWORD) or {}
        usuario_id = admin.get("id")
        if usuario_id is None:
            raise RuntimeError("Conta admin bootstrap indisponível.")
    return users_mod.criar_sessao(usuario_id)


def _valid_session(token: str) -> bool:
    return users_mod.validar_sessao(token) is not None


def _usuario_da_sessao(token: str):
    return users_mod.validar_sessao(token)


def _destroy_session(token: str) -> None:
    users_mod.revogar(token)


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


# ------------------------------------------------- coletor de métricas (SQLite)

_COLETOR_INTERVALO = 30  # segundos
_ultimo_estado_online: list = [None]  # caixa mutável p/ detectar transições


def _amostrar_disco() -> int | None:
    """Espaço livre (MB) no volume de dados — None se não der pra medir."""
    try:
        return shutil.disk_usage(DATA_DIR).free // (1024 * 1024)
    except OSError:
        return None


def _registrar_transicao(online: bool) -> None:
    era = _ultimo_estado_online[0]
    _ultimo_estado_online[0] = online
    if era is None or era == online:
        return
    store_mod.registrar_evento(
        "info" if online else "error", "server",
        "Servidor de jogo voltou a responder" if online
        else "Servidor de jogo parou de responder")


def coletor_metricas() -> None:
    """Thread daemon: amostra o servidor periodicamente e grava no SQLite."""
    while True:
        try:
            snap = status_combinado()
            if snap.get("online"):
                try:
                    resp = api("GET", "/v1/api/players")
                    lista = (resp.get("players")
                             if isinstance(resp, dict) else resp) or []
                    players_mod.atualizar(lista)
                except ApiError:
                    pass  # sem lista agora; tenta no próximo ciclo
            disco_livre = _amostrar_disco()
            if disco_livre is not None:
                snap.setdefault("metrics", {})["disk_free_mb"] = disco_livre
            store_mod.registrar_metricas(snap)
            _registrar_transicao(bool(snap.get("online")))
        except Exception as exc:  # nunca derruba o coletor
            print(f"[ERRO] coletor de métricas: {exc!r}")
        time.sleep(_COLETOR_INTERVALO)


def desbanidor_automatico() -> None:
    """Confirma no jogo a expiração de bans temporários do painel.
    A REST API não tem agendamento de ban — o painel guarda a expiração
    e envia o unban quando ela chega (com registro no livro local)."""
    while True:
        for b in players_mod.expirados():
            try:
                api("POST", "/v1/api/unban", {"userid": b["userid"]})
                players_mod.marcar_unban(b["userid"], por="expiracao")
                store_mod.registrar_evento(
                    "info", "player",
                    f"Ban temporário expirado e removido: {b['userid'][:40]}")
            except ApiError as e:
                # jogo offline/agora não dá — o livro já considera vencido;
                # o unban real acontece quando voltar a responder
                store_mod.registrar_evento(
                    "warn", "player",
                    f"Falhou unban automático de {b['userid'][:40]}: {e.message}")
        time.sleep(60)


def gamedata_proxy():
    """Proxy do endpoint opcional /v1/api/game-data (presente só em builds
    recentes). Nunca inventa dados: se o servidor não expõe, devolve
    disponivel:false com o motivo."""
    try:
        dados = api("GET", "/v1/api/game-data")
        return {"disponivel": True, "dados": dados}
    except ApiError as e:
        return {"disponivel": False,
                "motivo": ("Seu servidor de jogo não expõe game-data "
                           "(recurso de builds recentes)." if "404" in e.message
                           else e.message)}


def iniciar_coletores() -> None:
    threading.Thread(target=coletor_metricas, daemon=True,
                     name="coletor-metricas").start()
    threading.Thread(target=desbanidor_automatico, daemon=True,
                     name="desbanidor-automatico").start()
    threading.Thread(target=laco_recuperacao, daemon=True,
                     name="auto-recuperacao").start()


# ------------------------------------------------- auto recovery (watchdog)

_RECUPERACAO_PADRAO = {
    "habilitado": False,       # conservador: só liga se o admin quiser
    "fps_minimo": 15,
    "janela_minutos": 10,
    "cooldown_minutos": 60,
    "max_tentativas_dia": 3,
}
_ARQ_RECUPERACAO = os.path.join(DATA_DIR, "recovery_config.json")
_INTERVALO_COLETE_S = _COLETOR_INTERVALO


def config_recuperacao() -> dict:
    cfg = dict(_RECUPERACAO_PADRAO)
    try:
        with open(_ARQ_RECUPERACAO, encoding="utf-8") as fh:
            dados = json.load(fh)
        if isinstance(dados, dict):
            cfg.update({k: v for k, v in dados.items()
                        if k in cfg and isinstance(v, type(cfg[k]))})
    except (OSError, ValueError):
        pass
    return cfg


def salvar_config_recuperacao(nova: dict) -> dict:
    atual = config_recuperacao()
    for k in atual:
        if k in nova:
            try:
                atual[k] = type(atual[k])(nova[k])
            except (TypeError, ValueError):
                pass
    # sanidade
    atual["fps_minimo"] = max(1, min(int(atual["fps_minimo"]), 60))
    atual["janela_minutos"] = max(2, min(int(atual["janela_minutos"]), 240))
    atual["cooldown_minutos"] = max(5, min(int(atual["cooldown_minutos"]), 1440))
    atual["max_tentativas_dia"] = max(1, min(int(atual["max_tentativas_dia"]), 20))
    with open(_ARQ_RECUPERACAO, "w", encoding="utf-8") as fh:
        json.dump(atual, fh)
    return atual


def _tentativas_recuperacao_hoje() -> int:
    import datetime as _dt
    meia_noite = time.mktime(_dt.date.today().timetuple())
    eventos = store_mod.consultar_eventos(limite=100, kinds=["recovery"],
                                          desde_ts=meia_noite)
    return len([e for e in eventos if "iniciada" in e["message"]])


def laco_recuperacao() -> None:
    """Watchdog: FPS abaixo do mínimo pela janela configurada → restart
    gracioso. Cooldown + limite diário evitam loop infinito."""
    while True:
        try:
            cfg = config_recuperacao()
            if cfg["habilitado"]:
                agora = time.time()
                inicio = agora - cfg["janela_minutos"] * 60
                amostras = store_mod.consultar_metricas(inicio, agora)
                online = [a for a in amostras if a["online"] and a["fps"]]
                esperadas = max(2, int(cfg["janela_minutos"] * 60
                                       / _INTERVALO_COLETE_S * 0.7))
                degradado = (len(online) >= esperadas
                             and all(a["fps"] < cfg["fps_minimo"]
                                     for a in online))
                ultima_tentativa = max(
                    [e["ts"] for e in store_mod.consultar_eventos(
                        limite=50, kinds=["recovery"])
                     if "iniciada" in e["message"]] or [0])
                em_cooldown = (agora - ultima_tentativa
                               < cfg["cooldown_minutos"] * 60)
                no_limite = (_tentativas_recuperacao_hoje()
                             >= cfg["max_tentativas_dia"])
                if degradado:
                    if em_cooldown or no_limite:
                        store_mod.registrar_evento(
                            "warn", "recovery",
                            f"FPS baixo detectado — recuperação retida "
                            f"({'cooldown' if em_cooldown else 'limite diário'})")
                    else:
                        store_mod.registrar_evento(
                            "error", "recovery",
                            f"Recuperação iniciada: FPS < {cfg['fps_minimo']} "
                            f"por {cfg['janela_minutos']}min")
                        notif_mod.despachar(
                            "erro", "Auto-recuperação disparada",
                            f"FPS abaixo de {cfg['fps_minimo']} por "
                            f"{cfg['janela_minutos']} minutos. "
                            "Reinicio gracioso em curso.")
                        _exec_restart({"waittime": 120})
        except Exception as exc:
            print(f"[ERRO] auto-recuperação: {exc!r}")
        time.sleep(60)


# ------------------------------------------------- ponte eventos→notificações

_MAPA_NOTIFICACOES = (
    (("server", "error"), ("server_down", "Servidor de jogo caiu",
                           "A REST API parou de responder.")),
    (("server", "info"), ("server_up", "Servidor de jogo voltou", "")),
    (("player", "error"), ("ban", "Banimento aplicado", "")),
    (("player", "warn"), ("kick", "Kick aplicado", "")),
    (("backup", "info"), ("backup", "Backup concluído", "")),
    (("restore", "error"), ("restore", "Mundo restaurado", "")),
    (("settings", "warn"), ("settings", "Configurações alteradas", "")),
    (("scheduler", "warn"), ("scheduler", "Tarefa agendada executada", "")),
)


def _ponte_eventos(level: str, kind: str, message: str, detalhe) -> None:
    for (k, lv), evento in _MAPA_NOTIFICACOES:
        if k == kind and lv == level:
            notif_mod.despachar(evento[0], evento[1],
                                message or evento[2])
            return


def registrar_pontes() -> None:
    store_mod.ao_evento(_ponte_eventos)


# ------------------------------------------------- executores do agendador

def _exec_restart(params: dict) -> str:
    waittime = int((params or {}).get("waittime", 300))
    passos = _sequencia_desligar(
        f"Reinicio programado! O servidor volta em instantes. ({waittime}s)",
        waittime)
    falhou = [k for k, v in passos.items() if v != "ok"]
    store_mod.registrar_evento("warn", "scheduler",
                               f"Restart agendado executado ({waittime}s)",
                               {"passos": passos})
    if falhou:
        raise RuntimeError(f"falha em: {', '.join(falhou)}")
    return f"shutdown em {waittime}s disparado"


def _exec_save(_params: dict) -> str:
    api("POST", "/v1/api/save", {})
    store_mod.registrar_evento("info", "scheduler",
                               "Save agendado executado")
    return "save enviado"


def _exec_announce(params: dict) -> str:
    msg = (params or {}).get("message") or ""
    api("POST", "/v1/api/announce", {"message": msg[:500]})
    store_mod.registrar_evento("info", "scheduler",
                               f"Anúncio agendado: {msg[:80]}")
    return "anúncio enviado"


def _exec_backup_mundo(_params: dict) -> str:
    resultado = backups_mgr.criar_backup_mundo(PALWORLD_SAVE_DIR, BACKUP_DIR)
    store_mod.registrar_evento(
        "info", "backup",
        f"Backup do mundo criado: {resultado['nome']}",
        {"bytes": resultado["bytes"], "arquivos": resultado["arquivos"]})
    return f"{resultado['nome']} ({resultado['bytes']} bytes)"


def _exec_limpeza(params: dict) -> str:
    p = params or {}
    res = backups_mgr.retencao(BACKUP_DIR,
                               manter_ultimos=p.get("manter_ultimos", 10),
                               idade_dias=p.get("idade_dias", 30))
    store_mod.registrar_evento(
        "info", "backup",
        f"Limpeza de backups: {len(res['apagados'])} arquivo(s) removido(s)")
    return f"{len(res['apagados'])} removido(s)"


def registrar_executores_agenda() -> None:
    scheduler_mod.registrar_executor("restart", _exec_restart)
    scheduler_mod.registrar_executor("save", _exec_save)
    scheduler_mod.registrar_executor("announce", _exec_announce)
    scheduler_mod.registrar_executor("backup_mundo", _exec_backup_mundo)
    scheduler_mod.registrar_executor("limpeza_backups", _exec_limpeza)


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
    store_mod.registrar_evento(
        "warn", "server", f"Reinício solicitado ({waittime}s)",
        {"passos": passos})
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
                         "img-src 'self' data: blob:; connect-src 'self'; font-src 'self'; "
                         "frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        super().end_headers()

    def _send_json(self, status: int, obj, no_cache: bool = False) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control",
                         "no-cache" if no_cache else "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self):
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0:
            return {}
        if length > MAX_BODY:
            self._send_json(413, {"error": "Corpo da requisição grande demais."})
            return None
        raw = self.rfile.read(length)
        try:
            obj = json.loads(raw.decode("utf-8"))
            return obj if isinstance(obj, dict) else {}
        except json.JSONDecodeError:
            return {}

    def _token(self) -> str:
        return self.headers.get("X-Panel-Token", "")

    def _autenticado(self) -> bool:
        self._usuario = users_mod.validar_sessao(self._token())
        if self._usuario is not None:
            return True
        self._send_json(401, {"error": "Não autenticado. Faça login novamente."})
        return False

    def _requer(self, permissao: str) -> bool:
        """403 se o usuário autenticado não tem a permissão."""
        if users_mod.tem_permissao(getattr(self, "_usuario", None), permissao):
            return True
        self._send_json(403, {
            "error": f"Sem permissão ({permissao}) para esta ação."})
        return False

    def _requer_alguma(self, *permissoes: str) -> bool:
        """Autoriza se o usuário tiver ao menos uma permissão, emitindo um único 403."""
        usuario = getattr(self, "_usuario", None)
        if any(users_mod.tem_permissao(usuario, p) for p in permissoes):
            return True
        self._send_json(403, {
            "error": "Sem permissão para esta ação (requer: "
                     + " ou ".join(permissoes) + ")."})
        return False

    # ---------- API versionada /api/v1 + Prometheus ----------

    def _chave_ou_sessao(self):
        """Autenticação da API v1: X-API-Key/Bearer (API key) ou token de
        sessão do painel (permissões derivadas do papel)."""
        chave = self.headers.get("X-API-Key") or ""
        if not chave:
            authz = self.headers.get("Authorization") or ""
            if authz.startswith("Bearer "):
                chave = authz[7:].strip()
        if chave:
            info = users_mod.validar_api_key(chave)
            if info:
                return {"usuario": f"key:{info['nome']}", "papel": None,
                        "permissoes": set(info["permissoes"])}
            return None
        usuario = users_mod.validar_sessao(self._token())
        if usuario:
            return {"usuario": usuario["usuario"], "papel": usuario["papel"],
                    "permissoes": set(users_mod.permissoes_de(usuario["papel"]))}
        return None

    def _v1_401(self) -> None:
        corpo = b'{"error": "API key ausente ou inv\\u00e1lida."}'
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Bearer realm="painel-api"')
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(corpo)))
        self.end_headers()
        self.wfile.write(corpo)

    def _handle_api_v1(self, path: str, qs: dict, metodo: str,
                       payload: dict = None) -> None:
        try:
            identidade = self._chave_ou_sessao()
        except Exception as e:
            print(f"[ERRO] api-v1 auth: {e!r}")
            return self._send_json(500, {"error": "erro interno"})
        if not identidade:
            return self._v1_401()

        def tem(permissao):
            return permissao in identidade["permissoes"]

        def nao_autorizado(permissao):
            self._send_json(403,
                            {"error": f"sem permissão ({permissao})",
                             "identidade": identidade["usuario"]})

        if metodo == "GET":
            if path in ("/api/v1", "/api/v1/"):
                if not tem("SERVER_VIEW"):
                    return nao_autorizado("SERVER_VIEW")
                return self._send_json(200, {
                    "versao": "v1", "recursos": [
                        "GET /api/v1/server", "GET /api/v1/players",
                        "GET /api/v1/metrics", "GET /api/v1/backups",
                        "GET /api/v1/logs?limite=", "GET /api/v1/health",
                        "POST /api/v1/actions/save",
                        "POST /api/v1/actions/announce {message}",
                        "POST /api/v1/actions/restart {waittime}",
                        "POST /api/v1/actions/shutdown {waittime}",
                        "GET /metrics (Prometheus)"],
                    "identidade": identidade["usuario"]})

            if path == "/api/v1/server":
                if not tem("SERVER_VIEW"):
                    return nao_autorizado("SERVER_VIEW")
                snap = status_combinado()
                info = snap.get("info") or {}
                met = snap.get("metrics") or {}

                def _pegar(obj, *chaves):
                    baixo = {k.lower(): v for k, v in obj.items()} if obj else {}
                    for c in chaves:
                        v = baixo.get(c.lower())
                        if v is not None:
                            return v
                    return None

                return self._send_json(200, {
                    "online": snap.get("online", False),
                    "nome": _pegar(info, "servername", "name"),
                    "versao": _pegar(info, "version"),
                    "jogadores": _pegar(met, "currentplayernum"),
                    "capacidade": _pegar(met, "maxplayernum"),
                    "conectar": (f"{PUBLIC_HOST}:{GAME_PORT}"
                                 if GAME_PORT else None)})

            if path == "/api/v1/players":
                if not tem("SERVER_VIEW"):
                    return nao_autorizado("SERVER_VIEW")
                try:
                    return self._send_json(200,
                                           api("GET", "/v1/api/players"))
                except ApiError as e:
                    return self._send_json(e.status, {"error": e.message})

            if path == "/api/v1/metrics":
                if not tem("SERVER_VIEW"):
                    return nao_autorizado("SERVER_VIEW")
                amostra = store_mod.ultima_metrica() or {}
                return self._send_json(200, {
                    "atual": amostra,
                    "resumo_24h": store_mod.resumo_metricas(86400)})

            if path == "/api/v1/backups":
                if not tem("BACKUP_CREATE"):
                    return nao_autorizado("BACKUP_CREATE")
                return self._send_json(200, listar_backups())

            if path == "/api/v1/logs":
                if not tem("LOG_VIEW"):
                    return nao_autorizado("LOG_VIEW")
                try:
                    limite = int((qs.get("limite") or ["100"])[0])
                except ValueError:
                    limite = 100
                return self._send_json(200, {
                    "eventos": store_mod.consultar_eventos(limite=limite)})

            if path == "/api/v1/health":
                if not tem("SERVER_VIEW"):
                    return nao_autorizado("SERVER_VIEW")
                ultimo = store_mod.ultima_metrica()
                diag = health_mod.diagnostico({
                    "status_combinado": status_combinado,
                    "admin_password": ADMIN_PASSWORD,
                    "data_dir": DATA_DIR,
                    "livre_mb": _amostrar_disco(),
                    "backup_dir": BACKUP_DIR,
                    "listar_backups": listar_backups,
                    "ini_path": PALWORLD_INI,
                    "save_dir": PALWORLD_SAVE_DIR,
                    "ultima_metrica_ts": (ultimo or {}).get("ts"),
                    "intervalo_colete": _COLETOR_INTERVALO,
                    "banco_status": store_mod.status_banco,
                    "ultimo_snap": {"online": bool((ultimo or {}).get("online"))},
                })
                return self._send_json(200, diag)

            if path == "/metrics":
                if not tem("SERVER_VIEW"):
                    return nao_autorizado("SERVER_VIEW")
                amostra = store_mod.ultima_metrica() or {}
                linhas = ["# HELP palworld_up Servidor de jogo respondendo",
                          "# TYPE palworld_up gauge",
                          f"palworld_up {1 if amostra.get('online') else 0}"]
                for metrica, campo in (
                        ("palworld_players", "players"),
                        ("palworld_fps", "fps"),
                        ("palworld_frame_time", "frame_time"),
                        ("palworld_uptime_seconds", "uptime"),
                        ("palworld_base_camps", "base_camps"),
                        ("palworld_world_day", "world_day")):
                    valor = amostra.get(campo)
                    if valor is not None:
                        linhas.append(f"# TYPE {metrica} gauge")
                        linhas.append(f"{metrica} {valor}")
                livre = _amostrar_disco()
                if livre is not None:
                    linhas += ["# TYPE painel_disco_livre_bytes gauge",
                               f"painel_disco_livre_bytes {livre * 1024 * 1024:.0f}"]
                estado = health_mod.diagnostico({
                    "status_combinado": status_combinado,
                    "admin_password": ADMIN_PASSWORD,
                    "data_dir": DATA_DIR, "livre_mb": livre,
                    "backup_dir": BACKUP_DIR,
                    "listar_backups": listar_backups,
                    "ini_path": PALWORLD_INI, "save_dir": PALWORLD_SAVE_DIR,
                    "ultima_metrica_ts": amostra.get("ts"),
                    "intervalo_colete": _COLETOR_INTERVALO,
                    "banco_status": store_mod.status_banco,
                    "ultimo_snap": {"online": bool(amostra.get("online"))},
                })["estado"]
                valores = {"healthy": 0, "degraded": 1, "critical": 2,
                           "offline": 3, "maintenance": 4}
                linhas += ["# TYPE painel_saude gauge",
                           f"painel_saude {valores.get(estado, 3)}"]
                corpo = ("\n".join(linhas) + "\n").encode()
                self.send_response(200)
                self.send_header("Content-Type",
                                 "text/plain; version=0.0.4; charset=utf-8")
                self.send_header("Content-Length", str(len(corpo)))
                self.end_headers()
                return self.wfile.write(corpo)

            return self._send_json(404, {"error": "rota não encontrada"})

        # POST — ações
        acoes = {
            "/api/v1/actions/save": ("SERVER_SAVE",
                                     lambda: api("POST", "/v1/api/save", {})),
            "/api/v1/actions/announce": (
                "SERVER_SAVE",
                lambda: api("POST", "/v1/api/announce",
                            {"message": str((payload or {}).get(
                                "message", ""))[:500]})),
            "/api/v1/actions/restart": (
                "SERVER_RESTART", lambda: reiniciar(payload or {})[0]),
            "/api/v1/actions/shutdown": (
                "SERVER_STOP",
                lambda: {"passos": _sequencia_desligar(
                    str((payload or {}).get("message") or
                        f"O servidor sera desligado em "
                        f"{_waittime_seguro(payload or {})} segundos!"),
                    _waittime_seguro(payload or {}))}),
        }
        acao = acoes.get(path)
        if not acao:
            return self._send_json(404, {"error": "rota não encontrada"})
        permissao_req, executar = acao
        if not tem(permissao_req):
            return nao_autorizado(permissao_req)
        try:
            resultado = executar()
            store_mod.registrar_evento(
                "info", "action", f"Ação via API: {path}",
                {"por": identidade["usuario"]})
            return self._send_json(200, {"ok": True, "resultado": resultado})
        except ApiError as e:
            return self._send_json(e.status, {"error": e.message})

    # ---------- exportações CSV/JSON ----------

    def _handle_export(self, path: str, qs: dict) -> None:
        """GET /api/export/{jogadores|bans|audit|logs}?formato=csv|json"""
        alvo = path.split("/")[-1]
        formato = str((qs.get("formato") or ["csv"])[0]).lower()
        if formato not in ("csv", "json"):
            return self._send_json(400,
                                   {"error": "formato deve ser csv ou json"})
        permissoes = {
            "jogadores": "SERVER_VIEW", "bans": "LOG_VIEW",
            "audit": "LOG_VIEW", "logs": "LOG_VIEW",
            "metricas": "SERVER_VIEW",
        }
        if not self._requer(permissoes.get(alvo, "SERVER_VIEW")):
            return
        try:
            if alvo == "jogadores":
                linhas = players_mod.listar(por_pagina=100)["jogadores"]
                colunas = ("userid nome primeiro_visto ultimo_visto "
                           "sessoes tempo_total_s nivel_max online banido").split()
                registros = [[j[c] for c in colunas] for j in linhas]
                nome_arq = "jogadores"
            elif alvo == "bans":
                lista = players_mod.listar_bans()
                colunas = ("userid nome motivo criado_em expira_em ativo "
                           "removido_em removido_por origem").split()
                d = {c: None for c in colunas}
                registros = [[{**d, **b}.get(c) for c in colunas]
                             for b in lista]
                nome_arq = "bans"
            elif alvo == "audit":
                eventos = store_mod.consultar_eventos(limite=2000,
                                                      kinds=("auth", "action",
                                                             "player",
                                                             "settings",
                                                             "backup",
                                                             "restore"))
                colunas = ("ts level kind message detail",).split()
                registros = [[e[c] for c in colunas] for e in eventos]
                nome_arq = "audit"
            elif alvo == "logs":
                eventos = store_mod.consultar_eventos(limite=2000)
                colunas = ("ts level kind message",).split()
                registros = [[e[c] for c in colunas] for e in eventos]
                nome_arq = "logs"
            else:  # métricas
                amostras = store_mod.consultar_metricas(
                    time.time() - 30 * 86400)
                colunas = ("ts online fps frame_time players max_players "
                           "uptime base_camps world_day").split()
                registros = [[a[c] for c in colunas] for a in amostras]
                nome_arq = "metricas"

            if formato == "json":
                dados = [dict(zip(colunas, reg)) for reg in registros]
                corpo = json.dumps(dados, ensure_ascii=False).encode("utf-8")
                tipo_ct = "application/json; charset=utf-8"
            else:
                import csv as _csv
                import io
                buffer = io.StringIO()
                writer = _csv.writer(buffer)
                writer.writerow(colunas)
                for reg in registros:
                    writer.writerow(["" if v is None else v for v in reg])
                corpo = buffer.getvalue().encode("utf-8")
                tipo_ct = "text/csv; charset=utf-8"

            stamp = time.strftime("%Y%m%d-%H%M%S")
            self.send_response(200)
            self.send_header("Content-Type", tipo_ct)
            self.send_header("Content-Length", str(len(corpo)))
            self.send_header(
                "Content-Disposition",
                f'attachment; filename="{nome_arq}-{stamp}.{formato}"')
            self.end_headers()
            self.wfile.write(corpo)
        except Exception as e:
            print(f"[ERRO] export {path}: {e!r}")
            self._send_json(500, {"error": "falha ao exportar"})

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
            if path.startswith("/api/v1/") or path in ("/api/v1", "/metrics"):
                self._handle_api_v1(path, parse_qs(partes.query), "GET")
                return
            if path == "/status":
                if PUBLIC_STATUS:
                    self._serve_static("/status.html")
                else:
                    self._send_json(404, {"error": "página desabilitada"})
                return
            if path == "/manifest.webmanifest":
                self._serve_static("/manifest.webmanifest")
                return
            if path == "/sw.js":
                self._serve_static("/sw.js")
                return
            if path.startswith("/api/"):
                self._handle_api_get(path, parse_qs(partes.query))
            else:
                self._serve_static(path)
        except BrokenPipeError:
            pass
        except Exception as e:  # nunca derruba o servidor
            print(f"[ERRO] GET {path}: {e!r}")
            self._send_json(500, {"error": "erro interno do painel"})

    def do_DELETE(self):
        path = self.path.split("?", 1)[0]
        try:
            if not path.startswith("/api/"):
                return self._send_json(404, {"error": "rota não encontrada"})
            if not self._autenticado():
                return
            partes = path.strip("/").split("/")
            if len(partes) == 3 and partes[1] == "usuarios":
                if not self._requer("USER_MANAGE"):
                    return
                try:
                    uid = int(partes[2])
                except ValueError:
                    return self._send_json(400, {"error": "id inválido."})
                ok, erro = users_mod.excluir(uid)
                if erro:
                    return self._send_json(400, {"error": erro})
                store_mod.registrar_evento(
                    "warn", "auth", f"Usuário #{uid} excluído")
                return self._send_json(200, {"ok": True})
            if len(partes) == 3 and partes[1] == "keys":
                if not self._requer("USER_MANAGE"):
                    return
                try:
                    kid = int(partes[2])
                except ValueError:
                    return self._send_json(400, {"error": "id inválido."})
                return self._send_json(
                    200, {"ok": users_mod.revogar_api_key(kid)})
            if len(partes) == 3 and partes[1] == "scheduler":
                if not self._requer_alguma("SERVER_RESTART", "SETTINGS_EDIT"):
                    return
                try:
                    job_id = int(partes[2])
                except ValueError:
                    return self._send_json(400, {"error": "id inválido."})
                job = scheduler_mod.obter(job_id)
                if not job:
                    return self._send_json(404, {"error": "job não encontrado"})
                scheduler_mod.excluir(job_id)
                store_mod.registrar_evento(
                    "warn", "scheduler", f"Job excluído: {job['nome']}")
                return self._send_json(200, {"ok": True})
            return self._send_json(404, {"error": "rota não encontrada"})
        except BrokenPipeError:
            pass
        except Exception as e:
            print(f"[ERRO] DELETE {path}: {e!r}")
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
        if path == "/api/public/status":
            """Endpoint público: só dados não sensíveis, lidos do cache
            local (última amostra do coletor) — zero carga na API do jogo."""
            if not PUBLIC_STATUS:
                return self._send_json(404, {"error": "desabilitado"})
            amostra = store_mod.ultima_metrica() or {}
            extra = {}
            try:
                extra = json.loads(amostra.get("extra") or "{}")
            except ValueError:
                pass
            online = bool(amostra.get("online"))
            self._send_json(200, {
                "online": online,
                "nome": extra.get("server_name") or "",
                "versao": extra.get("version") or "",
                "jogadores": amostra.get("players"),
                "capacidade": amostra.get("max_players"),
                "atualizado_em": amostra.get("ts"),
                "conectar": (f"{PUBLIC_HOST}:{GAME_PORT}"
                             if GAME_PORT else None),
            }, no_cache=True)
            return
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

        if path == "/api/maintenance":
            if not self._autenticado():
                return
            self._send_json(200, maint_mod.ler_manutencao())
            return
        if path.startswith("/api/export/"):
            if not self._autenticado():
                return
            self._handle_export(path, qs or {})
            return
        rotas = {
            "/api/status": lambda: status_combinado(),
            "/api/me": lambda: {"usuario": getattr(self, "_usuario", None)},
            "/api/players": lambda: api("GET", "/v1/api/players"),
            "/api/settings": lambda: api("GET", "/v1/api/settings"),
            "/api/gamedata": gamedata_proxy,
        }
        rota = rotas.get(path)
        if rota is None:
            if path == "/api/metrics/history":
                if not self._autenticado():
                    return
                self._enviar_historico(qs or {})
                return
            if path == "/api/dashboard":
                if not self._autenticado():
                    return
                try:
                    self._enviar_dashboard()
                except Exception as e:
                    print(f"[ERRO] dashboard: {e!r}")
                    self._send_json(500, {"error": "erro interno do painel"})
                return
            if path in ("/api/players/registro", "/api/players/detalhe",
                        "/api/bans"):
                if not self._autenticado():
                    return
                try:
                    if path == "/api/players/registro":
                        def _int(nome, padrao):
                            try:
                                return int((qs.get(nome) or [str(padrao)])[0])
                            except (ValueError, IndexError):
                                return padrao
                        self._send_json(200, players_mod.listar(
                            busca=str((qs.get("busca") or [""])[0])[:100],
                            pagina=_int("pagina", 1),
                            por_pagina=_int("por_pagina", 25),
                            ordem=str((qs.get("ordem") or ["ultimo_visto"])[0])))
                    elif path == "/api/players/detalhe":
                        userid = str((qs.get("userid") or [""])[0]).strip()
                        ficha = players_mod.detalhe(userid) if userid else None
                        if ficha is None:
                            self._send_json(404,
                                            {"error": "Jogador não registrado."})
                        else:
                            self._send_json(200, ficha)
                    else:
                        ativos = str((qs.get("ativos") or [""])[0]) == "1"
                        self._send_json(200, {"bans": players_mod.listar_bans(
                            apenas_ativos=ativos,
                            busca=str((qs.get("busca") or [""])[0])[:100])})
                except Exception as e:
                    print(f"[ERRO] {path}: {e!r}")
                    self._send_json(500, {"error": "erro interno do painel"})
                return
            if path == "/api/keys":
                if not self._requer("USER_MANAGE"):
                    return
                self._send_json(200, {"chaves": users_mod.listar_api_keys()})
                return
            if path in ("/api/usuarios", "/api/audit", "/api/sessoes"):
                if not self._autenticado():
                    return
                try:
                    if path == "/api/usuarios":
                        if not self._requer("USER_MANAGE"):
                            return
                        self._send_json(200, {"usuarios": users_mod.listar()})
                    elif path == "/api/sessoes":
                        if not self._requer("USER_MANAGE"):
                            return
                        self._send_json(
                            200, {"sessoes": users_mod.listar_sessoes()})
                    else:
                        if not self._requer("LOG_VIEW"):
                            return
                        kinds_audit = ("auth", "action", "player",
                                       "settings", "backup", "restore",
                                       "recovery", "scheduler")
                        eventos = store_mod.consultar_eventos(
                            limite=500, kinds=list(kinds_audit))
                        self._send_json(200, {"eventos": eventos})
                except Exception as e:
                    print(f"[ERRO] {path}: {e!r}")
                    self._send_json(500,
                                    {"error": "erro interno do painel"})
                return
            if path == "/api/events":
                if not self._autenticado():
                    return
                try:
                    limite = int((qs.get("limite") or ["300"])[0])
                except ValueError:
                    limite = 300
                nivel = str((qs.get("level") or [""])[0])
                desde = str((qs.get("desde_id") or [""])[0])
                eventos = store_mod.consultar_eventos(
                    limite=limite,
                    level=nivel if nivel in ("info", "warn", "error") else None,
                    desde_ts=None)
                if desde.isdigit():
                    eventos = [e for e in eventos if e["id"] > int(desde)]
                self._send_json(200, {"eventos": eventos})
                return
            if path == "/api/stream":
                self._handle_sse(qs or {})
                return
            if path == "/api/scheduler":
                if not self._autenticado():
                    return
                self._send_json(200, {"jobs": scheduler_mod.listar()})
                return
            if path == "/api/health":
                if not self._autenticado():
                    return
                try:
                    ultimo = store_mod.ultima_metrica()
                    diag = health_mod.diagnostico({
                        "status_combinado": status_combinado,
                        "admin_password": ADMIN_PASSWORD,
                        "data_dir": DATA_DIR,
                        "livre_mb": _amostrar_disco(),
                        "backup_dir": BACKUP_DIR,
                        "listar_backups": listar_backups,
                        "ini_path": PALWORLD_INI,
                        "save_dir": PALWORLD_SAVE_DIR,
                        "ultima_metrica_ts": (ultimo or {}).get("ts"),
                        "intervalo_colete": _COLETOR_INTERVALO,
                        "banco_status": store_mod.status_banco,
                        "criar_backup": lambda: backups_mgr.criar_backup_mundo(
                            PALWORLD_SAVE_DIR, BACKUP_DIR),
                        "retencao": backups_mgr.retencao,
                        "ultimo_snap": {"online": bool(
                            (ultimo or {}).get("online"))},
                    })
                    self._send_json(200, diag)
                except Exception as e:
                    print(f"[ERRO] health: {e!r}")
                    self._send_json(500, {"error": "erro interno do diagnóstico"})
                return
            if path == "/api/recovery":
                if not self._autenticado():
                    return
                self._send_json(200, config_recuperacao())
                return
            if path == "/api/notifications":
                if not self._autenticado():
                    return
                cfg = notif_mod.mascarado()
                self._send_json(200, {"canais": cfg.get("canais", []),
                                      "eventos": list(notif_mod.EVENTOS)})
                return
            if path.startswith("/api/settings/"):
                if not self._autenticado():
                    return
                try:
                    if not self._handle_settings_get(path):
                        self._send_json(404, {"error": "rota não encontrada"})
                except Exception as e:
                    print(f"[ERRO] settings GET {path}: {e!r}")
                    self._send_json(500, {"error": "erro interno do editor"})
                return
            self._send_json(404, {"error": "rota não encontrada"})
            return
        if not self._autenticado():
            return
        try:
            self._send_json(200, rota())
        except ApiError as e:
            self._send_json(e.status, {"error": e.message})

    def _enviar_historico(self, qs: dict) -> None:
        """Histórico de métricas do SQLite. ?range=1h|6h|24h|7d|30d"""
        faixas = {"1h": 3600, "6h": 6 * 3600, "24h": 86400,
                  "7d": 7 * 86400, "30d": 30 * 86400}
        bruto = str((qs.get("range") or ["24h"])[0])
        segundos = faixas.get(bruto, 86400)
        agora = time.time()
        amostras = store_mod.consultar_metricas(agora - segundos, agora)
        self._send_json(200, {
            "range": bruto,
            "amostras": amostras,
            "resumo": store_mod.resumo_metricas(segundos, agora),
        })

    def _enviar_dashboard(self) -> None:
        """Tudo que o dashboard precisa numa chamada."""
        snap = status_combinado()
        backups = listar_backups()
        ultimo_backup = None
        if backups.get("backups"):
            b = backups["backups"][0]
            ultimo_backup = {"nome": b["nome"], "modificado": b["modificado"],
                             "bytes": b["bytes"]}
        eventos_srv = store_mod.consultar_eventos(limite=20, kinds=["server"])
        ultima_queda = next((e for e in eventos_srv if e["level"] == "error"),
                            None)
        disco_livre_mb = _amostrar_disco()
        self._send_json(200, {
            "status": snap,
            "ultimo_backup": ultimo_backup,
            "ultima_queda": ultima_queda,
            "disco_livre_mb": disco_livre_mb,
            "banco": store_mod.status_banco(),
        })

    # ---------- console (SSE) ----------

    def _handle_sse(self, qs: dict) -> None:
        """Server-Sent Events com os novos eventos do painel.
        O EventSource do navegador não envia cabeçalhos, então o token
        vem por ?token= — aceito apenas nesta rota de leitura."""
        token_sse = str((qs.get("token") or [""])[0])
        if not _valid_session(token_sse):
            self._send_json(401, {"error": "Não autenticado."})
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        try:
            self.wfile.write(b"retry: 3000\n\n")
            self.wfile.flush()
            ultimo_id = 0
            ultimo_ping = time.time()
            ultima_validacao = 0.0
            while True:
                agora = time.time()
                if agora - ultima_validacao >= 10:
                    if not _valid_session(token_sse):
                        break
                    ultima_validacao = agora
                eventos = store_mod.consultar_eventos(
                    limite=50, desde_ts=None)
                novos = [e for e in reversed(eventos) if e["id"] > ultimo_id]
                for ev in novos:
                    ultimo_id = max(ultimo_id, ev["id"])
                    linha = json.dumps(ev, ensure_ascii=False)
                    self.wfile.write(f"id: {ev['id']}\ndata: {linha}\n\n".encode())
                if novos:
                    self.wfile.flush()
                elif time.time() - ultimo_ping > 15:
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
                    ultimo_ping = time.time()
                time.sleep(2)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass  # cliente desconectou — fim normal do SSE

    # ---------- editor de configurações ----------

    def _settings_api_atual(self):
        """Settings vigentes do jogo (None se estiver offline)."""
        try:
            resp = api("GET", "/v1/api/settings")
            return resp if isinstance(resp, dict) else None
        except ApiError:
            return None

    def _handle_settings_get(self, path: str) -> bool:
        """Endpoints GET do editor. Devolve True se tratou a rota."""
        import json as _json
        if path == "/api/settings/editor":
            self._send_json(200, settings_ed_mod.estado(
                PALWORLD_INI, self._settings_api_atual()))
        elif path == "/api/settings/presets":
            self._send_json(200, {"presets": settings_ed_mod.presets_com_estado(
                PALWORLD_INI, self._settings_api_atual())})
        elif path == "/api/settings/backups":
            self._send_json(200, {"backups": settings_ini_mod.listar_backups(
                CONFIG_BACKUPS_DIR)})
        elif path == "/api/settings/export":
            dados = settings_ed_mod.exportar(PALWORLD_INI,
                                             self._settings_api_atual())
            corpo = _json.dumps(dados, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(corpo)))
            self.send_header("Content-Disposition",
                             'attachment; filename="config-palworld.json"')
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(corpo)
        elif path == "/api/settings/raw":
            texto = settings_ed_mod.bruto(PALWORLD_INI)
            if texto is None:
                self._send_json(404, {"error":
                    "PALWORLD_INI não configurado ou arquivo ausente."})
            else:
                self._send_json(200, {"texto": texto})
        else:
            return False
        return True

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
        if path.startswith("/api/v1/"):
            payload = self._read_json()
            if payload is None:
                return
            self._handle_api_v1(path, {}, "POST", payload)
            return
        payload = self._read_json()
        if payload is None:  # corpo excedente — 413 já enviado
            return
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
        nome_usuario = str(payload.get("usuario") or "").strip() or None

        usuario = users_mod.autenticar(nome_usuario, senha) \
            if nome_usuario else None
        token_sessao = None
        if usuario:
            token_sessao = _create_session(usuario["id"])
        elif _verificar_senha_panel(senha):
            # login de senha única → conta admin bootstrap
            users_mod.garantir_admin_inicial(PANEL_PASSWORD)
            admin = users_mod.autenticar(None, PANEL_PASSWORD) or {}
            token_sessao = _create_session(admin.get("id"))

        if token_sessao:
            self._usuario = {"usuario": nome_usuario or "admin",
                             "papel": (usuario or {}).get("papel", "admin")}
            _limpar_falhas(ip)
            store_mod.registrar_evento(
                "info", "auth", f"Login realizado ({nome_usuario or 'admin'})",
                {"ip": ip, "usuario": nome_usuario or "admin"})
            return self._send_json(200, {"ok": True, "token": token_sessao,
                                         "usuario": nome_usuario or "admin"})

        _registrar_falha(ip)
        time.sleep(0.5)  # freia força bruta
        store_mod.registrar_evento(
            "warn", "auth",
            f"Senha incorreta no login ({nome_usuario or 'admin'})",
            {"ip": ip})
        self._send_json(401, {"error": "Senha incorreta."})

    def _handle_api_post(self, path: str, payload: dict) -> None:
        # mapa de permissões das ações de escrita
        PERM_ACOES = (
            ("/api/announce", "SERVER_SAVE"), ("/api/save", "SERVER_SAVE"),
            ("/api/restart", "SERVER_RESTART"),
            ("/api/shutdown", "SERVER_STOP"),
            ("/api/kick", "PLAYER_KICK"), ("/api/ban", "PLAYER_BAN"),
            ("/api/unban", "PLAYER_BAN"),
        )
        permissao = dict(PERM_ACOES).get(path)
        if permissao and not self._requer(permissao):
            return
        if permissao and maint_mod.ler_manutencao().get("ativo"):
            return self._send_json(423, {
                "error": "Painel em MODO MANUTENÇÃO: ações bloqueadas. "
                         "Desative em Saúde → Manutenção.",
                "motivo": maint_mod.ler_manutencao().get("motivo")})
        try:
            if path == "/api/announce":
                msg = str(payload.get("message", "")).strip()
                if not msg:
                    return self._send_json(400, {"error": "Mensagem vazia."})
                resp = api("POST", "/v1/api/announce", {"message": msg[:500]})
                store_mod.registrar_evento(
                    "info", "action", f"Anúncio enviado: {msg[:80]}",
                    {"ip": self.client_address[0],
                         "usuario": getattr(self, "_usuario", {}).get("usuario")})
                return self._send_json(200, {"ok": True, "resposta": resp})

            if path == "/api/save":
                resp = api("POST", "/v1/api/save", {})
                store_mod.registrar_evento(
                    "info", "action", "Save do mundo solicitado pelo painel",
                    {"ip": self.client_address[0],
                         "usuario": getattr(self, "_usuario", {}).get("usuario")})
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
                if path == "/api/ban":
                    expira_em = None
                    try:
                        horas = float(payload.get("duracao_horas") or 0)
                        if horas > 0:
                            expira_em = int(time.time() + horas * 3600)
                    except (TypeError, ValueError):
                        pass
                    ficha = players_mod.detalhe(userid)
                    players_mod.registrar_ban(
                        userid,
                        nome=ficha["nome"] if ficha else "",
                        motivo=motivo[:200], expira_em=expira_em)
                    store_mod.registrar_evento(
                        "error", "player",
                        f"BAN{' temporário' if expira_em else ' permanente'} "
                        f"aplicado a {userid[:40]}",
                        {"motivo": motivo[:200],
                         "expira_em": expira_em,
                         "ip": self.client_address[0],
                         "usuario": getattr(self, "_usuario", {}).get("usuario")})
                else:
                    store_mod.registrar_evento(
                        "warn", "player",
                        f"KICK aplicado a {userid[:40]}",
                        {"motivo": motivo[:200], "ip": self.client_address[0],
                         "usuario": getattr(self, "_usuario", {}).get("usuario")})
                return self._send_json(200, {"ok": True, "resposta": resp})

            if path == "/api/unban":
                userid = str(payload.get("userid", "")).strip()
                if not userid:
                    return self._send_json(400, {"error": "userid obrigatório."})
                resp = api("POST", "/v1/api/unban", {"userid": userid})
                afetados = players_mod.marcar_unban(userid, por="painel")
                store_mod.registrar_evento(
                    "info", "player", f"Unban de {userid[:40]}",
                    {"registro_local": afetados, "ip": self.client_address[0],
                         "usuario": getattr(self, "_usuario", {}).get("usuario")})
                return self._send_json(200, {
                    "ok": True, "resposta": resp,
                    "bans_locais_removidos": afetados})

            if path in ("/api/settings/diff", "/api/settings/apply"):
                if not self._requer("SETTINGS_EDIT"):
                    return
                mudancas = payload.get("mudancas")
                if not isinstance(mudancas, dict):
                    return self._send_json(400,
                        {"error": "Envie {'mudancas': {chave: valor}}."})
                est = settings_ed_mod.estado(PALWORLD_INI,
                                             self._settings_api_atual())
                try:
                    if path == "/api/settings/diff":
                        validas, invalidas = settings_ed_mod.validar_mudancas(
                            mudancas, est["valores"])
                        return self._send_json(200, {
                            "ok": not invalidas, "validas": validas,
                            "invalidas": invalidas,
                            "aviso": "Configurações valem após reiniciar o servidor."})
                    resultado = settings_ed_mod.aplicar(
                        PALWORLD_INI, CONFIG_BACKUPS_DIR, mudancas,
                        est["valores"])
                    if resultado["ok"] and resultado["aplicadas"]:
                        store_mod.registrar_evento(
                            "warn", "settings",
                            f"{len(resultado['aplicadas'])} configuração(ões) "
                            "alterada(s) — restart necessário",
                            {"chaves": [m["chave"] for m in
                                        resultado["aplicadas"]],
                             "ip": self.client_address[0],
                         "usuario": getattr(self, "_usuario", {}).get("usuario")})
                    elif resultado["invalidas"]:
                        store_mod.registrar_evento(
                            "warn", "settings",
                            "Tentativa de aplicar valores inválidos",
                            {"ip": self.client_address[0],
                         "usuario": getattr(self, "_usuario", {}).get("usuario")})
                    return self._send_json(200, resultado)
                except PermissionError as e:
                    return self._send_json(409, {"error": str(e)})

            if path == "/api/settings/backups/restore":
                if not self._requer("SETTINGS_EDIT"):
                    return
                nome = str(payload.get("nome", "")).strip()
                if not nome:
                    return self._send_json(400,
                                           {"error": "Informe o nome do backup."})
                try:
                    settings_ed_mod.restaurar(PALWORLD_INI, CONFIG_BACKUPS_DIR,
                                              nome)
                    store_mod.registrar_evento(
                        "error", "settings",
                        f"Backup de config restaurado: {nome}",
                        {"ip": self.client_address[0],
                         "usuario": getattr(self, "_usuario", {}).get("usuario")})
                    return self._send_json(200, {
                        "ok": True,
                        "aviso": "Configuração restaurada. "
                                 "REINICIE o servidor de jogo."})
                except FileNotFoundError as e:
                    return self._send_json(404, {"error": str(e)})
                except (PermissionError, OSError) as e:
                    return self._send_json(409, {"error": str(e)})

            if path == "/api/settings/import":
                if not self._requer("SETTINGS_EDIT"):
                    return
                try:
                    validas, invalidas = settings_ed_mod.importar(
                        payload, PALWORLD_INI, self._settings_api_atual())
                except ValueError as e:
                    return self._send_json(400, {"error": str(e)})
                return self._send_json(200, {
                    "ok": not invalidas, "validas": validas,
                    "invalidas": invalidas,
                    "nota": "Revise e aplique para gravar no arquivo."})

            if path == "/api/scheduler":
                if not self._requer("SETTINGS_EDIT"):
                    return
                job, erro = scheduler_mod.criar(payload)
                if erro:
                    return self._send_json(400, {"error": erro})
                store_mod.registrar_evento(
                    "info", "scheduler", f"Job criado: {job['nome']}",
                    {"tipo": job["tipo"], "hora": job["hora"],
                     "intervalo_horas": job["intervalo_horas"]})
                return self._send_json(200, {"ok": True, "job": job})

            if path.startswith("/api/scheduler/"):
                if not self._requer_alguma("SERVER_RESTART", "SETTINGS_EDIT"):
                    return
                partes = path.split("/")
                try:
                    job_id = int(partes[3])
                except (IndexError, ValueError):
                    return self._send_json(400, {"error": "id inválido."})
                if len(partes) > 4 and partes[4] == "run":
                    ok, resultado = scheduler_mod.executar_agora(job_id)
                    store_mod.registrar_evento(
                        "warn" if ok else "error", "scheduler",
                        f"Execução manual de job #{job_id}: {resultado}")
                    return self._send_json(200 if ok else 400,
                                           {"ok": ok,
                                            "resultado": str(resultado)})
                job, erro = scheduler_mod.atualizar(job_id, payload)
                if erro:
                    return self._send_json(400, {"error": erro})
                return self._send_json(200, {"ok": True, "job": job})

            if path in ("/api/backups/criar", "/api/backups/excluir",
                        "/api/backups/restaurar"):
                if not self._autenticado():
                    return
                permissao_bk = ("BACKUP_RESTORE" if path.endswith("restaurar")
                                else "BACKUP_CREATE")
                if not self._requer(permissao_bk):
                    return
                try:
                    if path == "/api/backups/criar":
                        resultado = backups_mgr.criar_backup_mundo(
                            PALWORLD_SAVE_DIR, BACKUP_DIR)
                        store_mod.registrar_evento(
                            "info", "backup",
                            f"Backup manual do mundo: {resultado['nome']}",
                            {"bytes": resultado["bytes"],
                             "ip": self.client_address[0],
                         "usuario": getattr(self, "_usuario", {}).get("usuario")})
                        return self._send_json(200,
                                               {"ok": True, **resultado})
                    if path == "/api/backups/excluir":
                        nome = str(payload.get("nome", "")).strip()
                        if not nome:
                            return self._send_json(
                                400, {"error": "nome obrigatório."})
                        backups_mgr.excluir_backup(BACKUP_DIR, nome)
                        store_mod.registrar_evento(
                            "warn", "backup",
                            f"Backup excluído pelo painel: {nome[:80]}")
                        return self._send_json(200, {"ok": True})
                    # restauração do mundo
                    nome = str(payload.get("nome", "")).strip()
                    if not nome:
                        return self._send_json(400,
                                               {"error": "nome obrigatório."})
                    resultado = backups_mgr.restaurar_mundo(
                        BACKUP_DIR, nome, PALWORLD_SAVE_DIR)
                    store_mod.registrar_evento(
                        "error", "restore",
                        f"MUNDO RESTAURADO a partir de {nome[:80]}",
                        {"anterior_preservado_em":
                         resultado["anterior_preservado_em"],
                         "ip": self.client_address[0],
                         "usuario": getattr(self, "_usuario", {}).get("usuario")})
                    return self._send_json(200, {"ok": True, **resultado})
                except FileNotFoundError as e:
                    return self._send_json(404, {"error": str(e)})
                except (backups_mgr.BackupError, zipfile.BadZipFile) as e:
                    return self._send_json(400, {"error": str(e)})
                except PermissionError as e:
                    return self._send_json(409, {"error": str(e)})
                except OSError as e:
                    return self._send_json(500,
                        {"error": f"falha de disco ao operar o backup: {e}"})

            if path == "/api/health/corrigir":
                if not self._requer("SETTINGS_EDIT"):
                    return
                tipo = str(payload.get("acao", "")).strip()
                try:
                    ok, msg = health_mod.acao_corretiva(tipo, {
                        "criar_backup": lambda: backups_mgr.criar_backup_mundo(
                            PALWORLD_SAVE_DIR, BACKUP_DIR),
                        "retencao": backups_mgr.retencao,
                    })
                    store_mod.registrar_evento(
                        "info", "health", f"Correção executada: {msg}")
                    return self._send_json(200, {"ok": ok, "resultado": msg})
                except ValueError as e:
                    return self._send_json(400, {"error": str(e)})
                except Exception as e:
                    return self._send_json(500, {"error": str(e)})

            if path == "/api/recovery":
                if not self._requer("SETTINGS_EDIT"):
                    return
                cfg = salvar_config_recuperacao(payload)
                store_mod.registrar_evento(
                    "info", "recovery",
                    f"Config de auto-recuperação atualizada "
                    f"(habilitado={cfg['habilitado']})")
                return self._send_json(200, {"ok": True, **cfg})

            if path in ("/api/notifications", "/api/notifications/teste"):
                if not self._autenticado():
                    return
                if not self._requer("SETTINGS_EDIT"):
                    return
                if path.endswith("/teste"):
                    canal_id = str(payload.get("id", "")).strip()
                    ok, err = notif_mod.testar(canal_id)
                    return self._send_json(200 if ok else 400,
                                           {"ok": ok, "erro": err})
                if payload.get("canais") is None:
                    return self._send_json(
                        200, {"canais": [], "eventos": list(notif_mod.EVENTOS)})
                try:
                    notif_mod.mesclar_novos(payload)
                except (TypeError, OSError) as e:
                    return self._send_json(500, {"error": str(e)})
                store_mod.registrar_evento(
                    "info", "settings", "Canais de notificação atualizados")
                return self._send_json(200, {"ok": True})

            if path == "/api/maintenance":
                if not self._requer("SETTINGS_EDIT"):
                    return
                estado = maint_mod.definir_manutencao(
                    bool(payload.get("ativo")),
                    str(payload.get("motivo", "")))
                store_mod.registrar_evento(
                    "warn" if estado["ativo"] else "info", "settings",
                    f"Modo manutenção {'ATIVADO' if estado['ativo'] else 'desativado'}",
                    {"motivo": estado["motivo"],
                     "usuario": getattr(self, "_usuario", {}).get("usuario")})
                return self._send_json(200, {"ok": True, **estado})

            if path == "/api/emergency":
                if not (self._requer("SERVER_STOP")
                        and self._requer("BACKUP_CREATE")):
                    return
                try:
                    passos = maint_mod._sequencia_emergencia({
                        "anunciar": lambda msg: api(
                            "POST", "/v1/api/announce", {"message": msg}),
                        "salvar": lambda: api("POST", "/v1/api/save", {}),
                        "backup": lambda: backups_mgr.criar_backup_mundo(
                            PALWORLD_SAVE_DIR, BACKUP_DIR),
                        "desligar": lambda wt, msg: _sequencia_desligar(
                            msg, wt),
                    })
                except maint_mod.EmergenciaEmAndamento as e:
                    return self._send_json(409, {"error": str(e)})
                except ApiError as e:
                    return self._send_json(e.status, {"error": e.message})
                store_mod.registrar_evento(
                    "error", "action", "EMERGÊNCIA executada",
                    {"passos": passos,
                     "usuario": getattr(self, "_usuario", {}).get("usuario")})
                def _falhou(k, v):
                    if k == "backup":
                        return not str(v).endswith(".zip")
                    return str(v) != "ok"

                falhou = [k for k, v in passos.items() if _falhou(k, v)]
                return self._send_json(
                    200 if not falhou else 502,
                    {"ok": not falhou, "passos": passos})

            if path == "/api/keys":
                if not self._requer("USER_MANAGE"):
                    return
                chave, erro = users_mod.criar_api_key(
                    payload.get("nome"), payload.get("permissoes"))
                if erro:
                    return self._send_json(400, {"error": erro})
                store_mod.registrar_evento(
                    "warn", "auth",
                    f"API key criada: {chave['nome']}",
                    {"usuario": getattr(self, "_usuario", {}).get("usuario")})
                return self._send_json(200, {"ok": True,
                                             "chave": chave["chave"],
                                             "aviso": "Guarde agora: não é "
                                                      "exibida novamente."})

            if path == "/api/usuarios":
                if not self._requer("USER_MANAGE"):
                    return
                usuario_novo, erro = users_mod.criar(
                    payload.get("usuario"), payload.get("password") or "",
                    payload.get("papel") or "viewer")
                if erro:
                    return self._send_json(400, {"error": erro})
                store_mod.registrar_evento(
                    "warn", "auth",
                    f"Usuário criado: {usuario_novo['usuario']} "
                    f"({usuario_novo['papel']})",
                    {"usuario": getattr(self, "_usuario", {}).get("usuario")})
                return self._send_json(200, {"ok": True,
                                             "usuario": usuario_novo})

            if path.startswith("/api/usuarios/"):
                if not self._requer("USER_MANAGE"):
                    return
                partes = path.split("/")
                try:
                    uid = int(partes[3])
                except (IndexError, ValueError):
                    return self._send_json(400, {"error": "id inválido."})
                usuario_upd, erro = users_mod.atualizar(
                    uid,
                    senha=payload.get("senha"),
                    papel=payload.get("papel"),
                    ativo=payload.get("ativo"))
                if erro:
                    return self._send_json(400, {"error": erro})
                if payload.get("ativo") is False:
                    users_mod.revogar_todas(uid)
                store_mod.registrar_evento(
                    "warn", "auth", f"Usuário #{uid} atualizado",
                    {"campos": [k for k in ("senha", "papel", "ativo")
                                 if k in payload]})
                return self._send_json(200, {"ok": True,
                                             "usuario": usuario_upd})

            if path.startswith("/api/usuarios/") and self.command == "DELETE":
                pass  # tratado em do_DELETE

            if path == "/api/sessoes/revogar":
                if not self._requer("USER_MANAGE"):
                    return
                token_alvo = str(payload.get("token") or "").strip()
                uid = payload.get("usuario_id")
                if uid:
                    qtd = users_mod.revogar_todas(int(uid))
                elif payload.get("todos"):
                    qtd = users_mod.revogar_todas()
                elif token_alvo:
                    users_mod.revogar(token_alvo)
                    qtd = 1
                else:
                    return self._send_json(
                        400, {"error": "informe token, usuario_id ou todos."})
                store_mod.registrar_evento(
                    "warn", "auth", f"Sessões revogadas: {qtd}")
                return self._send_json(200, {"ok": True, "revogadas": qtd})

            self._send_json(404, {"error": "rota não encontrada"})
        except ApiError as e:
            self._send_json(e.status, {"error": e.message})


# ----------------------------------------------------------------- main

def main() -> None:
    try:
        httpd = ThreadingHTTPServer((PANEL_HOST, PANEL_PORT), PanelHandler)
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
    criado = users_mod.garantir_admin_inicial(PANEL_PASSWORD)
    registrar_executores_agenda()
    registrar_pontes()
    iniciar_coletores()
    scheduler_mod.iniciar()
    linha = "=" * 60
    print(linha)
    print(f"  🐑 Painel Palworld rodando em  http://{PANEL_HOST}:{PANEL_PORT}"
          + ("  (todas as interfaces)" if PANEL_HOST == "0.0.0.0" else "  (SOMENTE local)"))
    print(linha)
    if PANEL_PASSWORD_AUTOGERADA:
        print(f"  🔑 SENHA DE ACESSO GERADA: {PANEL_PASSWORD}")
        print(f"     (guardada em {os.path.relpath(_AUTH_FILE, _SCRIPT_DIR)})")
        print("     Para definir a sua: variável de ambiente PANEL_PASSWORD.")
    else:
        print(f"  Senha de acesso ao painel : {PANEL_PASSWORD}   "
              f"[{_fonte.get('PANEL_PASSWORD', '?')}]")
    print(f"  ADMIN_PASSWORD (do jogo)  : "
          f"{'(vazia)' if not ADMIN_PASSWORD else '(configurada)'}   "
          f"[{_fonte.get('ADMIN_PASSWORD', '?')}]")
    print(f"  API do jogo               : {PALWORLD_API}")
    print(linha)
    if _senha_fraca(ADMIN_PASSWORD):
        store_mod.registrar_evento(
            "warn", "security",
            "ADMIN_PASSWORD vazia ou fraca — ações no jogo vão falhar")
        print("  ⚠️  ADMIN_PASSWORD vazia/fraca — o painel não vai conseguir")
        print("     comandar o jogo. Defina a mesma senha nos DOIS serviços")
        print("     do Palworld Server.yml (palworld e Painel).")
        print(linha)
    store_mod.registrar_evento(
        "info", "panel", f"Painel iniciado na porta {PANEL_PORT}",
        {"api": PALWORLD_API})
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nEncerrando o painel...")


if __name__ == "__main__":
    main()
