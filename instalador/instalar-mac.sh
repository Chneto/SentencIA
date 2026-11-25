#!/usr/bin/env bash
# Instalador do SentencIA para macOS (Apple Silicon e Intel), sem conta de desenvolvedor Apple.
#
# Uso recomendado no Terminal (arquivos baixados por curl não recebem marca de quarentena):
#   curl -fsSL https://github.com/<dono>/SentencIA/releases/latest/download/instalar-mac.sh | bash
#
# O que este script faz (sem sudo):
#   1. Baixa o pacote SentencIA-vX.Y.Z.zip e confere o hash SHA-256 oficial;
#   2. Instala (ou atualiza) o programa em ~/Applications/SentencIA;
#   3. Configura os dados em ~/Library/Application Support/SentencIA (SENTENCIA_DATA_DIR);
#   4. Executa instalador/bootstrap.sh (uv + Python 3.12 + ocr-macos, seguranca, transcricao, nuvem);
#   5. Monta ~/Applications/SentencIA.app localmente (sem quarentena do Gatekeeper) com AppIcon.icns;
#   6. Instala ~/Applications/SentencIA/desinstalar.command (padrão: manter os dados do gabinete).
#
# Opções (úteis para atualização, uso offline e CI):
#   --repo <dono/repo>       repositório GitHub (padrão: SENTENCIA_REPO ou Chneto/SentencIA)
#   --versao <tag|latest>    versão a baixar (padrão: latest)
#   --pacote-local <zip>     usa um arquivo .zip já baixado (não acessa a internet)
#   --sha256-local <arquivo> arquivo SHA256SUMS.txt correspondente ao pacote local
#   --destino <pasta>        pasta do programa (padrão: ~/Applications/SentencIA)
#   --app-destino <caminho>  caminho do bundle .app (padrão: ~/Applications/SentencIA.app)
#   --dados <pasta>          pasta de dados (padrão: ~/Library/Application Support/SentencIA)
#   --sem-perguntas          modo não interativo
#   --sem-iniciar            instala/atualiza sem abrir o SentencIA ao final
#   --sem-navegador          inicia o servidor sem abrir o navegador
#   --sem-nuvem              não instala o extra opcional de nuvem (Gemini)
#   -h, --ajuda              mostra esta ajuda
set -Eeuo pipefail

# Apple Silicon rodando sob Rosetta: reexecuta nativamente em arm64.
if [ "$(uname -s)" = "Darwin" ] && [ "${SENTENCIA_SEM_REEXEC:-0}" != "1" ]; then
    if [ "$(sysctl -n sysctl.proc_translated 2>/dev/null || echo 0)" = "1" ]; then
        export SENTENCIA_SEM_REEXEC=1
        if [ -f "${BASH_SOURCE[0]:-}" ]; then
            exec arch -arm64 /bin/bash "${BASH_SOURCE[0]}" "$@"
        fi
    fi
fi

REPO="${SENTENCIA_REPO:-Chneto/SentencIA}"
VERSAO="${SENTENCIA_VERSAO:-latest}"
PACOTE_LOCAL=""
SHA256_LOCAL=""
PASTA_APP_DIR="${SENTENCIA_DESTINO:-$HOME/Applications/SentencIA}"
CAMINHO_APP_BUNDLE="${SENTENCIA_APP_BUNDLE:-$HOME/Applications/SentencIA.app}"
PASTA_DADOS="${SENTENCIA_DATA_DIR:-$HOME/Library/Application Support/SentencIA}"
SEM_PERGUNTAS=0
SEM_INICIAR=0
SEM_NAVEGADOR=0
COM_NUVEM=1

mostrar_ajuda() {
    cat <<'EOF'
Instalador do SentencIA para macOS (sem conta Apple, sem sudo).

Uso:
  curl -fsSL https://github.com/<dono>/SentencIA/releases/latest/download/instalar-mac.sh | bash

Opções:
  --repo <dono/repo>       repositório GitHub (padrão: Chneto/SentencIA)
  --versao <tag|latest>    versão (ex.: v2.4.0 ou latest)
  --pacote-local <zip>     instala a partir de um .zip local
  --sha256-local <arquivo> arquivo SHA256SUMS.txt do pacote local
  --destino <pasta>        pasta de instalação (padrão: ~/Applications/SentencIA)
  --app-destino <caminho>  atalho .app (padrão: ~/Applications/SentencIA.app)
  --dados <pasta>          pasta de dados (padrão: ~/Library/Application Support/SentencIA)
  --sem-perguntas          não faz perguntas interativas
  --sem-iniciar            não abre o programa ao concluir
  --sem-navegador          sobe o servidor sem abrir o navegador
  --sem-nuvem              omite o extra de nuvem (Gemini)
  -h, --ajuda              exibe esta ajuda
EOF
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --repo)
            [ "$#" -ge 2 ] || { echo "ERRO: --repo exige um valor." >&2; exit 2; }
            REPO="$2"; shift 2 ;;
        --versao)
            [ "$#" -ge 2 ] || { echo "ERRO: --versao exige um valor." >&2; exit 2; }
            VERSAO="$2"; shift 2 ;;
        --pacote-local)
            [ "$#" -ge 2 ] || { echo "ERRO: --pacote-local exige um caminho." >&2; exit 2; }
            PACOTE_LOCAL="$2"; shift 2 ;;
        --sha256-local)
            [ "$#" -ge 2 ] || { echo "ERRO: --sha256-local exige um caminho." >&2; exit 2; }
            SHA256_LOCAL="$2"; shift 2 ;;
        --destino)
            [ "$#" -ge 2 ] || { echo "ERRO: --destino exige um caminho." >&2; exit 2; }
            PASTA_APP_DIR="$2"; shift 2 ;;
        --app-destino)
            [ "$#" -ge 2 ] || { echo "ERRO: --app-destino exige um caminho." >&2; exit 2; }
            CAMINHO_APP_BUNDLE="$2"; shift 2 ;;
        --dados)
            [ "$#" -ge 2 ] || { echo "ERRO: --dados exige um caminho." >&2; exit 2; }
            PASTA_DADOS="$2"; shift 2 ;;
        --sem-perguntas) SEM_PERGUNTAS=1; shift ;;
        --sem-iniciar) SEM_INICIAR=1; shift ;;
        --sem-navegador) SEM_NAVEGADOR=1; shift ;;
        --sem-nuvem) COM_NUVEM=0; shift ;;
        -h|--ajuda|--help) mostrar_ajuda; exit 0 ;;
        *) echo "ERRO: opção desconhecida: $1 (use --ajuda)." >&2; exit 2 ;;
    esac
done

if [ ! -t 0 ]; then
    SEM_PERGUNTAS=1
fi

if [ -t 1 ]; then
    COR_ETAPA=$'\033[1;36m'
    COR_OK=$'\033[32m'
    COR_AVISO=$'\033[33m'
    COR_ERRO=$'\033[1;31m'
    COR_FIM=$'\033[0m'
else
    COR_ETAPA=""
    COR_OK=""
    COR_AVISO=""
    COR_ERRO=""
    COR_FIM=""
fi

etapa() { printf '\n%s==> %s%s\n' "$COR_ETAPA" "$1" "$COR_FIM"; }
info()  { printf '    %s\n' "$1"; }
ok()    { printf '    %sOK:%s %s\n' "$COR_OK" "$COR_FIM" "$1"; }
aviso() { printf '    %sAVISO:%s %s\n' "$COR_AVISO" "$COR_FIM" "$1"; }
erro()  { printf '\n    %sERRO:%s %s\n' "$COR_ERRO" "$COR_FIM" "$1" >&2; exit 1; }

TMP_DIR=""
limpar_tmp() {
    if [ -n "$TMP_DIR" ] && [ -d "$TMP_DIR" ]; then
        rm -rf "$TMP_DIR"
    fi
}
trap limpar_tmp EXIT

verificar_ambiente() {
    etapa "Verificando este Mac"
    local so arch versao_mac
    so="$(uname -s)"
    arch="$(uname -m)"
    if [ "$so" != "Darwin" ]; then
        erro "Este instalador é exclusivo para macOS (sistema detectado: $so)."
    fi
    versao_mac="$(sw_vers -productVersion 2>/dev/null || echo 'desconhecida')"
    if [ "$arch" = "arm64" ]; then
        ok "macOS $versao_mac (Apple Silicon — arm64)."
    else
        ok "macOS $versao_mac (Intel — $arch)."
    fi

    if ! command -v curl >/dev/null 2>&1; then
        erro "O comando 'curl' não foi encontrado. Instale as ferramentas de linha de comando da Apple com: xcode-select --install"
    fi
    if ! command -v unzip >/dev/null 2>&1; then
        erro "O comando 'unzip' não foi encontrado neste Mac."
    fi
    if ! command -v shasum >/dev/null 2>&1; then
        erro "O comando 'shasum' não foi encontrado neste Mac."
    fi

    if command -v xcode-select >/dev/null 2>&1; then
        if ! xcode-select -p >/dev/null 2>&1; then
            aviso "As Ferramentas de Linha de Comando da Apple (Xcode Command Line Tools) não estão instaladas."
            info "Na maioria dos Macs o SentencIA instala sem elas. Se alguma etapa pedir compilação,"
            info "execute no Terminal: xcode-select --install  e rode este instalador novamente."
        fi
    fi
}

baixar_arquivo() {
    local url="$1"
    local destino="$2"
    if ! curl -fL --retry 3 --retry-delay 2 --connect-timeout 20 -o "$destino" "$url"; then
        printf '\n    %sERRO:%s Não foi possível baixar:\n      %s\n' "$COR_ERRO" "$COR_FIM" "$url" >&2
        printf '    O que fazer:\n' >&2
        printf '      1. Verifique sua conexão com a internet (e se a rede/VPN do tribunal bloqueia o GitHub).\n' >&2
        printf '      2. Tente novamente em alguns instantes.\n' >&2
        printf '      3. Se você já baixou o arquivo .zip manualmente, rode:\n' >&2
        printf '         bash instalar-mac.sh --pacote-local ~/Downloads/SentencIA-vX.Y.Z.zip\n' >&2
        exit 1
    fi
}

resolver_pacote() {
    etapa "Obtendo o pacote do SentencIA"
    TMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/sentencia-mac-XXXXXX")"
    ARQUIVO_ZIP="$TMP_DIR/SentencIA.zip"
    ARQUIVO_SHA="$TMP_DIR/SHA256SUMS.txt"

    if [ -n "$PACOTE_LOCAL" ]; then
        [ -f "$PACOTE_LOCAL" ] || erro "Pacote local não encontrado: $PACOTE_LOCAL"
        cp "$PACOTE_LOCAL" "$ARQUIVO_ZIP"
        NOME_ZIP_ORIGINAL="$(basename "$PACOTE_LOCAL")"
        ok "Usando pacote local: $NOME_ZIP_ORIGINAL"
        if [ -n "$SHA256_LOCAL" ]; then
            [ -f "$SHA256_LOCAL" ] || erro "Arquivo SHA-256 local não encontrado: $SHA256_LOCAL"
            cp "$SHA256_LOCAL" "$ARQUIVO_SHA"
        elif [ -f "$(dirname "$PACOTE_LOCAL")/SHA256SUMS.txt" ]; then
            cp "$(dirname "$PACOTE_LOCAL")/SHA256SUMS.txt" "$ARQUIVO_SHA"
        else
            ARQUIVO_SHA=""
        fi
        return 0
    fi

    local api_url json_release url_zip url_sha
    if [ "$VERSAO" = "latest" ]; then
        api_url="https://api.github.com/repos/$REPO/releases/latest"
    else
        api_url="https://api.github.com/repos/$REPO/releases/tags/$VERSAO"
    fi

    json_release="$TMP_DIR/release.json"
    info "Consultando a versão disponível em github.com/$REPO ($VERSAO)..."
    baixar_arquivo "$api_url" "$json_release"

    url_zip="$(grep -Eo 'https://[^"]+SentencIA-v[0-9]+\.[0-9]+\.[0-9]+\.zip' "$json_release" | head -n 1 || true)"
    url_sha="$(grep -Eo 'https://[^"]+SHA256SUMS\.txt' "$json_release" | head -n 1 || true)"

    if [ -z "$url_zip" ]; then
        erro "Não encontrei o arquivo SentencIA-vX.Y.Z.zip na versão '$VERSAO' de $REPO."
    fi
    NOME_ZIP_ORIGINAL="$(basename "$url_zip")"
    info "Baixando $NOME_ZIP_ORIGINAL..."
    baixar_arquivo "$url_zip" "$ARQUIVO_ZIP"

    if [ -n "$url_sha" ]; then
        info "Baixando SHA256SUMS.txt para conferência de integridade..."
        baixar_arquivo "$url_sha" "$ARQUIVO_SHA"
    else
        erro "O arquivo SHA256SUMS.txt não foi encontrado na release. Por segurança, a instalação foi interrompida."
    fi
}

conferir_sha256() {
    if [ -z "${ARQUIVO_SHA:-}" ] || [ ! -f "$ARQUIVO_SHA" ]; then
        aviso "SHA256SUMS.txt não fornecido para o pacote local; pulando conferência de hash."
        return 0
    fi
    etapa "Conferindo a integridade do arquivo (SHA-256)"
    local esperado calculado
    esperado="$(awk -v alvo="$NOME_ZIP_ORIGINAL" '{ gsub(/\r$/, "", $2) } $2 == alvo || $2 == ("*" alvo) { print $1; exit }' "$ARQUIVO_SHA")"
    if [ -z "$esperado" ]; then
        esperado="$(awk 'NR == 1 { print $1 }' "$ARQUIVO_SHA")"
    fi
    [ -n "$esperado" ] || erro "Arquivo SHA256SUMS.txt vazio ou inválido."
    calculado="$(shasum -a 256 "$ARQUIVO_ZIP" | awk '{ print $1 }')"
    if [ "$calculado" != "$esperado" ]; then
        erro "O arquivo baixado não confere com o SHA-256 oficial (esperado: $esperado; obtido: $calculado). Tente baixar novamente."
    fi
    ok "Integridade confirmada (SHA-256 válido)."
}

instalar_arquivos() {
    etapa "Instalando o SentencIA em $PASTA_APP_DIR"
    local pasta_extraida raiz_extraida
    pasta_extraida="$TMP_DIR/extraido"
    mkdir -p "$pasta_extraida"
    unzip -q "$ARQUIVO_ZIP" -d "$pasta_extraida"

    raiz_extraida="$(find "$pasta_extraida" -mindepth 1 -maxdepth 1 -type d | head -n 1 || true)"
    if [ -z "$raiz_extraida" ] || [ ! -f "$raiz_extraida/pyproject.toml" ]; then
        raiz_extraida="$pasta_extraida"
    fi
    [ -f "$raiz_extraida/pyproject.toml" ] || erro "O pacote baixado não contém pyproject.toml."

    mkdir -p "$(dirname "$PASTA_APP_DIR")"
    mkdir -p "$PASTA_APP_DIR"

    # Se já havia servidor rodando desta instalação, encerra antes de atualizar.
    if [ -x "$PASTA_APP_DIR/.venv/bin/python" ] && [ -f "$PASTA_APP_DIR/launcher.py" ]; then
        SENTENCIA_DATA_DIR="$PASTA_DADOS" "$PASTA_APP_DIR/.venv/bin/python" "$PASTA_APP_DIR/launcher.py" --parar >/dev/null 2>&1 || true
    fi

    # Atualiza arquivos do programa preservando .venv, data/ e .pasta_dados para atualização rápida e segura.
    if command -v rsync >/dev/null 2>&1; then
        rsync -a --delete --exclude '.venv' --exclude 'data' --exclude '.pasta_dados' "$raiz_extraida/" "$PASTA_APP_DIR/"
    else
        # Fallback sem rsync: remove itens antigos exceto .venv, data e .pasta_dados e copia os novos.
        local item nome
        for item in "$PASTA_APP_DIR"/* "$PASTA_APP_DIR"/.[!.]*; do
            [ -e "$item" ] || continue
            nome="$(basename "$item")"
            if [ "$nome" = ".venv" ] || [ "$nome" = "data" ] || [ "$nome" = ".pasta_dados" ]; then
                continue
            fi
            rm -rf "$item"
        done
        cp -R "$raiz_extraida"/. "$PASTA_APP_DIR"/
    fi

    chmod +x "$PASTA_APP_DIR/Iniciar SentencIA.command" "$PASTA_APP_DIR/instalador/bootstrap.sh" 2>/dev/null || true
    if [ -f "$PASTA_APP_DIR/SentencIA.app/Contents/MacOS/sentencia" ]; then
        chmod +x "$PASTA_APP_DIR/SentencIA.app/Contents/MacOS/sentencia" 2>/dev/null || true
    fi

    mkdir -p "$PASTA_DADOS"
    printf '%s\n' "$PASTA_DADOS" > "$PASTA_APP_DIR/.pasta_dados"
    ok "Arquivos instalados em $PASTA_APP_DIR"
    ok "Pasta de dados configurada e persistida em $PASTA_DADOS"
}

executar_bootstrap() {
    etapa "Configurando Python e bibliotecas locais (uv + Python 3.12)"
    local args_bootstrap
    args_bootstrap=(--sem-iniciar)
    if [ "$SEM_PERGUNTAS" = "1" ]; then
        args_bootstrap+=(--sem-perguntas)
    fi
    if [ "$COM_NUVEM" = "1" ]; then
        args_bootstrap+=(--nuvem)
    fi
    SENTENCIA_DATA_DIR="$PASTA_DADOS" /bin/bash "$PASTA_APP_DIR/instalador/bootstrap.sh" "${args_bootstrap[@]}"
    ok "Ambiente Python pronto."
}

ler_versao_instalada() {
    local v
    v="$(awk -F'"' '/^version[[:space:]]*=/ { print $2; exit }' "$PASTA_APP_DIR/pyproject.toml" 2>/dev/null || true)"
    printf '%s' "${v:-2.4.0}"
}

montar_app_local() {
    etapa "Montando o aplicativo local em $CAMINHO_APP_BUNDLE (sem quarentena)"
    local versao icone_origem
    versao="$(ler_versao_instalada)"

    mkdir -p "$(dirname "$CAMINHO_APP_BUNDLE")"
    rm -rf "$CAMINHO_APP_BUNDLE"
    mkdir -p "$CAMINHO_APP_BUNDLE/Contents/MacOS" "$CAMINHO_APP_BUNDLE/Contents/Resources"

    icone_origem="$PASTA_APP_DIR/SentencIA.app/Contents/Resources/AppIcon.icns"
    if [ -f "$icone_origem" ]; then
        cp "$icone_origem" "$CAMINHO_APP_BUNDLE/Contents/Resources/AppIcon.icns"
    fi

    cat > "$CAMINHO_APP_BUNDLE/Contents/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
	<key>CFBundleDevelopmentRegion</key>
	<string>pt_BR</string>
	<key>CFBundleDisplayName</key>
	<string>SentencIA</string>
	<key>CFBundleExecutable</key>
	<string>sentencia</string>
	<key>CFBundleIconFile</key>
	<string>AppIcon</string>
	<key>CFBundleIdentifier</key>
	<string>local.fchneto.sentencia</string>
	<key>CFBundleInfoDictionaryVersion</key>
	<string>6.0</string>
	<key>CFBundleName</key>
	<string>SentencIA</string>
	<key>CFBundlePackageType</key>
	<string>APPL</string>
	<key>CFBundleShortVersionString</key>
	<string>${versao}</string>
	<key>CFBundleVersion</key>
	<string>${versao}</string>
	<key>LSMinimumSystemVersion</key>
	<string>13.0</string>
	<key>LSUIElement</key>
	<true/>
	<key>NSHighResolutionCapable</key>
	<true/>
	<key>NSHumanReadableCopyright</key>
	<string>FChNeto</string>
</dict>
</plist>
EOF

    cat > "$CAMINHO_APP_BUNDLE/Contents/MacOS/sentencia" <<EOF
#!/bin/bash
# Aplicativo local do SentencIA (montado neste Mac pelo instalador — sem quarentena).
set -euo pipefail

export SENTENCIA_DATA_DIR="\${SENTENCIA_DATA_DIR:-${PASTA_DADOS}}"
PASTA_PROGRAMA="${PASTA_APP_DIR}"
PYTHON_VENV="\$PASTA_PROGRAMA/.venv/bin/python"

if [ -x "\$PYTHON_VENV" ] && [ -f "\$PASTA_PROGRAMA/launcher.py" ]; then
    cd "\$PASTA_PROGRAMA"
    exec "\$PYTHON_VENV" "\$PASTA_PROGRAMA/launcher.py"
fi

if [ -f "\$PASTA_PROGRAMA/Iniciar SentencIA.command" ]; then
    exec /usr/bin/open -a Terminal "\$PASTA_PROGRAMA/Iniciar SentencIA.command"
fi

/usr/bin/osascript -e 'display dialog "Não encontrei os arquivos do SentencIA em ~/Applications/SentencIA. Reexecute o instalador no Terminal." with title "SentencIA" buttons {"OK"} default button 1 with icon caution' >/dev/null 2>&1 || true
exit 1
EOF
    chmod 755 "$CAMINHO_APP_BUNDLE/Contents/MacOS/sentencia"
    ok "Aplicativo criado em $CAMINHO_APP_BUNDLE (pronto para abrir pelo Launchpad/Dock/Finder)."
}

instalar_desinstalador() {
    local origem destino
    origem="$PASTA_APP_DIR/instalador/desinstalar-mac.sh"
    destino="$PASTA_APP_DIR/desinstalar.command"
    if [ -f "$origem" ]; then
        cp "$origem" "$destino"
    else
        cat > "$destino" <<EOF
#!/usr/bin/env bash
set -euo pipefail
PASTA_APP_DIR="${PASTA_APP_DIR}"
CAMINHO_APP_BUNDLE="${CAMINHO_APP_BUNDLE}"
PASTA_DADOS="${PASTA_DADOS}"
printf 'Desinstalação do SentencIA\n'
if [ -x "\$PASTA_APP_DIR/.venv/bin/python" ] && [ -f "\$PASTA_APP_DIR/launcher.py" ]; then
    SENTENCIA_DATA_DIR="\$PASTA_DADOS" "\$PASTA_APP_DIR/.venv/bin/python" "\$PASTA_APP_DIR/launcher.py" --parar >/dev/null 2>&1 || true
fi
rm -rf "\$CAMINHO_APP_BUNDLE" "\$PASTA_APP_DIR"
printf 'Programa removido. Seus dados foram mantidos em: %s\n' "\$PASTA_DADOS"
EOF
    fi
    chmod 755 "$destino"
    ok "Desinstalador disponível em $destino"
}

main() {
    printf '\n  SentencIA — instalador para macOS\n'
    printf '  Aplicação web local (100%% no seu computador — nenhum processo vai para a internet).\n'

    verificar_ambiente
    resolver_pacote
    conferir_sha256
    instalar_arquivos
    executar_bootstrap
    montar_app_local
    instalar_desinstalador

    etapa "Instalação concluída!"
    info "Para abrir o SentencIA, use 'SentencIA' no Launchpad, na pasta ~/Applications ou no Dock."
    info "Seus autos, configurações e modelos ficam em: $PASTA_DADOS"
    info "Para remover o programa depois: abra $PASTA_APP_DIR/desinstalar.command"

    if [ "$SEM_INICIAR" != "1" ]; then
        local args_launcher=()
        if [ "$SEM_NAVEGADOR" = "1" ]; then
            args_launcher+=(--sem-navegador)
        fi
        info "Iniciando o SentencIA..."
        cd "$PASTA_APP_DIR"
        SENTENCIA_DATA_DIR="$PASTA_DADOS" "$PASTA_APP_DIR/.venv/bin/python" "$PASTA_APP_DIR/launcher.py" "${args_launcher[@]}"
    fi
}

main "$@"
