#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Inicializador do Painel Palworld para Docker/CasaOS/ZimaOS.

Sincroniza a credencial usada pelo painel com a credencial interna do servidor.
O bootstrap do container Palworld grava essa credencial em um arquivo restrito
no volume compartilhado; instalações antigas também podem ser lidas diretamente
do PalWorldSettings.ini. A senha nunca é impressa nos logs.
"""

import os
import re
import runpy
import socket
import time
from urllib.parse import urlparse


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
INI_PADRAO = "/palworld/Pal/Saved/Config/LinuxServer/PalWorldSettings.ini"
SECRET_PADRAO = "/palworld/panel-admin-secret"


def _ler_admin_password_do_ini(caminho: str) -> str:
    try:
        with open(caminho, "r", encoding="utf-8", errors="replace") as fh:
            texto = fh.read()
    except OSError:
        return ""

    match = re.search(r'AdminPassword="([^"\r\n]*)"', texto)
    return match.group(1) if match else ""


def _ler_segredo(caminho: str) -> str:
    try:
        with open(caminho, "r", encoding="utf-8", errors="strict") as fh:
            return fh.read(4096).strip()
    except (OSError, UnicodeError):
        return ""


def _preparar_admin_password(espera_s: float = 30.0) -> None:
    # Valor não-vazio definido explicitamente pelo usuário sempre tem prioridade.
    if os.environ.get("ADMIN_PASSWORD"):
        return

    ini = os.environ.get("PALWORLD_INI") or INI_PADRAO
    segredo_path = os.environ.get("PALWORLD_ADMIN_SECRET_FILE") or SECRET_PADRAO
    limite = time.monotonic() + max(0.0, espera_s)

    while True:
        senha = _ler_segredo(segredo_path) or _ler_admin_password_do_ini(ini)
        if senha:
            os.environ["ADMIN_PASSWORD"] = senha
            origem = segredo_path if _ler_segredo(segredo_path) else ini
            print(f"[launcher] credencial administrativa carregada de {origem} (valor oculto).", flush=True)
            return

        if time.monotonic() >= limite:
            break
        time.sleep(0.5)

    print(
        "[launcher] AVISO: o servidor ainda não publicou a credencial administrativa. "
        "Confira os logs do container palworld-server e o arquivo "
        f"{segredo_path}.",
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
