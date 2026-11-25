# Histórico de mudanças

Formato inspirado em [Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/); versões seguem
[SemVer](https://semver.org/lang/pt-BR/).

## [2.4.0] — 2026-11-25

Mascotes do tema Matilha e instalador de uma linha corrigido.

- **Mascotes no rodapé (tema Matilha):** o pug e o rottweiler seguem o mouse, bebem água e comem nos potes do canto
  esquerdo, fazem cocô no canto direito (um clique limpa) e dormem quando o mouse fica parado. Mouse perto ou clique:
  abanam o rabo; na paleta, "petisco" faz os dois comerem. Não bloqueiam cliques, param com "reduzir movimento" e
  podem ser desligados em Configurações › Aparência ("Mascotes no rodapé").
- **Instalação no Windows em uma linha** (`irm ... | iex`) volta a funcionar: o `instalar.ps1` não tem mais BOM, que o
  PowerShell 5.1 lia como caracteres soltos. A Release passa a testar a instalação nesse mesmo modo.

## [2.3.1] — 2026-11-24

Correção do tema Matilha.

- O "rabo abanando" ao passar o mouse sobre botões principais não altera mais o tamanho do botão: em algumas larguras de tela (ex.: 1366 px) o cabeçalho piscava sem parar.
- Testes automáticos independentes do horário e da velocidade da máquina.

## [2.3.0] — 2026-11-24

Novo tema visual **Matilha** e conclusão do Ciclo 8 (sentença mais segura).

- **Custódia por evento:** a detração automática só usa períodos com vínculo documental inequívoco entre prisão e
  soltura; pares ambíguos ou solturas concorrentes vão para a confirmação do juízo.
- **Uma decisão de confissão por imputação:** Análise e Dosimetria precisam coincidir; o conflito aparece na tela e
  bloqueia o cálculo. Em fato posterior a 16/09/2025 a Dosimetria pede o tipo da confissão (Tema 1194/STJ).
- **Efeitos do art. 92, § 2º, do CP** (Lei 14.994/2024, fatos a partir de 10/10/2024): enquadramento do art. 121-A,
  § 1º, cargo e poder familiar registrados nas decisões acessórias; dado faltante vira pendência.
- **Sursis do art. 77, § 2º** (maior de 70 anos ou razões de saúde, pena até 4 anos, prova de 4 a 6 anos) e
  **exceção do art. 44, § 3º** para a substituição da pena do reincidente.
- **Prescrição retroativa:** a estabilidade da acusação precisa referir-se ao título cuja pena entra no cálculo.
- **Reauditoria** aponta comando sobre medidas protetivas contrário ao registro em qualquer seção da minuta.
- **OCR** não substitui mais texto nativo legível e curto (evita datas trocadas); detração por quantidade segue o
  marco de vigência da Lei 12.736/2012.
- **Rascunho do caso** guarda também as decisões acessórias (inclusive incompletas); a minuta anterior fica marcada como
  desatualizada quando o registro muda.

- **Tema Matilha** (Configurações › Aparência): o pug é o assistente (carregamentos, avisos, estados vazios) e o
  rottweiler é o guardião (sigilo, auditoria, erros). Claro e escuro ("a matilha dorme").
- Personagens animados com respiração, piscar e rabo abanando; variantes escolhíveis: **pug abricó ou branco e bege**,
  **rottweiler clássico ou atlético e feliz**. Fundo pastel com patinhas, lacinhos, gravatinhas, petiscos e brinquedos.
- Easter eggs: cliques no logo, código Konami, barra de progresso de pegadas, comandos secretos na paleta e outros.
  Sons desligados por padrão; animações respeitam "reduzir movimento".
- A folha da sentença, o editor, as exportações e a cópia para o PJe continuam inteiramente formais em qualquer tema.
## [2.2.1] — 2026-11-24

Correções da 2.2.0 e itens do Ciclo 8.

- **Decisões acessórias incompletas** (ex.: medidas protetivas "Manter" sem fundamento) deixam de gerar um erro técnico
  em inglês repetido a cada envio: a tela confere antes de enviar e mostra **um** aviso em português no campo.
- Erros de validação do servidor aparecem traduzidos, com o nome do campo; o mesmo aviso não se empilha.
- Decisões e confirmações de papel recusadas durante a troca de caso; papel do depoente só muda com todos os trechos
  confirmados com o mesmo papel.
- Trechos enviados à geração incluem todas as fontes citadas e várias passagens da mesma peça.
- Respostas da API sem cache e política de conteúdo (CSP); verificação automática de qualidade no GitHub (Node 24).

## [2.2.0] — 2026-11-24

Ciclo 7 completo e identidade técnica unificada como **SentencIA**.

- **Sentença mais segura:** registro dos elementos provados pelo juízo por imputação (a minuta só afirma o
  registrado); decisões explícitas na dosimetria (majorante do art. 147-A, § 1º, II, multa e reparação);
  Leis 15.160/2025, 15.380/2026 e 15.438/2026 por imputação; linha do tempo da prescrição; confissão
  (Tema 1194/STJ), antecedentes sem dupla valoração e detração única com intervalos documentados; decisões
  acessórias com fundamento próprio; todas as teses e pedidos apreciados.
- **Leitura dos autos:** LAYA com espécie "não identificada" em vez de palpite (aferição 95/95); OCR por página;
  segmentação de PDF sem sumário.
- **Transcrição:** nomes das partes reconhecidos em toda a audiência; modelo turbo automático na GPU; papel de
  cada trecho com confirmação humana.
- **Correções:** o aviso "Modelo … baixado com sucesso" não se repete mais (também no macOS); nome completo das
  partes; "Apreciação" no lugar de "Enfrentamento" das teses; tópico de prescrição só quando houver.
- **Segurança:** respostas da API sem cache (`no-store`) e política de conteúdo (CSP); envio externo autorizado
  por operação; verificador de sigilo falha fechado.
- **Nome técnico:** variáveis `SENTENCIA_*` (as `JVDFM_*` seguem aceitas com aviso), pasta de dados e cofre
  migrados para "SentencIA" sem perda; pacote `sentencia`. "JVDFM" permanece só como sigla do juizado.
- **Instaladores:** a versão vem do `pyproject.toml`; o release confere a tag e passa a versão ao instalador
  do Windows.

## [2.1.0] — 2026-11-21

Consolidação dos ciclos 1, 2 e 3: **correção crítica da integração com o Gemini (modelos descontinuados)**,
nova identidade visual **SentencIA** com 4 temas completos, pré-análise de processos em lote, painel de
jurisprudência do gabinete, acervo jurídico verificado (Tema 1197/STJ e atualização normativa), cópia segura
e fiel para o PJe, seletor de modelos do Ollama, OCR visível com progresso por página e "Refazer OCR",
política de retenção do arquivo morto, otimização de VRAM e "Modo GPU moderado".

> **Destaque — Correção do Gemini (modelos descontinuados):** o sistema deixou de depender dos modelos
> `gemini-2.5-*` fixos no código, descontinuados pelo Google (erro 404); agora a seleção é dinâmica com
> fallback automático em caso de indisponibilidade/timeout (504), botão **Testar chave** nas Configurações e
> mensagens claras para chave inválida, cota esgotada ou falta de permissão (TASK-005 e TASK-006).

### Renomeado

- O programa passa a se chamar **SentencIA** (antes "Sentença JVDFM"). Nova identidade visual — balança
  com centelha, "Sentenc" em serifa + "IA" em dourado —, favicon, ícones (Windows `.ico`, macOS `.icns`),
  miniaturas, banner e materiais de marca no Canva; manual em `docs/identidade/manual_de_marca.md` e gerador em
  `scripts/gerar_identidade.py` (TASK-009).
- Arquivos renomeados: `Iniciar SentencIA.bat`, `Iniciar SentencIA.command`, `Iniciar_SentencIA.vbs`,
  `SentencIA.app` e o pacote `SentencIA-v2.1.0.zip`. O atalho antigo da Área de Trabalho continua
  funcionando (`Iniciar_Sentenca_JVDFM.vbs` virou uma ponte para o novo iniciador).
- **Não mudaram** (para preservar suas configurações e a chave do Gemini): a pasta de dados
  (`SentencaJVDFM`), o cofre de senhas, as variáveis `JVDFM_*` e o nome interno do pacote Python.

### Adicionado

- **Menu do logo e 4 temas visuais completos** (Original, **Gabinete Clássico**, **Estúdio Moderno** e
  **Cockpit / Alta Densidade**), cada um com modo claro e escuro, paleta de comandos (`Ctrl+K`), modal
  **Sobre o SentencIA** e fontes tipográficas hospedadas localmente (SIL OFL 1.1) — TASK-003, TASK-004,
  TASK-007, TASK-010, TASK-011.
- **Pré-análise de processos em lote** (backend e interface): envio em streaming para disco com limites de
  segurança e proteção contra `MAX_PATH`, fila de triagem em segundo plano, visualização de resumo por processo
  e abertura direta de qualquer item na sessão ativa — TASK-016, TASK-018.
- **Painel de jurisprudência do gabinete**: consulta ao acervo fechado e gestão completa dos precedentes do
  juízo (cadastrar, editar, excluir, validar referências e exportar/importar JSON) com contador integrado ao
  menu do logo — TASK-017, TASK-019, TASK-026.
- **Seletor dinâmico de modelos do Gemini e botão "Testar chave"**: consulta modelos disponíveis pela API com
  fallback automático, cache de disponibilidade e feedback imediato de erro/sucesso nas Configurações — TASK-006.
- **Seletor de modelos instalados no Ollama e aviso de modelo ausente**: lista os modelos realmente instalados
  no Ollama local nas Configurações e alerta na interface quando o modelo configurado não está baixado — TASK-021.
- **Política de retenção do arquivo morto**: limpeza configurável dos arquivos arquivados em
  `data/cases/_arquivados/` com modo de simulação (*dry-run*), confirmação destrutiva explícita e controles
  dedicados nas Configurações (`POST /api/cases/arquivo-morto/retencao`) — TASK-002, TASK-013.
- **Otimização de VRAM e "Modo GPU moderado"**: política de coordenação entre Whisper e Ollama (cálculo de
  camadas GPU com medição real de VRAM e cache de 10 s), fila única global de GPU (`reservar_gpu`), teto de
  70% de VRAM, contexto limitado (`num_ctx ≤ 4096`), `keep_alive` reduzido e Whisper econômico
  (`int8_float16`, `beam_size=2`) configurável nas Configurações e visível na Telemetria — TASK-014, TASK-029.
- **OCR visível, estados explícitos e "Refazer OCR"**: barra de progresso por página durante o OCR de PDFs
  digitalizados, alerta explícito quando o motor de OCR está ausente ou falha (`ocr_falhou`), botão
  **Refazer OCR** (`POST /api/cases/refazer-ocr`) preservando todos os PDFs/transcrições/overrides do caso e
  cancelamento cooperativo — TASK-027.
- **Guia do usuário em linguagem de gabinete** (`docs/GUIA_DO_USUARIO.md`) cobrindo as 4 estações, temas,
  pré-análise em lote, jurisprudência do gabinete, cópia para o PJe e privacidade — TASK-023.
- **Ferramental de qualidade e empacotamento**: Playwright no grupo `dev` para testes E2E de layout/UI,
  script CLI de verificação de contraste WCAG (`scripts/verify_wcag.py`) e exclusão automática de `.git` em
  worktrees e arquivos de orquestração no `build_release.py` — TASK-008.
- **Nome do arquivo da sentença** no padrão `Sentença - {tipo} - Proc. {nº} - {sentenciado} - {tipo(s) penal(is)}`
  (ex.: `Sentença - Condenatória - Proc. 0804590-12.2025.8.00.0001 - FULANO - art. 129, § 13, CP.docx`).
  O tipo (Condenatória, Absolutória, Parcialmente condenatória, Extintiva da punibilidade...) vem do veredicto
  de cada crime; o reset da sessão reconhece o novo nome e o legado `Sentenca_JVDFM_*`.
- **Barra de formatação na folha** (estilo LibreOffice Writer): desfazer/refazer, estilo de parágrafo, fonte,
  tamanho, negrito, itálico, sublinhado, tachado, sobrescrito/subscrito, cor, alinhamento (Ctrl+L/E/R/J),
  entrelinhas, recuos, recuo de 1ª linha, listas, limpar formatação (Ctrl+M) e **margens da página**
  (ABNT NBR 14724, LibreOffice, moderada, estreita, larga ou personalizada).
- A formatação feita na folha é **preservada no .docx, no .pdf e no HTML do PJe**. Ao exportar, se só a
  formatação mudou ela é gravada sem reauditar; se o texto mudou, o sistema salva e reaudita antes.
- **Modelos de formatação do gabinete** (botão *Modelos*): fonte, tamanho, entrelinhas, recuo de 1ª linha,
  margens e numeração de páginas. Há 3 modelos de fábrica e você salva os seus (★), guardados em
  `templates_gabinete/formatos_gabinete.json` (o reset da sessão não os apaga).
- **Localizar e substituir** na folha (botão *Localizar*, Ctrl+H/Ctrl+F): anterior/próximo, diferenciar
  maiúsculas, substituir e substituir tudo (desfazível com Ctrl+Z; referências `ID-…` são preservadas).
- **Numeração de páginas** "Página x de y" no rodapé do .docx (campos do Word, atualizam sozinhos) e no .pdf;
  liga/desliga em *Margens*. Títulos no .docx não ficam órfãos no fim da página (manter com o próximo).
- **Histórico de versões e comparação** (botão *Versões*): cada geração e cada edição re-auditada vira uma
  versão da sessão (até 30). Compara qualquer versão com a folha atual (inclusive edições não salvas) ou com
  outra versão, parágrafo a parágrafo e palavra a palavra, e permite restaurar uma versão na folha.
- **Banco de trechos do gabinete** (botão *Trechos*): fundamentações e fórmulas recorrentes com título,
  categoria e atalho. Digite `/atalho` + Tab na folha, ou clique no trecho, para inserir no cursor;
  marcadores `[COMPLETAR PELO JUÍZO: ...]` e IDs inseridos já saem destacados. Salve a seleção da folha
  como trecho. Guardado em `templates_gabinete/trechos_gabinete.json` (o reset não apaga). Os exemplos
  que acompanham o sistema são só fórmulas neutras; o conteúdo de mérito é do juízo.
- **Painel de revisão** (botão *Revisão*, com contagem de pendências): reúne (a) as pendências do laudo
  — trechos `[COMPLETAR PELO JUÍZO]`, IDs inexistentes, citações fora da base, teses não enfrentadas, penas
  divergentes e seções substituídas — e (b) uma conferência da minuta pelo **art. 381 do CPP e pelo manual**
  (Taipina, 6ª ed., cap. I, item 4): seções e ordem, identificação do acusado, recebimento da denúncia,
  citação, pedidos finais, artigos na fundamentação e no dispositivo, data/assinatura, expressões que o
  manual desaconselha (“e tudo mais que dos autos consta”, “procedente a ação”), dosimetria no dispositivo e
  pequenos defeitos de forma. Cada item mostra a base, leva ao trecho na folha (“Ir ao trecho”), permite
  “Aplicar sugestão” (desfazível) e ser marcado como conferido. São apoio à revisão; a decisão é do juízo.
- **Pasta de exportação à sua escolha** (Configurações): cada exportação, além do download, é copiada para a
  pasta indicada, opcionalmente numa subpasta por processo (`Proc. nº…`). Nunca sobrescreve um arquivo que
  você tenha editado (grava `nome (2)`). Recusa pastas dentro dos dados/código do programa e avisa quando
  a pasta parece sincronizada com a nuvem. Botão *Pasta* abre a pasta no Explorer.
- **Provas orais citáveis.** Cada depoimento tem agora a transcrição integral com minutagem e botão
  *Citar* (copia “trecho” + depoente, mídia e minuto). O botão antigo mostrava só a síntese porque lia o
  campo errado (`segmentos` em vez de `segmentos_literais`).
- **O que cada parte alegou (IA local).** Marca quem fala (juiz, MP, defesa, depoente, "a conferir") e
  extrai as alegações com o trecho literal. Alegação cujo trecho não existe na transcrição é descartada.
  Tudo é sugestão, rotulada na tela. Endpoints `POST /api/cases/oral/analisar` e `GET /api/cases/oral/analise`.
- **Ajuste de nomes e do número do processo na transcrição** ("Carlos Eduarto" → nome dos autos; número
  ditado → CNJ), preservando o texto original do Whisper (`texto_whisper`) e indicando o ajuste.
- Suporte à GPU no Windows: registra as DLLs do CUDA instaladas por pip (`nvidia-cublas-cu12`,
  `nvidia-cudnn-cu12`) e só usa a GPU se o cuBLAS carregar.
- Whisper em GPU roda em processo separado (`whisper_worker`), com até 5 tentativas e queda para a CPU: o
  driver pode derrubar a GPU sob carga (aborto `0xC0000409`) e isso não pode encerrar o servidor. Frases com
  minutagem por palavra (modelos grandes devolvem trechos de ~30 s) e remoção de repetições idênticas.
  O campo "Modelo Whisper" aceita o nome de um modelo ou o caminho de uma pasta com o modelo baixado.

### Alterado

- **Arquitetura modular do frontend**: `app.js` dividido em 13 módulos ES acíclicos em `app/static/js/modulos/`
  sem necessidade de bundler — TASK-015.
- **Barra lateral simplificada**: removidos os atalhos duplicados de Acervo, Configurações e Telemetria da
  barra lateral (mantidos no menu do logo), liberando espaço útil para as 4 estações — TASK-026.
- **Dependências essenciais protegidas**: `winocr` (Windows), `ocrmac` (macOS) e `keyring` movidos para as
  dependências principais em `pyproject.toml` (mantendo os extras antigos como aliases), evitando que
  sincronizações de ambiente removam o OCR nativo ou o cofre de segredos — TASK-027.
- **Versão única da aplicação**: backend (`app/api/security.py`), iniciador (`launcher.py`) e empacotador
  (`scripts/build_release.py`) leem a versão diretamente de `pyproject.toml` (`2.1.0`), exposta em
  `/api/health` e `/api/status` e sincronizada no modal "Sobre" e na Telemetria — TASK-023.
- **Polimento de interface e notificações**: rótulo claro do botão de tema, notificações (*toasts*) com
  fechamento automático pausável no hover e limite simultâneo de 3 avisos, e melhorias de ergonomia das
  revisões anteriores — TASK-012, TASK-022.

### Corrigido

- **Gemini (modelos descontinuados e robustez de chamada)**: corrigida a falha de processamento causada por
  modelos `gemini-2.5-*` fixos no código (erro 404), com resolução dinâmica de modelo, tratamento de erros da
  API e fallback automático para timeouts (504) no modo automático — TASK-005, TASK-006.
- **Deduplicação de cópias `.docx` na pasta de exportação**: comparação de equivalência agora ignora
  metadados temporais (`docProps/core.xml`), evitando cópias duplicadas (`nome (2).docx`) em reexportações
  idênticas na virada de segundo sem sobrescrever arquivos editados — TASK-030.
- **OCR do Windows (`winocr`)**: corrigido erro em que todas as páginas digitalizadas falhavam por falta de
  `await` na chamada assíncrona do `winocr`; falhas agora ficam explicitamente registradas como `ocr_falhou` —
  TASK-028.
- **Acervo jurídico e citações do gerador (Tema 1197/STJ)**: auditado e corrigido o acervo fechado e as
  citações geradas pelo motor determinístico, eliminando referências imprecisas — TASK-024.
- **Auditoria visual e transbordo de textos**: corrigidos textos que saíam das caixas em botões, barras e
  cartões, além do contraste WCAG nos 4 temas — TASK-003, TASK-011.
- **Testes E2E no Windows**: servidor de teste unificado com teardown resiliente ao travamento de `app.log`
  no Windows — TASK-025.
- Degravação falhava com `open() got an unexpected keyword argument 'metadata_errors'`: o PyAV 19 removeu
  o argumento que o faster-whisper 1.2 usa. Compatibilizado em tempo de execução.
- A degravação travava (CPU ociosa) ao insistir na GPU depois de uma falha de CUDA. Agora uma falha de
  CUDA desativa a GPU até reiniciar, e a escolha confere antes se o cuBLAS carrega.
- Trocar o modelo Whisper agora refaz as transcrições feitas com outro modelo.
- O nome do arquivo perdia o tipo da sentença e os tipos penais depois de uma edição re-auditada; agora o
  veredicto por crime é preservado (com plano B pela dosimetria calculada).
- Parágrafos numerados longos (ex.: `1.1. Rejeição da preliminar: Não merece acolhimento...`) eram tratados
  como título e saíam em negrito, à esquerda e em azul. Agora só é título a linha curta, sem ponto final;
  o corpo permanece justificado. Seções principais em negrito; subseções em itálico; tudo em preto.
- IDs de peças (`ID-...`) na folha deixam de ser "pílulas" azuis em negrito e seguem a fonte do parágrafo.

### Segurança

- **Cópia segura e fiel para o PJe**: sanitização por lista branca de tags e estilos no HTML copiado para a
  área de transferência (bloqueando scripts/atributos perigosos), preservação de `Id.`/`ID-`, fallback limpo
  em texto simples e nota clara de que a assinatura digital é feita no próprio PJe — TASK-020.
- **Upload em lote seguro**: gravação em streaming para disco com teto de arquivos/tamanho e truncamento
  seguro para evitar estouro de caminho (`MAX_PATH`) no Windows — TASK-016.
- **Pacote de distribuição protegido**: `build_release.py` exclui explicitamente `.git` de worktrees,
  `.ai-collab/`, `.agent/` e `data/lote/` para impedir vazamento de metadados de desenvolvimento ou autos
  em lote — TASK-008, TASK-016.

## [2.0.0] — 2026-11

Versão de revisão integral: correções de mérito jurídico, eliminação de dados inventados, privacidade
por padrão, auditoria honesta e distribuição multiplataforma (Windows e macOS) com instalação em 1
clique. A versão 1.0.0 permanece intacta em pasta própria.

### Princípios que passam a valer em todo o sistema

- **Nada de dados inventados.** Data do fato, recebimento da denúncia, crimes imputados, data das
  peças, transcrições e valores de dano moral não extraídos ficam vazios, geram alerta e, quando a
  sentença depende deles, a API responde **422** com a lista de campos (`DadosInsuficientesError`).
  Acabaram a data de hoje como data de peça, o crime padrão `art_147a_cp`, os antecedentes
  desfavoráveis fixos, a transcrição fictícia "03:15", os R$ 3.000/4.000 de dano moral e os 10
  dias-multa fixos.
- **O juiz decide.** Em processo real a minuta exige veredicto explícito por crime (condenar, absolver,
  desclassificar, extinguir); sem ele a API responde **422** `decisao_judicial_pendente`. A simulação
  pode presumir condenação, sempre sinalizada (`simulacao_presume_condenacao`).
- **Telemetria honesta.** GPU, Ollama, Laya e rótulo do motor informam só o que foi medido ou usado
  ("indisponível" quando for o caso). Removidos a RTX 2060 fictícia e a lista falsa de modelos.
- **Privacidade por padrão.** Nada sai da máquina sem `permitir_envio_externo` **e** consentimento na
  requisição. Whisper local é o primeiro motor de transcrição.
- **Demonstração explícita.** O caso demo é reconhecido apenas pela flag `is_demo`, nunca pelo prefixo
  `ID-4100` (IDs reais do PJe podem começar assim).

### Fundação (`app/core`)

- Novos módulos compartilhados: `text_utils` (normalização sem acento), `errors`
  (`DadosInsuficientesError`, `DecisaoJudicialPendenteError`, `EnvioExternoNaoAutorizadoError`),
  `case_flags`, `case_context` (classificação, metadados e matriz oral calculados uma vez por conteúdo,
  cache LRU thread-safe, overrides de metadados do juiz), `ollama_client` (com `num_ctx`),
  `platform_utils` (binários, navegador e GPU em Windows/macOS/Linux) e `secrets` (cofre
  keyring/arquivo).
- `config`: `JVDFM_DATA_DIR`, novas pastas `exports`/`logs`, novos campos (`permitir_envio_externo`,
  `ollama_num_ctx`, `whisper_device`, limites de upload), gravação atômica, tolerância a campos
  inválidos e preservação de JSON corrompido. A chave do Gemini migra para o cofre na primeira carga.

### Dosimetria (`app/legal_engine`)

- Prescrição **por crime** (art. 119 do CP; pena sem o acréscimo do concurso — Súmula 497/STF), art.
  110, § 1º (Lei 12.234/2010) para fatos desde 06/05/2010, prescrição em abstrato pela pena máxima,
  art. 115 (menor de 21 / maior de 70) e contagem por calendário (art. 10 do CP). Sem data de
  recebimento a prescrição nunca é reconhecida.
- Regime inicial por espécie (art. 33; detenção e prisão simples nunca no fechado; art. 111 da LEP),
  regra documentada para penas mistas; Súmula 269/STJ.
- Compensação: Tema 585/STJ só para reincidência × confissão; demais casos pelo art. 67 do CP.
- Sursis (art. 77, II) e substituição (art. 44, III) não são concedidos automaticamente com vetor
  judicial negativo (alerta ao juiz).
- Multa cumulativa sem parametrização: quantum proporcional sugerido com alerta. Catálogo corrigido
  (multa cumulativa nos arts. 24-A pós-Lei 14.994/2024, 147-A, 147-B e 139; multa alternativa no art.
  21 da LCP; art. 129, § 13, anterior a 29/07/2021 usa o § 9º; aviso de atipicidade para fatos
  anteriores às leis criadoras dos arts. 147-A e 147-B).
- Dano moral pedido sem valor: indenização 0 e "arbitramento pendente" (nunca "ausência de pedido").
- Concurso formal/continuado: fração sugerida pelo número de crimes (Súmula 659/STJ); a do juiz
  prevalece.
- Aumento do art. 147-A, § 1º, II, como parâmetro explícito.

### Geração (`app/generation`, `app/demo`)

- Texto do caso demo em um único lugar (`app/demo/demo_sentence.py`).
- Seções determinísticas calculadas uma vez por geração e reutilizadas por dossiês, fallbacks,
  regeneração e montagem final.
- Confissão respeita negação; detração contada da prisão em flagrante até a soltura (não da data do
  fato); "recorrer em liberdade" só com alvará ou liberdade provisória nos autos.
- Preliminares e teses sem resposta específica, vetores sem fundamento e incisos não informados
  recebem `[COMPLETAR PELO JUÍZO: ...]`. Absolvição com texto por inciso do art. 386.
- Removidas sobras de casos reais (comarca, laudo, emendatio fixa do art. 150, "respondeu solto") e a
  chamada falsa ao Ollama usada só para o rótulo do motor.
- Ollama via `ollama_client` com `num_ctx` e contexto das seções anteriores resumido; origem de cada
  seção (`llm`, `deterministico`, `fallback_deterministico`) na telemetria.
- Duplo crivo: marcadores pendentes não forçam o modelo a inventar dados; continuam cobrados pela
  auditoria final. Crítico adversarial sem modelo fixo.

### Auditoria, Gemini, conhecimento e exportação

- Auditor: nova verificação de conteúdo (penas e dias-multa do dispositivo × dosimetria calculada;
  marcadores pendentes reprovam), cabeçalhos com `–`, `-` e `—`, score coerente (toda reprovação
  desconta; aprovada ⇔ 100), seções de fallback sinalizadas, acórdãos por número exato, sem IDs demo
  fixos, cache invalidado quando a base muda.
- Gemini: consentimento obrigatório, `google-genai` opcional com erro claro (503), sem troca
  silenciosa de motor, sem auto-blindagem silenciosa (só quando solicitada, e sinalizada), sem
  repetir a geração.
- Pseudonimização: número CNJ (processo, apenso, outros), prenomes isolados e nomes em nomes de
  arquivo; mapa determinístico salvo junto do script com hash conferido na importação.
- Base de conhecimento: acervo gravado só quando ausente/desatualizado (escrita atômica), IDs de
  precedente únicos. Clonador de estilo guarda a amostra completa e extrai um perfil observado; perfil
  padrão sem números de processos reais.
- Exportação: marca d'água **MINUTA NÃO AUDITADA** em DOCX, HTML e PDF; PDF gerado em memória e
  gravado de forma atômica.

### Ingestão e transcrição

- Caches por SHA-256 + versão de esquema (não mais por tamanho), gravados de forma atômica.
- `data_peca` vazia quando desconhecida; PDF sempre fechado; OCR por plataforma (winocr no Windows,
  ocrmac no macOS) com pool reutilizado e paralelo; `JVDFM_OCR=desligado`.
- Laya: campos vazios quando não extraídos, alertas de extração (`alertas_extracao`), sem teses
  fabricadas, emendatio do art. 150 só como alerta, correção do valor "R$" em minúsculas, confiança
  sem piso artificial.
- Transcrição: Whisper local primeiro (modelo e dispositivo das configurações, modelo em cache, CPU
  int8 no macOS), motores externos só com consentimento, falha explícita (`status: FALHOU`), IDs de
  mídia estáveis pelo conteúdo (`ID-MIDIA-<8 hex>[-NNN]`), duração via PyAV/ffprobe, papel do
  depoente por palavras inteiras.
- Prova oral: transcrições que falharam ficam listadas em `midias_sem_transcricao`; síntese neutra
  por temas, sem frases inventadas; coerência delegacia × juízo fica para avaliação do juiz.

### API e estado (`app/main.py`, `app/api`, `app/core/session_state.py`)

- Token de sessão (`X-JVDFM-Token`, injetado no `index.html`), checagem de Host (anti DNS
  rebinding), CORS e Origin restritos ao loopback, cabeçalhos de segurança, servidor só em
  `127.0.0.1` (`JVDFM_PORT`).
- `/api/health` mínimo e público; status detalhado e honesto em `/api/status`.
- Erros de domínio padronizados (422, 403, 503, 409, 500 em JSON) e log rotativo em `data/logs/app.log`.
- Uploads em streaming com limite (413), sem sobrescrever nem apagar arquivos existentes, lock de
  ingestão exclusiva (409), modo assíncrono com job e progresso.
- `GET /api/cases/active` sem efeitos colaterais; carregamento do disco em
  `POST /api/cases/load-from-disk`; complementação em `POST /api/cases/metadata-override`;
  chave do Gemini em `/api/secrets/gemini`; `POST /api/settings` parcial.
- Simulação de dosimetria com os campos corretos e sem datas fictícias; parâmetros opcionais (sem os
  R$ 3.000 de dano moral); registro de geração unificado; regeneração serializada.
- Exportação só com auditoria aprovada (ou `?rascunho=true`, com marca d'água), em `data/exports`,
  com nome sanitizado; nova rota HTML. Recuperação da última minuta em `GET /api/sentence/last`.
- Reset completo arquiva o caso em `data/cases/_arquivados/<timestamp>.zip` com deduplicação por hash;
  exclusão definitiva só com confirmação expressa.

### Interface (`app/static`)

- Adequação aos novos contratos da API: envio do token, consentimento de envio externo, veredicto por
  crime antes de gerar, formulário de complementação de metadados (422), exibição de alertas,
  marcadores pendentes e seções de fallback, telemetria honesta, exportação em rascunho e progresso do
  upload.

### Empacotamento e plataforma

- **Instalação em 1 clique, sem administrador:**
  - Windows: `Iniciar Sentenca JVDFM.bat` → `instalador/bootstrap.ps1` (UTF-8 com BOM, compatível com
    Windows PowerShell 5.1); macOS: `Iniciar Sentenca JVDFM.command` e `Sentenca JVDFM.app` →
    `instalador/bootstrap.sh` (bash 3.2, `set -euo pipefail`).
  - Instalam o `uv` (instalador oficial) e o Python 3.12, sincronizam as dependências travadas com os
    extras da plataforma (caindo para uma configuração menor se um extra opcional falhar), oferecem
    Ollama, modelo e atalho, e registram tudo em `data/logs/bootstrap.log`.
  - Idempotentes: a marca `data/.instalado` guarda versão, hash de `pyproject.toml`/`uv.lock`,
    plataforma e respostas; as execuções seguintes pulam o que já foi feito.
  - Funcionam em caminhos com espaços e acentos, avisam sobre OneDrive/iCloud e App Translocation,
    detectam Apple Silicon × Intel (e saem do Rosetta) e reparam um Python gerenciado danificado.
- **`launcher.py` multiplataforma** (substitui `launcher.pyw`, mantido como shim): usa o Python do
  `.venv`, escolhe porta livre e a repassa por `JVDFM_PORT`, aguarda `/api/health`, reutiliza um
  servidor já ativo, abre Chrome/Edge/Brave em modo aplicativo (ou o navegador padrão), inicia o
  Ollama se instalado, registra `data/logs/launcher.log` e `servidor.log` (stderr não é descartado) e
  oferece `--parar` e `--status`.
- `Iniciar_Sentenca_JVDFM.vbs` virou um shim silencioso para o novo iniciador; o antigo
  `iniciar_sistema.bat` foi removido.
- `scripts/criar_atalho_desktop.py` para Windows (Área de Trabalho real, inclusive redirecionada ao
  OneDrive, sem caminhos fixos), macOS (apelido na Mesa) e Linux (`.desktop`).
- `scripts/build_release.py` (com `--dry-run`): zip com permissões Unix 0755 para `.command`, `.sh`
  e o executável do `.app`, terminações de linha normalizadas, `SHA256SUMS.txt`, e verificação que
  recusa autos, caches, preferências, segredos, logs, exportações, amostras do gabinete, `Modelos de
  Sentenças`, capturas de tela, testes e caminhos pessoais.
- `pyproject.toml`: versão 2.0.0, Python `>=3.11,<3.15`, núcleo mínimo e extras `transcricao`,
  `ocr-windows`, `ocr-macos`, `nuvem` e `seguranca`; `uv.lock` resolvido para Windows, macOS (Apple
  Silicon e Intel, macOS 13+) e Linux.
- Testes: marcador `dados_reais` registrado; testes com autos reais são pulados sem
  `JVDFM_REAL_CASES_DIR`; suíte isolada do cofre do sistema operacional.
- README reescrito (instalação, Gatekeeper, privacidade, solução de problemas).

### Estabilização (integração entre as áreas)

- `CaseContext` completa `data_fato` e `data_recebimento_denuncia` a partir do topo do bundle
  (ISO ou dd/mm/aaaa, validadas; inválidas são ignoradas com aviso, nunca inventadas). O override do
  juízo continua prevalecendo.
- Art. 115 do CP: novo campo `MetadadosProcesso.data_nascimento_reu` (informado pelo juízo no
  formulário de complementação / `metadados_override`); as flags menor de 21 / maior de 70 são
  calculadas por ele, e as flags explícitas do juízo prevalecem.
- Base fechada: Súmulas 497/STF e 659/STJ, citadas pelo motor de dosimetria, passam a constar do
  acervo (o auditor não as bloqueia mais como citação externa).
- Antecedentes neutros sem certidão nos autos não afirmam mais o conteúdo de uma certidão inexistente.
- Helpers públicos `case_context.normalizar_override_metadado` / `normalizar_data` e
  `sentence_generator.resolver_data` (a API deixou de importar funções privadas).
- Testes com autos reais (`dados_reais`) atualizados para o comportamento v2 (veredicto explícito,
  emendatio decidida pelo juízo, exportação em rascunho com marcadores pendentes); o teste de
  persistência do acervo do gabinete não regrava mais `data/knowledge_base/custom_precedentes.json`.

### Removido

- `iniciar_sistema.bat` e o fluxo antigo de inicialização (VBS → BAT → `launcher.pyw`).
- Valores fictícios de hardware, transcrição, datas, crimes, antecedentes e dano moral.
- Detecção de demonstração por prefixo de ID.

## [1.0.0]

Primeira versão funcional (mantida em pasta própria, sem alterações).
