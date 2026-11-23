# Guia do Usuário — SentencIA v2.1.0

> **A inteligência que auxilia. A decisão que é sua.**
>
> Este guia explica, em linguagem direta de gabinete, como usar o **SentencIA v2.1.0** no dia a dia:
> das 4 estações de trabalho à cópia segura para o PJe, passando por temas, pré-análise em lote,
> jurisprudência do gabinete e privacidade.

---

## 1. Começando (e testando com o caso de demonstração)

Ao abrir o programa (pelo atalho **SentencIA** na Área de Trabalho ou por `Iniciar SentencIA.bat` /
`Iniciar SentencIA.command`), o sistema já inicia com um **caso de demonstração** (fictício, marcado
como demonstração) para você conhecer todas as telas sem precisar carregar um processo real.

### A barra lateral e o menu do logo

- Na **barra lateral**, ficam exclusivamente as **4 estações de trabalho** em ordem sequencial:
  1. **Autos & Mídias**
  2. **Análise & Provas**
  3. **Dosimetria da Pena**
  4. **Sentença**
- No canto superior esquerdo, clique no **logo SentencIA** para abrir o **menu do aplicativo**, onde
  estão:
  - **Temas e aparência** (4 desenhos + modo claro/escuro);
  - **Jurisprudência & Acervo** (com a contagem de precedentes disponíveis);
  - **Pré-análise em lote**;
  - **Configurações** (Ollama, Gemini, Whisper, OCR, pasta de exportação, arquivo morto, Modo GPU moderado);
  - **Telemetria Local** (versão do sistema, GPU/VRAM, Ollama, OCR e status dos motores);
  - **Sobre o SentencIA** (versão instalada e garantias de privacidade).
- **Atalho rápido:** pressione **`Ctrl+K`** (ou `Cmd+K` no macOS) para abrir a **Paleta de comandos**
  e saltar para qualquer estação, tema ou ferramenta pelo teclado.

---

## 2. Passo a passo das 4 estações

### Estação 1 — Autos & Mídias

1. Arraste ou selecione o **PDF integral dos autos** baixado do PJe e, se houver, os arquivos de
   **gravação das audiências** (`.mp4`, `.m4a`, `.mp3`, `.wav`, `.ogg`, `.mkv`).
2. Se os arquivos já estiverem na pasta `data/cases`, clique em **Carregar do disco**.
3. **Páginas digitalizadas (OCR):** quando o PDF contém páginas escaneadas sem camada de texto, o
   SentencIA aciona automaticamente o OCR nativo do sistema operacional e exibe o **progresso página
   a página**.
   - Se alguma página falhar ou o motor de OCR estiver indisponível, o sistema avisa de forma clara
     na tela.
   - Depois de corrigir o ambiente (por exemplo, instalar o idioma Português com OCR no Windows),
     basta clicar em **Refazer OCR** para reprocessar as páginas digitalizadas sem perder as demais
     peças nem as transcrições.
4. **Transcrição das audiências:** ocorre localmente com o Whisper. Mídias sem fala ou com falha
   ficam sinalizadas explicitamente como **FALHOU** (nada é inventado).
5. **Troca de caso segura:** ao abrir outro processo, o SentencIA registra uma "impressão digital" de
   cada arquivo (PDF e gravações). Se a troca não puder ser concluída — por exemplo, um arquivo mudou ou
   ficou indisponível —, o sistema avisa e o **caso anterior continua aberto**, sem misturar dados.
6. **Gravação sem processo identificado:** se não for possível ter certeza de que uma gravação pertence
   ao processo aberto, ela aparece como **pendente de vinculação**. Confira o processo da gravação e
   envie o arquivo pela área de **Mídias de Audiência**; nada é anexado por suposição.

### Estação 2 — Análise & Provas

1. Confira o **índice de peças** extraído do PDF (denúncia, recebimento, citação, laudos, alegações
   finais, certidões) e clique em qualquer peça para abrir o texto no painel lateral de evidências.
2. **Complementação de dados:** tudo o que não foi encontrado com segurança nos autos aparece listado
   para conferência (data do fato, data de recebimento da denúncia, data de nascimento do réu, crimes
   imputados, nomes das partes). Preencha o que faltar — o sistema nunca inventa datas, crimes ou valores.
   - **Nomes das partes:** quando as peças trazem o mesmo nome com mais ou menos sobrenomes, o
     SentencIA adota a **forma mais completa**. Se as peças trouxerem nomes realmente diferentes para a
     vítima ou para o acusado, aparece o alerta "nome diverge entre as peças dos autos" para você
     conferir antes de gerar a sentença.
3. **Provas orais citáveis:** cada depoimento mostra a transcrição integral com minutagem e o botão
   **Citar**, que copia o trecho literal já identificado por depoente, mídia e minuto.

### Estação 3 — Dosimetria da Pena

1. **Veredicto por crime (obrigatório em processo real):** para cada crime imputado, escolha a decisão
   do juízo:
   - **Condenar**
   - **Absolver** (com o inciso do art. 386 do CPP)
   - **Desclassificar** (informando o tipo penal resultante)
   - **Extinguir a punibilidade** (art. 107 do CP)
   Sem o veredicto explícito, o sistema não gera a minuta de um processo real.
2. **Parâmetros da dosimetria:** ajuste os vetores do art. 59 do CP, agravantes/atenuantes, causas de
   aumento/diminuição, tentativa, concurso de crimes, multa e eventual pedido de reparação mínima.
3. **Simulação instantânea:** o painel calcula pena-base, intermediária, definitiva, **regime inicial
   por espécie de pena**, **prescrição por crime** (art. 119 do CP e Súmula 497/STF) e alertas jurídicos
   (por exemplo, vedação de substituição ou sursis quando há vetor judicial negativo).
4. **Prescrição na minuta:** o tópico de prescrição só é escrito na sentença quando há prescrição a
   reconhecer. Se não houver, a minuta não abre esse tópico.

### Estação 4 — Sentença

1. **Gerar a minuta:** escolha o modo de geração (seccional ou monolítico) e o motor:
   - **Motor determinístico / Ollama local** (100% na sua máquina);
   - **Gemini (opcional):** só funciona se você habilitar o envio externo nas Configurações **e**
     marcar o consentimento na operação; antes do envio, o texto passa por **pseudonimização** (nomes
     e números de processo são substituídos por marcadores e restaurados na volta).
2. **Revisar na folha:**
   - Use a **barra de formatação** (fonte, tamanho, entrelinhas, recuos, margens ABNT/LibreOffice,
     numeração de páginas), **Localizar e substituir** (`Ctrl+F` / `Ctrl+H`), **Trechos do gabinete**
     (digite `/atalho` + `Tab`) e **Histórico de versões** para comparar alterações.
   - Abra o **Painel de revisão** para navegar item a item pelas pendências do laudo (marcadores
     `[COMPLETAR PELO JUÍZO: ...]`, IDs inexistentes, divergências entre dispositivo e dosimetria) e
     pela conferência formal do art. 381 do CPP.
3. **Auditoria anti-alucinação:** toda minuta recebe um laudo verificando forma, referências e
   aderência numérica das penas. A exportação definitiva exige auditoria aprovada; antes disso, você
   pode exportar um **rascunho** com a marca d'água **MINUTA NÃO AUDITADA**.
4. **Teses da defesa:** cada tese defensiva é respondida no tópico **"Da Apreciação Individualizada das
   Teses Defensivas"** (art. 381, III, do CPP).
5. **Versão desatualizada:** se a minuta tiver sido alterada em outra janela ou regenerada, ao salvar,
   regenerar ou copiar aparece o aviso "A revisão da minuta mudou. Reabra a versão atual". Isso evita
   sobrescrever um trabalho mais recente.
6. **Exportação congelada:** o arquivo exportado (Word, PDF, HTML ou cópia para o PJe) é uma cópia fixa
   da mesma versão da minuta que foi auditada; se a minuta mudou depois, a exportação avisa em vez de
   misturar versões.

---

## 3. Temas visuais

No **menu do logo**, escolha o desenho que melhor se adapta à sua tela e à sua rotina:

- **Original:** layout padrão do SentencIA.
- **Gabinete Clássico:** estética editorial forense com foco na leitura prolongada da folha.
- **Estúdio Moderno:** navegação superior segmentada e cartões limpos.
- **Cockpit / Alta Densidade:** máximo aproveitamento de espaço em tela, preservando largura confortável
  de leitura na folha.

Todos os 4 temas possuem **modo claro e escuro** e usam fontes hospedadas localmente (sem carregar
fontes da internet).

---

## 4. Pré-análise de processos em lote

Ideal para triagem de vários processos antes de sentenciar:

1. Abra **Pré-análise em lote** pelo menu do logo.
2. Selecione vários PDFs de processos de uma vez e inicie a triagem em segundo plano.
3. Acompanhe o andamento na fila: o sistema resume peças, crimes identificados, datas e eventuais
   alertas de cada processo.
4. Clique em **Abrir caso** em qualquer item da lista para carregá-lo diretamente nas 4 estações.

---

## 5. Jurisprudência do gabinete e acervo fechado

Abra **Jurisprudência & Acervo** pelo menu do logo:

- **Acervo fechado (verificado):** reúne súmulas e precedentes consolidados da área (como o Tema
  1197/STJ e súmulas do STJ/STF). O auditor usa essa base para evitar citações inventadas.
- **Precedentes do gabinete:** cadastre entendimentos próprios do juízo, edite ou exclua itens,
  execute a **validação** de referências e use **Exportar / Importar JSON** para compartilhar o
  repertório com assessores ou fazer cópia de segurança.

---

## 6. Cópia para o PJe e exportação

Na Estação 4, você pode baixar a sentença em **Word (`.docx`)**, **PDF**, **HTML** ou clicar em
**Copiar para o PJe**:

- **Fidelidade e segurança:** o conteúdo copiado para a área de transferência é higienizado por lista
  branca (preservando parágrafos, negritos, itálicos, recuos e referências `Id.`/`ID-`, sem tags ou
  estilos perigosos), com fallback limpo em texto simples caso o editor de destino não aceite HTML.
- **Assinatura digital no PJe:** o SentencIA **não assina** documentos. Cole a minuta no editor do
  PJe, faça a conferência final e realize a **assinatura eletrônica diretamente no PJe** com o seu
  certificado/token.
- **Pasta de exportação automática (opcional):** nas Configurações, você pode indicar uma pasta do
  computador para receber automaticamente uma cópia de cada exportação (com opção de subpasta por
  número de processo). O sistema nunca sobrescreve um arquivo editado por você.

---

## 7. Configurações úteis de rotina

No menu do logo → **Configurações**:

- **Ollama (IA local):** o seletor mostra os modelos efetivamente instalados na sua máquina e avisa
  se o modelo configurado precisar ser baixado.
- **Gemini (nuvem opcional):** salve a chave no cofre seguro do sistema operacional, clique em
  **Testar chave** para validar na hora e deixe o seletor de modelo em modo automático (com fallback
  em caso de indisponibilidade).
- **Modo GPU moderado:** se o computador esquentar, travar ou ficar lento ao usar transcrição e IA
  local, ative o **Modo GPU moderado**. Ele organiza as tarefas pesadas em fila única de GPU, limita
  o uso de memória de vídeo a 70%, reduz o contexto do Ollama e usa perfil econômico no Whisper.
- **Arquivo morto (retenção):** quando você usa "Limpar tudo", o caso anterior é arquivado em
  `data/cases/_arquivados/`. Nas Configurações, você pode simular (*dry-run*) e aplicar a limpeza de
  arquivos antigos conforme o prazo desejado.

---

## 8. Privacidade: o que fica e o que sai da máquina

| Operação | Fica 100% na sua máquina? | Observação |
|---|---|---|
| Leitura de PDFs, OCR nativo e classificação de peças | **Sim** | Processamento local; nada é enviado à rede |
| Transcrição de audiências com Whisper local | **Sim** | Motor padrão do sistema |
| Cálculo de dosimetria, prescrição e auditoria | **Sim** | 100% determinístico e local |
| Redação assistida com Ollama local | **Sim** | Modelo roda no próprio computador |
| Exportação `.docx`, `.pdf`, HTML e cópia para o PJe | **Sim** | Gravado apenas nas pastas locais escolhidas |
| Chave do Gemini | **Sim** | Guardada no cofre do sistema (Credential Manager / Keychain), nunca no navegador |
| Redação/refinamento com **Gemini** (opcional) | **Sai só com 2 autorizações** | Exige habilitar envio externo nas Configurações **e** consentir na operação; vai pseudonimizado |
| Transcrição em nuvem (opcional) | **Sai só com 2 autorizações** | Só é usada se você habilitar e consentir expressamente |
| Ollama em outro computador (remoto, opcional) | **Sai só com 2 autorizações** | Exige habilitar nas Configurações **e** confirmar o destino a cada operação |

- **Autorização a cada operação:** todo envio para fora da máquina (Ollama remoto, revisão crítica
  pela IA na nuvem, transcrição na nuvem) pede sua confirmação naquela operação. Sem ela, o SentencIA
  **recusa o envio** e avisa; nada sai por padrão. Se o endereço do Ollama remoto mudar, a confirmação
  é pedida de novo. Antes de sair, nomes e números de processo são **pseudonimizados**.

- O servidor do SentencIA escuta **apenas em `127.0.0.1`** (inacessível a outros computadores da rede)
  e exige token de sessão renovado a cada abertura.
- **Recomendação de sigilo:** instale o programa e salve os autos fora de pastas sincronizadas com a
  nuvem (como OneDrive, Google Drive ou iCloud Drive).
