#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Backups do mundo gerenciados pelo painel.

A imagem do servidor tem seu próprio backup por cron; aqui o painel oferece:

- backup sob demanda: zip da pasta SaveGames para o BACKUP_DIR;
- exclusão individual à prova de traversal;
- retenção configurável (manter N mais recentes / apagar acima de X dias);
- restauração segura: valida o zip contra path traversal, guarda o estado
  atual como <save>.pre-restauro-<data> e só então troca os arquivos.
"""

import os
import shutil
import threading
import time
import zipfile


class BackupError(Exception):
    pass


_PREFIXO = "palworld-painel-"

# restauração é operação crítica: uma por vez, em todo o processo
_restauro_lock = threading.Lock()


def _validar_caminho_no_zip(info: zipfile.ZipInfo) -> None:
    nome = info.filename.replace("\\", "/")
    if nome.startswith("/") or ".." in nome.split("/"):
        raise BackupError(f"entrada suspeita no zip: {info.filename}")
    if info.file_size > 4 * 1024 ** 3:  # 4 GB por arquivo
        raise BackupError(f"arquivo grande demais no zip: {info.filename}")


def _pasta_save_disponivel(save_dir: str) -> str | None:
    if not save_dir or not os.path.isdir(save_dir):
        return None
    return os.path.realpath(save_dir)


def criar_backup_mundo(save_dir: str, backup_dir: str) -> dict:
    """Zip do conteúdo de save_dir em backup_dir. Devolve nome/tamanho."""
    raiz_save = _pasta_save_disponivel(save_dir)
    if not raiz_save:
        raise BackupError(
            "Pasta de saves não encontrada — defina PALWORLD_SAVE_DIR "
            "(ex.: /palworld/Pal/Saved/SaveGames).")
    if not backup_dir:
        raise BackupError("BACKUP_DIR não configurado.")
    os.makedirs(backup_dir, exist_ok=True)
    nome = f"{_PREFIXO}{time.strftime('%Y%m%d-%H%M%S')}.zip"
    destino = os.path.join(backup_dir, nome)
    arquivos = 0
    with zipfile.ZipFile(destino, "w", zipfile.ZIP_DEFLATED) as zf:
        for base, _dirs, nomes in os.walk(raiz_save):
            for nome_arq in nomes:
                caminho = os.path.join(base, nome_arq)
                relativo = os.path.relpath(caminho, raiz_save)
                try:
                    zf.write(caminho, arcname=relativo)
                    arquivos += 1
                except OSError:
                    continue  # arquivo sumiu durante a cópia
    return {"nome": nome, "bytes": os.path.getsize(destino),
            "arquivos": arquivos}


def caminho_seguro(backup_dir: str, nome: str) -> str:
    """Resolve o arquivo dentro de backup_dir rejeitando traversal."""
    if not backup_dir:
        raise BackupError("BACKUP_DIR não configurado.")
    raiz = os.path.realpath(backup_dir)
    alvo = os.path.realpath(os.path.join(raiz, os.path.basename(nome)))
    if not alvo.startswith(raiz + os.sep) or not os.path.isfile(alvo):
        raise FileNotFoundError("Backup não encontrado.")
    return alvo


def excluir_backup(backup_dir: str, nome: str) -> None:
    alvo = caminho_seguro(backup_dir, nome)
    os.remove(alvo)


def retencao(backup_dir: str, manter_ultimos: int = 10,
             idade_dias: int = 30) -> dict:
    """Aplica política de retenção sobre os .zip do BACKUP_DIR."""
    if not backup_dir or not os.path.isdir(backup_dir):
        return {"apagados": [], "nota": "pasta indisponível"}
    agora = time.time()
    zips = []
    for nome in os.listdir(backup_dir):
        if not nome.lower().endswith(".zip"):
            continue
        caminho = os.path.join(backup_dir, nome)
        try:
            st = os.stat(caminho)
            zips.append((nome, st.st_mtime))
        except OSError:
            continue
    zips.sort(key=lambda x: x[1], reverse=True)  # mais novos primeiro
    apagados = []
    limite_idade = agora - max(0, int(idade_dias)) * 86400
    for posicao, (nome, mtime) in enumerate(zips):
        velho_demais = mtime < limite_idade
        fora_do_topo = posicao >= max(1, int(manter_ultimos))
        if velho_demais or fora_do_topo:
            try:
                os.remove(os.path.join(backup_dir, nome))
                apagados.append(nome)
            except OSError:
                continue
    return {"apagados": apagados, "total_restante": len(zips) - len(apagados)}


def restaurar_mundo(backup_dir: str, nome: str, save_dir: str) -> dict:
    """Troca SaveGames pelo zip escolhido, preservando o estado atual.

    Fluxo: valida zip (sem traversal, tamanho ok) → extrai em pasta irmã
    temporária → renomeia o save atual p/ .pre-restauro-<data> → promove a
    extração. O chamador deve reiniciar o servidor depois.
    Operação serializada: dois restores simultâneos corromperiam o save."""
    with _restauro_lock:
        return _restaurar_mundo_impl(backup_dir, nome, save_dir)


def _restaurar_mundo_impl(backup_dir: str, nome: str, save_dir: str) -> dict:
    raiz_save = _pasta_save_disponivel(save_dir)
    if not raiz_save:
        raise BackupError(
            "Pasta de saves não encontrada — restauro indisponível sem "
            "PALWORLD_SAVE_DIR.")
    origem = caminho_seguro(backup_dir, nome)

    pai = os.path.dirname(raiz_save)
    raiz_temporaria = os.path.join(
        pai, f".restauro-{time.strftime('%Y%m%d-%H%M%S')}")
    os.makedirs(raiz_temporaria, exist_ok=True)
    extracao = raiz_temporaria
    total = 0
    try:
        with zipfile.ZipFile(origem) as zf:
            infos = zf.infolist()
            for info in infos:
                _validar_caminho_no_zip(info)
            zf.extractall(extracao)
            total = len(infos)

        # Se TODAS as entradas vivem sob uma única pasta-raiz que tem o MESMO
        # nome da pasta destino (zip externo estilo "SaveGames/…"), descemos
        # um nível. Zips gerados por este módulo já são relativos à raiz.
        nomes = [i.filename.replace("\\", "/").split("/")[0]
                 for i in infos if i.filename.strip()]
        nome_destino = os.path.basename(os.path.normpath(raiz_save))
        if nomes and len(set(nomes)) == 1 and nomes[0] == nome_destino:
            unico = os.path.join(extracao, nomes[0])
            if os.path.isdir(unico):
                extracao = unico

        guardado = f"{raiz_save}.pre-restauro-{time.strftime('%Y%m%d-%H%M%S')}"
        n = 1
        while os.path.exists(guardado):
            n += 1
            guardado = f"{raiz_save}.pre-restauro-{time.strftime('%Y%m%d-%H%M%S')}-{n}"
        os.rename(raiz_save, guardado)
        try:
            shutil.copytree(extracao, raiz_save)
        except OSError:
            # desfaz: devolve o save original ao lugar
            os.rename(guardado, raiz_save)
            raise
    finally:
        shutil.rmtree(raiz_temporaria, ignore_errors=True)

    return {"restaurado": nome, "arquivos_extraidos": total,
            "anterior_preservado_em": os.path.basename(guardado),
            "aviso": "REINICIE o servidor para carregar o mundo restaurado."}
