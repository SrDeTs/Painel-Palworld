#!/bin/bash
# Bootstrap do servidor Palworld para CasaOS/ZimaOS.
# Preserva o PalWorldSettings.ini existente e garante que a REST API interna
# tenha uma senha compartilhada com o painel, sem expor 8212 para a rede.
set -euo pipefail

PALWORLD_ROOT="${PALWORLD_ROOT:-/palworld}"
INI="${PALWORLD_ROOT}/Pal/Saved/Config/LinuxServer/PalWorldSettings.ini"
SECRET="${PALWORLD_ROOT}/panel-admin-secret"
SAVE_ROOT="${PALWORLD_ROOT}/Pal/Saved/SaveGames"

umask 077

desativar_world_option() {
  # WorldOption.sav tem prioridade sobre PalWorldSettings.ini. Se ele veio de
  # uma migração ou ferramenta externa, inclusive AdminPassword é ignorada e a
  # REST API passa a responder Unauthorized. Mantemos uma cópia recuperável ao
  # lado do original em vez de apagar dados do usuário.
  if [[ "${PANEL_DISABLE_WORLD_OPTION:-true}" != "true" || ! -d "$SAVE_ROOT" ]]; then
    return
  fi

  local world_option destino sufixo
  while IFS= read -r -d '' world_option; do
    sufixo="$(date -u +%Y%m%dT%H%M%SZ)"
    destino="${world_option}.disabled-by-panel-${sufixo}"
    while [[ -e "$destino" ]]; do
      sufixo="${sufixo}-1"
      destino="${world_option}.disabled-by-panel-${sufixo}"
    done
    mv -- "$world_option" "$destino"
    echo "[palworld-bootstrap] WorldOption.sav desativado porque sobrescreve o INI; backup: $destino" >&2
  done < <(find "$SAVE_ROOT" -type f -name 'WorldOption.sav' -print0)
}

ler_senha_ini() {
  python3 - "$INI" <<'PY'
import re
import sys

path = sys.argv[1]
try:
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        text = fh.read()
except OSError:
    raise SystemExit(0)

m = re.search(r'AdminPassword="([^"\r\n]*)"', text)
if m and m.group(1):
    print(m.group(1), end="")
PY
}

# Ordem: variável explicitamente informada > INI existente > segredo persistido
# > segredo gerado automaticamente no primeiro boot.
admin_password="${ADMIN_PASSWORD:-}"
if [[ -z "$admin_password" && -s "$INI" ]]; then
  admin_password="$(ler_senha_ini || true)"
fi
if [[ -z "$admin_password" && -s "$SECRET" ]]; then
  admin_password="$(cat "$SECRET")"
fi
if [[ -z "$admin_password" ]]; then
  admin_password="$(python3 - <<'PY'
import secrets
print(secrets.token_urlsafe(24), end="")
PY
)"
  echo "[palworld-bootstrap] ADMIN_PASSWORD não configurada; uma senha interna forte foi gerada." >&2
fi

export ADMIN_PASSWORD="$admin_password"
printf '%s' "$ADMIN_PASSWORD" > "$SECRET"
chmod 600 "$SECRET" 2>/dev/null || true

desativar_world_option

# Se já existe configuração real, preserva todas as opções do usuário e altera
# somente o necessário para o painel: senha administrativa + REST API local.
if [[ -s "$INI" ]]; then
  python3 - "$INI" "$ADMIN_PASSWORD" <<'PY'
import os
import re
import sys
import tempfile

path, password = sys.argv[1], sys.argv[2]
with open(path, "r", encoding="utf-8", errors="replace") as fh:
    text = fh.read()


def quoted(value: str) -> str:
    return '"' + value.replace('\\', '\\\\').replace('"', '\\"') + '"'


def set_option(source: str, key: str, value: str) -> str:
    pattern = rf'(?<![A-Za-z0-9_]){re.escape(key)}=("(?:\\.|[^"\\])*"|[^,\)\r\n]*)'
    replacement = f"{key}={value}"
    if re.search(pattern, source):
        return re.sub(pattern, lambda _m: replacement, source, count=1)

    # Configurações do Palworld normalmente ficam em OptionSettings=(...).
    # Caso a chave não exista, insere antes do último ')' sem reformatar o INI.
    pos = source.rfind(")")
    if pos >= 0:
        sep = "" if source[:pos].rstrip().endswith("(") else ","
        return source[:pos] + sep + replacement + source[pos:]
    return source


text = set_option(text, "AdminPassword", quoted(password))
text = set_option(text, "RESTAPIEnabled", "True")
text = set_option(text, "RESTAPIPort", "8212")

directory = os.path.dirname(path)
fd, tmp = tempfile.mkstemp(prefix=".PalWorldSettings.", suffix=".tmp", dir=directory)
try:
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)
finally:
    try:
        os.unlink(tmp)
    except FileNotFoundError:
        pass
PY

  # A imagem oficial recompila o INI a partir das envs em todo boot. Desliga
  # isso somente quando já existe um INI para não apagar nome, senha do mundo,
  # rates e demais configurações feitas pelo usuário/painel.
  export DISABLE_GENERATE_SETTINGS=true
  echo "[palworld-bootstrap] INI existente preservado; REST API interna habilitada na porta 8212." >&2
else
  # Primeiro boot: deixa a imagem gerar a configuração normalmente, mas já com
  # REST API e senha administrativa fortes. Nos boots seguintes o INI será
  # preservado pelo bloco acima.
  export REST_API_ENABLED=true
  export REST_API_PORT=8212
  export DISABLE_GENERATE_SETTINGS=false
  echo "[palworld-bootstrap] Primeiro boot: configuração inicial será gerada pela imagem." >&2
fi

if [[ "${PALWORLD_BOOTSTRAP_TEST_ONLY:-false}" == "true" ]]; then
  exit 0
fi

exec /home/steam/server/init.sh
