#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Lógica do editor de configurações: junta catálogo, arquivo INI e API do jogo.

Regras:
- Valores efetivos = padrões oficiais ∪ valores lidos do INI (∪ API p/ exibição).
- Toda alteração passa pela validação do catálogo antes de tocar o disco.
- Aplicar SEMPRE cria um backup do INI anterior e devolve o nome dele.
- Senhas nunca voltam por JSON — só o marcador de preenchido/vazio.
"""

import json
import os

import settings_catalog as sc
import settings_ini as si

_MASKA = "\u2022\u2022\u2022\u2022\u2022\u2022"  # ••••••


def _mascarar(chave: str, valor):
    if chave in sc.chaves_sensíveis():
        return _MASKA if valor not in ("", None, False) else ""
    return valor


def estado(ini_path: str, api_settings: dict | None) -> dict:
    """Snapshot completo para montar o editor no frontend."""
    configurado = bool(ini_path) and os.path.isfile(ini_path)
    valores_ini = si.carregar(ini_path) if configurado else {}
    da_api = {}
    if isinstance(api_settings, dict):
        da_api = {k: v for k, v in api_settings.items()
                  if k not in valores_ini}

    catalogo = sc.catalogo()
    valores = {}
    fontes = {}
    for item in catalogo:
        chave = item["chave"]
        if chave in valores_ini:
            valores[chave] = _mascarar(chave, valores_ini[chave])
            fontes[chave] = "ini"
        elif chave in da_api and chave not in sc.chaves_sensíveis():
            valores[chave] = da_api[chave]
            fontes[chave] = "api"
        else:
            valores[chave] = ""
            fontes[chave] = "padrao"
        if valores.get(chave) == "":
            valores[chave] = _mascarar(chave, item["padrao"])
            if item["padrao"] != "":
                fontes[chave] = "padrao"

    return {
        "configurado": bool(ini_path),
        "ini_existe": configurado,
        "catalogo": catalogo,
        "valores": valores,
        "fontes": fontes,
        "categorias": [{"id": c, "rotulo": sc.ROTULO_CATEGORIA[c]}
                       for c in sc.CATEGORIAS_ORDEM],
    }


def validar_mudancas(mudancas: dict, valores_atuais: dict) -> tuple:
    """Devolve (validas[], invalidas[]) normalizadas."""
    validas, invalidas = [], []
    for chave, bruto in (mudancas or {}).items():
        ok, valor, motivo = sc.validar(chave, bruto)
        if not ok:
            invalidas.append({"chave": chave, "motivo": motivo})
            continue
        atual = valores_atuais.get(chave)
        # comparação textual estável entre tipos
        mudou = str(atual) != str(valor) and atual != valor
        if chave in sc.chaves_sensíveis() and (
                bruto in ("", None) or bruto == _MASKA):
            continue  # campo de senha tocado sem novo valor → ignora
        if not mudou:
            continue
        validas.append({"chave": chave, "de": atual, "para": valor})
    return validas, invalidas


def _mesclar_valores(ini_path: str, mudancas_validas: list) -> dict:
    """Valores atuais do INI (+padrões faltantes p/ chaves novas) + mudanças."""
    base = si.carregar(ini_path)
    for item in sc.catalogo():
        base.setdefault(item["chave"], item["padrao"])
    for m in mudancas_validas:
        base[m["chave"]] = m["para"]
    return base


def aplicar(ini_path: str, pasta_backups: str, mudancas: dict,
            valores_exibidos: dict) -> dict:
    """Valida → backup → grava. Devolve resumo p/ confirmar na UI."""
    if not ini_path:
        raise PermissionError(
            "Edição indisponível: defina PALWORLD_INI apontando para o "
            "PalWorldSettings.ini (monte o volume do jogo no container do painel).")
    validas, invalidas = validar_mudancas(mudancas, valores_exibidos)
    resultado = {"ok": not invalidas, "invalidas": invalidas,
                 "aplicadas": [], "backup": None,
                 "aviso": "As novas configurações valem após REINICIAR o "
                          "servidor de jogo."}
    if invalidas:
        return resultado
    if not validas:
        resultado["ok"] = True
        resultado["aviso"] = "Nenhuma alteração para aplicar."
        return resultado
    if not os.path.isfile(ini_path):
        # primeira escrita: garante a pasta do jogo
        os.makedirs(os.path.dirname(ini_path), exist_ok=True)
    nome_backup = si.criar_backup(ini_path, pasta_backups)
    valores = _mesclar_valores(ini_path, validas)
    si.gravar(ini_path, valores)
    resultado["aplicadas"] = validas
    resultado["backup"] = nome_backup
    return resultado


def restaurar(ini_path: str, pasta_backups: str, nome: str) -> None:
    """Restaura backup (criando cópia do estado atual antes)."""
    if not ini_path:
        raise PermissionError("PALWORLD_INI não configurado.")
    si.restaurar_backup(ini_path, pasta_backups, nome)


def exportar(ini_path: str, api_settings: dict | None) -> dict:
    """JSON sem senhas, pronto para importar em outro servidor."""
    est = estado(ini_path, api_settings)
    limpos = {k: v for k, v in est["valores"].items()
              if k not in sc.chaves_sensíveis()}
    return {"_formato": "painel-palworld-config", "_versao": 1,
            "valores": limpos}


def importar(payload: dict, ini_path: str, api_settings: dict | None) -> tuple:
    """Valida um export recebido e devolve (validas, invalidas) sem aplicar."""
    if payload.get("_formato") != "painel-palworld-config":
        raise ValueError("Arquivo sem o formato esperado (_formato inválido).")
    est = estado(ini_path, api_settings)
    return validar_mudancas(payload.get("valores") or {}, est["valores"])


def bruto(ini_path: str) -> str | None:
    texto = si.ler_bruto(ini_path) if ini_path else None
    if texto is None:
        return None
    for chave in sc.chaves_sensíveis():
        # mascara qualquer AdminPassword="..." / ServerPassword=... no texto
        import re
        padrao = re.compile(r'(\b%s\s*=\s*)("[^"]*"|\S+)' % chave)
        texto = padrao.sub(lambda m: m.group(1) + ('"%s"' % _MASKA
                            if m.group(2) not in ('""', "") else '""'),
                           texto)
    return texto


def presets_com_estado(ini_path: str, api_settings) -> list:
    est = estado(ini_path, api_settings)
    saida = []
    for p in sc.presets():
        validas, invalidas = validar_mudancas(p["mudancas"], est["valores"])
        saida.append({"nome": p["nome"], "descricao": p["descricao"],
                      "validas": validas, "invalidas": invalidas})
    return saida
