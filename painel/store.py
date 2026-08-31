#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Armazenamento local do painel — SQLite (só biblioteca padrão).

Guarda o histórico de métricas coletado periodicamente enquanto o servidor
de jogo responde, além de eventos internos (base do console e do audit log).
O arquivo fica em <data_dir>/painel.db em modo WAL, seguro para leitura
concorrente com o servidor web (threads).
"""

import json
import os
import sqlite3
import threading
import time

# ------------------------------------------------------------------ caminhos

DATA_DIR = os.path.abspath(os.environ.get("PANEL_DATA_DIR") or
                           os.path.join(os.path.dirname(os.path.abspath(__file__)), "data"))
DB_PATH = os.path.join(DATA_DIR, "painel.db")

SCHEMA_VERSION = 1

# janela de retenção do histórico bruto de métricas (30 dias)
METRICS_RETENTION_S = 30 * 86400

_lock = threading.Lock()
_conn = None  # criada sob demanda

# ganchos p/ notificações/auditoria registrados pelo painel principal
_ouvintes = []


def ao_evento(callback) -> None:
    """Registra callback(level, kind, message, detail) p/ cada novo evento."""
    _ouvintes.append(callback)


def _avisar_ouvintes(level: str, kind: str, message: str,
                     detail_json: str | None) -> None:
    import json as _json
    try:
        detalhe = _json.loads(detail_json) if detail_json else None
    except ValueError:
        detalhe = None
    for cb in _ouvintes:
        try:
            cb(level, kind, message, detalhe)
        except Exception:
            pass  # ouvinte quebrado nunca derruba o registro do evento


def _conectar() -> sqlite3.Connection:
    global _conn
    with _lock:
        if _conn is None:
            os.makedirs(DATA_DIR, exist_ok=True)
            _conn = sqlite3.connect(DB_PATH, check_same_thread=False)
            _conn.row_factory = sqlite3.Row
            _conn.execute("PRAGMA journal_mode=WAL")
            _conn.execute("PRAGMA synchronous=NORMAL")
            _criar_schema(_conn)
        return _conn


def _criar_schema(conn: sqlite3.Connection) -> None:
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS meta (
        chave TEXT PRIMARY KEY,
        valor TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS metrics (
        ts INTEGER PRIMARY KEY,          -- epoch seconds
        online INTEGER NOT NULL DEFAULT 0,
        fps REAL,
        frame_time REAL,
        players INTEGER,
        max_players INTEGER,
        uptime INTEGER,
        base_camps INTEGER,
        world_day INTEGER,
        extra TEXT                       -- JSON livre p/ campos futuros
    );
    CREATE TABLE IF NOT EXISTS events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts INTEGER NOT NULL,
        level TEXT NOT NULL,             -- info | warn | error
        kind TEXT NOT NULL,              -- categoria (auth, action, backup...)
        message TEXT NOT NULL,
        detail TEXT                      -- JSON opcional
    );
    CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts DESC);
    """)
    row = conn.execute("SELECT valor FROM meta WHERE chave='schema'").fetchone()
    if row is None:
        conn.execute("INSERT INTO meta(chave, valor) VALUES('schema', ?)",
                     (str(SCHEMA_VERSION),))
    conn.commit()


# ------------------------------------------------------------------ métricas

def registrar_metricas(snapshot: dict, ts: float = None) -> None:
    """Guarda uma amostra de métricas. `snapshot` vem de status_combinado().
    `ts` permite inserir amostras históricas em testes/backfill."""
    agora = int(ts if ts is not None else time.time())
    online = 1 if snapshot.get("online") else 0
    info = snapshot.get("info") or {}
    met = snapshot.get("metrics") or {}

    def _pegar(obj, *chaves):
        baixo = {k.lower(): v for k, v in obj.items()} if obj else {}
        for c in chaves:
            v = baixo.get(c.lower())
            if v is not None:
                return v
        return None

    def _num(v):
        try:
            return float(v) if v is not None else None
        except (TypeError, ValueError):
            return None

    def _int(v):
        n = _num(v)
        return int(n) if n is not None else None

    extra = {}
    if info:
        extra["server_name"] = _pegar(info, "servername", "name")
        extra["version"] = _pegar(info, "version")
    conn = _conectar()
    with _lock:
        conn.execute(
            """INSERT OR REPLACE INTO metrics
               (ts, online, fps, frame_time, players, max_players,
                uptime, base_camps, world_day, extra)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (agora, online,
             _num(_pegar(met, "serverfps", "serverfpsaverage")),
             _num(_pegar(met, "serverframetime")),
             _int(_pegar(met, "currentplayernum")),
             _int(_pegar(met, "maxplayernum")),
             _int(_pegar(met, "uptime")),
             _int(_pegar(met, "basecampnum")),
             _int(_pegar(met, "days")),
             json.dumps(extra, ensure_ascii=False) if extra else None))
        # poda: mantém a janela de retenção
        conn.execute("DELETE FROM metrics WHERE ts < ?",
                     (agora - METRICS_RETENTION_S,))
        conn.commit()


def consultar_metricas(inicio_ts: int, fim_ts: int = None) -> list:
    """Amostras no intervalo [inicio, fim], ordenadas pelo tempo."""
    fim = int(fim_ts if fim_ts is not None else time.time())
    conn = _conectar()
    with _lock:
        rows = conn.execute(
            "SELECT * FROM metrics WHERE ts BETWEEN ? AND ? ORDER BY ts ASC",
            (int(inicio_ts), int(fim))).fetchall()
    return [dict(r) for r in rows]


def resumo_metricas(janela_s: int, fim_ts: float = None) -> dict:
    """Agregados da janela p/ os cartões do dashboard."""
    fim = int(fim_ts if fim_ts is not None else time.time())
    inicio = fim - int(janela_s)
    conn = _conectar()
    with _lock:
        row = conn.execute(
            """SELECT COUNT(*) AS amostras,
                      SUM(online) AS online_amostras,
                      MIN(fps) AS fps_min, MAX(fps) AS fps_max,
                      AVG(fps) AS fps_med,
                      MAX(players) AS players_max,
                      AVG(frame_time) AS frame_med
               FROM metrics WHERE ts BETWEEN ? AND ?""",
            (inicio, fim)).fetchone()
    d = dict(row)
    total = d.get("amostras") or 0
    online = d.get("online_amostras") or 0
    d["pct_online"] = round(100 * online / total, 1) if total else None
    for chave in ("fps_min", "fps_max", "fps_med", "frame_med"):
        if d.get(chave) is not None:
            d[chave] = round(d[chave], 1)
    return d


def ultimo_evento(kind: str = None) -> dict | None:
    """Evento mais recente de uma categoria (None se nunca houve)."""
    q = "SELECT ts, level, kind, message FROM events"
    args: list = []
    if kind:
        q += " WHERE kind=? AND message NOT LIKE 'Painel iniciado%'"
        args.append(kind)
    else:
        q += " WHERE message NOT LIKE 'Painel iniciado%'"
    q += " ORDER BY id DESC LIMIT 1"
    conn = _conectar()
    with _lock:
        row = conn.execute(q, args).fetchone()
    return dict(row) if row else None


def ultima_metrica() -> dict | None:
    conn = _conectar()
    with _lock:
        row = conn.execute(
            "SELECT * FROM metrics ORDER BY ts DESC LIMIT 1").fetchone()
    return dict(row) if row else None


# ------------------------------------------------------------------ eventos

def registrar_evento(level: str, kind: str, message: str,
                     detail=None) -> None:
    """Registra um evento interno (console/auditoria). level: info|warn|error."""
    if level not in ("info", "warn", "error"):
        level = "info"
    conn = _conectar()
    with _lock:
        conn.execute(
            "INSERT INTO events (ts, level, kind, message, detail) VALUES (?,?,?,?,?)",
            (int(time.time()), level, kind, str(message)[:2000],
             json.dumps(detail, ensure_ascii=False) if detail else None))
        conn.commit()
    _avisar_ouvintes(level, kind, str(message)[:2000],
                     json.dumps(detail, ensure_ascii=False) if detail else None)


def consultar_eventos(limite: int = 500, level: str = None,
                      kinds: list = None, desde_ts: int = None) -> list:
    """Eventos recentes, mais novos primeiro."""
    q = "SELECT * FROM events WHERE 1=1"
    args: list = []
    if level in ("info", "warn", "error"):
        q += " AND level=?"
        args.append(level)
    if kinds:
        q += f" AND kind IN ({','.join('?' * len(kinds))})"
        args.extend(kinds)
    if desde_ts:
        q += " AND ts>=?"
        args.append(int(desde_ts))
    q += " ORDER BY id DESC LIMIT ?"
    args.append(max(1, min(int(limite), 5000)))
    conn = _conectar()
    with _lock:
        rows = conn.execute(q, args).fetchall()
    return [dict(r) for r in rows]


# ------------------------------------------------------------------ manutenção

def status_banco() -> dict:
    """Info básica para o diagnóstico do painel."""
    try:
        conn = _conectar()
        with _lock:
            m = conn.execute("SELECT COUNT(*) c FROM metrics").fetchone()["c"]
            e = conn.execute("SELECT COUNT(*) c FROM events").fetchone()["c"]
        tamanho = os.path.getsize(DB_PATH) if os.path.isfile(DB_PATH) else 0
        return {"ok": True, "metricas": m, "eventos": e, "bytes": tamanho}
    except sqlite3.Error as exc:
        return {"ok": False, "erro": str(exc)}
