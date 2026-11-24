#!/usr/bin/env bash
# Desinstalador do SentencIA para macOS (~/Applications/SentencIA/desinstalar.command).
# Padrão seguro: MANTÉM os dados do gabinete em ~/Library/Application Support/SentencIA.
#
# Opções (para uso não interativo/testes):
#   --destino <pasta>       pasta do programa (padrão: ~/Applications/SentencIA)
#   --app-destino <caminho> bundle .app (padrão: ~/Applications/SentencIA.app)
#   --dados <pasta>         pasta de dados (padrão: ~/Library/Application Support/SentencIA)
#   --manter-dados          mantém os dados sem perguntar (padrão)
#   --apagar-dados          remove também a pasta de dados (requer --sim ou confirmação)
#   --sim                   confirma a remoção do programa sem perguntar
set -euo pipefail

PASTA_APP_DIR="${SENTENCIA_DESTINO:-$HOME/Applications/SentencIA}"
CAMINHO_APP_BUNDLE="${SENTENCIA_APP_BUNDLE:-$HOME/Applications/SentencIA.app}"
PASTA_DADOS="${SENTENCIA_DATA_DIR:-}"
CONFIRMAR_AUTO=0
MODO_DADOS="perguntar"

while [ "$#" -gt 0 ]; do
    case "$1" in
        --destino)
            [ "$#" -ge 2 ] || { echo "ERRO: --destino exige um caminho." >&2; exit 2; }
            PASTA_APP_DIR="$2"; shift 2 ;;
        --app-destino)
            [ "$#" -ge 2 ] || { echo "ERRO: --app-destino exige um caminho." >&2; exit 2; }
            CAMINHO_APP_BUNDLE="$2"; shift 2 ;;
        --dados)
            [ "$#" -ge 2 ] || { echo "ERRO: --dados exige um caminho." >&2; exit 2; }
            PASTA_DADOS="$2"; shift 2 ;;
        --manter-dados) MODO_DADOS="manter"; shift ;;
        --apagar-dados) MODO_DADOS="apagar"; shift ;;
        --sim) CONFIRMAR_AUTO=1; shift ;;
        *) echo "ERRO: opção desconhecida: $1" >&2; exit 2 ;;
    esac
done

if [ -z "$PASTA_DADOS" ]; then
    if [ -f "$PASTA_APP_DIR/.pasta_dados" ]; then
        PASTA_DADOS="$(awk 'NF && $0 !~ /^[[:space:]]*#/ { gsub(/^[[:space:]]+|[[:space:]]+$/, ""); print; exit }' "$PASTA_APP_DIR/.pasta_dados" 2>/dev/null || true)"
    fi
    PASTA_DADOS="${PASTA_DADOS:-$HOME/Library/Application Support/SentencIA}"
fi

printf '\n  Desinstalador do SentencIA (macOS)\n\n'
printf '  Serão removidos:\n'
printf '    - Aplicativo: %s\n' "$CAMINHO_APP_BUNDLE"
printf '    - Programa:   %s\n' "$PASTA_APP_DIR"
printf '  Pasta de dados do gabinete:\n'
printf '    - %s\n\n' "$PASTA_DADOS"

if [ "$CONFIRMAR_AUTO" != "1" ]; then
    read -r -p "  Deseja remover o programa SentencIA deste Mac? [s/N]: " resp || resp=""
    resp="$(printf '%s' "$resp" | tr '[:upper:]' '[:lower:]' | tr -d '[:space:]')"
    case "$resp" in
        s|sim|y|yes) ;;
        *) printf '  Operação cancelada. Nada foi removido.\n'; exit 0 ;;
    esac
fi

APAGAR_DADOS=0
if [ "$MODO_DADOS" = "apagar" ]; then
    if [ "$CONFIRMAR_AUTO" = "1" ]; then
        APAGAR_DADOS=1
    else
        read -r -p "  Deseja APAGAR também os dados do gabinete (autos, configurações e precedentes)? [s/N]: " resp_dados || resp_dados=""
        resp_dados="$(printf '%s' "$resp_dados" | tr '[:upper:]' '[:lower:]' | tr -d '[:space:]')"
        case "$resp_dados" in
            s|sim|y|yes) APAGAR_DADOS=1 ;;
            *) APAGAR_DADOS=0 ;;
        esac
    fi
elif [ "$MODO_DADOS" = "perguntar" ] && [ "$CONFIRMAR_AUTO" != "1" ]; then
    read -r -p "  Manter seus dados do gabinete em '$PASTA_DADOS'? [S/n]: " resp_manter || resp_manter=""
    resp_manter="$(printf '%s' "$resp_manter" | tr '[:upper:]' '[:lower:]' | tr -d '[:space:]')"
    case "$resp_manter" in
        n|nao|não|no) APAGAR_DADOS=1 ;;
        *) APAGAR_DADOS=0 ;;
    esac
fi

if [ -x "$PASTA_APP_DIR/.venv/bin/python" ] && [ -f "$PASTA_APP_DIR/launcher.py" ]; then
    printf '  Encerrando o servidor do SentencIA (se estiver aberto)...\n'
    SENTENCIA_DATA_DIR="$PASTA_DADOS" "$PASTA_APP_DIR/.venv/bin/python" "$PASTA_APP_DIR/launcher.py" --parar >/dev/null 2>&1 || true
fi

if [ -e "$CAMINHO_APP_BUNDLE" ]; then
    rm -rf "$CAMINHO_APP_BUNDLE"
    printf '  Removido: %s\n' "$CAMINHO_APP_BUNDLE"
fi

if [ -e "$PASTA_APP_DIR" ]; then
    rm -rf "$PASTA_APP_DIR"
    printf '  Removido: %s\n' "$PASTA_APP_DIR"
fi

if [ "$APAGAR_DADOS" = "1" ] && [ -e "$PASTA_DADOS" ]; then
    rm -rf "$PASTA_DADOS"
    printf '  Dados removidos: %s\n' "$PASTA_DADOS"
else
    printf '  Seus dados foram mantidos com segurança em: %s\n' "$PASTA_DADOS"
fi

printf '\n  Desinstalação concluída.\n'
