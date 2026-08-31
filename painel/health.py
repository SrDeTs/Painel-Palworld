#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Monitor de saúde do ecossistema Palworld.

Cada verificação devolve um item com estado (ok|aviso|critico|erro),
mensagem legível, sugestão de solução e — quando seguro — uma ação de
correção executável pelo próprio painel.

Estado global agregado: healthy | degraded | critical | offline | maintenance.
"""

import os
import shutil
import time


class Verificacao(dict):
    """Item de diagnóstico: id, estado, mensagem, sugestão, correção."""
    def __init__(self, vid, estado, mensagem, sugestao="",
                 acao_corrigir=None):
        super().__init__(id=vid, estado=estado, mensagem=mensagem,
                         sugestao=sugestao, corrigivel=bool(acao_corrigir),
                         acao=acao_corrigir)


def _estado_global(itens, online: bool) -> str:
    if any(i["estado"] == "manutencao" for i in itens):
        return "maintenance"
    if not online:
        return "offline"
    if any(i["estado"] == "critico" for i in itens):
        return "critical"
    if any(i["estado"] == "aviso" for i in itens):
        return "degraded"
    return "healthy"


# ------------------------------------------------------------------ checagens

def _checar_api(status_combinado_fn):
    snap = status_combinado_fn()
    if snap.get("online"):
        met = snap.get("metrics") or {}
        met_normalizadas = {str(k).lower(): v for k, v in met.items()}
        fps = (met_normalizadas.get("serverfps")
               if met_normalizadas.get("serverfps") is not None
               else met_normalizadas.get("serverfpsaverage"))
        extra = f" (FPS {fps:.0f})" if isinstance(fps, (int, float)) else ""
        return Verificacao("api", "ok", f"REST API respondendo{extra}"), snap

    erro = str(snap.get("error") or "")
    erro_lower = erro.lower()
    if "401" in erro_lower or "rejeitou a senha" in erro_lower:
        return Verificacao(
            "api", "erro", "Senha administrativa rejeitada pela API do jogo",
            "Reinicie os serviços palworld e Painel para sincronizar o segredo "
            "compartilhado. Se os serviços não usam o mesmo volume, configure "
            "ADMIN_PASSWORD com o mesmo valor nos dois."), snap

    detalhe = f": {erro}" if erro else ""
    return Verificacao(
        "api", "erro", f"REST API do jogo não responde{detalhe}",
        "Verifique se o container 'palworld' está rodando no CasaOS/Docker "
        "(docker ps). O jogo pode estar iniciando — aguarde alguns minutos."), snap


def _checar_disco(data_dir: str, livre_mb: int | None):
    if livre_mb is None:
        return Verificacao("disco", "aviso", "Não foi possível medir o disco",
                           "Confira o espaço manualmente no painel do NAS.")
    if livre_mb < 2048:
        return Verificacao(
            "disco", "critico", f"Só {livre_mb} MB livres no volume de dados",
            "Apague backups antigos (aba Backups → Excluir) ou amplie o volume.",
            acao_corrigir={"tipo": "limpar_backups_antigos"})
    if livre_mb < 5120:
        return Verificacao("disco", "aviso", f"{livre_mb} MB livres no disco",
                           "Considere limpar backups antigos em breve.",
                           acao_corrigir={"tipo": "limpar_backups_antigos"})
    gb = livre_mb // 1024
    return Verificacao("disco", "ok", f"{gb} GB livres no volume de dados")


def _checar_backups(backup_dir: str, listar_fn):
    if not backup_dir:
        return Verificacao(
            "backups", "aviso", "BACKUP_DIR não configurado",
            "Monte /DATA/AppData/palworld/backups no container do painel e "
            "aponte BACKUP_DIR para ele.")
    if not os.path.isdir(backup_dir):
        try:
            os.makedirs(backup_dir, exist_ok=True)
        except OSError as exc:
            return Verificacao("backups", "critico",
                               f"Pasta de backups inacessível ({exc})",
                               "Cheque permissões do volume montado.")
    lista = listar_fn()
    total = len(lista.get("backups", []))
    if not total:
        return Verificacao(
            "backups", "aviso", "Nenhum backup encontrado ainda",
            "Crie um backup agora na aba Backups ou agende um na Agenda.")
    mais_recente = lista["backups"][0]
    idade_h = (time.time() - mais_recente["modificado"]) / 3600
    if idade_h > 48:
        return Verificacao(
            "backups", "aviso",
            f"Último backup tem {idade_h:.0f}h",
            "Agende backups periódicos na aba Agenda.",
            acao_corrigir={"tipo": "criar_backup"})
    return Verificacao("backups", "ok",
                       f"{total} backup(s); último há {idade_h:.0f}h")


def _checar_ini(ini_path: str):
    if not ini_path:
        return Verificacao(
            "ini", "aviso", "Editor de configurações indisponível",
            "Defina PALWORLD_INI apontando ao PalWorldSettings.ini para "
            "habilitar edição de configurações.")
    if not os.path.isfile(ini_path):
        return Verificacao(
            "ini", "erro", f"INI configurado mas ausente: {ini_path}",
            "O servidor gera esse arquivo no primeiro boot; confirme o mount "
            "do volume.")
    return Verificacao("ini", "ok", "PalWorldSettings.ini acessível")


def _checar_save_dir(save_dir: str):
    if not save_dir:
        return Verificacao(
            "save", "aviso", "Backup/restauração do mundo indisponíveis",
            "Defina PALWORLD_SAVE_DIR apontando à pasta SaveGames do jogo.")
    if not os.path.isdir(save_dir):
        return Verificacao(
            "save", "erro", f"Pasta de saves não encontrada: {save_dir}",
            "Confira o mount do volume do jogo no container do painel.")
    return Verificacao("save", "ok", "Pasta de saves acessível")


def _checar_world_option(save_dir: str):
    """Detecta configuração binária que prevalece sobre o INI do painel."""
    if not save_dir or not os.path.isdir(save_dir):
        return None
    try:
        for raiz, _pastas, arquivos in os.walk(save_dir):
            if "WorldOption.sav" in arquivos:
                return Verificacao(
                    "world_option", "critico",
                    "WorldOption.sav está sobrescrevendo o PalWorldSettings.ini",
                    "Reinicie o serviço palworld com "
                    "PANEL_DISABLE_WORLD_OPTION=true. O bootstrap renomeia esse "
                    "arquivo como backup e faz o INI voltar a valer.")
    except OSError:
        return None
    return None


def _checar_coletor(ultima_metrica_ts: int | None, intervalo_s: int):
    if not ultima_metrica_ts:
        return Verificacao("coletor", "aviso", "Sem amostras de métricas ainda",
                           "Aguarde ~1 minuto com o painel ligado.")
    idade = time.time() - ultima_metrica_ts
    if idade > max(intervalo_s * 6, 300):
        return Verificacao("coletor", "critico",
                           f"Coletor parado há {int(idade)}s",
                           "Reinicie o container do painel.", )
    return Verificacao("coletor", "ok", "Coletor de métricas ativo")


# ------------------------------------------------------------------ agregado

def diagnostico(ctx: dict) -> dict:
    """Roda todas as verificações. ctx traz dependências do painel."""
    itens = []

    api_item, _snap = _checar_api(ctx["status_combinado"])
    itens.append(api_item)

    # senha de admin coerente com o estado da API
    if not ctx.get("admin_password"):
        itens.append(Verificacao(
            "auth_jogo", "critico", "ADMIN_PASSWORD vazia",
            "Defina a mesma ADMIN_PASSWORD nos dois serviços do YAML "
            "(palworld e Painel). Sem ela o painel não comanda o jogo."))

    itens.append(_checar_disco(ctx["data_dir"], ctx.get("livre_mb")))
    itens.append(_checar_backups(ctx["backup_dir"], ctx["listar_backups"]))
    itens.append(_checar_ini(ctx["ini_path"]))
    itens.append(_checar_save_dir(ctx["save_dir"]))
    world_option = _checar_world_option(ctx["save_dir"])
    if world_option:
        itens.append(world_option)
    itens.append(_checar_coletor(ctx.get("ultima_metrica_ts"),
                                 ctx.get("intervalo_colete", 30)))

    banco = ctx["banco_status"]()
    if banco.get("ok"):
        itens.append(Verificacao(
            "banco", "ok",
            f"SQLite íntegro ({banco['metricas']} amostras, "
            f"{banco['eventos']} eventos)"))
    else:
        itens.append(Verificacao(
            "banco", "critico", f"Banco de dados com problema: {banco.get('erro')}",
            "Verifique permissões da pasta data/ do painel."))

    online = bool(ctx["ultimo_snap"].get("online")) \
        if ctx.get("ultimo_snap") else api_item["estado"] == "ok"
    return {
        "estado": _estado_global(itens, online),
        "itens": itens,
        "gerado_em": int(time.time()),
    }


def acao_corretiva(tipo: str, ctx: dict) -> tuple:
    """Executa a correção segura indicada por um diagnóstico."""
    if tipo == "criar_backup":
        resultado = ctx["criar_backup"]()
        return True, f"Backup criado: {resultado['nome']}"
    if tipo == "limpar_backups_antigos":
        res = ctx["retencao"](manter_ultimos=5, idade_dias=7)
        return True, f"{len(res['apagados'])} backup(s) antigo(s) removido(s)"
    raise ValueError(f"Ação corretiva desconhecida: {tipo}")
