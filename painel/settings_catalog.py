#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Catálogo das configurações do servidor Palworld (PalWorldSettings.ini).

Cada entrada descreve uma opção real da linha OptionSettings com metadados
para o editor: categoria, rótulo amigável, descrição em pt-BR, tipo,
faixa válida/opções, valor padrão oficial e se exige reinício do servidor.

Fontes dos padrões: documentação oficial (docs.palworldgame.com) e o
DefaultPalWorldSettings.ini que acompanha o servidor dedicado.
Chaves desconhecidas retornadas pela API continuam visíveis no modo bruto.
"""

TIPO_INT = "int"
TIPO_FLOAT = "float"
TIPO_BOOL = "bool"
TIPO_ENUM = "enum"
TIPO_STR = "string"
TIPO_SENHA = "password"

# chave: (categoria, tipo, padrão, extra{label, desc, min, max, opções})
_CATALOGO = {
    # ── Geral ────────────────────────────────────────────────────────────
    "Difficulty": ("geral", TIPO_ENUM, "None",
                   {"opcoes": ["None", "Easy", "Normal", "Hard"],
                    "label": "Dificuldade",
                    "desc": "Preset geral de dano/vida de jogadores e Pals."}),
    "DayTimeSpeedRate": ("geral", TIPO_FLOAT, 1.0,
                         {"min": 0.1, "max": 10.0, "label": "Velocidade do dia",
                          "desc": "Quanto tempo dura o período diurno. "
                                  "Maior = dias mais curtos."}),
    "NightTimeSpeedRate": ("geral", TIPO_FLOAT, 1.0,
                           {"min": 0.1, "max": 10.0, "label": "Velocidade da noite",
                            "desc": "Duração do período noturno."}),
    "ExpRate": ("geral", TIPO_FLOAT, 1.0,
                {"min": 0.1, "max": 100.0, "label": "Multiplicador de XP",
                 "desc": "Ganho de experiência dos jogadores."}),
    "AutoSaveSpan": ("geral", TIPO_FLOAT, 30.0,
                     {"min": 10.0, "max": 300.0, "label": "Intervalo de autosave",
                      "desc": "Segundos entre saves automáticos do mundo.",
                      "unidade": "s"}),

    # ── Mundo & spawns ───────────────────────────────────────────────────
    "PalSpawnNumRate": ("mundo", TIPO_FLOAT, 1.0,
                       {"min": 0.1, "max": 5.0, "label": "Densidade de spawns",
                        "desc": "Quantidade de Pals gerados no mapa."}),
    "bEnableInvaderEnemy": ("mundo", TIPO_BOOL, True,
                            {"label": "Invasões de inimigos",
                             "desc": "Raids atacando suas bases periodicamente."}),
    "CollectionDropRate": ("mundo", TIPO_FLOAT, 1.0,
                           {"min": 0.1, "max": 10.0, "label": "Coleta — itens",
                            "desc": "Itens obtidos ao coletar recursos no mapa."}),
    "CollectionObjectHpRate": ("mundo", TIPO_FLOAT, 1.0,
                               {"min": 0.1, "max": 10.0, "label": "Coleta — vida dos objetos",
                                "desc": "Resistência de árvores/pedras à coleta."}),
    "CollectionObjectRespawnSpeedRate": ("mundo", TIPO_FLOAT, 1.0,
                                         {"min": 0.1, "max": 10.0,
                                          "label": "Coleta — respawn",
                                          "desc": "Velocidade de reaparecimento dos recursos.",
                                          "nota": "maior = respawn mais RÁPIDO"}),

    # ── Drops & itens ────────────────────────────────────────────────────
    "EnemyDropItemRate": ("drops", TIPO_FLOAT, 1.0,
                          {"min": 0.1, "max": 10.0, "label": "Drop de inimigos",
                           "desc": "Itens deixados por Pals derrotados."}),
    "DropItemMaxNum": ("drops", TIPO_INT, 3000,
                       {"min": 0, "max": 20000, "label": "Limite de itens no chão",
                        "desc": "Excedente mais antigo é apagado. Alto demais pesa no servidor."}),
    "DropItemAliveMaxHours": ("drops", TIPO_FLOAT, 1.0,
                              {"min": 0.1, "max": 240.0, "label": "Itens somem após",
                               "desc": "Tempo até loot no chão desaparecer.",
                               "unidade": "h"}),
    "DropItemMaxNum_UNKO": ("drops", TIPO_INT, 100,
                            {"min": 0, "max": 5000, "label": "Limite de 'unko'",
                             "desc": "Cap específico dos drops de palunko (piada do jogo)."}),
    "EnableDropItemInMultiplayer": ("drops", TIPO_BOOL, False,
                                    {"label": "Jogadores dropam itens",
                                     "desc": "Soltar inventário ao morrer/desconectar."}),
    "ActiveUNKO": ("avancado", TIPO_BOOL, False,
                   {"label": "Modo unko ativo",
                    "desc": "Recurso cômico dos Pals (fezes). Easter egg oficial."}),

    # ── Captura & criação ────────────────────────────────────────────────
    "PalCaptureRate": ("captura", TIPO_FLOAT, 1.0,
                       {"min": 0.1, "max": 10.0, "label": "Taxa de captura",
                        "desc": "Facilidade de capturar Pals."}),
    "PalEggDefaultHatchingTime": ("captura", TIPO_FLOAT, 72.0,
                                  {"min": 0.1, "max": 240.0, "label": "Chocar ovo leva",
                                   "desc": "Tempo base de incubação.",
                                   "unidade": "h"}),

    # ── Pals ─────────────────────────────────────────────────────────────
    "PalDamageRateAttack": ("pals", TIPO_FLOAT, 1.0,
                            {"min": 0.1, "max": 10.0, "label": "Dano dos Pals (ataque)",
                             "desc": "Dano causado por Pals (aliados e selvagens)."}),
    "PalDamageRateDefense": ("pals", TIPO_FLOAT, 1.0,
                             {"min": 0.1, "max": 10.0, "label": "Defesa dos Pals",
                              "desc": "Dano recebido por Pals (menor = mais resistentes)."}),
    "PalStomachDecreaceRate": ("pals", TIPO_FLOAT, 1.0,
                               {"min": 0.0, "max": 10.0, "label": "Fome dos Pals",
                                "desc": "Velocidade com que Pals ficam com fome."}),
    "PalAutoHPRegeneRate": ("pals", TIPO_FLOAT, 1.0,
                            {"min": 0.0, "max": 10.0, "label": "Regeneração dos Pals",
                             "desc": "Recuperação automática de HP."}),
    "PalAutoHpRegeneRateInSleep": ("pals", TIPO_FLOAT, 1.0,
                                   {"min": 0.0, "max": 10.0,
                                    "label": "Regeneração dos Pals dormindo",
                                    "desc": "Recuperação de HP durante o sono."}),
    "WorkSpeedRate": ("pals", TIPO_FLOAT, 1.0,
                      {"min": 0.1, "max": 10.0, "label": "Velocidade de trabalho",
                       "desc": "Ritmo dos Pals trabalhando na base."}),

    # ── Jogadores ────────────────────────────────────────────────────────
    "PlayerDamageRateAttack": ("jogadores", TIPO_FLOAT, 1.0,
                               {"min": 0.1, "max": 10.0, "label": "Dano dos jogadores",
                                "desc": "Dano causado por jogadores."}),
    "PlayerDamageRateDefense": ("jogadores", TIPO_FLOAT, 1.0,
                                {"min": 0.1, "max": 10.0, "label": "Defesa dos jogadores",
                                 "desc": "Dano recebido por jogadores."}),
    "PlayerStomachDecreaceRate": ("jogadores", TIPO_FLOAT, 1.0,
                                  {"min": 0.0, "max": 10.0, "label": "Fome dos jogadores",
                                   "desc": "Velocidade da fome humana."}),
    "PlayerAutoHPRegeneRate": ("jogadores", TIPO_FLOAT, 1.0,
                               {"min": 0.0, "max": 10.0, "label": "Regeneração do jogador",
                                "desc": "Recuperação automática de HP."}),
    "PlayerAutoHpRegeneRateInSleep": ("jogadores", TIPO_FLOAT, 1.0,
                                      {"min": 0.0, "max": 10.0,
                                       "label": "Regeneração dormindo",
                                       "desc": "Recuperação de HP durante o sono."}),
    "CoopPlayerMaxNum": ("jogadores", TIPO_INT, 4,
                         {"min": 1, "max": 4, "label": "Tamanho do grupo (coop)",
                          "desc": "Jogadores por guilda em coop."}),

    # ── Guildas & bases ──────────────────────────────────────────────────
    "GuildPlayerMaxNum": ("guildas", TIPO_INT, 20,
                          {"min": 1, "max": 100, "label": "Membros por guilda"}),
    "bAutoResetGuildNoOnlinePlayers": ("guildas", TIPO_BOOL, False,
                                       {"label": "Resetar guilda inativa",
                                        "desc": "Dissolve guilda cujos membros não entram "
                                                "por X horas (ver abaixo)."}),
    "AutoResetGuildTimeNoOnlinePlayers": ("guildas", TIPO_FLOAT, 72.0,
                                          {"min": 1.0, "max": 720.0,
                                           "label": "Horas p/ resetar guilda",
                                           "desc": "Janela de inatividade que dispara o reset.",
                                           "unidade": "h"}),
    "BaseCampMaxNum": ("guildas", TIPO_INT, 128,
                       {"min": 1, "max": 256, "label": "Bases por mundo"}),
    "BaseCampWorkerNumMax": ("guildas", TIPO_INT, 15,
                             {"min": 1, "max": 40, "label": "Pals por base"}),

    # ── Construção ───────────────────────────────────────────────────────
    "BuildObjectDamageRate": ("construcao", TIPO_FLOAT, 1.5,
                              {"min": 0.1, "max": 10.0, "label": "Dano às construções",
                               "desc": "Vulnerabilidade de estruturas a ataques."}),
    "BuildObjectDeteriorationDamageRate": ("construcao", TIPO_FLOAT, 2.0,
                                           {"min": 0.0, "max": 10.0,
                                            "label": "Deterioração de construções",
                                            "desc": "Desgaste natural quando sem manutenção."}),

    # ── PvP & morte ──────────────────────────────────────────────────────
    "bFriendlyFire": ("pvp", TIPO_BOOL, False,
                      {"label": "Fogo amigo",
                       "desc": "Dano entre aliados (base de servers PvP)."}),
    "bEnableNonLethalWeapon": ("pvp", TIPO_BOOL, True,
                               {"label": "Armas não letais",
                                "desc": "Permite armas de dano zero contra jogadores."}),
    "DeathPenalty": ("pvp", TIPO_ENUM, "All",
                     {"opcoes": ["None", "Item", "ItemAndEquipment", "All"],
                      "label": "Penalidade de morte",
                      "desc": "None = nada perdido · Item = só inventário · "
                              "ItemAndEquipment = inventário+equipamento · "
                              "All = também os Pals."}),
    "bHardcore": ("pvp", TIPO_BOOL, False,
                  {"label": "Modo hardcore",
                   "desc": "Morte permanente: personagem é apagada ao morrer."}),
    "bEnablePlayerToExperienceDamagePokemon": ("pvp", TIPO_BOOL, True,
                                               {"label": "Dano colateral a aliados",
                                                "desc": "Ataques seus podem ferir Pals aliados "
                                                        "(estilo Pokémon)."}) ,

    # ── Performance & rede ───────────────────────────────────────────────
    "NetServerMaxTickRate": ("performance", TIPO_INT, 120,
                             {"min": 30, "max": 120, "label": "Tick rate da rede",
                              "desc": "Atualizações de rede por segundo. Reduzir alivia CPU.",
                              "unidade": "Hz"}),
    "bIsUseBackupSaveData": ("performance", TIPO_BOOL, True,
                             {"label": "Backups internos do jogo",
                              "desc": "O jogo mantém cópias de segurança do save."}),

    # ── Servidor & acesso ────────────────────────────────────────────────
    "ServerName": ("servidor", TIPO_STR, "",
                   {"max_len": 80, "label": "Nome do servidor",
                    "desc": "Exibido na lista/navegador de servidores."}),
    "ServerDescription": ("servidor", TIPO_STR, "",
                          {"max_len": 200, "label": "Descrição"}),
    "AdminPassword": ("servidor", TIPO_SENHA, "",
                      {"label": "Senha de admin",
                       "desc": "Usada pelo painel e pelo /AdminPassword no chat."}),
    "ServerPassword": ("servidor", TIPO_SENHA, "",
                       {"label": "Senha de entrada",
                        "desc": "Exigida dos jogadores para conectar. Vazio = aberto."}),
    "PublicPort": ("servidor", TIPO_INT, 8211,
                   {"min": 1024, "max": 65535, "label": "Porta pública UDP",
                    "desc": "Porta que os clientes usam para conectar."}),
    "ServerPlayerMaxNum": ("servidor", TIPO_INT, 32,
                           {"min": 1, "max": 128, "label": "Jogadores simultâneos",
                            "desc": "Slots do servidor."}),
    "bUseAuth": ("servidor", TIPO_BOOL, True,
                 {"label": "Exigir autenticação",
                  "desc": "Validação de senha/admin no login do jogo."}),
    "bIsShowPlayerList": ("servidor", TIPO_BOOL, False,
                          {"label": "Mostrar lista de jogadores",
                           "desc": "Lista de online visível no cliente."}),
    "BanListURL": ("servidor", TIPO_STR, "",
                   {"max_len": 300, "label": "Banlist externa (URL)",
                    "desc": "Lista de bans global opcional."}),
    "CrossplayPlatforms": ("servidor", TIPO_STR, "(Steam,Xbox)",
                           {"label": "Plataformas crossplay",
                            "desc": "Tuple ex.: (Steam,Xbox). Editar com cuidado."}),

    # ── Avançado ─────────────────────────────────────────────────────────
    "LogFormatType": ("avancado", TIPO_ENUM, "Text",
                      {"opcoes": ["Text", "Json"], "label": "Formato do log"}),
    "Region": ("avancado", TIPO_STR, "", {"label": "Região", "max_len": 40}),
}

# ordem estável das categorias p/ exibição
CATEGORIAS_ORDEM = ["geral", "mundo", "captura", "pals", "jogadores",
                    "guildas", "construcao", "drops", "pvp",
                    "performance", "servidor", "avancado"]

ROTULO_CATEGORIA = {
    "geral": "Geral", "mundo": "Mundo", "captura": "Captura",
    "pals": "Pals", "jogadores": "Jogadores", "guildas": "Guildas & Bases",
    "construcao": "Construção", "drops": "Drops", "pvp": "PvP & Morte",
    "performance": "Performance", "servidor": "Servidor", "avancado": "Avançado",
}


def _entrada(chave):
    cat, tipo, padrao, extra = _CATALOGO[chave]
    item = {
        "chave": chave,
        "categoria": cat,
        "tipo": tipo,
        "padrao": padrao,
        "requer_restart": True,   # OptionSettings só vale após reiniciar o processo
        "senha": tipo == TIPO_SENHA,
    }
    item.update(extra)
    return item


def catalogo() -> list:
    """Catálogo ordenado por categoria e nome."""
    itens = [_entrada(k) for k in _CATALOGO]
    itens.sort(key=lambda i: (CATEGORIAS_ORDEM.index(i["categoria"]),
                              i.get("label", i["chave"]).lower()))
    return itens


def chaves_sensíveis() -> set:
    return {k for k, v in _CATALOGO.items() if v[1] == TIPO_SENHA}


def validar(chave: str, valor):
    """Valida um valor candidato. Devolve (ok, valor_normalizado, motivo)."""
    if chave not in _CATALOGO:
        return False, None, "chave desconhecida"
    _, tipo, _, extra = _CATALOGO[chave]
    try:
        if tipo == TIPO_INT:
            n = int(valor)
            if "min" in extra and n < extra["min"]:
                return False, None, f"mínimo {extra['min']}"
            if "max" in extra and n > extra["max"]:
                return False, None, f"máximo {extra['max']}"
            return True, n, ""
        if tipo == TIPO_FLOAT:
            n = float(str(valor).replace(",", "."))
            if "min" in extra and n < extra["min"]:
                return False, None, f"mínimo {extra['min']}"
            if "max" in extra and n > extra["max"]:
                return False, None, f"máximo {extra['max']}"
            return True, n, ""
        if tipo == TIPO_BOOL:
            if isinstance(valor, bool):
                return True, valor, ""
            texto = str(valor).strip().lower()
            if texto in ("true", "1", "sim", "yes"):
                return True, True, ""
            if texto in ("false", "0", "nao", "não", "no"):
                return True, False, ""
            return False, None, "use true/false"
        if tipo == TIPO_ENUM:
            t = str(valor).strip()
            if t not in extra["opcoes"]:
                return False, None, f"um de: {', '.join(extra['opcoes'])}"
            return True, t, ""
        # string/password/list
        t = str(valor)
        limite = extra.get("max_len")
        if limite and len(t) > limite:
            return False, None, f"máximo {limite} caracteres"
        if tipo == TIPO_SENHA and "\x00" in t:
            return False, None, "caractere inválido"
        return True, t, ""
    except (TypeError, ValueError):
        return False, None, "valor inválido p/ " + tipo
