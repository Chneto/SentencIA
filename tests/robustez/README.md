# Testes de robustez (TASK-072)

Sete blocos, cada um com um método de teste diferente. Rodam na suíte normal (`uv run --extra nuvem pytest`) em
poucos segundos e só usam dados fictícios.

| Bloco | Arquivo | Método | O que protege |
|---|---|---|---|
| 1 | `test_bloco1_propriedades_dosimetria.py` | Testes baseados em propriedades (invariantes, monotonicidade, determinismo) | Pena-base sempre na faixa legal; Súmula 231; agravar nunca reduz; confissão nunca aumenta; mesmo cálculo sempre igual; soma do concurso material |
| 2 | `test_bloco2_valores_limite_vigencia.py` | Análise de valor-limite (véspera, dia, dia seguinte) | Vigência das Leis 13.641/2018, 14.132/2021, 14.188/2021, 14.994/2024, 15.280/2025 e 15.383/2026 |
| 3 | `test_bloco3_metamorficos_extracao.py` | Testes metamórficos | Extração de partes e crimes estável a quebras de linha, caixa do rótulo, peças irrelevantes, ordem e duplicatas |
| 4 | `test_bloco4_fuzzing.py` | Fuzzing determinístico com orçamento de tempo | Autos malformados (unicode raro, controle, HTML, injeção) não derrubam nem travam a extração |
| 5 | `test_bloco5_seguranca.py` | Testes adversariais (OWASP) e propriedades de privacidade | XSS na folha, travessia de pasta no nome do arquivo, pseudonimização sem vazamento e com ida e volta |
| 6 | `test_bloco6_contratos_api.py` | Varredura de rotas + particionamento em classes de equivalência | Nenhuma rota responde erro 500; entradas inválidas viram 4xx |
| 7 | `test_bloco7_desempenho.py` | Orçamento de desempenho e retrocesso catastrófico de regex | Peças enormes ou "quase casamentos" não travam o extrator de teses nem o crivo |

## Reproduzir e ampliar

- Uma falha mostra `[ROBUSTEZ_SEED=… caso=…]`. Rodar de novo com a mesma semente reproduz o caso:
  `$env:ROBUSTEZ_SEED=20261122; uv run --extra nuvem pytest tests/robustez -q`
- Bateria longa (mais casos por teste): `$env:ROBUSTEZ_N=1000`.
- Lei nova: acrescente uma linha nas tabelas `CRIACAO` ou `MUDANCA_DE_PENA` do bloco 2, com a fonte no docstring.

## Achados da primeira execução (22/11/2026)

- **Corrigido:** rótulos em maiúsculas (`ACUSADO:`, `VÍTIMA:`, comuns no PJe) não eram reconhecidos e o nome saía
  vazio (bloco 3). Os rótulos passaram a ser lidos sem diferenciar maiúsculas de minúsculas.
- **Comportamento documentado:** a despseudonimização devolve os nomes em MAIÚSCULAS (forma canônica forense); o
  bloco 5 compara sem diferenciar caixa.
- **Convenção do motor:** ano = 12 meses de 30 dias (360 dias) no cálculo em dias; o bloco 2 usa a mesma convenção.
- **Brechas de validação encontradas pelo bloco 6 (pendentes da TASK-061, marcadas `xfail(strict=True)`):** a simulação
  aceita veredicto numérico num crime existente e campo desconhecido no corpo; o override de metadados
  ignora em silêncio campos fora do envelope `metadados`. Quando a TASK-061 corrigir, os testes viram XPASS e falham,
  obrigando a promover os casos a exigência. O `xfail` só aceita a falha `AceitacaoIndevida` (HTTP 200): um 500 falha normalmente.
- **Revisão cruzada do CODEX (v2):** datas do bloco 2 literais das publicações oficiais (com o § 13 do art. 129 nos
  dois marcos), contratos exigem 422 com detalhe, concurso compara o mapa completo por espécie e o bloco 3 fixa o
  resultado esperado para cada família de rótulo.
