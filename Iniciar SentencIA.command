#!/bin/bash
# SentencIA — iniciador de 1 clique para macOS (abre no Terminal).
# Na primeira vez o macOS pode bloquear: clique com o botão direito > Abrir (ou Ajustes do Sistema >
# Privacidade e Segurança > "Abrir Mesmo Assim"). Detalhes no README.md.
set -euo pipefail
cd "$(dirname "$0")"
export JVDFM_DATA_DIR="${JVDFM_DATA_DIR:-$HOME/Library/Application Support/SentencIA}"
exec /bin/bash "./instalador/bootstrap.sh" "$@"
