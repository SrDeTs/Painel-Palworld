<div align="center">
  <img src="painel/static/logo.jpg" alt="Logo Painel Palworld" width="140">
  <h1>Painel Palworld</h1>
  <p>Game server panel completo para servidores dedicados de Palworld</p>
</div>

Painel web para administrar o servidor dedicado de Palworld
(imagem `thijsvanloef/palworld-server-docker`) pela **REST API oficial** do jogo.

- **Zero dependências**: backend em Python puro (só biblioteca padrão + SQLite), frontend em HTML/CSS/JS vanilla.
- Instala com `docker compose up -d` — sem pip, sem build, sem banco externo.

---

## O que o painel faz

| Área | Recursos |
|---|---|
| 📊 Dashboard | Status ao vivo, jogadores/capacidade, FPS, frame time, bases, dia do mundo, versão, disco livre + **gráficos históricos** (1h/6h/24h/7d/30d) armazenados em SQLite |
| 👥 Jogadores | Online agora (chutar/banir), **registro histórico** (primeiro/último acesso, tempo total, sessões, nível máx.), busca, ordenação e paginação |
| 🚫 Bans | Livro de bans do painel: permanente ou **temporário com auto-unban**, motivo, datas e histórico |
| 🗺️ Mapa | Game Data API (builds recentes): entidades com coordenadas em mapa canvas — degrada com aviso honesto quando a API não expõe |
| 📟 Console | Eventos do painel em **tempo real via SSE** (fallback polling): pausar, filtrar por nível, buscar, copiar, baixar |
| 💾 Backups | Lista/downloads dos zips do jogo + backup sob demanda gerado pelo painel, exclusão e **restauração segura multi-etapas** (preserva estado anterior, trava concorrência, dupla confirmação) |
| ⏰ Agenda | Tarefas agendadas: restart, save, anúncio, backup, limpeza — horário diário ou intervalo, executar agora |
| ⚙️ Configurações | **Editor completo** das 56 opções do `PalWorldSettings.ini` em 12 categorias: validação, min/máx, diff antes de aplicar, backup automático, presets, export/import, modo bruto |
| ❤️ Saúde | Diagnóstico da API/disco/backups/INI/saves/banco com sugestões e ações "Corrigir", **auto-recuperação** configurável (FPS baixo → restart gracioso com cooldown e limite diário) |
| 🔔 Notificações | Discord webhook, Telegram bot, webhook genérico — com filtro por evento e botão testar |
| 🚨 Emergência & Manutenção | Ação de emergência (anúncio→save→backup→shutdown, sem concorrência) e modo manutenção que bloqueia ações do painel |
| 🔐 Usuários | Multiusuário Admin/Moderador/Viewer com permissões granulares, sessões revogáveis e audit log |
| ⌨️ Busca global | `Ctrl+K` navega para qualquer seção e dispara ações rápidas, respeitando o papel do usuário |
| 🌐 Público | Página `/status` opcional (online, jogadores, versão, endereço de conexão) e PWA instalável no celular |

## Estrutura

```
Painel Palworld/
├── Palworld Server.yml      # compose completo (palworld + painel)
├── readme.md                # este arquivo
├── .env.example             # variáveis documentadas
├── .github/workflows/ci.yml # CI: sintaxe + 67 testes + smoke
├── painel/
│   ├── painel.py            # servidor HTTP + rotas (entrada)
│   ├── store.py             # SQLite: métricas históricas + eventos
│   ├── players.py           # registro de jogadores + livro de bans
│   ├── users.py             # usuários, papéis, permissões, sessões, API keys
│   ├── settings_catalog.py  # catálogo das 56 opções do jogo
│   ├── settings_ini.py      # leitura/escrita do PalWorldSettings.ini
│   ├── settings_editor.py   # validação, diff, apply, export/import
│   ├── scheduler.py         # agendador de tarefas
│   ├── backups_manager.py   # backup/restore/retenção do mundo
│   ├── health.py            # diagnóstico + correções seguras
│   ├── notifications.py     # Discord/Telegram/webhook
│   └── static/              # interface (HTML/CSS/JS + logo)
└── tests/
    ├── mock_palworld.py     # API falsa p/ testar sem o jogo rodando
    └── test_painel.py       # suíte automatizada (70 testes, stdlib puro)
```

## Instalação no CasaOS / ZimaOS / Docker

1. Copie a pasta do painel para o caminho que o compose espera:

   ```bash
   sudo mkdir -p /DATA/AppData/palworld/painel
   sudo cp -r "painel/." /DATA/AppData/palworld/painel/
   ```

2. Antes de subir, troque as senhas no `Palworld Server.yml`
   (e mantenha as duas IGUAIS):

   - `ADMIN_PASSWORD` (serviço `palworld` **e** serviço `Painel`)
   - `PANEL_PASSWORD` (senha de entrada no site do painel)

   Se ficar vazia/fraca, o painel gera uma senha forte automaticamente e
   mostra no log do container.

3. Escolha a porta do painel no serviço `Painel` do YAML:

   ```yaml
   ports:
     - mode: ingress
       target: 3564        # porta interna (deixe assim)
       published: "3564"   # ← troque aqui se quiser
   ```

4. Suba pelo CasaOS/ZimaOS ou direto:

   ```bash
   docker compose -f "Palworld Server.yml" up -d
   ```

5. Abra `http://SEU-IP:3564` e entre com a `PANEL_PASSWORD`.

> 🔒 A REST API do jogo (porta 8212) fica **isolada na rede interna do Docker**
> — não é publicada. Só o painel fala com ela (`http://palworld:8212`).

## Configuração (variáveis)

| Variável | Serviço | Para que serve | Padrão |
|---|---|---|---|
| `PANEL_PORT` | Painel | Porta do site | `3564` |
| `PANEL_PASSWORD` | Painel | Senha de entrada; vazio/fraco = **gerada autom.** | (gerada) |
| `ADMIN_PASSWORD` | Ambos | Senha admin do jogo — IGUAL nos dois serviços | — |
| `PALWORLD_API` | Painel | Endereço da REST API | `http://palworld:8212` |
| `PALWORLD_INI` | Painel | Caminho do ini → habilita o editor | — |
| `PALWORLD_SAVE_DIR` | Painel | Pasta SaveGames → habilita backup/restauração | derivado do INI |
| `BACKUP_DIR` | Painel | Pasta de backups montada | `/data/backups` |
| `PUBLIC_STATUS` | Painel | `0` desliga a página pública `/status` | ligada |
| `PUBLIC_HOST` | Painel | Endereço exibido na página pública | host da API |
| `API_TIMEOUT` | Painel | Timeout das chamadas ao jogo (s) | `4` |

Lista completa com exemplos: `.env.example`.

## Rodar direto no PC (sem Docker)

```bash
cd painel && python3 painel.py
```

Sem Docker não há YAML: configure por variáveis de ambiente
(`PANEL_PORT`, `PANEL_PASSWORD`, `PALWORLD_API`, `ADMIN_PASSWORD`,
`PALWORLD_INI`, `BACKUP_DIR`, ...).

## Testar sem o servidor de jogo (mock)

```bash
python3 tests/mock_palworld.py &          # API falsa na porta 8212
ADMIN_PASSWORD=123 PANEL_PORT=8080 python3 painel/painel.py &
# abra http://localhost:8080 (login: senha única "123"... ou defina PANEL_PASSWORD)
python3 tests/test_painel.py              # 70 testes, ~9s
```

O mock tem jogadores fake e responde anúncios/kicks/bans/saves/shutdowns.
A suíte sobe mock + painel de verdade em portas efêmeras e valida login,
rate limit, permissões, editor de config, bans, scheduler, backups, SSE,
segurança e path traversal.

## Segurança

- **Nunca inicia com senha fraca**: sem `PANEL_PASSWORD` (ou vazia/"123"),
  gera uma forte, guarda em `painel/data/painel_auth.json` (perm. 600) e
  mostra no log.
- REST API do jogo usa HTTP Basic sem TLS e dá controle total: no compose ela
  fica **sem exposição pública**; nunca faça port forwarding dela.
- Sessões revogáveis; rate limit de login (bloqueio de 60s após 5 erros);
  cabeçalhos CSP restritivos; proteção contra path traversal em downloads,
  restores e imports; corpo de requisição limitado (413).
- `.gitignore` mantém `config.env`, `data/` (senhas/banco) e logs fora do Git.

## API do próprio painel (/api/v1)

Autenticação por **API key** (header `X-API-Key` ou `Authorization: Bearer`)
ou pelo token normal da interface. Crie chaves na aba Usuários (admin).

```bash
KEY=ppk_...
curl -H "X-API-Key: $KEY" http://SEU-IP:3564/api/v1/server
curl -H "X-API-Key: $KEY" http://SEU-IP:3564/api/v1/players
curl -H "X-API-Key: $KEY" http://SEU-IP:3564/api/v1/metrics
curl -H "X-API-Key: $KEY" http://SEU-IP:3564/api/v1/logs?limite=100
curl -H "X-API-Key: $KEY" http://SEU-IP:3564/api/v1/health
curl -X POST -H "X-API-Key: $KEY" -H 'Content-Type: application/json' \
     -d '{"waittime":300}' http://SEU-IP:3564/api/v1/actions/restart
# Prometheus:
curl -H "X-API-Key: $KEY" http://SEU-IP:3564/metrics
```

Permissões da key são as mesmas granulares do painel (SERVER_VIEW,
SERVER_SAVE...). Métricas exportadas: `palworld_up`, `palworld_players`,
`palworld_fps`, `palworld_frame_time`, `palworld_uptime_seconds`,
`palworld_base_camps`, `palworld_world_day`, `painel_disco_livre_bytes`,
`painel_saude`.

## Limitações honestas

- A REST API do jogo é **somente leitura para settings**: o editor grava no
  `PalWorldSettings.ini` e exige reinício do servidor (fluxo já automatizado).
- Não há endpoint de logs do jogo nem de banlist: console mostra eventos do
  painel e bans vivem no livro local.
- CPU/RAM do container do jogo não são visíveis pelo painel (containers
  separados); disco livre do volume sim.
- Auto-recuperação não consegue religar um container morto via REST — só
  reinicia graciosamente quando a API responde (ex.: FPS degradado).

## FAQ

**Esqueci a senha do painel.** Defina `PANEL_PASSWORD` no YAML e recrie o
container — o hash do admin bootstrap é ressincronizado.

**O editor diz que está indisponível.** Faltam os mounts do volume do jogo
(`PALWORLD_INI`/save dir) no serviço Painel do YAML.

**Ban temporário não removeu.** O unban automático roda a cada 60s e precisa
da API do jogo respondendo; se estava offline, ele tenta de novo no próximo ciclo.

**Funciona no celular?** Sim — interface responsiva + PWA instalável
(Adicionar à tela inicial).

## Desenvolvimento

```bash
python3 tests/test_painel.py        # suíte completa
python3 tests/mock_palworld.py      # jogo falso p/ experimentar
```

Arquitetura: cada domínio é um módulo stdlib independente (store, players,
scheduler, backups_manager, health, notifications, users, settings_*),
comunicando-se por funções explícitas e SQLite local — pronto para evoluir
para múltiplos servidores sem reescrever a base. CI no GitHub Actions roda
sintaxe, testes nas versões 3.10–3.13, smoke E2E e checagem de JS/PWA.

## Roadmap

- [x] ~~Emergency mode~~ · ~~Export CSV/JSON~~ · ~~Notificações Discord/Telegram~~
- [x] ~~Busca global Ctrl+K~~
- [ ] Wizard visual de primeiro uso (hoje: senha autogerada + docs)
- [ ] Storage externo de backups (S3/Backblaze/SMB) via abstração
- [ ] Múltiplos servidores por instância (base já modularizada)
- [ ] Bot Discord com slash commands (webhooks já cobrem eventos)
