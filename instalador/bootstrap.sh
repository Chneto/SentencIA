#!/usr/bin/env bash
# Instalador e iniciador do SentencIA para macOS (Apple Silicon e Intel) e Linux.
# Idempotente e sem sudo: o uv e o Python ficam no perfil do usuário. Compatível com o bash 3.2
# do macOS. Registro completo em data/logs/bootstrap.log.
#
# Etapas: pasta gravável -> uv (instalador oficial astral.sh) -> Python 3.12 -> uv sync com os extras
# da plataforma (um extra opcional que falhe é omitido, com aviso) -> Ollama opcional (S/n) ->
# launcher.py (servidor + navegador). A instalação fica marcada em data/.instalado e só é refeita
# quando pyproject.toml/uv.lock mudarem (ou com --reinstalar).
#
# Opções: --sem-perguntas  --reinstalar  --nuvem  --sem-iniciar  --sem-navegador  --ajuda
# -E: o trap de ERR vale também dentro das funções (sem ele, uma falha lá encerraria em silêncio).
set -Eeuo pipefail

VERSAO_APP="2.3.0"   # conferido por tests/test_versao_coerente.py (igual ao pyproject.toml)
VERSAO_PYTHON="3.12"
UV_VERSAO_MINIMA="0.8.0"   # lê o uv.lock (revision 3) e o [tool.uv] do pyproject.toml

# Apple Silicon rodando sob Rosetta: reexecuta nativamente (arm64) para instalar o Python nativo.
if [ "$(uname -s)" = "Darwin" ] && [ "${SENTENCIA_SEM_REEXEC:-0}" != "1" ]; then
    if [ "$(sysctl -n sysctl.proc_translated 2>/dev/null || echo 0)" = "1" ]; then
        export SENTENCIA_SEM_REEXEC=1
        exec arch -arm64 /bin/bash "${BASH_SOURCE[0]}" "$@"
    fi
fi

RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
cd "$RAIZ"

SEM_PERGUNTAS=0
REINSTALAR=0
NUVEM=0
SEM_INICIAR=0
SEM_NAVEGADOR=0
for arg in "$@"; do
    case "$arg" in
        --sem-perguntas) SEM_PERGUNTAS=1 ;;
        --reinstalar) REINSTALAR=1 ;;
        --nuvem) NUVEM=1 ;;
        --sem-iniciar) SEM_INICIAR=1 ;;
        --sem-navegador) SEM_NAVEGADOR=1 ;;
        -h|--ajuda|--help)
            awk 'NR > 1 && /^#/ { sub(/^# ?/, ""); print; next } NR > 1 { exit }' "${BASH_SOURCE[0]}"
            exit 0 ;;
        *) echo "Opção desconhecida: $arg (use --ajuda)"; exit 2 ;;
    esac
done
# Sem terminal interativo não há como perguntar: assume o modo silencioso.
if [ ! -t 0 ]; then SEM_PERGUNTAS=1; fi

PASTA_DADOS_PROJETO="$RAIZ/data"
if [ -n "${SENTENCIA_DATA_DIR:-}" ]; then
    PASTA_DADOS="$SENTENCIA_DATA_DIR"
elif [ -f "$RAIZ/.pasta_dados" ]; then
    _linha_dados="$(awk 'NF && $0 !~ /^[[:space:]]*#/ { gsub(/^[[:space:]]+|[[:space:]]+$/, ""); print; exit }' "$RAIZ/.pasta_dados" 2>/dev/null || true)"
    PASTA_DADOS="${_linha_dados:-$PASTA_DADOS_PROJETO}"
else
    PASTA_DADOS="$PASTA_DADOS_PROJETO"
fi
PASTA_LOGS="$PASTA_DADOS/logs"
ARQUIVO_LOG="$PASTA_LOGS/bootstrap.log"
ARQUIVO_MARCA="$PASTA_DADOS_PROJETO/.instalado"
PYTHON_VENV="$RAIZ/.venv/bin/python"

export PYTHONUTF8=1
export PYTHONIOENCODING=utf-8
export UV_LINK_MODE=copy
export UV_NO_PROGRESS=1
export SENTENCIA_LAUNCHER_CONSOLE=1   # o launcher.py escreve aqui (e no log) em vez de abrir diálogos

if [ -t 1 ]; then
    COR_ETAPA=$'\033[1;36m'; COR_OK=$'\033[32m'; COR_AVISO=$'\033[33m'; COR_ERRO=$'\033[1;31m'; COR_CINZA=$'\033[90m'; COR_FIM=$'\033[0m'
else
    COR_ETAPA=""; COR_OK=""; COR_AVISO=""; COR_ERRO=""; COR_CINZA=""; COR_FIM=""
fi

# ----------------------------------------------------------------------------------------------
# Mensagens e registro
# ----------------------------------------------------------------------------------------------
registrar() {
    mkdir -p "$PASTA_LOGS" 2>/dev/null || true
    printf '%s [%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "${2:-INFO}" "$1" >> "$ARQUIVO_LOG" 2>/dev/null || true
}
etapa() { printf '\n%s==> %s%s\n' "$COR_ETAPA" "$1" "$COR_FIM"; registrar "ETAPA: $1"; }
info() { printf '    %s\n' "$1"; registrar "$1"; }
ok() { printf '    %sOK:%s %s\n' "$COR_OK" "$COR_FIM" "$1"; registrar "OK: $1"; }
aviso() { printf '    %sAVISO:%s %s\n' "$COR_AVISO" "$COR_FIM" "$1"; registrar "$1" "AVISO"; }

encerrar_com_erro() {
    printf '\n    %sERRO:%s %s\n' "$COR_ERRO" "$COR_FIM" "$1" >&2
    printf '    Registro completo: %s\n' "$ARQUIVO_LOG" >&2
    registrar "$1" "ERRO"
    if [ "$SEM_PERGUNTAS" != "1" ]; then
        read -r -p "    Pressione Enter para fechar..." _ || true
    fi
    exit 1
}
# shellcheck disable=SC2317,SC2329
tratar_erro() {
    # Em subshell ($(...)) apenas propaga a falha: a mensagem sai uma única vez, no shell principal.
    if [ "${BASH_SUBSHELL:-0}" -gt 0 ]; then exit 1; fi
    encerrar_com_erro "Falha inesperada na linha $1 (veja o registro)."
}
trap 'tratar_erro $LINENO' ERR

perguntar() {
    # perguntar "texto" padrao(s|n) -> retorno 0 = sim. Modo silencioso: nunca instala nada opcional.
    local texto="$1" padrao="$2" sufixo resposta
    if [ "$SEM_PERGUNTAS" = "1" ]; then return 1; fi
    if [ "$padrao" = "s" ]; then sufixo="(S/n)"; else sufixo="(s/N)"; fi
    read -r -p "    $texto $sufixo " resposta || resposta=""
    registrar "PERGUNTA: $texto -> '$resposta'"
    resposta="$(printf '%s' "$resposta" | tr '[:upper:]' '[:lower:]' | tr -d '[:space:]')"
    if [ -z "$resposta" ]; then resposta="$padrao"; fi
    case "$resposta" in s|sim|y|yes) return 0 ;; *) return 1 ;; esac
}

executar() {
    # Mostra e registra a saída (stdout+stderr) de um comando; devolve o código de saída dele.
    registrar "\$ $*"
    local codigo
    set +e
    "$@" 2>&1 | while IFS= read -r linha; do
        printf '      %s%s%s\n' "$COR_CINZA" "$linha" "$COR_FIM"
        printf '      %s\n' "$linha" >> "$ARQUIVO_LOG" 2>/dev/null
    done
    codigo=${PIPESTATUS[0]}
    set -e
    return "$codigo"
}

sha256_de() {
    # Impressão digital do arquivo (só para detectar mudança de pyproject.toml/uv.lock).
    if command -v shasum >/dev/null 2>&1; then shasum -a 256 "$1" | cut -d' ' -f1
    elif command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | cut -d' ' -f1
    elif command -v openssl >/dev/null 2>&1; then openssl dgst -sha256 -r "$1" | cut -d' ' -f1
    elif command -v cksum >/dev/null 2>&1; then cksum < "$1" | awk '{ print "cksum" $1 "x" $2 }'
    else printf 'sem-hash\n'; fi  # sem ferramenta de hash: a instalação é sempre conferida (mais lento, seguro)
}

# ----------------------------------------------------------------------------------------------
# Marca de instalação (data/.instalado, formato chave=valor)
# ----------------------------------------------------------------------------------------------
valor_marca() {
    if [ -f "$ARQUIVO_MARCA" ]; then
        awk -v k="$1" 'index($0, k "=") == 1 { sub(/^[^=]*=/, ""); sub(/\r$/, ""); print; exit }' "$ARQUIVO_MARCA"
    fi
    return 0
}
MARCA_VERSAO="$(valor_marca versao)"
MARCA_HASH="$(valor_marca hash)"
MARCA_PLATAFORMA="$(valor_marca plataforma)"
MARCA_EXTRAS="$(valor_marca extras)"
MARCA_NUVEM="$(valor_marca nuvem)"
MARCA_OLLAMA="$(valor_marca ollama)"
MARCA_MODELO_RECUSADO="$(valor_marca modelo_recusado)"
MARCA_ATALHO="$(valor_marca atalho)"
MARCA_EXTRAS_PENDENTES="$(valor_marca extras_pendentes)"
MARCA_TENTATIVAS_EXTRAS="$(valor_marca tentativas_extras)"
case "$MARCA_TENTATIVAS_EXTRAS" in ''|*[!0-9]*) MARCA_TENTATIVAS_EXTRAS=0 ;; esac

gravar_marca() {
    mkdir -p "$PASTA_DADOS_PROJETO"
    local tmp="$ARQUIVO_MARCA.tmp"
    {
        printf 'atalho=%s\n' "$MARCA_ATALHO"
        printf 'extras=%s\n' "$MARCA_EXTRAS"
        printf 'extras_pendentes=%s\n' "$MARCA_EXTRAS_PENDENTES"
        printf 'tentativas_extras=%s\n' "$MARCA_TENTATIVAS_EXTRAS"
        printf 'hash=%s\n' "$MARCA_HASH"
        printf 'instalado_em=%s\n' "$(date '+%Y-%m-%dT%H:%M:%S')"
        printf 'modelo_recusado=%s\n' "$MARCA_MODELO_RECUSADO"
        printf 'nuvem=%s\n' "$MARCA_NUVEM"
        printf 'ollama=%s\n' "$MARCA_OLLAMA"
        printf 'plataforma=%s\n' "$MARCA_PLATAFORMA"
        printf 'python=%s\n' "$VERSAO_PYTHON"
        printf 'versao=%s\n' "$MARCA_VERSAO"
    } > "$tmp"
    mv -f "$tmp" "$ARQUIVO_MARCA"
}

# ----------------------------------------------------------------------------------------------
# Plataforma
# ----------------------------------------------------------------------------------------------
SO="$(uname -s)"
ARQ="$(uname -m)"
EXTRA_OCR=""
BREW=""
if [ "$SO" = "Darwin" ]; then
    VERSAO_MAC="$(sw_vers -productVersion 2>/dev/null || echo 0)"
    MAIOR_MAC="${VERSAO_MAC%%.*}"
    if [ "$ARQ" = "arm64" ]; then CHIP="Apple Silicon"; else CHIP="Intel"; fi
    PLATAFORMA="macos-$ARQ"
    EXTRA_OCR="ocr-macos"
    for b in /opt/homebrew/bin/brew /usr/local/bin/brew; do
        if [ -x "$b" ]; then BREW="$b"; break; fi
    done
    if [ -z "$BREW" ] && command -v brew >/dev/null 2>&1; then BREW="$(command -v brew)"; fi
    DESCRICAO_SO="macOS $VERSAO_MAC ($CHIP)"
else
    PLATAFORMA="linux-$ARQ"
    DESCRICAO_SO="Linux ($ARQ)"
fi

# ----------------------------------------------------------------------------------------------
# uv, Python, dependências, Ollama
# ----------------------------------------------------------------------------------------------
encontrar_uv() {
    if command -v uv >/dev/null 2>&1; then command -v uv; return 0; fi
    local c
    for c in "$HOME/.local/bin/uv" "$HOME/.cargo/bin/uv" /opt/homebrew/bin/uv /usr/local/bin/uv; do
        if [ -x "$c" ]; then printf '%s\n' "$c"; return 0; fi
    done
    return 1
}

uv_versao_ok() {
    # 0 se o uv em $1 é >= UV_VERSAO_MINIMA (ou se a versão não pôde ser lida).
    local v menor
    v="$("$1" --version 2>/dev/null | sed -n 's/^uv \([0-9][0-9]*\.[0-9][0-9]*\.[0-9][0-9]*\).*/\1/p' | head -n 1)" || v=""
    if [ -z "$v" ]; then return 0; fi
    menor="$(printf '%s\n%s\n' "$UV_VERSAO_MINIMA" "$v" | sort -t. -k1,1n -k2,2n -k3,3n | head -n 1)"
    [ "$menor" = "$UV_VERSAO_MINIMA" ]
}

garantir_uv() {
    local uv
    if uv="$(encontrar_uv)"; then
        if ! uv_versao_ok "$uv"; then
            aviso "uv antigo demais para este projeto (mínimo $UV_VERSAO_MINIMA); atualizando..."
            executar "$uv" self update || true
            if ! uv_versao_ok "$uv"; then
                aviso "Não foi possível atualizar o uv; será instalada uma cópia própria no seu perfil de usuário."
                uv=""
            fi
        fi
    fi
    if [ -n "$uv" ]; then ok "uv encontrado ($uv)"; UV="$uv"; return 0; fi
    info "Instalando o uv (gerenciador de Python) no seu perfil de usuário — não requer senha de administrador..."
    if command -v curl >/dev/null 2>&1; then
        executar sh -c 'curl -LsSf https://astral.sh/uv/install.sh | sh' || true
    elif command -v wget >/dev/null 2>&1; then
        executar sh -c 'wget -qO- https://astral.sh/uv/install.sh | sh' || true
    fi
    if [ -x "$HOME/.local/bin/uv" ]; then uv="$HOME/.local/bin/uv"; else uv="$(encontrar_uv || true)"; fi
    if [ -z "$uv" ]; then
        encerrar_com_erro "Não foi possível instalar o uv. Verifique a internet ou instale manualmente: https://docs.astral.sh/uv/"
    fi
    local uv_dir
    uv_dir="$(dirname "$uv")"
    export PATH="$uv_dir:$PATH"
    UV="$uv"
    ok "uv instalado ($uv)"
}

garantir_python() {
    if executar "$UV" python find "$VERSAO_PYTHON"; then ok "Python $VERSAO_PYTHON disponível"; return 0; fi
    info "Baixando o Python $VERSAO_PYTHON (uma única vez)..."
    local codigo=0
    executar "$UV" python install "$VERSAO_PYTHON" || codigo=$?
    if [ "$codigo" = "0" ]; then executar "$UV" python find "$VERSAO_PYTHON" || codigo=$?; fi
    if [ "$codigo" != "0" ]; then
        # Instalação anterior corrompida (pasta apagada pela metade): reinstala.
        aviso "O Python $VERSAO_PYTHON gerenciado pelo uv parece danificado; reinstalando..."
        codigo=0
        executar "$UV" python install --reinstall "$VERSAO_PYTHON" || codigo=$?
    fi
    if [ "$codigo" != "0" ]; then encerrar_com_erro "Falha ao instalar o Python $VERSAO_PYTHON (código $codigo)."; fi
    ok "Python $VERSAO_PYTHON instalado"
}

sincronizar_dependencias() {
    # Define EXTRAS_INSTALADOS. Tentativas da mais completa para o núcleo mínimo.
    local base="seguranca" nuvem="" t e codigo rotulo
    if [ -n "$EXTRA_OCR" ]; then base="seguranca $EXTRA_OCR"; fi
    if [ "$1" = "1" ]; then nuvem=" nuvem"; fi
    local tentativas=("$base transcricao$nuvem" "$base$nuvem" "seguranca" "")
    if [ -x "$PYTHON_VENV" ]; then executar "$PYTHON_VENV" "$RAIZ/launcher.py" --parar || true; fi
    for t in "${tentativas[@]}"; do
        local args=(sync --frozen --no-dev --python "$VERSAO_PYTHON")
        for e in $t; do args+=(--extra "$e"); done
        if [ -n "$t" ]; then rotulo="$(printf '%s' "$t" | sed 's/ /, /g')"; else rotulo="somente o núcleo"; fi
        info "Instalando dependências ($rotulo)... a primeira vez pode levar alguns minutos."
        # Falhas transitórias de rede não podem reduzir a configuração: repete a MESMA tentativa antes.
        codigo=1
        local rep=1
        while [ "$rep" -le 2 ] && [ "$codigo" != "0" ]; do
            if [ "$rep" -gt 1 ]; then aviso "Repetindo a instalação com ($rotulo) (tentativa $rep de 2)..."; sleep 3; fi
            codigo=0
            executar "$UV" "${args[@]}" || codigo=$?
            rep=$((rep + 1))
        done
        if [ "$codigo" = "0" ]; then
            EXTRAS_INSTALADOS="$(printf '%s' "$t" | sed 's/ /,/g')"
            return 0
        fi
        aviso "A instalação com ($rotulo) falhou (código $codigo); tentando uma configuração menor."
    done
    encerrar_com_erro "Não foi possível instalar as dependências do sistema. Veja o registro: pode ser falha de conexão com a internet, versão antiga do uv ou plataforma sem suporte."
}

encontrar_ollama() {
    if command -v ollama >/dev/null 2>&1; then command -v ollama; return 0; fi
    local c
    for c in /opt/homebrew/bin/ollama /usr/local/bin/ollama /Applications/Ollama.app/Contents/Resources/ollama; do
        if [ -x "$c" ]; then printf '%s\n' "$c"; return 0; fi
    done
    return 1
}

ollama_responde() {
    curl -fsS --max-time 3 "$1/api/tags" >/dev/null 2>&1
}

configurar_ollama() {
    # Define MARCA_OLLAMA (instalado|recusado|ausente) e, se for o caso, MARCA_MODELO_RECUSADO.
    local exe="" modelo="" url="http://127.0.0.1:11434" saida i
    if ! exe="$(encontrar_ollama)"; then
        exe=""
        if [ "$MARCA_OLLAMA" = "recusado" ] && [ "$REINSTALAR" != "1" ]; then
            info "Ollama não instalado (opcional; você recusou antes). A minuta usará o motor determinístico."
            return 0
        fi
        info "O Ollama é OPCIONAL: um modelo de IA local (nada sai do computador) que refina a redação."
        info "Sem ele, o sistema funciona com o motor determinístico. Ocupa ~1 a 3 GB."
        if ! perguntar "Deseja instalar o Ollama agora?" s; then MARCA_OLLAMA="recusado"; return 0; fi
        if [ -n "$BREW" ]; then
            executar "$BREW" install ollama || aviso "Falha ao instalar o Ollama pelo Homebrew."
            exe="$(encontrar_ollama || true)"
        fi
        if [ -z "$exe" ]; then
            aviso "Abrindo a página oficial de download do Ollama; instale-o e execute este iniciador de novo."
            if [ "$SO" = "Darwin" ]; then open "https://ollama.com/download/mac" || true
            else info "Baixe em: https://ollama.com/download/linux"; fi
            MARCA_OLLAMA="ausente"
            return 0
        fi
        ok "Ollama instalado ($exe)"
    else
        ok "Ollama encontrado ($exe)"
    fi
    MARCA_OLLAMA="instalado"

    if saida="$("$PYTHON_VENV" -c 'from app.core.config import load_settings as l; s=l(); print(s.ollama_model); print(s.ollama_url)' 2>/dev/null)"; then
        modelo="$(printf '%s\n' "$saida" | sed -n '1p')"
        url="$(printf '%s\n' "$saida" | sed -n '2p' | sed 's#/*$##')"
    fi
    if [ -z "$modelo" ]; then aviso "Não foi possível ler o modelo configurado; pulando o download."; return 0; fi
    if [ -z "$url" ]; then url="http://127.0.0.1:11434"; fi

    if ! ollama_responde "$url"; then
        if [ "$SO" = "Darwin" ] && [ -d /Applications/Ollama.app ]; then
            open -g -a Ollama || true
        else
            mkdir -p "$PASTA_LOGS"
            nohup "$exe" serve >> "$PASTA_LOGS/ollama.log" 2>&1 &
        fi
        i=0
        while [ "$i" -lt 20 ] && ! ollama_responde "$url"; do sleep 1; i=$((i + 1)); done
    fi
    local nome="$modelo" lista
    case "$nome" in *:*) ;; *) nome="$nome:latest" ;; esac
    lista="$("$exe" list 2>/dev/null || true)"
    if printf '%s\n' "$lista" | awk -v n="$nome" '$1 == n { achou = 1 } END { exit !achou }'; then
        ok "Modelo '$modelo' já baixado"
        return 0
    fi
    if [ "$MARCA_MODELO_RECUSADO" = "$modelo" ] && [ "$REINSTALAR" != "1" ]; then
        info "Modelo '$modelo' não baixado (você recusou antes)."
        return 0
    fi
    if perguntar "Baixar agora o modelo '$modelo' (download de alguns GB)?" s; then
        info "Baixando '$modelo'... (pode demorar conforme a internet)"
        registrar "\$ ollama pull $modelo"
        if "$exe" pull "$modelo"; then ok "Modelo '$modelo' pronto"
        else aviso "Falha ao baixar '$modelo'. Tente depois: ollama pull $modelo"; fi
    else
        MARCA_MODELO_RECUSADO="$modelo"
    fi
}

configurar_atalho() {
    if [ -n "$MARCA_ATALHO" ]; then return 0; fi
    etapa "Atalho na Mesa"
    if perguntar "Criar um atalho do SentencIA na Mesa (Área de Trabalho)?" s; then
        if executar "$PYTHON_VENV" "$RAIZ/scripts/criar_atalho_desktop.py"; then MARCA_ATALHO="sim"
        else aviso "Não foi possível criar o atalho (veja o registro)."; MARCA_ATALHO="falhou"; fi
    else
        MARCA_ATALHO="recusado"
    fi
    gravar_marca
}

# ----------------------------------------------------------------------------------------------
# Principal
# ----------------------------------------------------------------------------------------------
printf '\n  SentencIA %s — instalador/iniciador (%s)\n' "$VERSAO_APP" "$DESCRICAO_SO"
registrar "===== bootstrap $VERSAO_APP | $DESCRICAO_SO | bash $BASH_VERSION ====="
registrar "Pasta do programa: $RAIZ"

etapa "Conferindo a pasta do programa"
case "$RAIZ" in
    */AppTranslocation/*)
        encerrar_com_erro "O macOS abriu o programa a partir de uma cópia temporária protegida. Mova a pasta inteira do programa para Documentos (ou Aplicativos) pelo Finder e abra 'Iniciar SentencIA.command' de novo." ;;
esac
if [ "$SO" = "Darwin" ]; then
    # Zip baixado/extraído propaga com.apple.quarantine a todos os arquivos; sem removê-la, o atalho para o
    # .app (e o próprio .app) cairia sempre em App Translocation. O usuário já aprovou este .command.
    xattr -dr com.apple.quarantine "$RAIZ" 2>/dev/null || true
fi
if ! ( : > "$RAIZ/.teste_gravacao.$$" ) 2>/dev/null; then
    encerrar_com_erro "A pasta '$RAIZ' não permite gravação. Copie a pasta do programa para Documentos e tente de novo."
fi
rm -f "$RAIZ/.teste_gravacao.$$"
if [ "$SO" = "Darwin" ] && [ "${MAIOR_MAC:-0}" -lt 13 ] 2>/dev/null; then
    aviso "macOS $VERSAO_MAC: recomenda-se o macOS 13 (Ventura) ou mais recente; alguns componentes opcionais podem não instalar."
fi
case "$RAIZ" in
    */Library/CloudStorage/*|*/iCloud*|*/Mobile\ Documents/*)
        aviso "A pasta está em um serviço de nuvem: os autos sigilosos (data/cases) e o ambiente Python (.venv) serão sincronizados."
        aviso "Para sigilo e desempenho, prefira uma pasta local fora da nuvem." ;;
esac
ok "$RAIZ"

HASH_ATUAL="$(sha256_de "$RAIZ/pyproject.toml")-$(sha256_de "$RAIZ/uv.lock")"
INSTALADO=0
if [ -x "$PYTHON_VENV" ] && [ "$MARCA_HASH" = "$HASH_ATUAL" ] && [ "$HASH_ATUAL" != "sem-hash-sem-hash" ] && [ "$MARCA_PLATAFORMA" = "$PLATAFORMA" ] && [ "$MARCA_VERSAO" = "$VERSAO_APP" ]; then
    INSTALADO=1
fi
# O venv só é válido se o Python base ainda existe e o núcleo do servidor importa.
if [ "$INSTALADO" = "1" ] && ! "$PYTHON_VENV" -c 'import fastapi, uvicorn' >/dev/null 2>&1; then
    aviso "O ambiente Python existente não funciona (interpretador base ausente ou inacessível); será recriado."
    INSTALADO=0
fi
# Extra opcional que ficou de fora (rede instável): tenta de novo nas próximas execuções (até 3 vezes).
if [ "$INSTALADO" = "1" ] && [ -n "$MARCA_EXTRAS_PENDENTES" ] && [ "$MARCA_TENTATIVAS_EXTRAS" -lt 3 ] && [ "$SEM_PERGUNTAS" != "1" ]; then
    aviso "Componente opcional pendente ($MARCA_EXTRAS_PENDENTES); tentando instalar de novo."
    INSTALADO=0
fi
QUER_NUVEM=0
if [ "$NUVEM" = "1" ] || [ "$MARCA_NUVEM" = "sim" ]; then QUER_NUVEM=1; fi
if [ "$NUVEM" = "1" ] && [ "$MARCA_NUVEM" != "sim" ]; then INSTALADO=0; fi

if [ "$INSTALADO" = "1" ] && [ "$REINSTALAR" != "1" ]; then
    etapa "Ambiente já instalado"
    ok "extras: $MARCA_EXTRAS"
    # Etapas opcionais que ficaram pendentes (Ollama ausente/não configurado, atalho nunca respondido).
    if [ "$SEM_PERGUNTAS" != "1" ]; then
        if [ -z "$MARCA_OLLAMA" ] || [ "$MARCA_OLLAMA" = "ausente" ]; then
            etapa "IA local opcional (Ollama)"
            configurar_ollama
            gravar_marca
        fi
        configurar_atalho
    fi
else
    etapa "Gerenciador de Python (uv)"
    garantir_uv

    etapa "Python $VERSAO_PYTHON"
    garantir_python

    if [ "$NUVEM" != "1" ] && [ -z "$MARCA_NUVEM" ] && [ "$SEM_PERGUNTAS" != "1" ]; then
        info "Componentes de NUVEM (Gemini) são opcionais e só enviam dados com sua autorização expressa."
        if perguntar "Instalar os componentes opcionais de nuvem?" n; then QUER_NUVEM=1; fi
    fi

    etapa "Dependências do sistema"
    EXTRAS_INSTALADOS=""
    sincronizar_dependencias "$QUER_NUVEM"
    case ",$EXTRAS_INSTALADOS," in
        *,transcricao,*) ;;
        *) aviso "Transcrição local (Whisper) indisponível nesta máquina; as mídias ficarão como FALHOU até ser instalada." ;;
    esac
    MARCA_VERSAO="$VERSAO_APP"
    MARCA_HASH="$HASH_ATUAL"
    MARCA_PLATAFORMA="$PLATAFORMA"
    MARCA_EXTRAS="$EXTRAS_INSTALADOS"
    case ",$EXTRAS_INSTALADOS," in
        *,transcricao,*) MARCA_EXTRAS_PENDENTES=""; MARCA_TENTATIVAS_EXTRAS=0 ;;
        *) MARCA_EXTRAS_PENDENTES="transcricao"; MARCA_TENTATIVAS_EXTRAS=$((MARCA_TENTATIVAS_EXTRAS + 1)) ;;
    esac
    if [ "$QUER_NUVEM" = "1" ]; then MARCA_NUVEM="sim"; else MARCA_NUVEM="nao"; fi
    gravar_marca
    ok "Dependências instaladas"

    etapa "IA local opcional (Ollama)"
    configurar_ollama
    gravar_marca

    if [ "$SEM_PERGUNTAS" != "1" ]; then configurar_atalho; fi
fi

if [ "$SEM_INICIAR" = "1" ]; then
    ok "Instalação conferida (sem iniciar, a pedido)."
    exit 0
fi

etapa "Iniciando o SentencIA"
ARGS_LAUNCHER=("$RAIZ/launcher.py")
if [ "$SEM_NAVEGADOR" = "1" ]; then ARGS_LAUNCHER+=(--sem-navegador); fi
CODIGO=0
executar "$PYTHON_VENV" "${ARGS_LAUNCHER[@]}" || CODIGO=$?
if [ "$CODIGO" != "0" ]; then
    encerrar_com_erro "O sistema não iniciou (código $CODIGO). Veja também data/logs/launcher.log e data/logs/servidor.log."
fi
if [ "$SEM_NAVEGADOR" = "1" ]; then ok "Servidor ativo em segundo plano (endereço acima)."
else ok "Sistema aberto no navegador. O servidor continua ativo em segundo plano."; fi
info "Esta janela do Terminal já pode ser fechada."
info "Para encerrar o servidor: .venv/bin/python launcher.py --parar"
exit 0
