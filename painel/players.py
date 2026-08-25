#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Registro local de jogadores e livro de bans.

A REST API do jogo só lista quem está ONLINE agora — não há histórico,
tempo de jogo nem lista de banidos. Este módulo mantém essas informações
observando o servidor periodicamente:

- `atualizar(lista_online)` registra/apaga sessões conforme jogadores
  entram e saem, acumulando tempo total e nível máximo;
- `registrar_ban` grava o ban aplicado pelo painel (permanente ou com
  expiração); `expirados()` devolve os temporários vencidos p/ um
  desbanimento automático controlado.
"""

import os
import sqlite3
import threading
import time

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
DB_PATH = os.path.join(DATA_DIR, "painel.db")

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
    CREATE TABLE IF NOT EXISTS players (
        userid TEXT PRIMARY KEY,
        nome TEXT NOT NULL DEFAULT '',
        primeiro_visto INTEGER NOT NULL,
        ultimo_visto INTEGER NOT NULL,
        sessoes INTEGER NOT NULL DEFAULT 0,
        tempo_total_s INTEGER NOT NULL DEFAULT 0,
        nivel_max INTEGER,
        ultima_pos TEXT,
        online INTEGER NOT NULL DEFAULT 0
    );
    CREATE TABLE IF NOT EXISTS sessoes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        userid TEXT NOT NULL,
        inicio INTEGER NOT NULL,
        fim INTEGER,
        duracao_s INTEGER,
        FOREIGN KEY(userid) REFERENCES players(userid)
    );
    CREATE INDEX IF NOT EXISTS idx_sessoes_userid ON sessoes(userid, inicio DESC);
    CREATE TABLE IF NOT EXISTS bans (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        userid TEXT NOT NULL,
        nome TEXT,
        motivo TEXT,
        criado_em INTEGER NOT NULL,
        expira_em INTEGER,              -- NULL = permanente
        ativo INTEGER NOT NULL DEFAULT 1,
        removido_em INTEGER,
        removido_por TEXT,
        origem TEXT NOT NULL DEFAULT 'painel'
    );
    CREATE INDEX IF NOT EXISTS idx_bans_userid ON bans(userid);
    """)
    conn.commit()


# ------------------------------------------------------------------ presença

def atualizar(online_agora: list, agora: float = None) -> dict:
    """Concilia o estado observado com o registro. Devolve contagens."""
    ts = int(agora if agora is not None else time.time())
    vistos = {}
    for p in online_agora or []:
        userid = str(p.get("userId") or p.get("userid") or "").strip()
        if not userid:
            continue
        vistos[userid] = {
            "nome": str(p.get("name") or ""),
            "nivel": p.get("level"),
            "pos": ",".join(str(p.get(k)) for k in ("locationX", "locationY", "locationZ")
                            if p.get(k) is not None) or None,
        }

    conn = _conectar()
    resumo = {"online": len(vistos), "entraram": 0, "sairam": 0}
    with _lock:
        conhecidos = {r["userid"]: dict(r) for r in
                      conn.execute("SELECT * FROM players WHERE online=1").fetchall()}

        # entradas novas e atualizações dos que seguem online
        for userid, dados in vistos.items():
            if userid in conhecidos:
                conn.execute(
                    """UPDATE players SET nome=?, ultimo_visto=?,
                       nivel_max=MAX(COALESCE(nivel_max,-1), COALESCE(?, -1)),
                       ultima_pos=COALESCE(?, ultima_pos)
                       WHERE userid=?""",
                    (dados["nome"], ts,
                     int(dados["nivel"]) if dados["nivel"] is not None else None,
                     dados["pos"], userid))
                continue
            resumo["entraram"] += 1
            conn.execute(
                """INSERT INTO players(userid, nome, primeiro_visto, ultimo_visto,
                   sessoes, tempo_total_s, nivel_max, ultima_pos, online)
                   VALUES(?,?,?,?,
                     COALESCE((SELECT sessoes FROM players WHERE userid=?),0)+1,
                     0, ?, ?, 1)
                   ON CONFLICT(userid) DO UPDATE SET
                     nome=excluded.nome, ultimo_visto=excluded.ultimo_visto,
                     online=1, sessoes=sessoes+1""",
                (userid, dados["nome"], ts, ts, userid,
                 int(dados["nivel"]) if dados["nivel"] is not None else None,
                 dados["pos"]))
            conn.execute(
                "INSERT INTO sessoes(userid, inicio) VALUES(?,?)", (userid, ts))

        # saídas: fecham a sessão aberta e somam duração
        for userid in set(conhecidos) - set(vistos):
            resumo["sairam"] += 1
            row = conn.execute(
                "SELECT id, inicio FROM sessoes WHERE userid=? AND fim IS NULL "
                "ORDER BY id DESC LIMIT 1", (userid,)).fetchone()
            duracao = 0
            if row:
                duracao = max(0, ts - int(row["inicio"]))
                conn.execute(
                    "UPDATE sessoes SET fim=?, duracao_s=? WHERE id=?",
                    (ts, duracao, row["id"]))
            conn.execute(
                """UPDATE players SET online=0, ultimo_visto=?,
                   tempo_total_s=tempo_total_s+? WHERE userid=?""",
                (ts, duracao, userid))

        conn.commit()
    return resumo


def listar(busca: str = "", pagina: int = 1, por_pagina: int = 25,
           ordem: str = "ultimo_visto") -> dict:
    """Catálogo paginado. ordem: ultimo_visto|nome|tempo|sessoes|nivel."""
    colunas = {"ultimo_visto": "ultimo_visto DESC", "nome": "nome COLLATE NOCASE ASC",
               "tempo": "tempo_total_s DESC", "sessoes": "sessoes DESC",
               "nivel": "nivel_max DESC"}
    ordem_sql = colunas.get(ordem, colunas["ultimo_visto"])
    filtro = f"%{busca.strip()}%"
    conn = _conectar()
    with _lock:
        total = conn.execute(
            "SELECT COUNT(*) c FROM players WHERE userid LIKE ? OR nome LIKE ?",
            (filtro, filtro)).fetchone()["c"]
        rows = conn.execute(
            f"""SELECT * FROM players WHERE userid LIKE ? OR nome LIKE ?
                ORDER BY {ordem_sql} LIMIT ? OFFSET ?""",
            (filtro, filtro, max(1, min(por_pagina, 100)),
             (max(1, pagina) - 1) * por_pagina)).fetchall()
    itens = []
    for r in rows:
        d = dict(r)
        d["banido"] = esta_banido(d["userid"])
        itens.append(d)
    return {"total": total, "pagina": max(1, pagina),
            "por_pagina": por_pagina, "jogadores": itens}


def detalhe(userid: str, limite_sessoes: int = 30) -> dict | None:
    """Ficha do jogador + últimas sessões."""
    conn = _conectar()
    with _lock:
        row = conn.execute("SELECT * FROM players WHERE userid=?",
                           (userid,)).fetchone()
        if row is None:
            return None
        sessoes = [dict(s) for s in conn.execute(
            "SELECT * FROM sessoes WHERE userid=? ORDER BY inicio DESC LIMIT ?",
            (userid, limite_sessoes)).fetchall()]
    ficha = dict(row)
    ficha["banido"] = esta_banido(userid)
    ficha["ban_ativo"] = ban_ativo(userid)
    ficha["sessoes_lista"] = sessoes
    return ficha


# ------------------------------------------------------------------ bans

def registrar_ban(userid: str, nome: str = "", motivo: str = "",
                  expira_em: int = None, origem: str = "painel") -> dict:
    ts = int(time.time())
    conn = _conectar()
    with _lock:
        cur = conn.execute(
            """INSERT INTO bans(userid, nome, motivo, criado_em, expira_em, ativo, origem)
               VALUES(?,?,?,?,?,1,?)""",
            (userid, nome or None, motivo or None, ts, expira_em, origem))
        conn.execute(
            """INSERT INTO players(userid, nome, primeiro_visto, ultimo_visto)
               VALUES(?,?,?,?)
               ON CONFLICT(userid) DO UPDATE SET nome=COALESCE(NULLIF(excluded.nome,''), nome)""",
            (userid, nome or "", ts, ts))
        conn.commit()
        return {"id": cur.lastrowid, "userid": userid, "expira_em": expira_em}


def marcar_unban(userid: str, por: str = "manual") -> int:
    """Desativa bans ativos do jogador. Devolve quantos foram afetados."""
    conn = _conectar()
    with _lock:
        cur = conn.execute(
            """UPDATE bans SET ativo=0, removido_em=?, removido_por=?
               WHERE userid=? AND ativo=1""", (int(time.time()), por, userid))
        conn.commit()
    return cur.rowcount


def ban_ativo(userid: str) -> dict | None:
    conn = _conectar()
    with _lock:
        row = conn.execute(
            """SELECT * FROM bans WHERE userid=? AND ativo=1
               ORDER BY id DESC LIMIT 1""", (userid,)).fetchone()
    return dict(row) if row else None


def esta_banido(userid: str) -> bool:
    b = ban_ativo(userid)
    if not b:
        return False
    if b["expira_em"] is not None and b["expira_em"] <= time.time():
        return False  # vencido; o desbanidor automático logo confirma no jogo
    return True


def listar_bans(apenas_ativos: bool = False, busca: str = "") -> list:
    q = ("SELECT * FROM bans WHERE (userid LIKE ? OR COALESCE(nome,'') LIKE ?)"
         ) if busca else "SELECT * FROM bans"
    args: list = []
    if busca:
        filtro = f"%{busca.strip()}%"
        args = [filtro, filtro]
    if apenas_ativos:
        q += " AND" if "WHERE" in q else " WHERE"
        q += " ativo=1"
    q += " ORDER BY id DESC LIMIT 500"
    conn = _conectar()
    with _lock:
        rows = conn.execute(q, args).fetchall()
    saida = []
    for r in rows:
        d = dict(r)
        d["vencido"] = bool(d["expira_em"] and d["expira_em"] <= time.time())
        saida.append(d)
    return saida


def expirados(agora: float = None) -> list:
    """Bans temporários já vencidos que ainda constam como ativos."""
    ts = time.time() if agora is None else agora
    conn = _conectar()
    with _lock:
        rows = conn.execute(
            """SELECT * FROM bans WHERE ativo=1 AND expira_em IS NOT NULL
               AND expira_em <= ?""", (ts,)).fetchall()
    return [dict(r) for r in rows]


# ------------------------------------------------------------------ manutenção

def status_registro() -> dict:
    conn = _conectar()
    with _lock:
        j = conn.execute("SELECT COUNT(*) c FROM players").fetchone()["c"]
        on = conn.execute("SELECT COUNT(*) c FROM players WHERE online=1").fetchone()["c"]
        b = conn.execute("SELECT COUNT(*) c FROM bans WHERE ativo=1").fetchone()["c"]
    return {"jogadores": j, "online": on, "bans_ativos": b}
