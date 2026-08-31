#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Notificações configuráveis — Discord, Telegram e webhook genérico.

A configuração vive em data/notifications.json (fora do Git). Os segredos
nunca voltam inteiros pela API do painel: a listagem mascara os valores e
só sobrescreve quando o usuário envia um novo.

Eventos suportados (chaves): server_down, server_up, ban, kick, backup,
restore, settings, scheduler, erro.
Cada canal pode filtrar quais eventos interessam (`eventos: [...]` vazio =
todos).
"""

import json
import os
import threading
import urllib.request

DATA_DIR = os.path.abspath(os.environ.get("PANEL_DATA_DIR") or
                           os.path.join(os.path.dirname(os.path.abspath(__file__)), "data"))
ARQUIVO = os.path.join(DATA_DIR, "notifications.json")

EVENTOS = ("server_down", "server_up", "ban", "kick", "backup",
           "restore", "settings", "scheduler", "erro")

_lock = threading.Lock()
_maska = "\u2022" * 8


def _padrao() -> dict:
    return {"canais": []}


def carregar() -> dict:
    try:
        with open(ARQUIVO, encoding="utf-8") as fh:
            dados = json.load(fh)
        if isinstance(dados, dict) and isinstance(dados.get("canais"), list):
            return dados
    except (OSError, ValueError):
        pass
    return _padrao()


def salvar(config: dict) -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    fd = os.open(ARQUIVO, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(config, fh, ensure_ascii=False, indent=2)


def mascarado(config: dict | None = None) -> dict:
    """Versão segura p/ devolver ao navegador: segredos viram ••••••••."""
    cfg = config if config is not None else carregar()
    saida = {"canais": []}
    for c in cfg.get("canais", []):
        copia = dict(c)
        for chave in ("token", "webhook_url"):
            if copia.get(chave):
                copia[chave] = _maska
        saida["canais"].append(copia)
    return saida


def mesclar_novos(config_nova: dict) -> dict:
    """Aplica mudanças preservando segredos mascarados não tocados."""
    atual = carregar()
    canais_atuais = {c.get("id"): c for c in atual.get("canais", [])}
    novos = []
    for c in config_nova.get("canais", []):
        c = dict(c)
        cid = c.get("id")
        antigo = canais_atuais.get(cid, {})
        for chave in ("token", "webhook_url"):
            if c.get(chave) in (_maska, "", None) and antigo.get(chave):
                c[chave] = antigo[chave]  # veio mascarado → mantém o real
        novos.append(c)
    salvar({"canais": novos})
    return {"canais": novos}


# ------------------------------------------------------------------ envio

def _post_json(url: str, payload: dict, timeout: float = 6.0) -> tuple:
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status == 200 or 200 <= resp.status < 300, ""
    except Exception as exc:
        return False, str(exc)


def _enviar_canal(canal: dict, titulo: str, mensagem: str) -> tuple:
    tipo = canal.get("tipo")
    if tipo == "discord":
        url = canal.get("webhook_url") or ""
        if not url.startswith("https://discord.com/api/webhooks/") and \
           not url.startswith("https://ptb.discord.com/api/webhooks/") and \
           not url.startswith("https://canary.discord.com/api/webhooks/"):
            return False, "URL de webhook do Discord inválida"
        ok, err = _post_json(url, {
            "username": canal.get("nome") or "Painel Palworld",
            "content": f"**{titulo}**\n{mensagem}"[:2000]})
        return ok, err
    if tipo == "telegram":
        token = canal.get("token") or ""
        chat_id = canal.get("chat_id") or ""
        if not token or not chat_id:
            return False, "Telegram exige token e chat_id"
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        return _post_json(url, {"chat_id": chat_id,
                                "text": f"{titulo}\n{mensagem}"[:4000]})
    if tipo == "webhook":
        url = canal.get("webhook_url") or ""
        if not url.startswith(("http://", "https://")):
            return False, "URL de webhook inválida"
        return _post_json(url, {"evento": titulo, "mensagem": mensagem})
    return False, f"tipo de canal desconhecido: {tipo}"


def despachar(evento: str, titulo: str, mensagem: str = "") -> None:
    """Envia aos canais inscritos no evento. Nunca levanta exceção."""
    cfg = carregar()
    for canal in cfg.get("canais", []):
        if not canal.get("habilitado", True):
            continue
        filtro = canal.get("eventos") or []
        if filtro and evento not in filtro:
            continue
        def _enviar(canal=canal):
            ok, err = _enviar_canal(canal, titulo, mensagem)
            if not ok:
                print(f"[NOTIFICACAO] {canal.get('tipo')} falhou: {err}")
        threading.Thread(target=_enviar, daemon=True).start()


def testar(canal_id: str) -> tuple:
    """Botão 'testar' da UI: dispara mensagem de teste num canal específico."""
    cfg = carregar()
    for canal in cfg.get("canais", []):
        if canal.get("id") == canal_id:
            ok, err = _enviar_canal(
                canal, "Teste do Painel Palworld",
                "Se você leu isso, as notificações estão funcionando! 🐑")
            return ok, err
    return False, "canal não encontrado"
