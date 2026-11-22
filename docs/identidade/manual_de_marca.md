# SentencIA — manual de marca

![Banner](banner.png)

## Ideia central

**Sentenc + IA.** O nome junta a *sentença* (a peça que o magistrado assina) à *IA* (a assistência que
organiza, calcula e confere). O desenho conta a mesma história em duas camadas:

- **Balança** — a Justiça, em continuidade com a marca anterior (azul-marinho e dourado).
- **Centelha** no topo — a inteligência artificial que *ilumina*, mas não toma o lugar do fiel da balança.
  A decisão continua sendo do juiz; por isso a centelha fica acima da balança, nunca nos pratos.

Na tipografia, o mesmo contraste: **"Sentenc"** em serifa (tradição forense) e **"IA"** em sans-serif
dourada (tecnologia).

## Assinatura e tom

| | |
|---|---|
| **Nome** | SentencIA (sempre com **S**, **I** e **A** maiúsculos; "o SentencIA") |
| **Frase-assinatura** | *A inteligência que auxilia. A decisão que é sua.* |
| **Descritor** | Redação forense assistida |
| **Promessas que a marca pode fazer** | auditoria anti-alucinação em quatro camadas · fundamentação rastreável às peças dos autos · execução local por padrão |
| **O que a marca nunca diz** | que decide, que substitui o magistrado ou que "garante" a sentença |

> Nos nomes de arquivo, pastas e comandos usa-se **SentencIA** (ou `sentencia`, minúsculo, em
> identificadores técnicos). "JVDFM" continua sendo apenas a sigla do juizado.

## Cores

| Papel | Cor | Uso |
|---|---|---|
| Marinho profundo | `#0A1A33` | fundos escuros, banner |
| Marinho | `#12284B` | fundo do símbolo e dos ícones |
| Ouro | `#E3B64A` (`#E0B84D` no tema escuro da interface) | balança, "IA" sobre fundo escuro |
| Ouro escuro | `#A87A12` | "IA" sobre fundo claro (contraste) |
| Creme | `#FFF3CF` | a centelha |
| Papel | `#F5F4EF` | fundo claro da interface |

## Tipografia

- **Nome / títulos de marca:** Newsreader (na interface) ou Georgia Bold (imagens estáticas).
- **"IA" e rótulos:** Plus Jakarta Sans ExtraBold (interface) ou Segoe UI Black (imagens estáticas).
- **Texto corrido da sentença:** Times New Roman 12 pt (ABNT), sem relação com a marca.

## Símbolo

- `app/static/assets/sentencia_mark.svg` — completo, para tamanhos a partir de 48 px.
- `app/static/assets/favicon.svg` — simplificado (traços mais grossos), legível a 16 px.
- Na interface o símbolo é um *sprite* (`<use href="#sia-marca"/>`) definido uma vez em `index.html`.
- Área de respiro: mantenha, ao redor do símbolo, no mínimo 1/8 da sua largura.
- Tamanho mínimo: 16 px (use o simplificado abaixo de 48 px).

**Não faça:** esticar ou girar; trocar as cores; pôr a centelha nos pratos; escrever "Sentencia" ou
"SENTENCIA" em material de divulgação; colocar a marca **dentro do documento judicial exportado**
(a sentença, o .docx, o PDF e o HTML do PJe saem sem qualquer marca do programa).

## Onde a marca aparece

| Peça | Arquivo |
|---|---|
| Cabeçalho da interface, estado vazio da folha | sprite `#sia-marca` + texto "Sentenc**IA**" |
| Aba do navegador | `favicon.svg`, `app_icon.ico`, `apple-touch-icon.png` |
| Atalho do Windows e janela | `app_icon.ico` (16 a 256 px) |
| macOS (Dock, Finder) | `SentencIA.app/Contents/Resources/AppIcon.icns` |
| README e pré-visualização do repositório (1280×640) | `docs/identidade/banner.png` |
| Miniatura quadrada (512 px) | `docs/identidade/miniatura.png` |
| Logotipo horizontal (fundo transparente) | `logo_horizontal_claro.png` / `logo_horizontal_escuro.png` |

## Como regenerar

Todas as imagens saem do mesmo desenho vetorial. Para mudar uma cor ou uma forma, edite
`scripts/gerar_identidade.py` e rode:

```powershell
uv run python scripts/gerar_identidade.py
```

Os `.svg` mantêm o degradê; os rasters (`.png`, `.ico`, `.icns`) usam a versão plana, porque o
renderizador de SVG do PyMuPDF não suporta degradês.


## Materiais no Canva

Os materiais gráficos institucionais, de documentação e de divulgação do **SentencIA** ficam organizados na pasta do Canva [SentencIA — Identidade e Materiais](https://www.canva.com/folder/FAHbhpPGNR4) e exportados em `docs/identidade/canva/`. Todos seguem a paleta oficial da marca (`#0A1A33`, `#12284B`, `#E3B64A`, `#A87A12`, `#FFF3CF`, `#F5F4EF`), sem elementos pagos/Pro e usando exclusivamente capturas do **caso de demonstração** (tema *Gabinete Clássico*).

> Como a conta conectada não possui Canva Pro (Kit de Marca bloqueado), a paleta, a tipografia e as regras de uso foram consolidadas no design **Guia de Estilo de Marca** (`DAHbhmrGvec`).

| Material | Arquivo exportado (`docs/identidade/canva/`) | Formato / Dimensão | Design no Canva |
|---|---|---|---|
| Guia de Estilo de Marca | `sentencia-guia-estilo.pdf` | A4 (1 pág.) | [DAHbhmrGvec](https://canva.link/im55bbgxj3k4pho) |
| Banner do README / repositório | `sentencia-banner.png` | PNG 1280×640 | [DAHbhoV4lgk](https://canva.link/79oz3nvlkuqntkw) |
| Miniatura social / Open Graph | `sentencia-miniatura.png` | PNG 1200×630 | [DAHbhqNNMVA](https://canva.link/mmokn339zcowilg) |
| Apresentação institucional (9 slides) | `sentencia-apresentacao.pdf`, `sentencia-apresentacao.pptx` | 16:9 (PDF + PPTX) | [DAHbhvl6Ahw](https://www.canva.com/d/kgZZGxxlA0NOQO1) |
| Guia rápido do gabinete | `sentencia-guia-rapido.pdf` | A4 (2 págs.) | [DAHbhved-08](https://canva.link/uex2tg68hsgj80w) |
| Cartaz / Ficha de privacidade (LGPD) | `sentencia-privacidade.pdf` | A4 (1 pág.) | [DAHbhlncrWg](https://www.canva.com/d/h4YXiguuPHcxR_Y) |

### Regras de uso dos materiais

- **Sigilo absoluto (LGPD / Segredo de Justiça):** nunca suba autos reais, nomes de partes, números de processos reais ou capturas fora do caso de demonstração (`app/demo/`) para o Canva.
- **Fidelidade ao software:** ao atualizar apresentações ou guias, confira todas as afirmações contra `README.md`, `CHANGELOG.md` e `docs/agents.md`; não apresente itens de roadmap como prontos.
- **Acessibilidade e licenças:** mantenha contraste mínimo WCAG AA, corpo de texto ≥ 11 pt nos PDFs impressos e evite fotos de pessoas ou elementos Pro/pagos.
- **Separado do documento judicial:** esses materiais são exclusivamente para divulgação, capacitação e consulta no gabinete; a marca nunca entra na sentença exportada (`.docx`, `.pdf` ou HTML do PJe).
