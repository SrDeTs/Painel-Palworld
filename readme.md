# 🐑 Painel Palworld

Painel web para administrar o servidor dedicado de Palworld
(imagem `thijsvanloef/palworld-server-docker`) pela **REST API oficial** do jogo.

- **Zero dependências**: backend em Python puro (só biblioteca padrão), frontend em HTML/CSS/JS vanilla.

---

## O que o painel faz

| Recurso | Como funciona |
|---|---|
| 📊 Status ao vivo | Nome, versão, FPS, frame time, jogadores, uptime (atualiza a cada 5s) |
| 👥 Jogadores | Lista quem está online com nível, ping, posição — botões **Chutar** e **Banir** |
| 📣 Anúncios | Mensagem no chat do jogo para todos os jogadores |
| 💾 Salvar mundo | Save forçado no disco |
| 🔄 Reiniciar | Anuncia → salva → desliga com contagem → **o Docker religa sozinho** (`restart: unless-stopped`) |
| ⏻ Desligar | Mesmo fluxo do reinício, sem prometer volta |
| ⚙️ Configurações | Mostra as configurações vigentes do servidor (somente leitura) |
| 🗄️ Backups | Lista os backups do mundo gerados pelo container, com download pelo navegador |

## Estrutura

```
Painel Palworld/
├── Palworld Server.yml      # compose completo (palworld + painel)
├── readme.md                # este arquivo
├── painel/
│   ├── painel.py            # servidor do painel (proxy + auth + estáticos)
│   ├── logo.jpg             # marca usada no site e no favicon
│   ├── static/              # interface (HTML/CSS/JS + fontes + logo)
└── tests/
    ├── mock_palworld.py     # API falsa p/ testar sem o jogo rodando
    └── test_painel.py       # suíte automatizada (27 testes, stdlib puro)
```

## Instalação no CasaOS / servidor (192.168.1.36)

1. **Copie a pasta do painel** para o caminho que o compose espera:

2. **Antes de subir**, troque as senhas no `Palworld Server.yml`
   (e mantenha as duas IGUAIS):

   - `ADMIN_PASSWORD` (serviço `palworld` **e** serviço `Painel`)
   - `PANEL_PASSWORD` (senha de entrada no site do painel)

   > ℹ️ A imagem do servidor não tem variável pra `AdminPlayers` — para definir
   > admins do jogo, edite `AdminPlayers` direto no `PalWorldSettings.ini`
   > com o container parado.

   > 🗄️ A aba **Backups** do painel já vem pronta no compose: ele monta
   > `/DATA/AppData/palworld/backups` (onde o serviço palworld grava os backups)
   > dentro do container do painel via `BACKUP_DIR`. Se a pasta não existir ainda,
   > o Docker cria na primeira subida.

3. **Escolha a porta do painel** no serviço `Painel` do YAML — você não está
   preso à 3564.

   Depois disso o painel abre em `http://SEU-IP:PORTA-QUE-VOCE-ESCOLHEU`.

4. Suba/atualize o app pelo ZimaOS/CasaOS ou Docker Tanto Faz

5. Abra **http://SEU-IP:PORTA-QUE-VOCE-ESCOLHEU**
   e entre com a `PANEL_PASSWORD` (`SUA-SENHA`).

## Testar sem o servidor de jogo (mock)

```bash
python3 tests/mock_palworld.py &          # API falsa na porta 8212
ADMIN_PASSWORD=123 PANEL_PORT=8080 python3 painel/painel.py &
# abra http://localhost:8080  (senha do painel: 123, vem do config.env)
```

O mock tem dois jogadores fake, responde anúncios/kicks/bans/saves/shutdowns
e imprime tudo no terminal — dá pra validar o painel inteiro offline.

### Suíte automatizada

Sobe mock + painel de verdade em portas efêmeras e valida login, rate limit,
sessão, proxy das rotas, kick/ban/unban, reinício agregado, cabeçalhos de
segurança e path traversal:

```bash
python3 tests/test_painel.py              # 27 testes, ~4 segundos
```

## Segurança

- A REST API do jogo usa **HTTP Basic sem TLS** e dá controle total do servidor.
- A sessão do painel dura 12h e é renovada a cada uso; erros de login têm
  atraso proposital e **bloqueio de 60s após 5 erros** do mesmo IP.
- O painel manda cabeçalhos de segurança em toda resposta (CSP restritiva,
  anti-clickjacking, anti-sniffing) — script externo ou inline não roda.

## Por que "reiniciar" funciona sem acesso ao Docker?

O container do jogo usa `restart: unless-stopped`. Quando o painel pede
shutdown, o processo do jogo sai graciosamente (depois do save) e o Docker,
vendo o container sair, **religa ele automaticamente**. É o mesmo efeito de um
reinício manual, mas seguro — sempre com aviso aos jogadores e save do mundo.
Para deixar o servidor offline de verdade, pare o container pelo CasaOS/Docker.
