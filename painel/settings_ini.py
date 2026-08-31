#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Leitura e escrita do PalWorldSettings.ini (formato da Unreal Engine).

O arquivo tem uma única linha relevante:
    OptionSettings=(Chave=Valor,Outra="texto com espaços",Lista=(A,B))
Este módulo extrai, altera e grava esses pares preservando o resto do
arquivo, além de criar/restaurar backups numerados por data.
"""

import os
import shutil
import stat
import tempfile
import time

_CABECALHO = """; Este arquivo é gerenciado pelo jogo e pelo Painel Palworld.
; Alterações manuais também funcionam, mas aplique pelo painel para
; validar valores e manter cópias de segurança automáticas.

[/Script/Pal.PalGameWorldSettings]
"""

_MARKER = "OptionSettings=("


def _dividir_topo(conteudo: str) -> list:
    """Divide no vírgula de topo, respeitando aspas e parênteses aninhados."""
    partes, atual = [], []
    profundidade = 0
    dentro_aspas = False
    i = 0
    while i < len(conteudo):
        c = conteudo[i]
        if c == '"' and (i == 0 or conteudo[i - 1] != "\\"):
            dentro_aspas = not dentro_aspas
            atual.append(c)
        elif dentro_aspas:
            atual.append(c)
        elif c == "(":
            profundidade += 1
            atual.append(c)
        elif c == ")":
            profundidade -= 1
            atual.append(c)
        elif c == "," and profundidade == 0:
            partes.append("".join(atual))
            atual = []
        else:
            atual.append(c)
        i += 1
    if atual:
        partes.append("".join(atual))
    return [p.strip() for p in partes if p.strip()]


def _partir_par(parte: str):
    """'Chave=Valor' → (chave, valor_str). Valor pode conter '=' interno."""
    chave, sep, valor = parte.partition("=")
    return chave.strip(), valor.strip()


def _valor_para_texto(valor) -> str:
    """Converte o tipo do catálogo para o formato do arquivo."""
    if isinstance(valor, bool):
        return "True" if valor else "False"
    if isinstance(valor, int):
        return str(valor)
    if isinstance(valor, float):
        return repr(valor)
    texto = str(valor)
    # listas cruas do jogo (ex.: (Steam,Xbox)) passam intactas
    if texto.startswith("(") and texto.endswith(")"):
        return texto
    # aspas quando vazio, tem separadores, ou colidiria com outro tipo ao reler
    precisa = texto == "" or any(ch in texto for ch in ' ,"\t')
    if not precisa and not isinstance(_texto_para_valor(texto), str):
        precisa = True  # ex.: ServerName=123 viraria int na releitura
    if precisa:
        return '"%s"' % texto.replace("\\", "\\\\").replace('"', '\\"')
    return texto


def _texto_para_valor(texto: str):
    """Converte o trecho bruto do arquivo p/ tipos Python básicos."""
    t = texto.strip()
    if t.startswith('"') and t.endswith('"') and len(t) >= 2:
        corpo = t[1:-1]
        return (corpo.replace('\\"', '"').replace("\\\\", "\\"))
    if t in ("True", "False"):
        return t == "True"
    if t.startswith("(") and t.endswith(")"):
        return t  # tuple cru (ex.: CrossplayPlatforms)
    try:
        return int(t)
    except ValueError:
        pass
    try:
        return float(t)
    except ValueError:
        pass
    return t


def carregar(caminho: str) -> dict:
    """Lê os pares OptionSettings. Arquivo ausente/corrompido → {}."""
    try:
        with open(caminho, encoding="utf-8") as fh:
            texto = fh.read()
    except OSError:
        return {}
    inicio = texto.find(_MARKER)
    if inicio < 0:
        return {}
    fim = texto.find(")", inicio)
    # o conteúdo nunca fecha parêntese externo antes do fim da linha;
    # procuramos o ')' que fecha o nível 0
    nivel = 0
    fim = None
    j = inicio + len(_MARKER) - 1  # aponta pro '('
    while j < len(texto):
        c = texto[j]
        if c == '"':
            j += 1
            while j < len(texto) and texto[j] != '"':
                j += 1
        elif c == "(":
            nivel += 1
        elif c == ")":
            nivel -= 1
            if nivel == 0:
                fim = j
                break
        j += 1
    if fim is None:
        return {}
    interno = texto[inicio + len(_MARKER):fim]
    valores = {}
    for parte in _dividir_topo(interno):
        chave, valor = _partir_par(parte)
        if chave:
            valores[chave] = _texto_para_valor(valor)
    return valores


def _linha_optionsettings(valores: dict, ordem_original: list) -> str:
    """Monta a linha OptionSettings=() seguindo ordem original quando houver."""
    ordenadas = [k for k in ordem_original if k in valores]
    ordenadas += [k for k in valores if k not in ordem_original]
    pares = []
    for chave in ordenadas:
        pares.append(f"{chave}={_valor_para_texto(valores[chave])}")
    return _MARKER + ",".join(pares) + ")"


def _limites_optionsettings(texto: str):
    """Devolve (início interno, fim interno) preservando o texto original."""
    marcador = texto.find(_MARKER)
    if marcador < 0:
        return None
    inicio = marcador + len(_MARKER)
    nivel = 1
    dentro_aspas = False
    escapado = False
    for pos in range(inicio, len(texto)):
        c = texto[pos]
        if dentro_aspas:
            if escapado:
                escapado = False
            elif c == "\\":
                escapado = True
            elif c == '"':
                dentro_aspas = False
            continue
        if c == '"':
            dentro_aspas = True
        elif c == "(":
            nivel += 1
        elif c == ")":
            nivel -= 1
            if nivel == 0:
                return inicio, pos
    return None


def _segmentos_topo(texto: str, inicio: int, fim: int) -> list:
    """Faixas dos pares separados por vírgula no nível superior."""
    faixas = []
    comeco = inicio
    nivel = 0
    dentro_aspas = False
    escapado = False
    for pos in range(inicio, fim):
        c = texto[pos]
        if dentro_aspas:
            if escapado:
                escapado = False
            elif c == "\\":
                escapado = True
            elif c == '"':
                dentro_aspas = False
            continue
        if c == '"':
            dentro_aspas = True
        elif c == "(":
            nivel += 1
        elif c == ")":
            nivel -= 1
        elif c == "," and nivel == 0:
            faixas.append((comeco, pos))
            comeco = pos + 1
    faixas.append((comeco, fim))
    return faixas


def _escrever_atomico(caminho: str, texto: str) -> None:
    """Substitui o arquivo preservando modo e proprietário quando existentes."""
    os.makedirs(os.path.dirname(caminho), exist_ok=True)
    try:
        anterior = os.stat(caminho)
    except OSError:
        anterior = None
    fd, tmp = tempfile.mkstemp(prefix=".PalWorldSettings.", suffix=".tmp",
                               dir=os.path.dirname(caminho))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
            if anterior:
                os.fchmod(fh.fileno(), stat.S_IMODE(anterior.st_mode))
                try:
                    os.fchown(fh.fileno(), anterior.st_uid, anterior.st_gid)
                except PermissionError:
                    pass
            fh.write(texto)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, caminho)
    finally:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass


def aplicar_mudancas(caminho: str, mudancas: dict) -> None:
    """Altera somente as chaves informadas, sem reserializar o restante."""
    if not mudancas:
        return
    try:
        with open(caminho, encoding="utf-8", newline="") as fh:
            texto = fh.read()
    except OSError:
        gravar(caminho, dict(mudancas))
        return

    limites = _limites_optionsettings(texto)
    if not limites:
        valores = carregar(caminho)
        valores.update(mudancas)
        gravar(caminho, valores)
        return

    inicio, fim = limites
    pendentes = dict(mudancas)
    substituicoes = []
    for seg_inicio, seg_fim in _segmentos_topo(texto, inicio, fim):
        bruto = texto[seg_inicio:seg_fim]
        igual = bruto.find("=")
        if igual < 0:
            continue
        chave = bruto[:igual].strip()
        if chave not in pendentes:
            continue
        valor_inicio = seg_inicio + igual + 1
        valor_fim = seg_fim
        while valor_inicio < valor_fim and texto[valor_inicio].isspace():
            valor_inicio += 1
        while valor_fim > valor_inicio and texto[valor_fim - 1].isspace():
            valor_fim -= 1
        substituicoes.append(
            (valor_inicio, valor_fim, _valor_para_texto(pendentes[chave])))
        # Se um arquivo corrompido repetir a chave, todas as ocorrências devem
        # receber o mesmo valor; a remoção fica para depois da varredura.

    chaves_encontradas = {
        texto[s:e].split("=", 1)[0].strip()
        for s, e in _segmentos_topo(texto, inicio, fim)
        if "=" in texto[s:e]
    }
    faltantes = [(k, v) for k, v in pendentes.items()
                  if k not in chaves_encontradas]
    if faltantes:
        inserir_em = fim
        while inserir_em > inicio and texto[inserir_em - 1].isspace():
            inserir_em -= 1
        conteudo = texto[inicio:inserir_em].strip()
        separador = "" if not conteudo or conteudo.endswith(",") else ","
        novos = ",".join(
            f"{chave}={_valor_para_texto(valor)}"
            for chave, valor in faltantes)
        substituicoes.append((inserir_em, inserir_em, separador + novos))

    for comeco, termino, novo in sorted(substituicoes, reverse=True):
        texto = texto[:comeco] + novo + texto[termino:]
    _escrever_atomico(caminho, texto)


def gravar(caminho: str, valores: dict) -> None:
    """Grava os valores preservando linhas fora da OptionSettings."""
    ordem_original = list(carregar(caminho).keys())
    linha_nova = _linha_optionsettings(valores, ordem_original)
    try:
        with open(caminho, encoding="utf-8") as fh:
            texto = fh.read()
    except OSError:
        texto = _CABECALHO
    linhas = texto.splitlines()
    saida = []
    substituida = False
    for linha in linhas:
        if linha.startswith(_MARKER):
            saida.append(linha_nova)
            substituida = True
        else:
            saida.append(linha)
    if not substituida:
        saida.append(linha_nova)
    _escrever_atomico(caminho, "\n".join(saida).rstrip("\n") + "\n")


# ------------------------------------------------------------------ backups

def nome_backup(quando: float = None) -> str:
    return time.strftime("config-%Y%m%d-%H%M%S.ini",
                         time.localtime(quando or time.time()))


def criar_backup(caminho_ini: str, pasta_backups: str) -> str | None:
    """Copia o INI atual p/ pasta de backups. None se não houver INI."""
    if not caminho_ini or not os.path.isfile(caminho_ini):
        return None
    os.makedirs(pasta_backups, exist_ok=True)
    destino = os.path.join(pasta_backups, nome_backup())
    # evita colisão na mesma segunda
    n = 1
    while os.path.exists(destino):
        destino = os.path.join(pasta_backups,
                               nome_backup().replace(".ini", f"-{n}.ini"))
        n += 1
    shutil.copy2(caminho_ini, destino)
    return os.path.basename(destino)


def listar_backups(pasta_backups: str) -> list:
    if not pasta_backups or not os.path.isdir(pasta_backups):
        return []
    itens = []
    for nome in sorted(os.listdir(pasta_backups)):
        if not nome.endswith(".ini"):
            continue
        caminho = os.path.join(pasta_backups, nome)
        if os.path.isfile(caminho):
            st = os.stat(caminho)
            itens.append({"nome": nome, "bytes": st.st_size,
                          "modificado": int(st.st_mtime)})
    itens.sort(key=lambda i: i["modificado"], reverse=True)
    return itens


def restaurar_backup(caminho_ini: str, pasta_backups: str, nome: str) -> bool:
    """Devolve um backup ao lugar do INI (com proteção contra traversal)."""
    if not caminho_ini:
        raise ValueError("INI não configurado")
    raiz = os.path.realpath(pasta_backups)
    alvo = os.path.realpath(os.path.join(raiz, os.path.basename(nome)))
    if not alvo.startswith(raiz + os.sep) or not os.path.isfile(alvo):
        raise FileNotFoundError("backup não encontrado")
    os.makedirs(os.path.dirname(caminho_ini), exist_ok=True)
    shutil.copy2(alvo, caminho_ini)
    return True


def ler_bruto(caminho_ini: str) -> str | None:
    try:
        with open(caminho_ini, encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return None
