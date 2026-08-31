#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Modo manutenção e modo emergência.

Manutenção: bandeira persistente que bloqueia as ações administrativas do
painel (restart/save/bans/backups...) exibindo um motivo — útil durante
restaurações ou atualizações manuais. Não impede jogadores de entrarem no
jogo (isso é da camada do jogo/Docker).

Emergência: ação única que encadeia anúncio → save → backup do mundo →
shutdown gracioso, impedindo operações concorrentes enquanto roda.
"""

import json
import os
import threading

DATA_DIR = os.path.abspath(os.environ.get("PANEL_DATA_DIR") or
                           os.path.join(os.path.dirname(os.path.abspath(__file__)), "data"))
ARQ_MANUTENCAO = os.path.join(DATA_DIR, "manutencao.json")

_lock_emergencia = threading.Lock()


# ------------------------------------------------------------------ manutenção

def ler_manutencao() -> dict:
    try:
        with open(ARQ_MANUTENCAO, encoding="utf-8") as fh:
            dados = json.load(fh)
        if isinstance(dados, dict):
            return {"ativo": bool(dados.get("ativo")),
                    "motivo": str(dados.get("motivo") or "")[:300],
                    "desde": dados.get("desde")}
    except (OSError, ValueError):
        pass
    return {"ativo": False, "motivo": "", "desde": None}


def definir_manutencao(ativo: bool, motivo: str = "") -> dict:
    estado = {"ativo": bool(ativo),
              "motivo": str(motivo or "")[:300],
              "desde": int(__import__("time").time()) if ativo else None}
    os.makedirs(DATA_DIR, exist_ok=True)
    fd = os.open(ARQ_MANUTENCAO, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(estado, fh)
    return estado


# ------------------------------------------------------------------ emergência

class EmergenciaEmAndamento(RuntimeError):
    pass


def _sequencia_emergencia(painel_api) -> dict:
    """Executa o fluxo de emergência usando os callbacks do painel.
    painel_api: dict com funções 'anunciar', 'salvar', 'backup', 'desligar'."""
    if not _lock_emergencia.acquire(blocking=False):
        raise EmergenciaEmAndamento("Já existe uma emergência em andamento.")
    try:
        passos = {}
        # 1. avisa
        try:
            painel_api["anunciar"]("⚠️ EMERGÊNCIA: servidor será desligado "
                                   "em 60 segundos pelo administrador.")
            passos["anuncio"] = "ok"
        except Exception as exc:
            passos["anuncio"] = str(exc)[:150]
        # 2. salva
        try:
            painel_api["salvar"]()
            passos["save"] = "ok"
        except Exception as exc:
            passos["save"] = str(exc)[:150]
        # 3. backup do mundo (pelo painel)
        try:
            bk = painel_api["backup"]()
            passos["backup"] = bk.get("nome", "ok")
        except Exception as exc:
            passos["backup"] = str(exc)[:150]
        # 4. desliga com contagem (o Docker religa se for restart pretendido;
        #    para parar de verdade, o admin para o container depois)
        try:
            painel_api["desligar"](60,
                "EMERGÊNCIA: desligamento em 60 segundos!")
            passos["shutdown"] = "ok"
        except Exception as exc:
            passos["shutdown"] = str(exc)[:150]
        return passos
    finally:
        _lock_emergencia.release()
