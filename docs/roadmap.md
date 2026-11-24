# ROADMAP.MD — Roteiro de Evolução e Aperfeiçoamento Estratégico
## Sistema Especialista de Sentenças Penais — JVDFM (Versão 3.0)

Este roadmap delineia o planejamento estratégico e a evolução técnica do sistema, organizando as fases de implementação para transformar a ferramenta em uma plataforma de referência nacional em inteligência artificial judiciária.

---

### Linha do Tempo e Marcos do Projeto

```
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│     FASE 1      │ ──▶ │     FASE 2      │ ──▶ │     FASE 3      │ ──▶ │     FASE 4      │
│  Núcleo Básico  │     │  Cooperação     │     │ Auto-Correção   │     │ Otimização VRAM │
│  & Dosimetria   │     │ Multi-Agentes   │     │ & Redundância   │     │ & Hardware Local│
│   [CONCLUÍDO]   │     │   [CONCLUÍDO]   │     │   [CONCLUÍDO]   │     │  [PLANEJADO]    │
└─────────────────┘     └─────────────────┘     └─────────────────┘     └─────────────────┘
                                                        │
                                              ┌─────────┴─────────┐
                                              │    FASE 2.5       │
                                              │  Reset de Produção │
                                              │   [CONCLUÍDO]     │
                                              └───────────────────┘
```

---

### Fase 1: Fundação do Sistema e Rigor Normativo (Concluído)
- [x] Ingestão e segmentação automatizada de PDFs do PJe (PyMuPDF + Laya System 1).
- [x] Transcrição local de audiências em áudio/vídeo (.mp4) via Whisper sem dependência de internet.
- [x] Motor matemático da Dosimetria Trifásica com análise das **8 circunstâncias judiciais do art. 59 do CP** de forma individualizada para cada delito, conforme modelos do TJRN e manual da ENFAM.
- [x] Blindagem contra *bis in idem* no art. 61, II, 'f', do CP e aplicação estrita da Súmula 444 e da Súmula 231 do STJ.
- [x] Acervo fechado com 60 verbetes de jurisprudência e doutrina autênticos com referências ABNT NBR 6023.
- [x] Auditor Anti-Alucinação em 4 Camadas com pontuação de 0 a 100 e bloqueio de citações apócrifas.
- [x] Interface gráfica Web Studio Editorial com design Liquid Glass, Modo Papel e alternância Claro/Escuro.
- [x] Exportação tripla com formatação milimétrica: Word (.docx), HTML PJe e PDF.
- [x] Suíte de testes automatizados (42 testes na fundação).

---

### Fase 2: Cooperação Multi-Camadas Python + Ollama (Concluído na Geração 3)
- [x] **Desacoplamento Modular de Pré-Análise:** rotinas Python dedicadas processam a lógica jurídica de cada ponto controvertido (prescrição, materialidade pericial, tipicidade e teses defensivas) gerando "Dossiês de Restrição" (`DossieRestricaoSecao`) antes de qualquer chamada generativa.
- [x] **Generatividade Delimitada (Prompt Bounding):** o modelo local recebe payload com fatos provados, regra jurídica e conclusão pré-calculada; cabe ao modelo apenas a redação gramatical e a argumentação estilística (`construir_prompt_seccional` com cláusulas de proibição de alucinação).
- [x] **Cooperação Bidirecional Python ⮂ Ollama:**
  - o Python analisa a lógica inicial e preenche as balizas;
  - o Ollama redige o texto da fundamentação com base naquelas balizas;
  - o Python/sistema analisa a saída gerada para verificar se foram introduzidos fatos novos ou alteradas as diretrizes legais (Duplo Crivo: Auditor Python 4 Camadas + Agente 7);
  - se aprovado, avança para a próxima seção; se reprovado, o trecho é automaticamente refeito (até 2 retentativas cirúrgicas) e, persistindo a falha, aplica-se fallback determinístico em Python.
- [x] **Estruturação Formal do Relatório das Partes:** síntese padronizada das alegações finais do Ministério Público e da Defesa Técnica na seção de Relatório, com enfrentamento simétrico das teses catalogadas pelo Agente 3.
- [x] **Pipeline Seccional In-Flight Gating (M1):** máquina de estados das 5 seções (Relatório, Preliminares, Mérito por Delito, Dosimetria por Delito, Dispositivo) com memória selada, gating imediato e regeneração atômica de seção.
- [x] **Agente 7 — Crítica Adversarial (M2):** `app/generation/adversarial_critic.py` (schema `RelatorioCriticaAdversarial`, F07–F12), 45 testes dedicados; Gate 2 do E2E remediado (F09/F10 sem `xfail`).
- [x] **Marco M3 — Auditoria Seccional Granular + Duplo Crivo:** `auditar_secao` e wrappers por seção no auditor; módulo `app/generation/duplo_crivo.py` combinando auditoria determinística e Agente 7; 75 testes dedicados; fallback sinalizado no esgotamento das 2 retentativas.
- [x] **Marco M4 — API Seccional Real + Reset de Produção:** rotas reais `/api/sentence/generate-sectional`, `/api/sentence/regenerate-section` e `/api/cases/reset`; estado de sessão autoritativo com `generation_id`; estado vazio anti-poluição; eliminação dos shims de teste do conftest E2E.

---

### Fase 2.5: Reset de Produção e Início de Nova Sentença (Concluído)
Exigência direta do usuário: iniciar uma nova sentença **sem vício ou poluição da sentença anterior**.
- [x] **Backend autoritativo de reset** (`app/core/session_state.py`):
  - modo `producao`: limpa minuta, auditoria, dosimetria, telemetria do pipeline e exportações (`Sentença - *` e legado `Sentenca_JVDFM_*`), **preservando os autos** para prolatar novamente o mesmo processo;
  - modo `completo`: além do acima, **arquiva** (não destrói) autos, mídias, transcrições e caches em `data/cases/_arquivados/<timestamp>/`, zera o bundle ativo e suprime o autoload de demo/autos antigos;
  - `generation_id` monotônico para rastreabilidade; lock de estado; proteção de caminhos (`Path.resolve()` + parent) e preservação incondicional de `knowledge_base`, `templates_gabinete` e `user_settings.json`.
- [x] **UI Liquid Glass:** botão **“Nova Sentença”** na ribbon do processo, modal acessível com os dois modos (o modo destrutivo exige confirmação explícita por checkbox), atalho no Command Palette (Ctrl+K), limpeza integral do estado do frontend e renderização de estado vazio editorial sem qualquer resíduo do caso anterior.
- [x] **Endurecimento de robustez:** upload de PDF corrompido ou vazio é rejeitado com **HTTP 422** explicativo (sem HTTP 500) e não apaga a produção em curso nem deixa resíduos em `data/cases`.
- [x] **Verificação ponta a ponta:** 363/363 testes globais, 95/95 E2E, fluxo vivo em servidor real (49/49 checks) e clique real do botão em Chrome headless (18/18 checks, DOM sem resíduos).

---

### Fase 2.8: Redesign "Gabinete Editorial Limpo" + Maturidade Técnica v3.0 (Concluído em 18/11/2026)
- [x] **UX/UI Editorial Limpo:** Tema Claro Editorial padrão (`#F6F5F1`/`#FFFFFF`/`#1E3A5F`), 4 Estações Diretas sem navegação concorrente, remoção de anéis/velocímetros SVG gigantes, upload bipartido com barra de progresso real (`/api/cases/upload-progress`), folha editável com `Re-Auditar` (`POST /api/sentence/update-and-audit`) e gavetas laterais para Auditoria 4 Camadas e Inspetor `ID-...`.
- [x] **Deduplicação APF × IP e Densidade:** Script Py `compacto` (~59 KB em caso típico) vs `integral`, preservando laudos, depoimentos e teses.
- [x] **Veredicto por Delito & Concurso:** `Condenar` / `Absolver (Art. 386)` / `Desclassificar (Art. 383)` / `Extinguir (Art. 107)`, Procedência Parcial e Absolvição Integral auditadas em 4 camadas, Concurso Material/Formal/Continuado + regra benéfica do Art. 70 § único.
- [x] **Catálogo Penal Expandido & Privacidade:** 22 tipos penais mapeados, pseudonimização ampliada (`RG`, `TELEFONE`, `ENDERECO`, `TESTEMUNHA`, `DESCENDENTE`), Gemini Files API >18MB, `initial_prompt` forense e timbre TJRN em `.docx` com ABNT intacto.
- [x] **Cobertura v3.0:** 382 testes aprovados sem regressões (`tests/test_v3_enhancements.py` + suíte integral + smoke Playwright).

---

### Fase 3: Ciclo de Crítica Adversarial e Auto-Correção Cognitiva (Concluído na Geração 3)
- [x] **Agente Crítico Adversarial (Ollama em Modo Tribunal):** persona de Desembargador Revisor / Promotor Corregedor avaliando coerência probatória, contradições e padrão CNJ 492/2023 (com Tier 2 determinístico quando o Ollama está offline).
- [x] **Pipeline de Regeneração Cirúrgica:** regeneração de apenas o parágrafo/seção afetada, preservando todas as demais seções seladas (`SectionalPipelineEngine.regenerar_secao_atomica`), com até 2 retentativas e fallback determinístico.
- [x] **Matriz de Conformidade com o Protocolo de Gênero (CNJ 492):** crivo dedicado no Agente 7 (F12) que bloqueia estereótipos, vitimização secundária e a tese de legítima defesa da honra (ADPF 779/STF).

---

### Fase 4: Otimização de Performance para Hardware Local (Planejado)
- [ ] **Aproveitamento Eficiente da VRAM (NVIDIA RTX 2060 6GB):**
  - Implementação de quantização 4-bit (Q4_K_M) para modelos de 7B/8B (como Llama-3-8B-Instruct ou Qwen-2.5-7B-Instruct em português), operando inteiramente na VRAM de 6GB com latência mínima.
  - Alternância inteligente entre CPU e GPU durante a transcrição Whisper e a geração LLM, evitando saturação de memória.
- [ ] **Cache Local de Contexto e Pré-Processamento em Batch:** cache de embeddings das peças do processo para acelerar respostas e reduzir tempo de inferência local.

---

### Fase 5: Integração Completa de Gabinete e Fluxos em Lote (Longo Prazo)
- [ ] **Importação Direta de Pastas do PJe:** arrastar múltiplos processos e enfileirar a geração de relatórios e minutas.
- [ ] **Painel de Controle de Jurisprudência Customizada:** interface visual para o magistrado e assessores adicionarem novos precedentes do gabinete ao acervo permanente com validação automática de dados.
- [ ] **Assinatura Digital Integrada:** suporte a certificação digital ICP-Brasil (Token A3) no arquivo PDF diretamente pela ferramenta.
- [ ] **Política de Retenção do Arquivo Morto:** rotina de expurgo/compactação de `data/cases/_arquivados/` com registro de auditoria.
