# Design System, Stitch Specification & TasteForge Contract — SentencIA Studio V2

## 1. Team Agent Orchestration Kanban (`team-agent-orchestration`)

| Card ID | Work Item | Owner Role | State | Evidence & Merge Gate |
| :--- | :--- | :--- | :--- | :--- |
| `JVDFM-V2-01` | **Stitch Design System & TasteForge Distillation** | `design-director` | `merged` | `design/stitch-design-system.json`, `design/forge-taste-profile.json`, `design/forge-style-spec.json` |
| `JVDFM-V2-02` | **Liquid Glass Spatial Shell & Zero-Emoji SVG Iconography** | `ui-architect` | `merged` | Floating Glass Rail, Pill Dock, SVG fractalNoise 1.8%, Specular 1px inset highlight, 0 emojis |
| `JVDFM-V2-03` | **Rich Visualizers (VRAM Radial Gauge, Laya Spine, Whisper Waveform, Triphasic Arc)** | `dataviz-engineer` | `merged` | Dynamic SVG arcs, 64-bar interactive audio waveform, connected chronological nodes |
| `JVDFM-V2-04` | **Floating Judicial Letterhead Reader & 4-Layer Anti-Hallucination Pipeline** | `legal-ux-specialist` | `merged` | Cotton paper sheet, Republic watermark, line numbering, sticky outline rail, 100% score ring |
| `JVDFM-V2-05` | **Backend Pytest & Live Chrome DevTools Verification** | `qa-verifier` | `merged` | `10 passed` in `pytest`, live DOM/console/screenshot verification on `127.0.0.1:8000` |

---

## 2. Direção Estética Autoral (`frontend-design-direction` + `taste` + `StitchMCP`)

- **Diagnóstico da V1 (Superado):** A V1 apresentava estética quadrada, rústica e típica de templates gerados por IA ("AI-slop"), com excesso de caixas retangulares aninhadas, bordas pesadas e emojis genéricos (`📂`, `🎙️`, `⚖️`, `📜`).
- **Direção da V2 (*Editorial Judicial Studio & Apple Liquid Glass*):**
  1. **Zero Emojis:** 100% dos ícones são vetores SVG customizados de traço fino (`stroke-width="1.5"`), alinhados opticamente (`make-interfaces-feel-better`).
  2. **Arquitetura Espacial Liquid Glass (`liquid-glass-design`):**
     - Superfícies translúcidas (`backdrop-filter: blur(22px) saturate(165%)`) sobre iluminação atmosférica radial (*ambient mesh glow*) e textura orgânica de grão editorial (`feTurbulence fractalNoise` a `1.8%` de opacidade).
     - Reflexo especular superior (`inset 0 1px 0 0 rgba(255, 255, 255, 0.12)`) e *Concentric Border Radii* (`24px` containers flutuantes, `16px` módulos editoriais, `12px` controles internos, `9999px` cápsulas).
  3. **Tipografia Editorial Autoral (Fontes Locais Offline em `app/static/fonts/`):**
     - **Display / Títulos Forenses & Minuta:** `'Newsreader'`, `'Georgia'`, `'Times New Roman'`, serif (OFL 1.1).
     - **Interface & Operação:** `'Plus Jakarta Sans'`, `-apple-system`, `'Segoe UI'`, sans-serif (OFL 1.1).
     - **Métricas, Prazos, Penas & Timestamps:** `'JetBrains Mono'`, `'Consolas'`, monospace com `font-variant-numeric: tabular-nums` (OFL 1.1).

---

## 3. Os 6 Módulos Visuais de Estúdio (Fim das Caixas Quadradas)

1. **Dossiê & Ingestão Magnética (`view-autos-upload` & `view-autos-pecas`):**
   - Zona de drop magnética com anéis orbitais concêntricos em SVG animado;
   - **Radial Gauge SVG** em tempo real para telemetria da GPU NVIDIA GeForce RTX 2060 (6GB VRAM) e latência de `18ms` do motor Laya System 1.
2. **Linha do Tempo Cronológica Fluida (`view-autos-timeline`):**
   - Espinha dorsal vertical contínua com gradiente luminoso, nós vetoriais por fase (`Inquisitorial`, `Medida Protetiva`, `Judicial`), filtros em cápsula e **Liquid Glass Slide-Over Drawer** para leitura imersiva da peça sem perder o contexto.
3. **Estúdio de Prova Oral & Waveform (`view-oral-transcricao`, `view-oral-indireto`, `view-oral-matriz`):**
   - Player simulado de audiência PJe Mídias com **Visualizador de Ondas Sonoras (SVG Waveform de 64 bandas)** interativo e sincronizado aos timestamps (`[00:42]`, `[01:46]`, `[03:31]`);
   - **Matriz de Corroboração (Resolução CNJ nº 492/2023)** diagramada como revista jurídica editorial em colunas assimétricas com selo vetorial oficial e citações caligráficas.
4. **Calculadora Trifásica em Arco Dinâmico (`view-decisao-calculadora`, `view-decisao-sinoptico`, `view-decisao-intertemporal`):**
   - **Anel/Arco Trifásico SVG Dinâmico** que redesenha proporcionalmente o peso da 1ª Fase (Art. 59 CP), 2ª Fase (Agravantes/Atenuantes) e 3ª Fase, além da divisão por espécie de pena (Reclusão vs. Detenção);
   - Seletores táteis com travas vetoriais de cadeado para **Súmulas 231, 444, 545 e 588 do STJ** e **Trava Anti-*Bis in Idem***, acompanhados de **Fita Contábil Forense** ao vivo.
5. **Studio Reader da Minuta de Sentença (`view-sentenca-minuta`):**
   - **Folha Timbrada Judicial Flutuante** com marca d'água vetorial das Armas da Justiça, numeração lateral de parágrafos forenses, **Sumário Navegável Flutuante (Split-View Outline)** à esquerda e citações `[ID-...]` com preview instantâneo.
6. **Certificação Anti-Alucinação em 4 Camadas (`view-sentenca-auditoria`):**
   - **Score Ring SVG de 100% de Fidelidade**, pipeline vetorial conectado das 4 camadas de auditoria e matriz de rastreabilidade probatória.

---

## 4. Contrato de Estados de Conteúdo (`contentStates`)

| Estado | Gatilho | Feedback Visível e Próxima Ação Segura |
| :--- | :--- | :--- |
| **Loading** | Processamento de PDF/OCR, transcrição GPU (`faster-whisper`) ou geração (`Ollama`) | Anéis orbitais animados e medidor radial de VRAM em tempo real (`tabular-nums`) |
| **Empty** | Nenhum arquivo adicional enviado na zona de drop | Ilustração vetorial orbital com botão de 1 clique para carregar o Processo Demonstrativo |
| **Error** | Servidor Ollama offline ou falha de leitura de arquivo | Indicador semântico âmbar com fallback automático determinístico em Python |
| **Partial** | Réu confessa parcialmente ou parte das páginas exige OCR | Tags editoriais indicando `OCR Seletivo` ou `Convergência Parcial` na Matriz CNJ 492/2023 |
| **Success** | Sentença gerada e aprovada nas 4 camadas do Auditor Anti-Alucinação | Anel SVG Esmeralda (`100% Fidelidade`), Toast com ícone vetorial e exportação `.docx`, HTML PJe e `.pdf` |
| **Permission** | Execução 100% local sob segredo de justiça | Cápsula `Sigilo Local (127.0.0.1)` com cadeado vetorial no cabeçalho |
