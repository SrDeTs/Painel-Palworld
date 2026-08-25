#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Inicializador do Painel Palworld para Docker/CasaOS/ZimaOS.

Antes de carregar o painel, tenta aproveitar a senha administrativa já gravada
no PalWorldSettings.ini. Isso evita um caso comum em instalações existentes:
o servidor tem AdminPassword configurada no save/INI, mas a variável
ADMIN_PASSWORD do container do painel ficou vazia.

A senha nunca é impressa nos logs.
"""

import os
import re
import runpy
import socket
from urllib.parse import urlparse


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
INI_PADRAO = "/palworld/Pal/Saved/Config/LinuxServer/PalWorldSettings.ini"


def _ler_admin_password_do_ini(caminho: str) -> str:
    try:
        with open(caminho, "r", encoding="utf-8", errors="replace") as fh:
            texto = fh.read()
    except OSError:
        return ""

    # PalWorldSettings.ini normalmente guarda tudo em OptionSettings=(...).
    # AdminPassword é serializada como AdminPassword="valor".
    match = re.search(r'AdminPassword="([^"\r\n]*)"', texto)
    return match.group(1) if match else ""


def _preparar_admin_password() -> None:
    # Valor não-vazio definido pelo usuário sempre tem prioridade.
    if os.environ.get("ADMIN_PASSWORD"):
        return

    ini = os.environ.get("PALWORLD_INI") or INI_PADRAO
    senha = _ler_admin_password_do_ini(ini)
    if senha:
        os.environ["ADMIN_PASSWORD"] = senha
        print(f"[launcher] ADMIN_PASSWORD carregada de {ini} (valor oculto).", flush=True)
    else:
        print(
            "[launcher] AVISO: ADMIN_PASSWORD está vazia e não foi encontrada "
            f"em {ini}. Defina a mesma senha administrativa no servidor e no painel.",
            flush=True,
        )


def _diagnosticar_rede() -> None:
    alvo = os.environ.get("PALWORLD_API") or "http://palworld:8212"
    parsed = urlparse(alvo)
    host = parsed.hostname or "palworld"
    port = parsed.port or 8212
    try:
        socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        print(f"[launcher] REST API alvo: {host}:{port} (DNS resolvido).", flush=True)
    except OSError as exc:
        # Não aborta: o servidor pode ainda estar iniciando. O painel se recupera
        # automaticamente assim que a API ficar disponível.
        print(
            f"[launcher] AVISO: ainda não foi possível resolver {host}:{port}: {exc}",
            flush=True,
        )


if __name__ == "__main__":
    _preparar_admin_password()
    _diagnosticar_rede()
    runpy.run_path(os.path.join(SCRIPT_DIR, "painel.py"), run_name="__main__")
