#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Agendador de tarefas do painel (só biblioteca padrão).

Jobs ficam no SQLite e uma thread os verifica a cada tick. Tipos suportados:

- restart          → anuncia e desliga com contagem (o Docker religa sozinho)
- save             → save forçado do mundo
- announce         → anúncio no chat
- backup_mundo     → zip da pasta de save pelo painel
- limpeza_backups  → retenção dos zips antigos (manter N / idade máxima)

Agendamento: `hora` fixa diária ("03:00") ou `intervalo_horas` (a partir do
último disparo). Tudo registrável no console/auditoria.
"""

import json
import os
import sqlite3
import threading
import time
from datetime import datetime, timedelta

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
DB_PATH = os.path.join(DATA_DIR, "painel.db")

TIPOS = ("restart", "save", "announce", "backup_mundo", "limpeza_backups")

_lock = threading.Lock()
_conn = None

# callback injetado pelo painel.py p/ executar ações que precisam da API
_executores = {}


def registrar_executor(tipo: str, funcao) -> None:
    _executores[tipo] = funcao


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
    CREATE TABLE IF NOT EXISTS jobs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nome TEXT NOT NULL,
        tipo TEXT NOT NULL,
        habilitado INTEGER NOT NULL DEFAULT 1,
        hora TEXT,                     -- "HH:MM" diário (ou NULL)
        intervalo_horas REAL,          -- ou intervalo em horas (ou NULL)
        params TEXT,                   -- JSON por tipo
        ultimo_run INTEGER,
        proximo_run INTEGER NOT NULL,
        ultimo_resultado TEXT
    );
    """)
    conn.commit()


# ------------------------------------------------------------- cálculo de agenda

def _proximo_disparo(hora: str = None, intervalo_horas: float = None,
                     base_ts: float = None) -> int:
    """Próximo timestamp: hoje no horário HH:MM (ou amanhã se já passou),
    ou base + intervalo."""
    agora = base_ts if base_ts is not None else time.time()
    if hora:
        try:
            hh, mm = (int(x) for x in str(hora).split(":")[:2])
            if not (0 <= hh < 24 and 0 <= mm < 60):
                raise ValueError
        except (ValueError, TypeError):
            return int(agora) + 3600  # horário inválido: tenta em 1h
        alvo = datetime.fromtimestamp(agora).replace(
            hour=hh, minute=mm, second=0, microsecond=0)
        if alvo.timestamp() <= agora:
            alvo += timedelta(days=1)
        return int(alvo.timestamp())
    if intervalo_horas and float(intervalo_horas) > 0:
        return int(agora + float(intervalo_horas) * 3600)
    return int(agora) + 86400  # nada configurado: reavalia em um dia


# ------------------------------------------------------------------ CRUD

def listar(apenas_habilitados: bool = False) -> list:
    q = "SELECT * FROM jobs"
    if apenas_habilitados:
        q += " WHERE habilitado=1"
    q += " ORDER BY proximo_run ASC"
    conn = _conectar()
    with _lock:
        rows = conn.execute(q).fetchall()
    return [_job_dict(r) for r in rows]


def _job_dict(r: sqlite3.Row) -> dict:
    d = dict(r)
    try:
        d["params"] = json.loads(d.get("params") or "{}")
    except (ValueError, TypeError):
        d["params"] = {}
    return d


def obter(job_id: int):
    conn = _conectar()
    with _lock:
        row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    return _job_dict(row) if row else None


def validar_job(payload: dict) -> tuple:
    """Devolve (ok, dados_normalizados|motivo_erro)."""
    nome = str(payload.get("nome") or "").strip()[:80]
    tipo = str(payload.get("tipo") or "").strip()
    if not nome:
        return False, "nome obrigatório"
    if tipo not in TIPOS:
        return False, f"tipo deve ser um de: {', '.join(TIPOS)}"
    hora = payload.get("hora")
    intervalo = payload.get("intervalo_horas")
    if not hora and not intervalo:
        return False, "defina 'hora' (HH:MM) ou 'intervalo_horas'"
    if hora is not None:
        hora = str(hora).strip()
        try:
            hh, mm = (int(x) for x in hora.split(":"))
            if not (0 <= hh < 24 and 0 <= mm < 60):
                raise ValueError
        except (ValueError, TypeError):
            return False, "hora inválida (use HH:MM)"
    try:
        intervalo = float(intervalo) if intervalo else None
    except (TypeError, ValueError):
        return False, "intervalo_horas inválido"
    if intervalo is not None and not (0.05 <= intervalo <= 8760):
        return False, "intervalo_horas fora da faixa (0.05–8760)"
    params = payload.get("params") or {}
    if not isinstance(params, dict):
        return False, "params deve ser objeto"
    if tipo == "restart":
        wt = params.get("waittime", 300)
        try:
            params["waittime"] = max(15, min(int(wt), 600))
        except (TypeError, ValueError):
            return False, "waittime inválido"
    if tipo == "announce":
        msg = str(params.get("message") or "").strip()
        if not msg:
            return False, "announce exige params.message"
        params["message"] = msg[:500]
    if tipo == "limpeza_backups":
        params.setdefault("manter_ultimos", 10)
        params.setdefault("idade_dias", 30)
        try:
            params["manter_ultimos"] = max(1, min(int(params["manter_ultimos"]), 500))
            params["idade_dias"] = max(0, min(int(params["idade_dias"]), 3650))
        except (TypeError, ValueError):
            return False, "parâmetros de limpeza inválidos"
    return True, {"nome": nome, "tipo": tipo,
                  "habilitado": 1 if payload.get("habilitado", True) else 0,
                  "hora": hora or None,
                  "intervalo_horas": intervalo,
                  "params": json.dumps(params, ensure_ascii=False)}


def criar(payload: dict):
    ok, dados = validar_job(payload)
    if not ok:
        return None, dados
    conn = _conectar()
    with _lock:
        cur = conn.execute(
            """INSERT INTO jobs(nome, tipo, habilitado, hora, intervalo_horas,
               params, proximo_run) VALUES(?,?,?,?,?,?,?)""",
            (dados["nome"], dados["tipo"], dados["habilitado"], dados["hora"],
             dados["intervalo_horas"], dados["params"],
             _proximo_disparo(dados["hora"], dados["intervalo_horas"])))
        conn.commit()
    return obter(cur.lastrowid), None


def atualizar(job_id: int, payload: dict):
    existente = obter(job_id)
    if not existente:
        return None, "job não encontrado"
    mesclado = {**existente, **payload}
    ok, dados = validar_job(mesclado)
    if not ok:
        return None, dados
    recalcular = any(k in payload for k in
                     ("hora", "intervalo_horas", "habilitado"))
    conn = _conectar()
    with _lock:
        conn.execute(
            """UPDATE jobs SET nome=?, tipo=?, habilitado=?, hora=?,
               intervalo_horas=?, params=? WHERE id=?""",
            (dados["nome"], dados["tipo"], dados["habilitado"], dados["hora"],
             dados["intervalo_horas"], dados["params"], job_id))
        if recalcular:
            novo = (_proximo_disparo(dados["hora"], dados["intervalo_horas"])
                    if dados["habilitado"] else int(time.time()) + 86400 * 365)
            conn.execute("UPDATE jobs SET proximo_run=? WHERE id=?",
                         (novo, job_id))
        conn.commit()
    return obter(job_id), None


def excluir(job_id: int) -> bool:
    conn = _conectar()
    with _lock:
        cur = conn.execute("DELETE FROM jobs WHERE id=?", (job_id,))
        conn.commit()
    return cur.rowcount > 0


def marcar_executado(job_id: int, resultado: str, sucesso: bool = True) -> None:
    job = obter(job_id)
    if not job:
        return
    proximo = _proximo_disparo(job["hora"], job["intervalo_horas"])
    conn = _conectar()
    with _lock:
        conn.execute(
            """UPDATE jobs SET ultimo_run=?, proximo_run=?, ultimo_resultado=?
               WHERE id=?""",
            (int(time.time()), proximo,
             ("ok: " if sucesso else "falhou: ") + str(resultado)[:200],
             job_id))
        conn.commit()


def executar_agora(job_id: int) -> tuple:
    """Dispara imediatamente (usado pelo botão 'executar agora')."""
    job = obter(job_id)
    if not job:
        return False, "job não encontrado"
    executor = _executores.get(job["tipo"])
    if not executor:
        return False, f"sem executor para '{job['tipo']}'"
    try:
        resultado = executor(job["params"])
        marcar_executado(job_id, resultado or "manual", sucesso=True)
        return True, resultado or "ok"
    except Exception as exc:  # falha registrada, agenda tenta de novo depois
        marcar_executado(job_id, str(exc), sucesso=False)
        return False, str(exc)


# ------------------------------------------------------------------ loop

_tick_s = 20


def _vencidos(agora: float = None) -> list:
    ts = time.time() if agora is None else agora
    conn = _conectar()
    with _lock:
        rows = conn.execute(
            "SELECT id FROM jobs WHERE habilitado=1 AND proximo_run<=?",
            (ts,)).fetchall()
    return [r["id"] for r in rows]


def laco_agendador() -> None:
    """Thread daemon principal do agendador."""
    while True:
        try:
            for job_id in _vencidos():
                ok, resultado = executar_agora(job_id)
                job = obter(job_id)
                nome = job["nome"] if job else f"#{job_id}"
                print(f"[AGENDA] {nome}: {resultado}")
        except Exception as exc:  # nunca derruba o laço
            print(f"[ERRO] agendador: {exc!r}")
        time.sleep(_tick_s)


def iniciar() -> None:
    threading.Thread(target=laco_agendador, daemon=True,
                     name="agendador").start()
