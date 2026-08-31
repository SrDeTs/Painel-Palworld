#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Usuários, papéis, permissões e sessões do painel.

Papéis → permissões:
- admin:     todas
- moderador: view/restart/stop/save + kick/ban + backup criar + logs + agenda
- viewer:    somente leitura (view + logs)

A primeira conta "admin" nasce da PANEL_PASSWORD (ambiente/gerada) — o login
com senha única continua funcionando para ela. Sessões vivem no SQLite e
podem ser revogadas individualmente ou em massa.
"""

import hashlib
import json
import os
import secrets
import sqlite3
import threading
import time

DATA_DIR = os.path.abspath(os.environ.get("PANEL_DATA_DIR") or
                           os.path.join(os.path.dirname(os.path.abspath(__file__)), "data"))
DB_PATH = os.path.join(DATA_DIR, "painel.db")

PERMISSOES_TODAS = (
    "SERVER_VIEW", "SERVER_RESTART", "SERVER_STOP", "SERVER_SAVE",
    "PLAYER_KICK", "PLAYER_BAN", "BACKUP_CREATE", "BACKUP_RESTORE",
    "SETTINGS_EDIT", "LOG_VIEW", "USER_MANAGE",
)

_POR_PAPEL = {
    "admin": PERMISSOES_TODAS,
    "moderador": ("SERVER_VIEW", "SERVER_RESTART", "SERVER_STOP",
                  "SERVER_SAVE", "PLAYER_KICK", "PLAYER_BAN",
                  "BACKUP_CREATE", "LOG_VIEW"),
    "viewer": ("SERVER_VIEW", "LOG_VIEW"),
}

PAPEIS = tuple(_POR_PAPEL)

_lock = threading.Lock()
_conn = None


def _conectar() -> sqlite3.Connection:
    global _conn
    with _lock:
        if _conn is None:
            os.makedirs(DATA_DIR, exist_ok=True)
            _conn = sqlite3.connect(DB_PATH, check_same_thread=False)
            _conn.row_factory = sqlite3.Row
            _conn.execute("PRAGMA journal_mode=WAL")
            _criar_schema(_conn)
        return _conn


def _criar_schema(conn: sqlite3.Connection) -> None:
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS usuarios (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        usuario TEXT UNIQUE NOT NULL,
        pbkdf2 TEXT NOT NULL,
        papel TEXT NOT NULL DEFAULT 'viewer',
        ativo INTEGER NOT NULL DEFAULT 1,
        criado_em INTEGER NOT NULL,
        ultimo_login INTEGER
    );
    CREATE TABLE IF NOT EXISTS sessoes_usuarios (
        token TEXT PRIMARY KEY,
        usuario_id INTEGER NOT NULL,
        criado INTEGER NOT NULL,
        expira INTEGER NOT NULL,
        revogada INTEGER NOT NULL DEFAULT 0,
        FOREIGN KEY(usuario_id) REFERENCES usuarios(id)
    );
    CREATE TABLE IF NOT EXISTS api_keys (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nome TEXT NOT NULL,
        chave TEXT UNIQUE NOT NULL,
        permissoes TEXT NOT NULL DEFAULT '[]',
        ativa INTEGER NOT NULL DEFAULT 1,
        criada_em INTEGER NOT NULL,
        ultimo_uso INTEGER
    );
    """)
    conn.commit()


# ------------------------------------------------------------------ senhas

def hash_senha(senha: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", senha.encode("utf-8"), salt, 200_000)
    return f"pbkdf2${salt.hex()}${digest.hex()}"


def conferir_senha(senha: str, registro: str) -> bool:
    try:
        _, sal_hex, dig_hex = registro.split("$")
        esperado = bytes.fromhex(dig_hex)
        calculado = hashlib.pbkdf2_hmac(
            "sha256", senha.encode("utf-8"), bytes.fromhex(sal_hex), 200_000)
        return secrets.compare_digest(calculado, esperado)
    except (ValueError, AttributeError):
        return False


# ------------------------------------------------------------------ usuários

def garantir_admin_inicial(senha_bootstrap: str) -> bool:
    """Cria o admin 'admin' com a PANEL_PASSWORD se não houver nenhum usuário.
    Se ele já existe, mantém o hash sincronizado com a senha do ambiente
    (a PANEL_PASSWORD é sempre a fonte da verdade para essa conta).
    Devolve True se criou agora."""
    if senha_bootstrap is None or senha_bootstrap == "":
        return False
    conn = _conectar()
    with _lock:
        row = conn.execute(
            "SELECT id, pbkdf2 FROM usuarios WHERE usuario='admin'").fetchone()
        total = conn.execute("SELECT COUNT(*) c FROM usuarios").fetchone()["c"]
        if row:
            # senha do ambiente mudou? atualiza o hash do admin bootstrap
            if not conferir_senha(senha_bootstrap, row["pbkdf2"]):
                conn.execute(
                    "UPDATE usuarios SET pbkdf2=? WHERE id=?",
                    (hash_senha(senha_bootstrap), row["id"]))
                conn.commit()
            return False
        conn.execute(
            """INSERT INTO usuarios(usuario, pbkdf2, papel, ativo, criado_em)
               VALUES('admin', ?, 'admin', 1, ?)""",
            (hash_senha(senha_bootstrap), int(time.time())))
        conn.commit()
        del total
    return True


def autenticar(usuario: str | None, senha: str):
    """Devolve dict do usuário ou None. Sem nome de usuário, tenta 'admin'."""
    alvo = (usuario or "admin").strip()
    conn = _conectar()
    with _lock:
        row = conn.execute(
            "SELECT * FROM usuarios WHERE usuario=? AND ativo=1",
            (alvo,)).fetchone()
    if row and conferir_senha(senha, row["pbkdf2"]):
        with _lock:
            conn.execute("UPDATE usuarios SET ultimo_login=? WHERE id=?",
                         (int(time.time()), row["id"]))
            conn.commit()
        d = dict(row)
        d.pop("pbkdf2")
        return d
    return None


def listar() -> list:
    conn = _conectar()
    with _lock:
        rows = conn.execute(
            "SELECT id, usuario, papel, ativo, criado_em, ultimo_login "
            "FROM usuarios ORDER BY id").fetchall()
    return [dict(r) for r in rows]


def obter_por_id(usuario_id: int):
    conn = _conectar()
    with _lock:
        row = conn.execute(
            "SELECT id, usuario, papel, ativo FROM usuarios WHERE id=?",
            (usuario_id,)).fetchone()
    return dict(row) if row else None


def criar(usuario: str, senha: str, papel: str) -> tuple:
    usuario = (usuario or "").strip()
    if len(usuario) < 3:
        return None, "nome de usuário muito curto (mín. 3)"
    if len(senha) < 6:
        return None, "senha muito curta (mín. 6)"
    if papel not in PAPEIS:
        return None, f"papel deve ser um de: {', '.join(PAPEIS)}"
    conn = _conectar()
    try:
        with _lock:
            cur = conn.execute(
                """INSERT INTO usuarios(usuario, pbkdf2, papel, ativo, criado_em)
                   VALUES(?,?,?,?,?)""",
                (usuario, hash_senha(senha), papel, 1, int(time.time())))
            conn.commit()
    except sqlite3.IntegrityError:
        return None, "usuário já existe"
    return obter_por_id(cur.lastrowid), None


def atualizar(usuario_id: int, *, senha: str = None, papel: str = None,
              ativo: bool = None) -> tuple:
    alvo = obter_por_id(usuario_id)
    if not alvo:
        return None, "usuário não encontrado"
    if papel is not None and papel not in PAPEIS:
        return None, f"papel deve ser um de: {', '.join(PAPEIS)}"
    # protege o último admin ativo
    if alvo["papel"] == "admin" and (papel not in (None, "admin")
                                     or ativo is False):
        conn = _conectar()
        with _lock:
            admins = conn.execute(
                "SELECT COUNT(*) c FROM usuarios WHERE papel='admin' AND ativo=1"
            ).fetchone()["c"]
        if admins <= 1:
            return None, "impossível remover o último admin ativo"
    sets, args = [], []
    if senha:
        if len(senha) < 6:
            return None, "senha muito curta (mín. 6)"
        sets.append("pbkdf2=?"); args.append(hash_senha(senha))
    if papel is not None:
        sets.append("papel=?"); args.append(papel)
    if ativo is not None:
        sets.append("ativo=?"); args.append(1 if ativo else 0)
    if not sets:
        return obter_por_id(usuario_id), None
    args.append(usuario_id)
    conn = _conectar()
    with _lock:
        conn.execute(f"UPDATE usuarios SET {', '.join(sets)} WHERE id=?",
                     args)
        conn.commit()
    return obter_por_id(usuario_id), None


def excluir(usuario_id: int) -> tuple:
    alvo = obter_por_id(usuario_id)
    if not alvo:
        return False, "usuário não encontrado"
    if alvo["papel"] == "admin":
        conn = _conectar()
        with _lock:
            admins = conn.execute(
                "SELECT COUNT(*) c FROM usuarios WHERE papel='admin' AND ativo=1"
            ).fetchone()["c"]
        if admins <= 1:
            return False, "impossível excluir o último admin"
    conn = _conectar()
    with _lock:
        conn.execute("DELETE FROM sessoes_usuarios WHERE usuario_id=?",
                     (usuario_id,))
        conn.execute("DELETE FROM usuarios WHERE id=?", (usuario_id,))
        conn.commit()
    return True, None


# ------------------------------------------------------------------ API keys

def criar_api_key(nome: str, permissoes: list) -> tuple:
    nome = (nome or "").strip()[:60] or "sem-nome"
    perms = [p for p in (permissoes or []) if p in PERMISSOES_TODAS]
    if not perms:
        return None, "informe ao menos uma permissão válida"
    chave = "ppk_" + secrets.token_urlsafe(30)
    conn = _conectar()
    with _lock:
        cur = conn.execute(
            """INSERT INTO api_keys(nome, chave, permissoes, ativa, criada_em)
               VALUES(?,?,?,1,?)""",
            (nome, chave, json.dumps(perms), int(time.time())))
        conn.commit()
        return {"id": cur.lastrowid, "nome": nome,
                "chave": chave,  # visível SÓ na criação
                "permissoes": perms}, None


def listar_api_keys() -> list:
    conn = _conectar()
    with _lock:
        rows = conn.execute(
            "SELECT id, nome, permissoes, ativa, criada_em, ultimo_uso "
            "FROM api_keys ORDER BY id").fetchall()
    saida = []
    for r in rows:
        d = dict(r)
        d["chave_mascarada"] = d["chave"][:8] + "…" \
            if "chave" in d else ""
        try:
            d["permissoes"] = json.loads(d.get("permissoes") or "[]")
        except ValueError:
            d["permissoes"] = []
        saida.append(d)
    return saida


def revogar_api_key(api_id: int) -> bool:
    conn = _conectar()
    with _lock:
        cur = conn.execute("DELETE FROM api_keys WHERE id=?", (api_id,))
        conn.commit()
    return cur.rowcount > 0


def validar_api_key(chave: str):
    """Devolve {'nome','permissoes'} se a chave estiver ativa."""
    if not chave:
        return None
    conn = _conectar()
    with _lock:
        row = conn.execute(
            "SELECT id, nome, permissoes FROM api_keys WHERE chave=? AND ativa=1",
            (chave,)).fetchone()
        if row:
            conn.execute("UPDATE api_keys SET ultimo_uso=? WHERE id=?",
                         (int(time.time()), row["id"]))
            conn.commit()
    if not row:
        return None
    try:
        perms = json.loads(row["permissoes"])
    except ValueError:
        perms = []
    return {"id": row["id"], "nome": row["nome"], "permissoes": perms}


# ------------------------------------------------------------------ sessões

SESSION_TTL = 12 * 3600


def criar_sessao(usuario_id: int) -> str:
    token = secrets.token_urlsafe(32)
    agora = time.time()
    conn = _conectar()
    with _lock:
        conn.execute(
            """INSERT INTO sessoes_usuarios(token, usuario_id, criado, expira)
               VALUES(?,?,?,?)""", (token, usuario_id, agora, agora + SESSION_TTL))
        # poda sessões vencidas
        conn.execute("DELETE FROM sessoes_usuarios WHERE expira<?", (agora,))
        conn.commit()
    return token


def validar_sessao(token: str):
    """Devolve dict do usuário se a sessão vale (e a renova)."""
    if not token:
        return None
    agora = time.time()
    conn = _conectar()
    with _lock:
        row = conn.execute(
            """SELECT s.usuario_id, s.expira, u.usuario, u.papel, u.ativo
               FROM sessoes_usuarios s JOIN usuarios u ON u.id=s.usuario_id
               WHERE s.token=? AND s.revogada=0""", (token,)).fetchone()
        if not row or row["expira"] < agora or not row["ativo"]:
            return None
        conn.execute("UPDATE sessoes_usuarios SET expira=? WHERE token=?",
                     (agora + SESSION_TTL, token))
        conn.commit()
    return {"id": row["usuario_id"], "usuario": row["usuario"],
            "papel": row["papel"]}


def revogar(token: str) -> None:
    conn = _conectar()
    with _lock:
        conn.execute(
            "UPDATE sessoes_usuarios SET revogada=1 WHERE token=?", (token,))
        conn.commit()


def revogar_todas(usuario_id: int = None) -> int:
    conn = _conectar()
    with _lock:
        if usuario_id:
            cur = conn.execute(
                """UPDATE sessoes_usuarios SET revogada=1
                   WHERE usuario_id=? AND revogada=0""", (usuario_id,))
        else:
            cur = conn.execute(
                "UPDATE sessoes_usuarios SET revogada=1 WHERE revogada=0")
        conn.commit()
    return cur.rowcount


def listar_sessoes() -> list:
    agora = time.time()
    conn = _conectar()
    with _lock:
        rows = conn.execute(
            """SELECT s.token, s.criado, s.expira, u.usuario, u.papel
               FROM sessoes_usuarios s JOIN usuarios u ON u.id=s.usuario_id
               WHERE s.revogada=0 AND s.expira>? ORDER BY s.criado DESC""",
            (agora,)).fetchall()
    saida = []
    for r in rows:
        d = dict(r)
        d["token_curto"] = d.pop("token")[:8] + "…"
        saida.append(d)
    return saida


# ------------------------------------------------------------------ permissões

def permissoes_de(papel: str) -> list:
    return list(_POR_PAPEL.get(papel, ()))


def tem_permissao(usuario: dict | None, permissao: str) -> bool:
    if not usuario:
        return False
    return permissao in permissoes_de(usuario.get("papel", ""))
