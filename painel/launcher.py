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
    # O segredo é publicado pelo bootstrap do servidor depois que ele decide
    # qual senha realmente ficou ativa (env, INI existente ou senha gerada).
    # Por isso ele é a fonte de verdade. Isso também evita a armadilha comum do
    # CasaOS: editar ADMIN_PASSWORD somente no serviço Painel e deixar o jogo
    # usando a senha que já estava salva no PalWorldSettings.ini.
    senha_explicita = (os.environ.get("ADMIN_PASSWORD") or "").strip()
    ini = os.environ.get("PALWORLD_INI") or INI_PADRAO
    segredo_path = os.environ.get("PALWORLD_ADMIN_SECRET_FILE") or SECRET_PADRAO
    limite = time.monotonic() + max(0.0, espera_s)

    while True:
        senha_segredo = _ler_segredo(segredo_path)
        if senha_segredo:
            os.environ["ADMIN_PASSWORD"] = senha_segredo
            if senha_explicita and senha_explicita != senha_segredo:
                print(
                    "[launcher] AVISO: ADMIN_PASSWORD do Painel era diferente "
                    "da credencial ativa publicada pelo servidor; usando o "
                    "segredo compartilhado (valores ocultos).",
                    flush=True,
                )
            else:
                print(
                    f"[launcher] credencial administrativa sincronizada por "
                    f"{segredo_path} (valor oculto).",
                    flush=True,
                )
            return

        # Sem um valor explícito, instalações antigas ainda podem obter a senha
        # diretamente do INI enquanto o arquivo de segredo não existe.
        if not senha_explicita:
            senha_ini = _ler_admin_password_do_ini(ini)
            if senha_ini:
                os.environ["ADMIN_PASSWORD"] = senha_ini
                print(
                    f"[launcher] credencial administrativa carregada de {ini} "
                    "(valor oculto).",
                    flush=True,
                )
                return

        if time.monotonic() >= limite:
            break
        time.sleep(0.5)

    if senha_explicita:
        # Compatibilidade com instalações onde os dois serviços não montam o
        # mesmo volume. Uma eventual divergência aparecerá na aba Saúde.
        os.environ["ADMIN_PASSWORD"] = senha_explicita
        print(
            "[launcher] AVISO: o servidor não publicou o segredo compartilhado; "
            "mantendo ADMIN_PASSWORD do Painel (valor oculto).",
            flush=True,
        )
        return

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
