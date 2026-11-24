import json
import logging
import os
import re
import tempfile
import threading
import time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple
from app.core.config import KNOWLEDGE_DIR

logger = logging.getLogger(__name__)

CUSTOM_KB_FILE = KNOWLEDGE_DIR / "custom_precedentes.json"
ACERVO_COMPLETO_FILE = KNOWLEDGE_DIR / "acervo_completo.json"

#: Versão do formato de exportação/importação do acervo do gabinete.
FORMATO_EXPORTACAO_VERSAO = 1
#: Comprimento mínimo exigido para o texto literal de um precedente do gabinete.
MIN_TEXTO_LITERAL_CHARS = 20
#: Tribunais e conselhos reconhecidos na validação do acervo do gabinete.
TRIBUNAIS_RECONHECIDOS: frozenset[str] = frozenset(
    {
        "STF",
        "STJ",
        "TST",
        "TSE",
        "STM",
        "CNJ",
        "CJF",
        "FONAVID",
        "FONAJE",
        "FONAJUV",
        "TRF1",
        "TRF2",
        "TRF3",
        "TRF4",
        "TRF5",
        "TRF6",
        "TJAC",
        "TJAL",
        "TJAM",
        "TJAP",
        "TJBA",
        "TJCE",
        "TJDFT",
        "TJES",
        "TJGO",
        "TJMA",
        "TJMG",
        "TJMS",
        "TJMT",
        "TJPA",
        "TJPB",
        "TJPE",
        "TJPI",
        "TJPR",
        "TJRJ",
        "TJRN",
        "TJRO",
        "TJRR",
        "TJRS",
        "TJSC",
        "TJSE",
        "TJSP",
        "TJTO",
    }
)


class PrecedenteOficialReadOnlyError(PermissionError):
    """Tentativa de alterar ou excluir um item do acervo oficial (somente leitura)."""


class PrecedenteNaoEncontradoError(KeyError):
    """Precedente do gabinete não localizado pelo identificador informado."""


class PrecedenteDuplicadoError(ValueError):
    """Precedente com referência equivalente já existente no acervo oficial ou do gabinete."""

    def __init__(self, mensagem: str, *, item_existente: Dict[str, Any], origem: str) -> None:
        super().__init__(mensagem)
        self.item_existente = dict(item_existente)
        self.origem = origem

#: Serializa leituras-modificações-escritas do acervo do gabinete (IDs únicos, sem corrida).
_KB_LOCK = threading.RLock()
#: Contador local de alterações feitas por este processo (compõe ``versao_acervo``).
_VERSAO_LOCAL = 0

_DATA_CONFERENCIA = "2026-11-21"
_URL_STJ_SUMULAS = "https://www.stj.jus.br/sites/portalp/Jurisprudencia/Sumulas"
_URL_STF_SUMULAS = "https://portal.stf.jus.br/textos/verTexto.asp?servico=jurisprudenciaSumula"
_URL_STJ_TESES_41 = (
    "https://www.stj.jus.br/docs_internet/jurisprudencia/jurisprudenciaemteses/"
    "Jurisprudencia%20em%20Teses%2041%20-%20Violencia%20Domestica%20e%20Familiar%20Contra%20Mulher.pdf"
)
_URL_FONAVID = "https://www.tjpr.jus.br/documents/d/cevid/fonavid_enunciados_atualizados_xviifonavid-1-pdf"
_URL_PCSC_2026 = "https://pc.sc.gov.br/wp-content/uploads/2026/05/atualizacao-de-jurisprudencia-Lei-11.340.2006.pdf"
URL_BANCO_CNJ_GENERO = (
    "https://paineisanalytics.cnj.jus.br/single/"
    "?appid=f3bb4296-6c88-4c1f-b3bb-8a51e4268a58&sheet=03bb002c-6256-4b1d-9c93-a421f1bf8833"
)
_URL_CNJ_RES_492 = "https://atos.cnj.jus.br/atos/detalhar/4986"
_URL_SCON_STJ = "https://scon.stj.jus.br"
_URL_TJRN = "https://www.tjrn.jus.br"
_URL_STF_TEMA_1370 = "https://portal.stf.jus.br/jurisprudenciaRepercussao/tema.asp?num=1370"
_URL_STF_TEMA_1412 = "https://portal.stf.jus.br/jurisprudenciaRepercussao/tema.asp?num=1412"
_URL_STF_ADPF_779 = "https://portal.stf.jus.br/noticias/verNoticiaDetalhe.asp?idConteudo=511556"
_URL_STF_ADC_19 = "https://portal.stf.jus.br/processos/detalhe.asp?incidente=2584650"
_URL_STF_ADI_4424 = "https://portal.stf.jus.br/processos/detalhe.asp?incidente=3897992"
_URL_BDJUR_STJ = "https://bdjur.stj.jus.br"
_URL_JUSPODIVM = "https://www.editorajuspodivm.com.br"


def _url_tema_stj(num: int) -> str:
    return (
        "https://processo.stj.jus.br/repetitivos/temas_repetitivos/pesquisa.jsp"
        f"?novaConsulta=true&tipo_pesquisa=T&cod_tema_inicial={num}&cod_tema_final={num}"
    )


def _verif(status: str, fonte_url: str) -> Dict[str, str]:
    return {"status": status, "fonte_url": fonte_url, "conferido_em": _DATA_CONFERENCIA}


# Base Consolidada e Verificada de Jurisprudência, Súmulas, Temas e Normas (STF, STJ, FONAVID, TJRN e CNJ)
# Acesso sempre por ID via obter_item_oficial_por_id() — nunca por índice posicional.
SUMULAS_E_TEMAS_VERIFICADOS: List[Dict[str, Any]] = [
    # 0
    {
        "id": "SUMULA-536-STJ",
        "tipo": "SUMULA_STJ",
        "categoria": "SUMULAS",
        "tribunal": "STJ",
        "referencia": "Súmula 536/STJ",
        "titulo": "Inaplicabilidade da Lei nº 9.099/1995 (Suspensão Condicional do Processo e Transação Penal)",
        "texto_literal": "A suspensão condicional do processo e a transação penal não se aplicam na hipótese de delitos sujeitos ao rito da Lei Maria da Penha.",
        "materia": "Processo Penal e Juizados Especiais",
        "fonte_oficial": "Súmula da Terceira Seção do STJ (DJe 23/10/2015)",
        "observacao": "Amostra do 1º grau do TJRN (2024–2026): precedente citado em 16 sentenças.",
        "natureza": "LITERAL",
        "verificacao": _verif("verificado", _URL_STJ_SUMULAS),
    },
    # 1
    {
        "id": "SUMULA-542-STJ",
        "tipo": "SUMULA_STJ",
        "categoria": "SUMULAS",
        "tribunal": "STJ",
        "referencia": "Súmula 542/STJ",
        "titulo": "Ação Penal Pública Incondicionada na Lesão Corporal Doméstica",
        "texto_literal": "A ação penal relativa ao crime de lesão corporal resultante de violência doméstica contra a mulher é pública incondicionada.",
        "materia": "Ação Penal e Condição de Procedibilidade",
        "fonte_oficial": "Súmula da Terceira Seção do STJ (DJe 01/10/2015)",
        "observacao": "Amostra do 1º grau do TJRN (2024–2026): 11 citações à súmula. Em 43 sentenças sobre retratação ou reconciliação, perdão ou retomada da convivência não afastam a ação penal pública incondicionada (ver também ADI 4.424/STF) nem autorizam a insignificância (Súmula 589/STJ).",
        "natureza": "LITERAL",
        "verificacao": _verif("verificado", _URL_STJ_SUMULAS),
    },
    # 2
    {
        "id": "SUMULA-588-STJ",
        "tipo": "SUMULA_STJ",
        "categoria": "SUMULAS",
        "tribunal": "STJ",
        "referencia": "Súmula 588/STJ",
        "titulo": "Vedação de Substituição por Pena Restritiva de Direitos",
        "texto_literal": "A prática de crime ou contravenção penal contra a mulher com violência ou grave ameaça no ambiente doméstico impossibilita a substituição da pena privativa de liberdade por restritiva de direitos.",
        "materia": "Execução Penal e Penas Alternativas",
        "fonte_oficial": "Súmula da Terceira Seção do STJ (DJe 23/02/2017)",
        "observacao": "Amostra do 1º grau do TJRN (2024–2026): cerca de 116 citações à súmula. Em 72 sentenças sobre substituição e sursis, a violência ou grave ameaça impede a substituição (art. 44, I, do CP), e a suspensão condicional da pena é examinada separadamente (art. 77 do CP).",
        "natureza": "LITERAL",
        "verificacao": _verif("verificado", _URL_STJ_SUMULAS),
    },
    # 3
    {
        "id": "SUMULA-589-STJ",
        "tipo": "SUMULA_STJ",
        "categoria": "SUMULAS",
        "tribunal": "STJ",
        "referencia": "Súmula 589/STJ",
        "titulo": "Inaplicabilidade do Princípio da Insignificância na Violência Doméstica",
        "texto_literal": "É inaplicável o princípio da insignificância nos crimes ou contravenções penais praticados contra a mulher no âmbito das relações domésticas.",
        "materia": "Tipicidade Material e Bagatela",
        "fonte_oficial": "Súmula da Terceira Seção do STJ (DJe 23/02/2017)",
        "observacao": "Amostra do 1º grau do TJRN (2024–2026): 13 citações à súmula e 16 sentenças sobre a inaplicabilidade da insignificância.",
        "natureza": "LITERAL",
        "verificacao": _verif("verificado", _URL_STJ_SUMULAS),
    },
    # 4
    {
        "id": "SUMULA-600-STJ",
        "tipo": "SUMULA_STJ",
        "categoria": "SUMULAS",
        "tribunal": "STJ",
        "referencia": "Súmula 600/STJ",
        "titulo": "Desnecessidade de Coabitação entre Autor e Vítima",
        "texto_literal": "Para a configuração da violência doméstica e familiar prevista no artigo 5º da Lei n. 11.340/2006 (Lei Maria da Penha) não se exige a coabitação entre autor e vítima.",
        "materia": "Competência e Âmbito de Aplicação da LMP",
        "fonte_oficial": "Súmula da Terceira Seção do STJ (DJe 24/11/2017)",
        "observacao": "Amostra do 1º grau do TJRN (2024–2026): precedente citado em 15 sentenças.",
        "natureza": "LITERAL",
        "verificacao": _verif("verificado", _URL_STJ_SUMULAS),
    },
    # 5
    {
        "id": "SUMULA-231-STJ",
        "tipo": "SUMULA_STJ",
        "categoria": "SUMULAS",
        "tribunal": "STJ",
        "referencia": "Súmula 231/STJ",
        "titulo": "Limite Mínimo Legal na Segunda Fase da Dosimetria",
        "texto_literal": "A incidência da circunstância atenuante não pode conduzir à redução da pena abaixo do mínimo legal.",
        "materia": "Dosimetria da Pena (Segunda Fase)",
        "fonte_oficial": "Súmula da Terceira Seção do STJ (DJ 15/10/1999)",
        "observacao": "Amostra do 1º grau do TJRN (2024–2026): precedente citado em cerca de 46 sentenças.",
        "natureza": "LITERAL",
        "verificacao": _verif("verificado", _URL_STJ_SUMULAS),
    },
    # 6
    {
        "id": "SUMULA-444-STJ",
        "tipo": "SUMULA_STJ",
        "categoria": "SUMULAS",
        "tribunal": "STJ",
        "referencia": "Súmula 444/STJ",
        "titulo": "Vedação de Maus Antecedentes por Inquéritos e Ações em Curso",
        "texto_literal": "É vedada a utilização de inquéritos policiais e ações penais em curso para agravar a pena-base.",
        "materia": "Dosimetria da Pena (Primeira Fase)",
        "fonte_oficial": "Súmula da Terceira Seção do STJ (DJe 13/05/2010)",
        "observacao": "Amostra do 1º grau do TJRN (2024–2026): cerca de 29 citações à súmula. Sobre maus antecedentes, ver também o Tema 129/STF. A suficiência da folha de antecedentes para comprovar a reincidência é objeto da Súmula 636/STJ.",
        "natureza": "LITERAL",
        "verificacao": _verif("verificado", _URL_STJ_SUMULAS),
    },
    # 7
    {
        "id": "SUMULA-545-STJ",
        "tipo": "SUMULA_STJ",
        "categoria": "SUMULAS",
        "tribunal": "STJ",
        "referencia": "Súmula 545/STJ",
        "titulo": "Atenuante da Confissão Espontânea Utilizada na Formação do Convencimento",
        "texto_literal": "Quando a confissão for utilizada para a formação do convencimento do julgador, o réu fará jus à atenuante prevista no art. 65, III, d, do Código Penal.",
        "materia": "Dosimetria da Pena (Atenuantes)",
        "fonte_oficial": "Súmula da Terceira Seção do STJ (DJe 03/08/2015)",
        "observacao": "Amostra do 1º grau do TJRN (2024–2026): cerca de 24 citações à súmula. Em 89 sentenças sobre confissão, a atenuante do art. 65, III, d, do CP é reconhecida quando a confissão é usada na condenação, em regra à fração de 1/6, com possibilidade de compensação e observância do mínimo legal (Súmula 231/STJ).",
        "natureza": "LITERAL",
        "verificacao": _verif("verificado", _URL_STJ_SUMULAS),
    },
    # 8
    {
        "id": "TEMA-983-STJ",
        "tipo": "TEMA_REPETITIVO_STJ",
        "categoria": "TEMAS_REPETITIVOS",
        "tribunal": "STJ",
        "referencia": "Tema Repetitivo 983/STJ",
        "titulo": "Indenização Mínima por Danos Morais In Re Ipsa na Violência Doméstica",
        "texto_literal": "Nos casos de violência contra a mulher praticados no âmbito doméstico e familiar, é possível a fixação de valor mínimo indenizatório a título de dano moral, desde que haja pedido expresso da acusação ou da parte ofendida, ainda que não especificada a quantia, e independentemente de instrução probatória.",
        "materia": "Reparação de Danos (Art. 387, IV, CPP)",
        "fonte_oficial": "STJ, Terceira Seção, REsp 1.643.051/MS e REsp 1.675.874/MS, Rel. Min. Rogerio Schietti Cruz, j. 28/02/2018, DJe 08/03/2018",
        "observacao": "Amostra do 1º grau do TJRN (2024–2026): cerca de 97 citações ao tema e 98 sentenças sobre dano moral mínimo. A amostra registra fixação sem instrução específica quando há pedido expresso na denúncia. Sem pedido, não há fixação. Correção monetária pela Súmula 362/STJ e juros pela Súmula 54/STJ. Os paradigmas REsp 1.675.874/MS e REsp 1.643.051/MS foram citados em cerca de 21 e 13 sentenças, respectivamente.",
        "natureza": "LITERAL",
        "verificacao": _verif("verificado", _url_tema_stj(983)),
    },
    # 9 (índice preservado por compatibilidade; tese e fonte corrigidas conforme STJ)
    {
        "id": "TEMA-1197-STJ",
        "tipo": "TEMA_REPETITIVO_STJ",
        "categoria": "TEMAS_REPETITIVOS",
        "tribunal": "STJ",
        "referencia": "Tema Repetitivo 1197/STJ",
        "titulo": "Aplicação da Agravante do Art. 61, II, 'f', do CP com a Lei Maria da Penha (Sem Bis in Idem)",
        "texto_literal": "A aplicação da agravante do art. 61, inc. II, alínea f, do Código Penal (CP), em conjunto com as disposições da Lei Maria da Penha (Lei n. 11.340/2006), não configura bis in idem.",
        "materia": "Dosimetria da Pena (Segunda Fase — Agravante de Violência contra a Mulher)",
        "fonte_oficial": "STJ, Terceira Seção, REsp 2.027.794/MS, REsp 2.026.129/MS e REsp 2.029.515/MS, Rel. Min. Og Fernandes, j. 12/06/2024, DJe 24/06/2024",
        "natureza": "LITERAL",
        "verificacao": _verif("verificado", _url_tema_stj(1197)),
    },
    # 10
    {
        "id": "RES-CNJ-492-2023",
        "tipo": "NORMA_CNJ",
        "categoria": "NORMAS_CNJ",
        "tribunal": "CNJ",
        "referencia": "Resolução CNJ nº 492/2023",
        "titulo": "Protocolo para Julgamento com Perspectiva de Gênero (Obrigatoriedade no Poder Judiciário)",
        "texto_literal": "Determina a adoção obrigatória das diretrizes do Protocolo para Julgamento com Perspectiva de Gênero em todo o Poder Judiciário brasileiro, conferindo especial relevância à palavra da mulher vítima em crimes praticados na clandestinidade doméstica e vedando estereótipos de gênero na valoração probatória.",
        "materia": "Hermenêutica Forense e Valoração Probatória",
        "fonte_oficial": "Resolução nº 492/2023 do Conselho Nacional de Justiça",
        "observacao": "Amostra do 1º grau do TJRN (2024–2026): cerca de 17 citações à resolução e 27 sentenças sobre julgamento com perspectiva de gênero. O Protocolo é aplicado com observância das garantias processuais e do art. 155 do CPP.",
        "natureza": "RESUMO",
        "verificacao": _verif("verificado", _URL_CNJ_RES_492),
    },
    # 11 - Súmulas Adicionais STJ
    {
        "id": "SUMULA-642-STJ",
        "tipo": "SUMULA_STJ",
        "categoria": "SUMULAS",
        "tribunal": "STJ",
        "referencia": "Súmula 642/STJ",
        "titulo": "Transmissibilidade do Direito à Reparação por Danos Morais aos Herdeiros",
        "texto_literal": "O direito à indenização por danos morais transmite-se com o falecimento do titular, possuindo os herdeiros da vítima legitimidade ativa para ajuizar ou prosseguir a ação indenizatória.",
        "materia": "Reparação de Danos e Legitimidade Sucessória",
        "fonte_oficial": "Súmula da Corte Especial do STJ (DJe 11/12/2020)",
        "natureza": "LITERAL",
        "verificacao": _verif("verificado", _URL_STJ_SUMULAS),
    },
    {
        "id": "SUMULA-269-STJ",
        "tipo": "SUMULA_STJ",
        "categoria": "SUMULAS",
        "tribunal": "STJ",
        "referencia": "Súmula 269/STJ",
        "titulo": "Regime Prisional Semiaberto ao Reincidente com Circunstâncias Judiciais Favoráveis",
        "texto_literal": "É admissível a adoção do regime prisional semi-aberto aos reincidentes condenados a pena igual ou inferior a quatro anos se favoráveis as circunstâncias judiciais.",
        "materia": "Execução Penal e Regime Prisional (Art. 33 CP)",
        "fonte_oficial": "Súmula da Terceira Seção do STJ (DJ 29/05/2002)",
        "natureza": "LITERAL",
        "verificacao": _verif("verificado", _URL_STJ_SUMULAS),
    },
    {
        "id": "SUMULA-440-STJ",
        "tipo": "SUMULA_STJ",
        "categoria": "SUMULAS",
        "tribunal": "STJ",
        "referencia": "Súmula 440/STJ",
        "titulo": "Vedação de Regime Mais Gravoso Fundado Apenas na Gravidade em Abstrato",
        "texto_literal": "Fixada a pena-base no mínimo legal, é vedado o estabelecimento de regime prisional mais gravoso do que o cabível em razão da sanção imposta, com base apenas na gravidade abstrata do delito.",
        "materia": "Individualização da Pena e Regime Prisional",
        "fonte_oficial": "Súmula da Terceira Seção do STJ (DJe 13/05/2010)",
        "natureza": "LITERAL",
        "verificacao": _verif("verificado", _URL_STJ_SUMULAS),
    },
    # Súmulas STF e Súmula Vinculante
    {
        "id": "SUMULA-718-STF",
        "tipo": "SUMULA_STF",
        "categoria": "SUMULAS",
        "tribunal": "STF",
        "referencia": "Súmula 718/STF",
        "titulo": "Gravidade em Abstrato Não Justifica Imposição de Regime Mais Severo",
        "texto_literal": "A opinião do julgador sobre a gravidade em abstrato do crime não constitui motivação idônea para a imposição de regime de cumprimento de pena mais severo do que o permitido segundo a pena aplicada.",
        "materia": "Regime Prisional e Motivação Judicial",
        "fonte_oficial": "Súmula do Plenário do STF (DJ 09/10/2003)",
        "natureza": "LITERAL",
        "verificacao": _verif("verificado", _URL_STF_SUMULAS),
    },
    {
        "id": "SUMULA-719-STF",
        "tipo": "SUMULA_STF",
        "categoria": "SUMULAS",
        "tribunal": "STF",
        "referencia": "Súmula 719/STF",
        "titulo": "Necessidade de Fundamentação Concreta para Regime Mais Severo",
        "texto_literal": "A imposição do regime de cumprimento mais severo do que a pena aplicada permitir exige motivação idônea.",
        "materia": "Regime Prisional e Motivação Idônea",
        "fonte_oficial": "Súmula do Plenário do STF (DJ 09/10/2003)",
        "natureza": "LITERAL",
        "verificacao": _verif("verificado", _URL_STF_SUMULAS),
    },
    {
        "id": "SUMULA-VINCULANTE-56-STF",
        "tipo": "SUMULA_VINCULANTE_STF",
        "categoria": "SUMULAS",
        "tribunal": "STF",
        "referencia": "Súmula Vinculante 56/STF",
        "titulo": "Vedação de Cumprimento de Pena em Regime Mais Gravoso por Falta de Vaga Prisional",
        "texto_literal": "A falta de estabelecimento penal adequado não autoriza a manutenção do condenado em regime prisional mais gravoso, devendo-se observar, nessa hipótese, os parâmetros fixados no RE 641.320/RS.",
        "materia": "Execução Penal e Dignidade Humana",
        "fonte_oficial": "STF, Plenário, Súmula Vinculante 56 (DJe 08/08/2016)",
        "natureza": "LITERAL",
        "verificacao": _verif("verificado", _URL_STF_SUMULAS),
    },
    # Temas Repetitivos Adicionais STJ
    {
        "id": "TEMA-1189-STJ",
        "tipo": "TEMA_REPETITIVO_STJ",
        "categoria": "TEMAS_REPETITIVOS",
        "tribunal": "STJ",
        "referencia": "Tema Repetitivo 1189/STJ",
        "titulo": "Vedação de Pena de Multa Isolada na Violência Doméstica (Art. 17 da Lei Maria da Penha)",
        "texto_literal": "A vedação constante do art. 17 da Lei n. 11.340/2006 (Lei Maria da Penha) obsta a imposição, nos casos de violência doméstica e familiar contra a mulher, de pena de multa isoladamente, ainda que prevista de forma autônoma no preceito secundário do tipo penal imputado.",
        "materia": "Pena de Multa e Prevenção Geral (Art. 17 LMP)",
        "fonte_oficial": "STJ, Terceira Seção, REsp 2.049.327/RJ, Rel. Min. Sebastião Reis Júnior, j. 14/06/2023, DJe 16/06/2023",
        "natureza": "LITERAL",
        "verificacao": _verif("verificado", _url_tema_stj(1189)),
    },
    {
        "id": "TEMA-1249-STJ",
        "tipo": "TEMA_REPETITIVO_STJ",
        "categoria": "TEMAS_REPETITIVOS",
        "tribunal": "STJ",
        "referencia": "Tema Repetitivo 1249/STJ",
        "titulo": "Natureza Inibitória e Vigência por Prazo Indeterminado das Medidas Protetivas de Urgência",
        "texto_literal": "I - As medidas protetivas de urgência (MPUs) têm natureza jurídica de tutela inibitória e sua vigência não se subordina à existência (atual ou vindoura) de boletim de ocorrência, inquérito policial, processo cível ou criminal. II - A duração das MPUs vincula-se à persistência da situação de risco à mulher, razão pela qual devem ser fixadas por prazo temporalmente indeterminado; III - Eventual reconhecimento de causa de extinção de punibilidade, arquivamento do inquérito policial ou absolvição do acusado não origina, necessariamente, a extinção da medida protetiva de urgência, máxime pela possibilidade de persistência da situação de risco ensejadora da concessão da medida. IV - Não se submetem a prazo obrigatório de revisão periódica, mas devem ser reavaliadas pelo magistrado, de ofício ou a pedido do interessado, quando constatado concretamente o esvaziamento da situação de risco. A revogação deve sempre ser precedida de contraditório, com as oitivas da vítima e do suposto agressor. Em caso de extinção da medida, a ofendida deve ser comunicada, nos termos do art. 21 da Lei n. 11.340/2006.",
        "materia": "Medidas Protetivas de Urgência (Arts. 19 e 22 da Lei 11.340/2006)",
        "fonte_oficial": "STJ, Terceira Seção, REsp 2.070.717/MG (e 2.070.857/MG, 2.070.863/MG, 2.071.109/MG), Rel. Min. Joel Ilan Paciornik, Rel. p/ acórdão Min. Rogerio Schietti Cruz, j. 13/11/2024, DJe 25/03/2025 (Info 836)",
        "natureza": "LITERAL",
        "verificacao": _verif("verificado", _url_tema_stj(1249)),
    },
    {
        "id": "TEMA-1186-STJ",
        "tipo": "TEMA_REPETITIVO_STJ",
        "categoria": "TEMAS_REPETITIVOS",
        "tribunal": "STJ",
        "referencia": "Tema Repetitivo 1186/STJ",
        "titulo": "Prevalência da Lei Maria da Penha sobre o ECA em Razão do Gênero Feminino da Ofendida",
        "texto_literal": "1. A condição de gênero feminino é suficiente para atrair a aplicabilidade da Lei Maria da Penha em casos de violência doméstica e familiar, prevalecendo sobre a questão etária. 2. A Lei Maria da Penha prevalece quando suas disposições conflitarem com as de estatutos específicos, como o da Criança e do Adolescente.",
        "materia": "Competência Especializada e Conflito Aparente de Leis (LMP x ECA)",
        "fonte_oficial": "STJ, Terceira Seção, REsp 2.015.598/PA, Rel. Min. Ribeiro Dantas, j. 06/02/2025, DJEN 13/02/2025",
        "natureza": "LITERAL",
        "verificacao": _verif("verificado", _url_tema_stj(1186)),
    },
    {
        "id": "TEMA-177-STJ",
        "tipo": "TEMA_REPETITIVO_STJ",
        "categoria": "TEMAS_REPETITIVOS",
        "tribunal": "STJ",
        "referencia": "Tema Repetitivo 177/STJ",
        "titulo": "Ação Penal Pública Incondicionada nas Lesões Corporais contra a Mulher (Tese Revisada)",
        "texto_literal": "A ação penal nos crimes de lesão corporal leve cometidos em detrimento da mulher, no âmbito doméstico e familiar, é pública incondicionada.",
        "materia": "Ação Penal Pública Incondicionada (Art. 129, § 9º/§ 13, do CP)",
        "fonte_oficial": "STJ, Terceira Seção, Pet 11.805/DF (revisão do Tema 177), Rel. Min. Rogerio Schietti Cruz, j. 10/05/2017, DJe 17/05/2017 (Súmula 542/STJ; Tema 713/STF)",
        "natureza": "LITERAL",
        "verificacao": _verif("verificado", _url_tema_stj(177)),
    },
    {
        "id": "TEMA-1167-STJ",
        "tipo": "TEMA_REPETITIVO_STJ",
        "categoria": "TEMAS_REPETITIVOS",
        "tribunal": "STJ",
        "referencia": "Tema Repetitivo 1167/STJ",
        "titulo": "Audiência do Art. 16 da Lei Maria da Penha (Iniciativa da Vítima para Retratação)",
        "texto_literal": "A audiência prevista no art. 16 da Lei 11.340/2006 tem por objetivo confirmar a retratação, não a representação, e não pode ser designada de ofício pelo juiz. Sua realização somente é necessária caso haja manifestação do desejo da vítima de se retratar trazida aos autos antes do recebimento da denúncia.",
        "materia": "Art. 16 da Lei Maria da Penha e Retratação",
        "fonte_oficial": "STJ, Terceira Seção, REsp 1.964.293/MG e REsp 1.977.547/MG, Rel. Min. Reynaldo Soares da Fonseca, j. 08/03/2023, DJe 29/03/2023",
        "natureza": "LITERAL",
        "verificacao": _verif("verificado", _URL_STJ_TESES_41),
    },
    {
        "id": "TEMA-1333-STJ",
        "tipo": "TEMA_REPETITIVO_STJ",
        "categoria": "TEMAS_REPETITIVOS",
        "tribunal": "STJ",
        "referencia": "Tema Repetitivo 1333/STJ",
        "titulo": "Agravante do Art. 61, II, 'f', do CP nas Contravenções Penais (Exceto Vias de Fato com § 2º)",
        "texto_literal": '1 - A agravante prevista no art. 61, II, "f", do Código Penal é aplicável às contravenções penais praticadas no contexto de violência doméstica contra a mulher, salvo se houver previsão diversa pela Lei das Contravenções Penais, por força do que dispõem seu art. 1º e o art. 12 do Código Penal. 2 - Não é possível tal aplicação para a contravenção penal de vias de fato, prevista no art. 21 da Lei das Contravenções Penais, na hipótese de incidência de seu §2º, incluído pela Lei n. 14.994/2024, por força dos princípios da especialidade e da proibição de bis in idem.',
        "materia": "Dosimetria da Pena e Contravenções Penais (Vias de Fato)",
        "fonte_oficial": "STJ, Terceira Seção, REsp 2.186.684/MG (e 2.185.716/MG, 2.184.869/MG, 2.185.960/MG), Rel. Min. Carlos Pires Brandão, j. 07/08/2025, DJe 18/08/2025",
        "natureza": "LITERAL",
        "verificacao": _verif("verificado", _url_tema_stj(1333)),
    },
    # Julgados de Controle Concentrado e Repercussão Geral do STF
    {
        "id": "ADC-19-STF",
        "tipo": "CONTROLE_CONCENTRADO_STF",
        "categoria": "STF_VINCULANTE",
        "tribunal": "STF",
        "referencia": "ADC 19/STF",
        "titulo": "Constitucionalidade dos Arts. 1º, 33 e 41 da Lei Maria da Penha e Afastamento da Lei 9.099/1995",
        "texto_literal": "O Supremo Tribunal Federal declarou a constitucionalidade dos artigos 1º, 33 e 41 da Lei nº 11.340/2006, assentando que o tratamento diferenciado dispensado à mulher visa compensar a vulnerabilidade histórica e estrutural de gênero, sendo constitucional a vedação absoluta à aplicação dos institutos despenalizadores da Lei nº 9.099/1995.",
        "materia": "Constitucionalidade da Lei Maria da Penha",
        "fonte_oficial": "STF, Plenário, ADC 19/DF, Rel. Min. Marco Aurélio, j. 09/02/2012 (DJe 29/04/2014)",
        "natureza": "RESUMO",
        "verificacao": _verif("verificado", _URL_STF_ADC_19),
    },
    {
        "id": "ADI-4424-STF",
        "tipo": "CONTROLE_CONCENTRADO_STF",
        "categoria": "STF_VINCULANTE",
        "tribunal": "STF",
        "referencia": "ADI 4424/STF",
        "titulo": "Natureza Pública Incondicionada da Ação Penal nas Lesões Corporais contra a Mulher",
        "texto_literal": "A ação penal relativa a lesão corporal resultante de violência doméstica contra a mulher (seja leve, grave ou culposa) é pública incondicionada, dando-se interpretação conforme aos artigos 12, I, e 16 da Lei nº 11.340/2006 para afastar a necessidade de representação da vítima ou designação de audiência de retratação.",
        "materia": "Ação Penal Pública Incondicionada",
        "fonte_oficial": "STF, Plenário, ADI 4424/DF, Rel. Min. Marco Aurélio, j. 09/02/2012 (DJe 01/08/2014)",
        "natureza": "RESUMO",
        "verificacao": _verif("verificado", _URL_STF_ADI_4424),
    },
    {
        "id": "ADPF-779-STF",
        "tipo": "CONTROLE_CONCENTRADO_STF",
        "categoria": "STF_VINCULANTE",
        "tribunal": "STF",
        "referencia": "ADPF 779/STF",
        "titulo": "Inconstitucionalidade e Nulidade Absoluta da Tese da Legítima Defesa da Honra",
        "texto_literal": (
            "O STF declarou inconstitucional a tese da legítima defesa da honra, por contrariar a dignidade da pessoa humana, "
            "a proteção à vida e a igualdade de gênero, e a excluiu do âmbito do instituto da legítima defesa. Nos crimes de "
            "feminicídio ou de agressão contra mulheres, a defesa, a acusação, a autoridade policial e o juízo não podem "
            "utilizá-la, direta ou indiretamente, nas fases pré-processual ou processual penal nem no julgamento do Tribunal "
            "do Júri, sob pena de nulidade do ato e do julgamento."
        ),
        "materia": "Excludente Inconstitucional e Direitos Fundamentais",
        "fonte_oficial": "STF, Plenário, ADPF 779/DF, Rel. Min. Dias Toffoli, mérito julgado em 01/08/2023, por unanimidade",
        "observacao": "A ADPF 779 veda a legítima defesa da honra em qualquer fase, inclusive de forma indireta. Ciúme, suspeita de traição, término do relacionamento ou conduta da vítima não justificam a agressão nem atenuam sua reprovação. Na amostra do 1º grau do TJRN (2024–2026), 45 sentenças abordaram agressões recíprocas ou legítima defesa comum. O art. 25 do CP exige prova de seus requisitos, e a versão isolada do réu não basta.",
        "natureza": "RESUMO",
        "verificacao": _verif("verificado", _URL_STF_ADPF_779),
    },
    # Jurisprudência em Teses do STJ — Edição nº 41 (revisada em 16/12/2022, literais verificados)
    # Notas: Tese 4 = Súmula 600/STJ; Tese 5 superada (AgRg na MPUMP 6/DF); Tese 9 superada (Lei 13.641/2018);
    # Tese 11 = Súmula 542/STJ; Tese 14 = Súmula 536/STJ; Tese 15 = Súmula 588/STJ; Tese 17 = Tema 1167/STJ.
    {
        "id": "TESE-STJ-ED41-01",
        "tipo": "TESE_JURISPRUDENCIA_STJ",
        "categoria": "TESES_STJ",
        "tribunal": "STJ",
        "referencia": "STJ - Jurisprudência em Teses - Edição 41 - Tese 1",
        "titulo": "Objeto de Proteção da Lei Maria da Penha (Âmbito Doméstico, Familiar ou Afetivo)",
        "texto_literal": "A Lei n. 11.340/2006, denominada Lei Maria da Penha, objetiva proteger a mulher da violência doméstica e familiar que lhe cause morte, lesão, sofrimento físico, sexual ou psicológico, e dano moral ou patrimonial, desde que o crime seja cometido no âmbito da unidade doméstica, da família ou em qualquer relação íntima de afeto.",
        "materia": "Âmbito de Aplicação da Lei Maria da Penha",
        "fonte_oficial": "STJ, Jurisprudência em Teses, Edição n. 41 (Violência doméstica e familiar contra mulher), tese 1, revisada em 16/12/2022",
        "natureza": "LITERAL",
        "verificacao": _verif("verificado", _URL_STJ_TESES_41),
    },
    {
        "id": "TESE-STJ-ED41-02",
        "tipo": "TESE_JURISPRUDENCIA_STJ",
        "categoria": "TESES_STJ",
        "tribunal": "STJ",
        "referencia": "STJ - Jurisprudência em Teses - Edição 41 - Tese 2",
        "titulo": "Uniões Homoafetivas como Entidade Familiar na Lei Maria da Penha",
        "texto_literal": "A Lei Maria da Penha atribuiu às uniões homoafetivas o caráter de entidade familiar, ao prever, no seu artigo 5º, parágrafo único, que as relações pessoais mencionadas naquele dispositivo independem de orientação sexual.",
        "materia": "Entidade Familiar e Uniões Homoafetivas",
        "fonte_oficial": "STJ, Jurisprudência em Teses, Edição n. 41 (Violência doméstica e familiar contra mulher), tese 2, revisada em 16/12/2022",
        "natureza": "LITERAL",
        "verificacao": _verif("verificado", _URL_STJ_TESES_41),
    },
    {
        "id": "TESE-STJ-ED41-03",
        "tipo": "TESE_JURISPRUDENCIA_STJ",
        "categoria": "TESES_STJ",
        "tribunal": "STJ",
        "referencia": "STJ - Jurisprudência em Teses - Edição 41 - Tese 3",
        "titulo": "Sujeitos Passivo (Mulher) e Ativo (Homem ou Mulher) na Violência Doméstica",
        "texto_literal": "O sujeito passivo da violência doméstica objeto da Lei Maria da Penha é a mulher, já o sujeito ativo pode ser tanto o homem quanto a mulher, desde que fique caracterizado o vínculo de relação doméstica, familiar ou de afetividade, além da convivência, com ou sem coabitação.",
        "materia": "Sujeitos Ativo e Passivo da Violência Doméstica",
        "fonte_oficial": "STJ, Jurisprudência em Teses, Edição n. 41 (Violência doméstica e familiar contra mulher), tese 3, revisada em 16/12/2022",
        "natureza": "LITERAL",
        "verificacao": _verif("verificado", _URL_STJ_TESES_41),
    },
    {
        "id": "TESE-STJ-ED41-06",
        "tipo": "TESE_JURISPRUDENCIA_STJ",
        "categoria": "TESES_STJ",
        "tribunal": "STJ",
        "referencia": "STJ - Jurisprudência em Teses - Edição 41 - Tese 6",
        "titulo": "Presunção de Vulnerabilidade, Hipossuficiência ou Fragilidade da Mulher",
        "texto_literal": "A vulnerabilidade, hipossuficiência ou fragilidade da mulher têm-se como presumidas nas circunstâncias descritas na Lei n. 11.340/2006.",
        "materia": "Vulnerabilidade Presumida da Mulher",
        "fonte_oficial": "STJ, Jurisprudência em Teses, Edição n. 41 (Violência doméstica e familiar contra mulher), tese 6, revisada em 16/12/2022",
        "natureza": "LITERAL",
        "verificacao": _verif("verificado", _URL_STJ_TESES_41),
    },
    {
        "id": "TESE-STJ-ED41-07",
        "tipo": "TESE_JURISPRUDENCIA_STJ",
        "categoria": "TESES_STJ",
        "tribunal": "STJ",
        "referencia": "STJ - Jurisprudência em Teses - Edição 41 - Tese 7",
        "titulo": "Agressão de Namorado ou Ex-Namorado Decorrente do Relacionamento",
        "texto_literal": "A agressão do namorado contra a namorada, mesmo cessado o relacionamento, mas que ocorra em decorrência dele, está inserida na hipótese do art. 5º, III, da Lei n. 11.340/2006, caracterizando a violência doméstica.",
        "materia": "Vínculo Afetivo Anterior e Ex-Namoro",
        "fonte_oficial": "STJ, Jurisprudência em Teses, Edição n. 41 (Violência doméstica e familiar contra mulher), tese 7, revisada em 16/12/2022",
        "natureza": "LITERAL",
        "verificacao": _verif("verificado", _URL_STJ_TESES_41),
    },
    {
        "id": "TESE-STJ-ED41-08",
        "tipo": "TESE_JURISPRUDENCIA_STJ",
        "categoria": "TESES_STJ",
        "tribunal": "STJ",
        "referencia": "STJ - Jurisprudência em Teses - Edição 41 - Tese 8",
        "titulo": "Competência Cumulativa dos Juizados de Violência Doméstica (Julgamento e Execução)",
        "texto_literal": "Os Juizados de Violência Doméstica e Familiar contra a Mulher têm competência cumulativa para o julgamento e a execução das causas decorrentes da prática de violência doméstica e familiar contra a mulher.",
        "materia": "Competência dos Juizados Especializados",
        "fonte_oficial": "STJ, Jurisprudência em Teses, Edição n. 41 (Violência doméstica e familiar contra mulher), tese 8, revisada em 16/12/2022",
        "natureza": "LITERAL",
        "verificacao": _verif("verificado", _URL_STJ_TESES_41),
    },
    {
        "id": "TESE-STJ-ED41-10",
        "tipo": "TESE_JURISPRUDENCIA_STJ",
        "categoria": "TESES_STJ",
        "tribunal": "STJ",
        "referencia": "STJ - Jurisprudência em Teses - Edição 41 - Tese 10",
        "titulo": "Inaplicabilidade da Insignificância e da Bagatela Imprópria na Violência Doméstica",
        "texto_literal": "Não é possível a aplicação dos princípios da insignificância e da bagatela imprópria nos delitos praticados com violência ou grave ameaça no âmbito das relações domésticas e familiares.",
        "materia": "Tipicidade Material e Bagatela",
        "fonte_oficial": "STJ, Jurisprudência em Teses, Edição n. 41 (Violência doméstica e familiar contra mulher), tese 10, revisada em 16/12/2022",
        "natureza": "LITERAL",
        "verificacao": _verif("verificado", _URL_STJ_TESES_41),
    },
    {
        "id": "TESE-STJ-ED41-12",
        "tipo": "TESE_JURISPRUDENCIA_STJ",
        "categoria": "TESES_STJ",
        "tribunal": "STJ",
        "referencia": "STJ - Jurisprudência em Teses - Edição 41 - Tese 12",
        "titulo": "Cabimento de Prisão Preventiva para Garantir Execução de Medidas Protetivas",
        "texto_literal": "É cabível a decretação de prisão preventiva para garantir a execução de medidas de urgência nas hipóteses em que o delito envolver violência doméstica e familiar contra a mulher.",
        "materia": "Prisão Preventiva e Medidas Protetivas",
        "fonte_oficial": "STJ, Jurisprudência em Teses, Edição n. 41 (Violência doméstica e familiar contra mulher), tese 12, revisada em 16/12/2022",
        "natureza": "LITERAL",
        "verificacao": _verif("verificado", _URL_STJ_TESES_41),
    },
    {
        "id": "TESE-STJ-ED41-13",
        "tipo": "TESE_JURISPRUDENCIA_STJ",
        "categoria": "TESES_STJ",
        "tribunal": "STJ",
        "referencia": "STJ - Jurisprudência em Teses - Edição 41 - Tese 13",
        "titulo": "Especial Relevância Probatória da Palavra da Vítima na Violência Doméstica",
        "texto_literal": "Nos crimes praticados no âmbito doméstico e familiar, a palavra da vítima tem especial relevância para fundamentar o recebimento da denúncia ou a condenação, pois normalmente são cometidos sem a presença de testemunhas.",
        "materia": "Valoração Probatória e Depoimento da Vítima",
        "fonte_oficial": "STJ, Jurisprudência em Teses, Edição n. 41 (Violência doméstica e familiar contra mulher), tese 13, revisada em 16/12/2022",
        "observacao": "Amostra do 1º grau do TJRN (2024–2026): em 161 sentenças, a clandestinidade do fato confere relevo ao relato da vítima quando firme, coerente e corroborado por elementos como laudo, prontuário, depoimentos policiais ou mídias. O HC 615.661/MS (STJ, 6ª Turma, j. 24/11/2020) foi citado em 9 sentenças da amostra e aguarda conferência da ementa oficial no SCON/STJ para eventual inclusão autônoma.",
        "natureza": "LITERAL",
        "verificacao": _verif("verificado", _URL_STJ_TESES_41),
    },
    {
        "id": "TESE-STJ-ED41-16",
        "tipo": "TESE_JURISPRUDENCIA_STJ",
        "categoria": "TESES_STJ",
        "tribunal": "STJ",
        "referencia": "STJ - Jurisprudência em Teses - Edição 41 - Tese 16",
        "titulo": "Inadequação do Habeas Corpus para Revogar Medidas Protetivas sem Restrição de Locomoção",
        "texto_literal": "O habeas corpus não constitui meio idôneo para se pleitear a revogação de medidas protetivas previstas no art. 22 da Lei n. 11.340/2006 que não implicam constrangimento ao direito de ir e vir do paciente.",
        "materia": "Medidas Protetivas e Habeas Corpus",
        "fonte_oficial": "STJ, Jurisprudência em Teses, Edição n. 41 (Violência doméstica e familiar contra mulher), tese 16, revisada em 16/12/2022",
        "natureza": "LITERAL",
        "verificacao": _verif("verificado", _URL_STJ_TESES_41),
    },
    # Precedentes Verificados do STJ e do STF (Anexo B.3)
    {
        "id": "STJ-AGRG-MPUMP-6-DF",
        "tipo": "PRECEDENTE_STJ",
        "categoria": "TESES_STJ",
        "tribunal": "STJ",
        "referencia": "AgRg na MPUMP 6/DF",
        "titulo": "Presunção de Hipossuficiência e Vulnerabilidade da Mulher (Superação da Antiga Tese 5 da Ed. 41)",
        "texto_literal": "Hipossuficiência e vulnerabilidade da mulher são presumidas no âmbito da Lei Maria da Penha, sendo desnecessário demonstrar situação concreta de subjugação para sua aplicação (superou a antiga tese 5 da Edição 41).",
        "materia": "Vulnerabilidade Presumida da Mulher",
        "fonte_oficial": "STJ, Corte Especial, AgRg na MPUMP 6/DF, Rel. Min. Nancy Andrighi, j. 18/05/2022, DJe 20/05/2022",
        "natureza": "RESUMO",
        "verificacao": _verif("verificado", _URL_STJ_TESES_41),
    },
    {
        "id": "STJ-ARESP-2763567-RN",
        "tipo": "PRECEDENTE_STJ",
        "categoria": "TESES_STJ",
        "tribunal": "STJ",
        "referencia": "AREsp 2.763.567/RN",
        "titulo": "Suficiência da Condição Objetiva de Mulher em Violência Doméstica no Art. 129, § 13, do CP",
        "texto_literal": "À luz do hodierno microssistema de proteção às mulheres, considera-se suficiente a mera condição - objetiva - da vítima de violência doméstica e/ou familiar do sexo feminino (cuja presunção de vulnerabilidade presume-se, objetivamente, por força da lei) para atrair a proteção e reação Estatal (especial e profícua) positivada no art. 129, § 13, do Estatuto Repressor, de modo a prescindir a análise da motivação específica da conduta do agressor.",
        "materia": "Lesão Corporal contra a Mulher (Art. 129, § 13, CP) e Vulnerabilidade Objetiva",
        "fonte_oficial": "STJ, 6ª Turma, AREsp 2.763.567/RN, Rel. Min. Otávio de Almeida Toledo (Des. conv. TJSP), j. 03/09/2025, DJEN 08/09/2025",
        "natureza": "LITERAL",
        "verificacao": _verif("verificado", URL_BANCO_CNJ_GENERO),
    },
    {
        "id": "STJ-AGRG-RESP-2080317-GO",
        "tipo": "PRECEDENTE_STJ",
        "categoria": "TESES_STJ",
        "tribunal": "STJ",
        "referencia": "AgRg no REsp 2.080.317/GO",
        "titulo": "Violência de Irmão contra Irmã e Competência Especializada da Lei Maria da Penha",
        "texto_literal": "Na lesão praticada por irmão contra irmã no âmbito familiar, a vulnerabilidade da mulher é presumida, sendo desnecessária a comprovação de motivação específica de gênero para atrair a Lei Maria da Penha e a competência do juizado especializado.",
        "materia": "Competência Especializada e Violência Familiar (Irmão contra Irmã)",
        "fonte_oficial": "STJ, 5ª Turma, AgRg no REsp 2.080.317/GO, Rel. Min. Joel Ilan Paciornik, j. 04/03/2024, DJe 06/03/2024 (Info 803)",
        "natureza": "RESUMO",
        "verificacao": _verif("verificado", _URL_PCSC_2026),
    },
    {
        "id": "STJ-APN-1079-DF",
        "tipo": "PRECEDENTE_STJ",
        "categoria": "TESES_STJ",
        "tribunal": "STJ",
        "referencia": "APn 1.079/DF",
        "titulo": "Palavra da Vítima Corroborada, Vedação a Estereótipos de Gênero e Dano Moral In Re Ipsa",
        "texto_literal": "A palavra da vítima corroborada por perícia e testemunhas tem relevante valor probatório; a tese defensiva de autolesão ou interesse patrimonial desacompanhada de prova reforça estereótipos de gênero; cabível o dano moral mínimo in re ipsa.",
        "materia": "Valoração Probatória, Protocolo de Gênero e Dano Moral",
        "fonte_oficial": "STJ, Corte Especial, APn 1.079/DF, Rel. Min. Antonio Carlos Ferreira, j. 15/10/2025, DJEN 23/10/2025 (Info 870)",
        "natureza": "RESUMO",
        "verificacao": _verif("verificado", _URL_PCSC_2026),
    },
    {
        "id": "STJ-AGRG-ARESP-2682075-SE",
        "tipo": "PRECEDENTE_STJ",
        "categoria": "TESES_STJ",
        "tribunal": "STJ",
        "referencia": "AgRg no AREsp 2.682.075/SE",
        "titulo": "Especial Relevância da Palavra da Vítima, Laudo Compatível e Controle de Convencionalidade",
        "texto_literal": "A palavra da vítima assume especial relevância pela clandestinidade dos delitos domésticos; laudo pericial compatível reforça sua credibilidade; o in dubio pro reo exige dúvida razoável, impondo-se o controle de convencionalidade e a observância do Protocolo para Julgamento com Perspectiva de Gênero.",
        "materia": "Valoração Probatória e Controle de Convencionalidade",
        "fonte_oficial": "STJ, 5ª Turma, AgRg no AREsp 2.682.075/SE, Rel. Min. Daniela Teixeira, j. 19/02/2025, DJEN 24/02/2025",
        "natureza": "RESUMO",
        "verificacao": _verif("verificado", URL_BANCO_CNJ_GENERO),
    },
    {
        "id": "STJ-AGRG-ARESP-3103801-PB",
        "tipo": "PRECEDENTE_STJ",
        "categoria": "TESES_STJ",
        "tribunal": "STJ",
        "referencia": "AgRg no AREsp 3.103.801/PB",
        "titulo": "Valoração da Retratação da Vítima em Juízo sob o Protocolo de Perspectiva de Gênero",
        "texto_literal": "Declarações da vítima na fase policial corroboradas por testemunhas e laudo pericial bastam à condenação; eventual retratação da ofendida em juízo deve ser avaliada no contexto concreto (reaproximação, filhos, gravidez, medida protetiva anterior) à luz do Protocolo para Julgamento com Perspectiva de Gênero.",
        "materia": "Prova Oral, Retratação da Vítima e Protocolo de Gênero",
        "fonte_oficial": "STJ, 6ª Turma, AgRg no AREsp 3.103.801/PB, Rel. Min. Rogerio Schietti Cruz, j. 15/09/2026, DJEN 22/09/2026",
        "natureza": "RESUMO",
        "verificacao": _verif("verificado", URL_BANCO_CNJ_GENERO),
    },
    {
        "id": "STJ-INFO-846-CONSUNCAO",
        "tipo": "PRECEDENTE_STJ",
        "categoria": "TESES_STJ",
        "tribunal": "STJ",
        "referencia": "STJ - Informativo 846 (Violação de Domicílio e Lesão Corporal)",
        "titulo": "Inaplicabilidade da Consunção entre Violação de Domicílio e Lesão Corporal",
        "texto_literal": "Não há consunção entre o crime de violação de domicílio e o de lesão corporal praticados no contexto de violência doméstica contra a mulher, por tutelarem objetividades jurídicas distintas.",
        "materia": "Violação de Domicílio (Art. 150 CP), Concurso Material e Consunção",
        "fonte_oficial": "STJ, 6ª Turma, processo em segredo de justiça, Rel. Min. Otávio de Almeida Toledo, j. 12/03/2025 (Info 846)",
        "natureza": "RESUMO",
        "verificacao": _verif("verificado", _URL_PCSC_2026),
    },
    {
        "id": "STJ-CE-AMEACA-COLERA",
        "tipo": "PRECEDENTE_STJ",
        "categoria": "TESES_STJ",
        "tribunal": "STJ",
        "referencia": "STJ - Corte Especial - Info Extra 21 (Ameaça em Contexto de Cólera)",
        "titulo": "Ameaça Proferida em Contexto de Cólera ou Ira Não Afasta a Tipicidade",
        "texto_literal": "A ameaça proferida em contexto de cólera ou ira no âmbito da violência doméstica contra a mulher não afasta a tipicidade do delito do art. 147 do Código Penal.",
        "materia": "Crime de Ameaça (Art. 147 CP) e Estado de Ânimo Exaltado",
        "fonte_oficial": "STJ, Corte Especial, processo em segredo de justiça, Rel. Min. Antonio Carlos Ferreira, j. 10/06/2024 (Info Extra 21)",
        "natureza": "RESUMO",
        "verificacao": _verif("verificado", _URL_PCSC_2026),
    },
    {
        "id": "STJ-INFO-824-RELACAO-BREVE",
        "tipo": "PRECEDENTE_STJ",
        "categoria": "TESES_STJ",
        "tribunal": "STJ",
        "referencia": "STJ - Informativo 824 (Relação de Afeto Breve ou Efêmera)",
        "titulo": "Relação de Afeto Breve ou Efêmera Não Afasta a Lei Maria da Penha",
        "texto_literal": "A circunstância de a relação íntima de afeto ter sido breve ou efêmera não afasta a incidência da Lei Maria da Penha.",
        "materia": "Âmbito de Aplicação e Relação Íntima de Afeto",
        "fonte_oficial": "STJ, 6ª Turma, processo em segredo de justiça, Rel. Min. Antonio Saldanha Palheiro, j. 12/08/2024 (Info 824)",
        "natureza": "RESUMO",
        "verificacao": _verif("verificado", _URL_PCSC_2026),
    },
    {
        "id": "STJ-AGRG-ARESP-2330912-DF",
        "tipo": "PRECEDENTE_STJ",
        "categoria": "TESES_STJ",
        "tribunal": "STJ",
        "referencia": "AgRg no AREsp 2.330.912/DF",
        "titulo": "Aproximação com Consentimento Livre da Vítima no Art. 24-A (Ponderação Judicial)",
        "texto_literal": "A aproximação do agressor com o consentimento livre e espontâneo da vítima pode tornar atípica a conduta do art. 24-A da Lei Maria da Penha, exigindo ponderação concreta pelo juízo quanto à ausência de coação ou intimidação.",
        "materia": "Art. 24-A da LMP e Consentimento da Vítima",
        "fonte_oficial": "STJ, 5ª Turma, AgRg no AREsp 2.330.912/DF, Rel. Min. Ribeiro Dantas, j. 22/08/2023 (Info 785)",
        "natureza": "RESUMO",
        "verificacao": _verif("verificado", _URL_PCSC_2026),
    },
    {
        "id": "STJ-AGRG-HC-860073-SC",
        "tipo": "PRECEDENTE_STJ",
        "categoria": "TESES_STJ",
        "tribunal": "STJ",
        "referencia": "AgRg no HC 860.073/SC",
        "titulo": "Ineficácia do Consentimento da Vítima Quando Há Intimidação no Art. 24-A",
        "texto_literal": "O consentimento da ofendida não afasta a tipicidade do crime de descumprimento de medida protetiva de urgência (art. 24-A da Lei Maria da Penha) quando há intimidação da vítima pelo agente.",
        "materia": "Art. 24-A da LMP e Intimidação da Vítima",
        "fonte_oficial": "STJ, 5ª Turma, AgRg no HC 860.073/SC, Rel. Min. Ribeiro Dantas, j. 13/11/2024 (Info Extra 24)",
        "natureza": "RESUMO",
        "verificacao": _verif("verificado", _URL_PCSC_2026),
    },
    {
        "id": "STJ-HC-437535-SP",
        "tipo": "PRECEDENTE_STJ",
        "categoria": "TESES_STJ",
        "tribunal": "STJ",
        "referencia": "HC 437.535/SP",
        "titulo": "Contravenção Penal de Vias de Fato Não Autoriza Prisão Preventiva",
        "texto_literal": "A prática da contravenção penal de vias de fato no contexto de violência doméstica contra a mulher não justifica a decretação de prisão preventiva.",
        "materia": "Prisão Preventiva e Contravenção Penal (Vias de Fato)",
        "fonte_oficial": "STJ, HC 437.535/SP, Rel. p/ acórdão Min. Rogerio Schietti Cruz, j. 26/06/2018 (Info 632)",
        "natureza": "RESUMO",
        "verificacao": _verif("verificado", _URL_PCSC_2026),
    },
    {
        "id": "STJ-CC-190666-MG",
        "tipo": "PRECEDENTE_STJ",
        "categoria": "TESES_STJ",
        "tribunal": "STJ",
        "referencia": "CC 190.666/MG",
        "titulo": "Competência do Domicílio da Vítima para Medida Protetiva sem Alterar a da Ação Penal",
        "texto_literal": "O juízo do domicílio da vítima é competente para apreciar o pedido de medida protetiva de urgência, ainda que o fato tenha ocorrido em outro local, sem alterar a competência para a futura ação penal.",
        "materia": "Competência Territorial e Medidas Protetivas",
        "fonte_oficial": "STJ, 3ª Seção, CC 190.666/MG, Rel. Min. Laurita Vaz, j. 08/02/2023 (Info 764)",
        "natureza": "RESUMO",
        "verificacao": _verif("verificado", _URL_PCSC_2026),
    },
    {
        "id": "STJ-RESP-1977124-SP",
        "tipo": "PRECEDENTE_STJ",
        "categoria": "TESES_STJ",
        "tribunal": "STJ",
        "referencia": "REsp 1.977.124/SP",
        "titulo": "Aplicabilidade da Lei Maria da Penha às Mulheres Trans",
        "texto_literal": "A Lei Maria da Penha aplica-se às mulheres trans em situação de violência doméstica e familiar, afastado o critério exclusivamente biológico para definição da proteção legal.",
        "materia": "Identidade de Gênero e Tutela Protetiva da Mulher Trans",
        "fonte_oficial": "STJ, 6ª Turma, REsp 1.977.124/SP, Rel. Min. Rogerio Schietti Cruz, j. 05/04/2022, DJe 22/04/2022",
        "natureza": "RESUMO",
        "verificacao": _verif("verificado", _URL_PCSC_2026),
    },
    {
        "id": "STJ-INFO-860-MPU-RISCO",
        "tipo": "PRECEDENTE_STJ",
        "categoria": "TESES_STJ",
        "tribunal": "STJ",
        "referencia": "STJ - Informativo 860 (Manutenção de MPU e Persistência do Risco)",
        "titulo": "Manutenção da Medida Protetiva Depende da Persistência do Risco (Não de Novos Fatos)",
        "texto_literal": "A manutenção das medidas protetivas de urgência depende da persistência da situação de risco à mulher, e não da ocorrência de novos fatos delituosos; exigir da vítima nova prova inverte indevidamente o ônus probatório.",
        "materia": "Medidas Protetivas de Urgência e Persistência do Risco",
        "fonte_oficial": "STJ, 6ª Turma, processo em segredo de justiça, Rel. Min. Sebastião Reis Júnior, j. 13/08/2025 (Info 860)",
        "natureza": "RESUMO",
        "verificacao": _verif("verificado", _URL_PCSC_2026),
    },
    {
        "id": "STF-MI-7452-DF",
        "tipo": "PRECEDENTE_STF",
        "categoria": "STF_VINCULANTE",
        "tribunal": "STF",
        "referencia": "MI 7.452/DF",
        "titulo": "Aplicação Analógica da LMP a Casais Homoafetivos Masculinos e Mulheres Travestis/Transexuais",
        "texto_literal": "A Lei Maria da Penha aplica-se, por analogia, a casais homoafetivos do sexo masculino e a mulheres travestis ou transexuais nas relações intrafamiliares (observar o Enunciado 78 FONAVID quanto à competência).",
        "materia": "Âmbito de Aplicação da LMP e Tutela de Minorias",
        "fonte_oficial": "STF, Plenário, MI 7.452/DF, Rel. Min. Alexandre de Moraes, j. 24/02/2025 (Info 1167)",
        "natureza": "RESUMO",
        "verificacao": _verif("verificado", _URL_PCSC_2026),
    },
    {
        "id": "STF-ADI-6138-DF",
        "tipo": "CONTROLE_CONCENTRADO_STF",
        "categoria": "STF_VINCULANTE",
        "tribunal": "STF",
        "referencia": "ADI 6.138/DF",
        "titulo": "Constitucionalidade do Art. 12-C da LMP (Afastamento do Lar pela Autoridade Policial)",
        "texto_literal": "É constitucional o art. 12-C da Lei Maria da Penha, que autoriza o afastamento imediato do agressor do lar pela autoridade policial ou policial, de forma supletiva e excepcional, mediante comunicação ao juízo em 24 horas.",
        "materia": "Constitucionalidade do Art. 12-C da LMP e Afastamento do Lar",
        "fonte_oficial": "STF, Plenário, ADI 6.138/DF, Rel. Min. Alexandre de Moraes, j. 23/03/2022 (Info 1048)",
        "natureza": "RESUMO",
        "verificacao": _verif("verificado", _URL_PCSC_2026),
    },
    # Enunciados Oficiais FONAVID vigentes (atualizados até o XVII FONAVID — São Luís/MA, 10–14/11/2025)
    # Revogados (não cadastrados): 2, 5, 6, 8, 10, 12, 27, 28, 34, 41, 55.
    {
        "id": "ENUNCIADO-01-FONAVID",
        "tipo": "ENUNCIADO_FONAVID",
        "categoria": "FONAVID",
        "tribunal": "FONAVID",
        "referencia": "Enunciado 1 FONAVID",
        "titulo": "Período ou Término da Relação Afetiva Não Afasta Incidência da Lei Maria da Penha",
        "texto_literal": "Para incidência da Lei Maria da Penha, não importa o período de relacionamento entre a mulher em situação de violência e a pessoa autora de violências, nem o tempo decorrido desde o seu rompimento, bastando que reste comprovado que a violência decorreu da relação de afeto.",
        "materia": "Âmbito Temporal e Vínculo de Afeto",
        "fonte_oficial": "FONAVID — Enunciado 1 (atualizado até o XVII FONAVID)",
        "natureza": "LITERAL",
        "verificacao": _verif("verificado", _URL_FONAVID),
    },
    {
        "id": "ENUNCIADO-03-FONAVID",
        "tipo": "ENUNCIADO_FONAVID",
        "categoria": "FONAVID",
        "tribunal": "FONAVID",
        "referencia": "Enunciado 3 FONAVID",
        "titulo": "Competência Cível Restrita a Medidas Protetivas nos Juizados Especializados",
        "texto_literal": "A competência cível dos Juizados de Violência Doméstica e Familiar contra a Mulher é restrita às medidas protetivas de urgência previstas na Lei Maria da Penha, devendo as ações cíveis e as de Direito de Família ser processadas e julgadas pelas varas cíveis e de família, respectivamente.",
        "materia": "Competência dos Juizados Especializados",
        "fonte_oficial": "FONAVID — Enunciado 3 (atualizado até o XVII FONAVID)",
        "natureza": "LITERAL",
        "verificacao": _verif("verificado", _URL_FONAVID),
    },
    {
        "id": "ENUNCIADO-04-FONAVID",
        "tipo": "ENUNCIADO_FONAVID",
        "categoria": "FONAVID",
        "tribunal": "FONAVID",
        "referencia": "Enunciado 4 FONAVID",
        "titulo": "Audiência do Art. 16 da LMP Cabível Apenas com Manifestação Expressa de Retratação",
        "texto_literal": "A audiência prevista no artigo 16 da Lei 11.340/2006 é cabível apenas nos casos de ação penal pública condicionada à representação, desde que haja manifestação expressa de retratação da mulher em situação de violência.",
        "materia": "Art. 16 da Lei Maria da Penha e Retratação",
        "fonte_oficial": "FONAVID — Enunciado 4 (alterado no XVI FONAVID – Salvador/BA)",
        "natureza": "LITERAL",
        "verificacao": _verif("verificado", _URL_FONAVID),
    },
    {
        "id": "ENUNCIADO-07-FONAVID",
        "tipo": "ENUNCIADO_FONAVID",
        "categoria": "FONAVID",
        "tribunal": "FONAVID",
        "referencia": "Enunciado 7 FONAVID",
        "titulo": "Cabimento do Sursis Penal (Art. 77 do CP) na Violência Doméstica",
        "texto_literal": "Presentes os requisitos legais, é possível a aplicação do Sursis de que trata o artigo 77 do Código Penal aos crimes praticados no contexto de violência doméstica e familiar contra a mulher.",
        "materia": "Suspensão Condicional da Pena (Sursis — Art. 77 CP)",
        "fonte_oficial": "FONAVID — Enunciado 7 (alterado no XVI FONAVID – Salvador/BA)",
        "natureza": "LITERAL",
        "verificacao": _verif("verificado", _URL_FONAVID),
    },
    {
        "id": "ENUNCIADO-11-FONAVID",
        "tipo": "ENUNCIADO_FONAVID",
        "categoria": "FONAVID",
        "tribunal": "FONAVID",
        "referencia": "Enunciado 11 FONAVID",
        "titulo": "Multa Pecuniária para Eficácia das Medidas Protetivas sem Prejuízo do Art. 24-A",
        "texto_literal": "A multa pecuniária poderá ser fixada a fim de assegurar a eficácia das medidas protetivas de urgência, sem prejuízo da configuração do crime previsto no artigo 24-A da Lei 11.340/2006.",
        "materia": "Medidas Protetivas e Art. 24-A da LMP",
        "fonte_oficial": "FONAVID — Enunciado 11 (alterado no XVI FONAVID – Salvador/BA)",
        "natureza": "LITERAL",
        "verificacao": _verif("verificado", _URL_FONAVID),
    },
    {
        "id": "ENUNCIADO-19-FONAVID",
        "tipo": "ENUNCIADO_FONAVID",
        "categoria": "FONAVID",
        "tribunal": "FONAVID",
        "referencia": "Enunciado 19 FONAVID",
        "titulo": "Não Comparecimento à Audiência do Art. 16 Implica Prosseguimento do Feito",
        "texto_literal": "O não comparecimento da mulher em situação de violência à audiência prevista no artigo 16 da Lei 11.340/2006 tem como consequência o prosseguimento do feito.",
        "materia": "Art. 16 da Lei Maria da Penha e Prosseguimento da Ação",
        "fonte_oficial": "FONAVID — Enunciado 19 (atualizado até o XVII FONAVID)",
        "natureza": "LITERAL",
        "verificacao": _verif("verificado", _URL_FONAVID),
    },
    {
        "id": "ENUNCIADO-20-FONAVID",
        "tipo": "ENUNCIADO_FONAVID",
        "categoria": "FONAVID",
        "tribunal": "FONAVID",
        "referencia": "Enunciado 20 FONAVID",
        "titulo": "Boletim de Ocorrência como Representação da Ofendida",
        "texto_literal": "O registro do boletim de ocorrência pela mulher em situação de violência deve ser considerado representação, se não houver manifestação expressa desta em sentido contrário.",
        "materia": "Ação Penal e Representação",
        "fonte_oficial": "FONAVID — Enunciado 20 (alterado no XVI FONAVID – Salvador/BA)",
        "natureza": "LITERAL",
        "verificacao": _verif("verificado", _URL_FONAVID),
    },
    {
        "id": "ENUNCIADO-21-FONAVID",
        "tipo": "ENUNCIADO_FONAVID",
        "categoria": "FONAVID",
        "tribunal": "FONAVID",
        "referencia": "Enunciado 21 FONAVID",
        "titulo": "Competência Recursal dos Tribunais de Justiça Independentemente da Pena",
        "texto_literal": "A competência para apreciar os recursos contra as decisões proferidas pelos Juizados de Violência Doméstica contra a Mulher é dos Tribunais de Justiça, independentemente da pena.",
        "materia": "Competência Recursal",
        "fonte_oficial": "FONAVID — Enunciado 21 (atualizado até o XVII FONAVID)",
        "natureza": "LITERAL",
        "verificacao": _verif("verificado", _URL_FONAVID),
    },
    {
        "id": "ENUNCIADO-22-FONAVID",
        "tipo": "ENUNCIADO_FONAVID",
        "categoria": "FONAVID",
        "tribunal": "FONAVID",
        "referencia": "Enunciado 22 FONAVID",
        "titulo": "Prisão Preventiva na Violência Doméstica Independe de Prévia Manifestação do MP",
        "texto_literal": "A decretação da prisão preventiva, ainda que decorrente da conversão da prisão em flagrante, independe de prévia manifestação do Ministério Público.",
        "materia": "Prisão Preventiva e Medidas Cautelares",
        "fonte_oficial": "FONAVID — Enunciado 22 (atualizado até o XVII FONAVID)",
        "natureza": "LITERAL",
        "verificacao": _verif("verificado", _URL_FONAVID),
    },
    {
        "id": "ENUNCIADO-24-FONAVID",
        "tipo": "ENUNCIADO_FONAVID",
        "categoria": "FONAVID",
        "tribunal": "FONAVID",
        "referencia": "Enunciado 24 FONAVID",
        "titulo": "Competência Especializada Restrita às Violências Baseadas no Gênero",
        "texto_literal": "A competência do Juizado da Violência Doméstica e Familiar contra a Mulher restringe-se às violências baseadas no gênero, na forma dos artigos 5° e 7° da Lei Maria da Penha e do artigo 1° da Convenção de Belém do Pará, não sendo suficiente que a mulher em situação de violência seja do gênero feminino.",
        "materia": "Competência Material e Motivação de Gênero",
        "fonte_oficial": "FONAVID — Enunciado 24 (atualizado até o XVII FONAVID)",
        "natureza": "LITERAL",
        "verificacao": _verif("verificado", _URL_FONAVID),
    },
    {
        "id": "ENUNCIADO-25-FONAVID",
        "tipo": "ENUNCIADO_FONAVID",
        "categoria": "FONAVID",
        "tribunal": "FONAVID",
        "referencia": "Enunciado 25 FONAVID",
        "titulo": "Aplicação Universal do Protocolo de Julgamento com Perspectiva de Gênero",
        "texto_literal": "As normas de tutela de direitos humanos das mulheres e o Protocolo para Julgamento com Perspectiva de gênero não se restringem aos Juizados de Violência Doméstica e Familiar contra a Mulher.",
        "materia": "Protocolo de Perspectiva de Gênero e Abrangência",
        "fonte_oficial": "FONAVID — Enunciado 25 (alterado no XVI FONAVID – Salvador/BA)",
        "natureza": "LITERAL",
        "verificacao": _verif("verificado", _URL_FONAVID),
    },
    {
        "id": "ENUNCIADO-26-FONAVID",
        "tipo": "ENUNCIADO_FONAVID",
        "categoria": "FONAVID",
        "tribunal": "FONAVID",
        "referencia": "Enunciado 26 FONAVID",
        "titulo": "Medida Protetiva de Comparecimento a Atendimento Psicossocial e Pedagógico",
        "texto_literal": "A juíza ou o juiz poderá determinar medida protetiva de urgência que obrigue a pessoa autora de violências a comparecer a atendimento psicossocial e pedagógico, como prática de enfrentamento da violência doméstica e familiar contra as mulheres.",
        "materia": "Medidas Protetivas e Grupos Reflexivos",
        "fonte_oficial": "FONAVID — Enunciado 26 (alterado no XVI FONAVID – Salvador/BA)",
        "natureza": "LITERAL",
        "verificacao": _verif("verificado", _URL_FONAVID),
    },
    {
        "id": "ENUNCIADO-29-FONAVID",
        "tipo": "ENUNCIADO_FONAVID",
        "categoria": "FONAVID",
        "tribunal": "FONAVID",
        "referencia": "Enunciado 29 FONAVID",
        "titulo": "Prisão Cautelar para Assegurar Integridade Física e Psíquica da Ofendida",
        "texto_literal": "A prisão cautelar da pessoa autora de violências independentemente de concessão ou descumprimento de medida protetiva é possível, inclusive de ofício, a fim de assegurar a integridade física e/ou psicológica da ofendida.",
        "materia": "Prisão Cautelar e Proteção da Vítima",
        "fonte_oficial": "FONAVID — Enunciado 29 (atualizado até o XVII FONAVID)",
        "natureza": "LITERAL",
        "verificacao": _verif("verificado", _URL_FONAVID),
    },
    {
        "id": "ENUNCIADO-31-FONAVID",
        "tipo": "ENUNCIADO_FONAVID",
        "categoria": "FONAVID",
        "tribunal": "FONAVID",
        "referencia": "Enunciado 31 FONAVID",
        "titulo": "Aplicação de Medidas Protetivas nas Varas do Tribunal do Júri",
        "texto_literal": "As medidas protetivas de urgência, previstas na Lei Maria da Penha, são aplicáveis nas Varas do Tribunal do Júri.",
        "materia": "Medidas Protetivas no Tribunal do Júri",
        "fonte_oficial": "FONAVID — Enunciado 31 (alterado no XI FONAVID – São Paulo/SP)",
        "natureza": "LITERAL",
        "verificacao": _verif("verificado", _URL_FONAVID),
    },
    {
        "id": "ENUNCIADO-36-FONAVID",
        "tipo": "ENUNCIADO_FONAVID",
        "categoria": "FONAVID",
        "tribunal": "FONAVID",
        "referencia": "Enunciado 36 FONAVID",
        "titulo": "Mecanismo Compulsório de Controle Eletrônico para Garantia de Medidas Protetivas",
        "texto_literal": "Poderá ser utilizado mecanismo compulsório de controle eletrônico em desfavor da pessoa autora de violências para a garantia do cumprimento das medidas protetivas de urgência.",
        "materia": "Monitoramento Eletrônico e Efetividade Protetiva",
        "fonte_oficial": "FONAVID — Enunciado 36 (atualizado até o XVII FONAVID)",
        "natureza": "LITERAL",
        "verificacao": _verif("verificado", _URL_FONAVID),
    },
    {
        "id": "ENUNCIADO-38-FONAVID",
        "tipo": "ENUNCIADO_FONAVID",
        "categoria": "FONAVID",
        "tribunal": "FONAVID",
        "referencia": "Enunciado 38 FONAVID",
        "titulo": "Audiência de Custódia, Avaliação de MPUs e Notificação da Vítima (Art. 21 da LMP)",
        "texto_literal": "Quando da audiência de custódia, e sendo deferida a liberdade provisória à pessoa autora de violências, a juíza ou o juiz deverá avaliar a hipótese de deferimento das medidas protetivas de urgência previstas na Lei 11.340/2006. A mulher em situação de violência deve ser notificada dos atos processuais relativos à pessoa autora de violências, especialmente dos pertinentes ao ingresso e à saída da prisão, por qualquer meio de comunicação, sem prejuízo da intimação do seu advogado ou do defensor público, nos termos do artigo 21 da Lei 11.340/2006.",
        "materia": "Audiência de Custódia, Medidas Protetivas e Art. 21 da LMP",
        "fonte_oficial": "FONAVID — Enunciado 38 (atualizado até o XVII FONAVID)",
        "natureza": "LITERAL",
        "verificacao": _verif("verificado", _URL_FONAVID),
    },
    {
        "id": "ENUNCIADO-39-FONAVID",
        "tipo": "ENUNCIADO_FONAVID",
        "categoria": "FONAVID",
        "tribunal": "FONAVID",
        "referencia": "Enunciado 39 FONAVID",
        "titulo": "Natureza Objetiva da Qualificadora do Feminicídio (Redação Anterior à Lei 14.994/2024)",
        "texto_literal": "A qualificadora do feminicídio, nos termos do artigo 121, §2°A, I e II, do Código Penal (redação anterior à Lei 14.994/2024), é de natureza objetiva, dispensando qualquer valoração específica.",
        "materia": "Feminicídio e Qualificadora Objetiva",
        "fonte_oficial": "FONAVID — Enunciado 39 (alterado no XVI FONAVID – Salvador/BA)",
        "natureza": "LITERAL",
        "verificacao": _verif("verificado", _URL_FONAVID),
    },
    {
        "id": "ENUNCIADO-42-FONAVID",
        "tipo": "ENUNCIADO_FONAVID",
        "categoria": "FONAVID",
        "tribunal": "FONAVID",
        "referencia": "Enunciado 42 FONAVID",
        "titulo": "Intimação com Hora Certa de Medidas Protetivas de Urgência",
        "texto_literal": "É cabível a intimação com hora certa de medidas protetivas de urgência, em analogia à citação com hora certa (art. 362 do CPP e art. 227 do CPC).",
        "materia": "Intimação com Hora Certa e Celeridade Processual",
        "fonte_oficial": "FONAVID — Enunciado 42 (IX FONAVID – Natal/RN)",
        "natureza": "LITERAL",
        "verificacao": _verif("verificado", _URL_FONAVID),
    },
    {
        "id": "ENUNCIADO-43-FONAVID",
        "tipo": "ENUNCIADO_FONAVID",
        "categoria": "FONAVID",
        "tribunal": "FONAVID",
        "referencia": "Enunciado 43 FONAVID",
        "titulo": "Intimação por Edital após Esgotadas Possibilidades de Intimação Pessoal",
        "texto_literal": "Esgotadas todas as possibilidades de intimação pessoal, será cabível a intimação por edital das decisões de medidas protetivas de urgência.",
        "materia": "Intimação por Edital e Eficácia Cautelar",
        "fonte_oficial": "FONAVID — Enunciado 43 (IX FONAVID – Natal/RN)",
        "natureza": "LITERAL",
        "verificacao": _verif("verificado", _URL_FONAVID),
    },
    {
        "id": "ENUNCIADO-45-FONAVID",
        "tipo": "ENUNCIADO_FONAVID",
        "categoria": "FONAVID",
        "tribunal": "FONAVID",
        "referencia": "Enunciado 45 FONAVID",
        "titulo": "Concessão Autônoma de Medidas Protetivas com Base na Palavra da Mulher",
        "texto_literal": "As medidas protetivas de urgência previstas na Lei 11.340/2006 podem ser deferidas de forma autônoma, apenas com base na palavra da mulher em situação de violência, quando ausentes outros elementos probantes nos autos.",
        "materia": "Palavra da Vítima e Concessão Autônoma de Medidas Protetivas",
        "fonte_oficial": "FONAVID — Enunciado 45 (IX FONAVID – Natal/RN)",
        "natureza": "LITERAL",
        "verificacao": _verif("verificado", _URL_FONAVID),
    },
    {
        "id": "ENUNCIADO-46-FONAVID",
        "tipo": "ENUNCIADO_FONAVID",
        "categoria": "FONAVID",
        "tribunal": "FONAVID",
        "referencia": "Enunciado 46 FONAVID",
        "titulo": "Aplicabilidade da Lei Maria da Penha às Mulheres Trans",
        "texto_literal": "A Lei 11.340/2006 se aplica às mulheres trans, independentemente de alteração registral do nome e de cirurgia de redesignação sexual, sempre que configuradas as hipóteses do artigo 5° da Lei 11.340/2006.",
        "materia": "Identidade de Gênero e Tutela de Mulheres Trans",
        "fonte_oficial": "FONAVID — Enunciado 46 (IX FONAVID – Natal/RN)",
        "natureza": "LITERAL",
        "verificacao": _verif("verificado", _URL_FONAVID),
    },
    {
        "id": "ENUNCIADO-47-FONAVID",
        "tipo": "ENUNCIADO_FONAVID",
        "categoria": "FONAVID",
        "tribunal": "FONAVID",
        "referencia": "Enunciado 47 FONAVID",
        "titulo": "Plenitude de Defesa no Júri e Vedação de Perguntas Ofensivas à Dignidade da Mulher",
        "texto_literal": "A plenitude da defesa no júri deve observar normativas e documentos nacionais e internacionais de direitos humanos das mulheres, bem como a jurisprudência dos Sistemas Global e Regional de Direitos Humanos, cabendo à juíza ou ao juiz presidente impedir perguntas e/ou argumentos ofensivos à dignidade da mulher.",
        "materia": "Tribunal do Júri e Dignidade da Mulher",
        "fonte_oficial": "FONAVID — Enunciado 47 (XVI FONAVID – Salvador/BA)",
        "natureza": "LITERAL",
        "verificacao": _verif("verificado", _URL_FONAVID),
    },
    {
        "id": "ENUNCIADO-48-FONAVID",
        "tipo": "ENUNCIADO_FONAVID",
        "categoria": "FONAVID",
        "tribunal": "FONAVID",
        "referencia": "Enunciado 48 FONAVID",
        "titulo": "Competência dos Juizados Especializados para o Crime do Art. 24-A da LMP",
        "texto_literal": "A competência para processar e julgar o crime de descumprimento de medidas protetivas de urgência previsto no art. 24-A da Lei Maria da Penha é dos Juizados de Violência Doméstica e Familiar contra a Mulher e, onde não houver, das Varas Criminais com competência cumulativa para processar e julgar as causas decorrentes da prática de violência doméstica e familiar contra a mulher.",
        "materia": "Competência Criminal Art. 24-A LMP",
        "fonte_oficial": "FONAVID — Enunciado 48 (X FONAVID – Recife/PE)",
        "natureza": "LITERAL",
        "verificacao": _verif("verificado", _URL_FONAVID),
    },
    {
        "id": "ENUNCIADO-51-FONAVID",
        "tipo": "ENUNCIADO_FONAVID",
        "categoria": "FONAVID",
        "tribunal": "FONAVID",
        "referencia": "Enunciado 51 FONAVID",
        "titulo": "Não Revogação do Art. 20 da LMP pelas Reformas do CPP",
        "texto_literal": "O artigo 20 da Lei Maria da Penha não foi revogado tacitamente pelas modificações do Código de Processo Penal, ante o princípio da especialidade.",
        "materia": "Prisão Preventiva e Especialidade da LMP",
        "fonte_oficial": "FONAVID — Enunciado 51 (XI FONAVID – São Paulo/SP)",
        "natureza": "LITERAL",
        "verificacao": _verif("verificado", _URL_FONAVID),
    },
    {
        "id": "ENUNCIADO-37-FONAVID",
        "tipo": "ENUNCIADO_FONAVID",
        "categoria": "FONAVID",
        "tribunal": "FONAVID",
        "referencia": "Enunciado 37 FONAVID",
        "titulo": "Concessão de Medida Protetiva Independente de Tipificação Penal Estrita",
        "texto_literal": "A concessão da medida protetiva de urgência não está condicionada à existência de fato que configure, em tese, ilícito penal.",
        "materia": "Medidas Protetivas Cíveis e Autonomia em Relação ao Crime",
        "fonte_oficial": "FONAVID — Enunciado 37 (atualizado até o XVII FONAVID)",
        "natureza": "LITERAL",
        "verificacao": _verif("verificado", _URL_FONAVID),
    },
    {
        "id": "ENUNCIADO-50-FONAVID",
        "tipo": "ENUNCIADO_FONAVID",
        "categoria": "FONAVID",
        "tribunal": "FONAVID",
        "referencia": "Enunciado 50 FONAVID",
        "titulo": "Respeito à Vontade da Ofendida de Não Depor em Juízo",
        "texto_literal": "Deve ser respeitada a vontade da mulher em situação de violência de não se expressar durante seu depoimento em juízo, após devidamente informada dos seus direitos.",
        "materia": "Garantias Processuais da Vítima e Direito ao Silêncio",
        "fonte_oficial": "FONAVID — Enunciado 50 (XI FONAVID – São Paulo/SP)",
        "natureza": "LITERAL",
        "verificacao": _verif("verificado", _URL_FONAVID),
    },
    {
        "id": "ENUNCIADO-52-FONAVID",
        "tipo": "ENUNCIADO_FONAVID",
        "categoria": "FONAVID",
        "tribunal": "FONAVID",
        "referencia": "Enunciado 52 FONAVID",
        "titulo": "Aplicação do Formulário Nacional de Avaliação de Risco (FONAR)",
        "texto_literal": "A aplicação do Formulário Nacional de Avaliação de Risco poderá ser articulada pela juíza ou juiz junto com a Rede de Enfrentamento e de Atendimento às mulheres em situação de violência, com o apoio da respectiva Coordenadoria da Violência Doméstica.",
        "materia": "Formulário Nacional de Risco (FONAR) e Rede de Proteção",
        "fonte_oficial": "FONAVID — Enunciado 52 (XVI FONAVID – Salvador/BA)",
        "natureza": "LITERAL",
        "verificacao": _verif("verificado", _URL_FONAVID),
    },
    {
        "id": "ENUNCIADO-54-FONAVID",
        "tipo": "ENUNCIADO_FONAVID",
        "categoria": "FONAVID",
        "tribunal": "FONAVID",
        "referencia": "Enunciado 54 FONAVID",
        "titulo": "Análise de Medidas Protetivas sem Condicionamento ao Formulário de Risco",
        "texto_literal": "As Medidas Protetivas de Urgência deverão ser analisadas independentemente do preenchimento do Formulário Nacional de Avaliação de Risco, o qual deverá ser aplicado, preferencialmente, pela Polícia Civil, no momento do registro da ocorrência policial, visando a celeridade dos encaminhamentos da mulher em situação de violência para a rede de proteção.",
        "materia": "Celeridade na Concessão de MPUs e Formulário de Risco",
        "fonte_oficial": "FONAVID — Enunciado 54 (XI FONAVID – São Paulo/SP)",
        "natureza": "LITERAL",
        "verificacao": _verif("verificado", _URL_FONAVID),
    },
    {
        "id": "ENUNCIADO-57-FONAVID",
        "tipo": "ENUNCIADO_FONAVID",
        "categoria": "FONAVID",
        "tribunal": "FONAVID",
        "referencia": "Enunciado 57 FONAVID",
        "titulo": "Depoimento Especial da Lei 13.431/2017 para Evitar Retraumatização da Mulher",
        "texto_literal": "O depoimento especial, previsto na Lei 13.431/2017, poderá ser utilizado para a escuta da mulher diante da gravidade das diversas formas de violência doméstica e familiar, a fim de preservar a dignidade da pessoa humana e evitar retraumatizações, em conformidade à Resolução 492/2023, do Conselho Nacional de Justiça.",
        "materia": "Instrução Probatória e Não Revitimização",
        "fonte_oficial": "FONAVID — Enunciado 57 (alterado no XVI FONAVID – Salvador/BA)",
        "natureza": "LITERAL",
        "verificacao": _verif("verificado", _URL_FONAVID),
    },
    {
        "id": "ENUNCIADO-58-FONAVID",
        "tipo": "ENUNCIADO_FONAVID",
        "categoria": "FONAVID",
        "tribunal": "FONAVID",
        "referencia": "Enunciado 58 FONAVID",
        "titulo": "Desnecessidade de Exame Pericial para Prova do Dano Emocional no Art. 147-B do CP",
        "texto_literal": "O exame pericial não é necessário para a prova do dano emocional do crime de violência psicológica, descrito no artigo 147-B do Código Penal.",
        "materia": "Violência Psicológica (Art. 147-B do CP) e Prova",
        "fonte_oficial": "FONAVID — Enunciado 58 (alterado no XVI FONAVID – Salvador/BA)",
        "natureza": "LITERAL",
        "verificacao": _verif("verificado", _URL_FONAVID),
    },
    {
        "id": "ENUNCIADO-59-FONAVID",
        "tipo": "ENUNCIADO_FONAVID",
        "categoria": "FONAVID",
        "tribunal": "FONAVID",
        "referencia": "Enunciado 59 FONAVID",
        "titulo": "Violência na Presença de Filhas e Filhos como Circunstância Judicial Desfavorável (Art. 59 CP)",
        "texto_literal": "A violência praticada contra a mulher na presença das filhas e filhos pode ser valorada como circunstância judicial desfavorável no momento de fixação da pena base (artigo 59 do Código Penal).",
        "materia": "Dosimetria da Pena (Art. 59 do CP)",
        "fonte_oficial": "FONAVID — Enunciado 59 (alterado no XVI FONAVID – Salvador/BA)",
        "natureza": "LITERAL",
        "verificacao": _verif("verificado", _URL_FONAVID),
    },
    {
        "id": "ENUNCIADO-60-FONAVID",
        "tipo": "ENUNCIADO_FONAVID",
        "categoria": "FONAVID",
        "tribunal": "FONAVID",
        "referencia": "Enunciado 60 FONAVID",
        "titulo": "Aplicação do Art. 217 do CPP sob Perspectiva de Gênero (Oitiva sem Presença do Réu)",
        "texto_literal": "O artigo 217 do CPP deve ser aplicado sob a perspectiva de gênero, em audiências presenciais ou por videoconferência, assegurando-se que mulheres em situação de violência e testemunhas possam ser ouvidas sem a presença do réu, observada a participação da Defesa Técnica.",
        "materia": "Instrução Probatória e Art. 217 do CPP",
        "fonte_oficial": "FONAVID — Enunciado 60 (XIV FONAVID – Belém/PA)",
        "natureza": "LITERAL",
        "verificacao": _verif("verificado", _URL_FONAVID),
    },
    {
        "id": "ENUNCIADO-61-FONAVID",
        "tipo": "ENUNCIADO_FONAVID",
        "categoria": "FONAVID",
        "tribunal": "FONAVID",
        "referencia": "Enunciado 61 FONAVID",
        "titulo": "Ciúme e Sentimento de Posse como Circunstâncias Judiciais Desfavoráveis (Art. 59 CP)",
        "texto_literal": "O ciúme e o sentimento de posse da pessoa autora de violências sobre a mulher em situação de violência, em contexto de violência doméstica e familiar, são elementos que podem ser valorados como circunstâncias judiciais desfavoráveis no momento de fixação da pena base (artigo 59 do Código Penal).",
        "materia": "Dosimetria da Pena (Art. 59 do CP)",
        "fonte_oficial": "FONAVID — Enunciado 61 (XIV FONAVID – Belém/PA)",
        "natureza": "LITERAL",
        "verificacao": _verif("verificado", _URL_FONAVID),
    },
    {
        "id": "ENUNCIADO-64-FONAVID",
        "tipo": "ENUNCIADO_FONAVID",
        "categoria": "FONAVID",
        "tribunal": "FONAVID",
        "referencia": "Enunciado 64 FONAVID",
        "titulo": "Arquivamento ou Absolvição Não Extingue Automaticamente as Medidas Protetivas",
        "texto_literal": "O arquivamento do inquérito policial ou a absolvição da pessoa autora de violência não é requisito determinante para a revogação das medidas protetivas de urgência, ante a sua natureza autônoma, observada a existência de fatores de risco que justifiquem a sua manutenção.",
        "materia": "Medidas Protetivas de Urgência e Autonomia",
        "fonte_oficial": "FONAVID — Enunciado 64 (XIV FONAVID – Belém/PA)",
        "natureza": "LITERAL",
        "verificacao": _verif("verificado", _URL_FONAVID),
    },
    {
        "id": "ENUNCIADO-70-FONAVID",
        "tipo": "ENUNCIADO_FONAVID",
        "categoria": "FONAVID",
        "tribunal": "FONAVID",
        "referencia": "Enunciado 70 FONAVID",
        "titulo": "Diligências diante da Ausência da Ofendida em Audiência",
        "texto_literal": "Caso a mulher em situação de violência, devidamente intimada, deixe de comparecer na audiência, é recomendada a realização de diligências a fim de verificar o motivo da ausência, atentando-se para o princípio da autonomia da vontade da ofendida e eventuais riscos de revitimização.",
        "materia": "Instrução Processual e Autonomia da Vítima",
        "fonte_oficial": "FONAVID — Enunciado 70 (XV FONAVID – Porto Alegre/RS)",
        "natureza": "LITERAL",
        "verificacao": _verif("verificado", _URL_FONAVID),
    },
    {
        "id": "ENUNCIADO-71-FONAVID",
        "tipo": "ENUNCIADO_FONAVID",
        "categoria": "FONAVID",
        "tribunal": "FONAVID",
        "referencia": "Enunciado 71 FONAVID",
        "titulo": "Assistência Jurídica Qualificada às Mulheres em Situação de Violência (Arts. 27 e 28 LMP)",
        "texto_literal": "A assistência jurídica qualificada é direito das mulheres em situação de violência e de vítimas diretas e indiretas de feminicídio, abrangendo a formulação de perguntas e participação ativa no processo, inclusive com direito a sustentação em plenário do júri, conforme previsto nos artigos 27 e 28 da Lei 11.340/2006 e Recomendação 33 da CEDAW, em obediência ao critério da diligência devida.",
        "materia": "Assistência Jurídica Qualificada e Direitos da Vítima",
        "fonte_oficial": "FONAVID — Enunciado 71 (alterado no XVI FONAVID – Salvador/BA)",
        "natureza": "LITERAL",
        "verificacao": _verif("verificado", _URL_FONAVID),
    },
    {
        "id": "ENUNCIADO-72-FONAVID",
        "tipo": "ENUNCIADO_FONAVID",
        "categoria": "FONAVID",
        "tribunal": "FONAVID",
        "referencia": "Enunciado 72 FONAVID",
        "titulo": "Proteção da Lei Maria da Penha às Relações Íntimas de Afeto em Meio Virtual",
        "texto_literal": "As relações íntimas de afeto mantidas no âmbito das redes sociais ou qualquer outro meio virtual, ainda que sem contato físico, estão protegidas pela Lei Maria da Penha.",
        "materia": "Âmbito de Aplicação e Violência em Meio Virtual",
        "fonte_oficial": "FONAVID — Enunciado 72 (XV FONAVID – Porto Alegre/RS)",
        "natureza": "LITERAL",
        "verificacao": _verif("verificado", _URL_FONAVID),
    },
    {
        "id": "ENUNCIADO-74-FONAVID",
        "tipo": "ENUNCIADO_FONAVID",
        "categoria": "FONAVID",
        "tribunal": "FONAVID",
        "referencia": "Enunciado 74 FONAVID",
        "titulo": "Perícia Psicológica ou Psiquiátrica na Lesão à Saúde Mental (Art. 129 do CP)",
        "texto_literal": "A configuração da materialidade do crime de lesão à saúde mental previsto no artigo 129 do Código Penal dependerá de perícia psicológica ou psiquiátrica, que deverá ser realizada com perspectiva de gênero.",
        "materia": "Lesão Corporal à Saúde Mental (Art. 129 do CP)",
        "fonte_oficial": "FONAVID — Enunciado 74 (XV FONAVID – Porto Alegre/RS)",
        "natureza": "LITERAL",
        "verificacao": _verif("verificado", _URL_FONAVID),
    },
    {
        "id": "ENUNCIADO-76-FONAVID",
        "tipo": "ENUNCIADO_FONAVID",
        "categoria": "FONAVID",
        "tribunal": "FONAVID",
        "referencia": "Enunciado 76 FONAVID",
        "titulo": "Manutenção da Participação em Grupos Reflexivos Mesmo após Revogação de Outras MPUs",
        "texto_literal": "A revogação de medidas protetivas de urgência não implicará na automática revogação da que determina a participação em grupos reflexivos e responsabilizantes de homens autores de violência (art. 22, VI e VII, da Lei nº 11.340/06), a qual poderá ser mantida até o encerramento do programa/acompanhamento sociopsicopedagógico, como mecanismo de prevenção e proteção da mulher em situação de violência doméstica e familiar, nos termos dos artigos 226, § 8º da CF/88 e 1º a 4º da Lei 11.340/2006.",
        "materia": "Medidas Protetivas e Grupos Reflexivos",
        "fonte_oficial": "FONAVID — Enunciado 76 (XVII FONAVID – São Luís/MA)",
        "natureza": "LITERAL",
        "verificacao": _verif("verificado", _URL_FONAVID),
    },
    {
        "id": "ENUNCIADO-78-FONAVID",
        "tipo": "ENUNCIADO_FONAVID",
        "categoria": "FONAVID",
        "tribunal": "FONAVID",
        "referencia": "Enunciado 78 FONAVID",
        "titulo": "Competência para MPUs de Casais Homoafetivos Masculinos e Limites do Art. 40-A da LMP",
        "texto_literal": "A apreciação das medidas protetivas de urgência requeridas por casais homoafetivos do gênero masculino compete às varas criminais, cíveis, família, juizados especiais criminais, varas de tribunais do júri, a depender da natureza da demanda e das normas de organização judiciária. A competência das varas e juizados de violência doméstica e familiar é restrita aos limites previstos no art. 40-A da Lei Maria da Penha e se reserva aos pedidos de medidas protetivas de urgência requeridas em favor das mulheres cis ou trans em situação de violência doméstica e familiar.",
        "materia": "Competência Especializada e Art. 40-A da LMP",
        "fonte_oficial": "FONAVID — Enunciado 78 (XVII FONAVID – São Luís/MA)",
        "natureza": "LITERAL",
        "verificacao": _verif("verificado", _URL_FONAVID),
    },
    {
        "id": "ENUNCIADO-79-FONAVID",
        "tipo": "ENUNCIADO_FONAVID",
        "categoria": "FONAVID",
        "tribunal": "FONAVID",
        "referencia": "Enunciado 79 FONAVID",
        "titulo": "Revogação de MPUs Somente Após Prévia Manifestação da Mulher e Intervenção do MP",
        "texto_literal": "As medidas protetivas de urgência somente poderão ser revogadas, após oportunizada a prévia e facilitada manifestação da mulher em situação de violência, garantida a assistência jurídica qualificada e a intervenção do Ministério Público, nos termos dos artigos 25, 27 e 28 da Lei Maria da Penha. A análise do pedido levará em consideração o fundamento e a gravidade dos fatos, desde que cessados os riscos à integridade física, psicológica, sexual, patrimonial ou moral da ofendida.",
        "materia": "Medidas Protetivas de Urgência e Contraditório",
        "fonte_oficial": "FONAVID — Enunciado 79 (XVII FONAVID – São Luís/MA)",
        "natureza": "LITERAL",
        "verificacao": _verif("verificado", _URL_FONAVID),
    },
    {
        "id": "ENUNCIADO-80-FONAVID",
        "tipo": "ENUNCIADO_FONAVID",
        "categoria": "FONAVID",
        "tribunal": "FONAVID",
        "referencia": "Enunciado 80 FONAVID",
        "titulo": "Violência Processual de Gênero na Análise de Risco para Medidas Protetivas",
        "texto_literal": "Configura violência processual de gênero o uso instrumentalizado, reiterado, protelatório ou abusivo do sistema jurídico, com o fim de retaliar, punir, silenciar, desgastar, descredibilizar, fragilizar ou sobrecarregar a mulher em situação de violência doméstica e familiar, causar sofrimento físico e/ou psíquico ou prejuízo financeiro, devendo tal prática ser considerada na análise do risco, para aplicação ou manutenção das medidas protetivas de urgência, com imediata comunicação aos juízos das respectivas demandas.",
        "materia": "Violência Processual de Gênero e Análise de Risco",
        "fonte_oficial": "FONAVID — Enunciado 80 (XVII FONAVID – São Luís/MA)",
        "natureza": "LITERAL",
        "verificacao": _verif("verificado", _URL_FONAVID),
    },
    {
        "id": "ENUNCIADO-81-FONAVID",
        "tipo": "ENUNCIADO_FONAVID",
        "categoria": "FONAVID",
        "tribunal": "FONAVID",
        "referencia": "Enunciado 81 FONAVID",
        "titulo": "Orientações na Sentença Condenatória para Execução da Reparação de Danos (Art. 387, IV, CPP)",
        "texto_literal": "Na decisão condenatória devem constar orientações às partes acerca de como proceder à execução/pagamento da quantia referente à reparação dos danos causados pela infração, prevista no artigo 387, IV, do CPP.",
        "materia": "Reparação de Danos (Art. 387, IV, CPP) e Sentença",
        "fonte_oficial": "FONAVID — Enunciado 81 (XVII FONAVID – São Luís/MA)",
        "natureza": "LITERAL",
        "verificacao": _verif("verificado", _URL_FONAVID),
    },
    # Súmulas e Temas Incorporados dos Modelos de Sentença
    {
        "id": "SUMULA-54-STJ",
        "tipo": "SUMULA_STJ",
        "categoria": "SUMULAS",
        "tribunal": "STJ",
        "referencia": "Súmula 54/STJ",
        "titulo": "Fluência dos Juros Moratórios a partir do Evento Danoso (Responsabilidade Extracontratual / Delitual)",
        "texto_literal": "Os juros moratórios fluem a partir do evento danoso, em caso de responsabilidade extracontratual.",
        "materia": "Reparação de Danos Morais (Art. 387, IV, CPP e Art. 398 CC)",
        "fonte_oficial": "Súmula da Segunda Seção do STJ (DJ 01/10/1992)",
        "observacao": "Amostra do 1º grau do TJRN (2024–2026): precedente citado em cerca de 23 sentenças.",
        "natureza": "LITERAL",
        "verificacao": _verif("verificado", _URL_STJ_SUMULAS),
    },
    {
        "id": "SUMULA-362-STJ",
        "tipo": "SUMULA_STJ",
        "categoria": "SUMULAS",
        "tribunal": "STJ",
        "referencia": "Súmula 362/STJ",
        "titulo": "Termo Inicial da Correção Monetária na Indenização por Dano Moral",
        "texto_literal": "A correção monetária do valor da indenização do dano moral incide desde a data do arbitramento.",
        "materia": "Reparação de Danos Morais e Atualização Monetária (Art. 387, IV, CPP)",
        "fonte_oficial": "Súmula da Corte Especial do STJ (DJe 03/11/2008)",
        "observacao": "Amostra do 1º grau do TJRN (2024–2026): precedente citado em cerca de 29 sentenças.",
        "natureza": "LITERAL",
        "verificacao": _verif("verificado", _URL_STJ_SUMULAS),
    },
    {
        "id": "TEMA-585-STJ",
        "tipo": "TEMA_REPETITIVO_STJ",
        "categoria": "TEMAS_REPETITIVOS",
        "tribunal": "STJ",
        "referencia": "Tema Repetitivo 585/STJ",
        "titulo": "Compensação entre Confissão Espontânea e Reincidência / Multirreincidência (Tese Revisada)",
        "texto_literal": "É possível, na segunda fase da dosimetria da pena, a compensação integral da atenuante da confissão espontânea com a agravante da reincidência, seja ela específica ou não. Todavia, nos casos de multirreincidência, deve ser reconhecida a preponderância da agravante prevista no art. 61, I, do Código Penal, sendo admissível a sua compensação proporcional com a atenuante da confissão espontânea, em estrito atendimento aos princípios da individualização da pena e da proporcionalidade.",
        "materia": "Dosimetria da Pena (Segunda Fase — Art. 67 do CP)",
        "fonte_oficial": "STJ, Terceira Seção, REsp 1.947.845/SP e REsp 1.931.145/SP (revisão do Tema 585), Rel. Min. Sebastião Reis Júnior, j. 22/06/2022, DJe 24/06/2022",
        "natureza": "LITERAL",
        "verificacao": _verif("verificado", _url_tema_stj(585)),
    },
    # Citadas pelo motor de dosimetria (prescrição por crime e fração da continuidade delitiva)
    {
        "id": "SUMULA-497-STF",
        "tipo": "SUMULA_STF",
        "categoria": "SUMULAS",
        "tribunal": "STF",
        "referencia": "Súmula 497/STF",
        "titulo": "Prescrição no Crime Continuado sem o Acréscimo da Continuação",
        "texto_literal": "Quando se tratar de crime continuado, a prescrição regula-se pela pena imposta na sentença, não se computando o acréscimo decorrente da continuação.",
        "materia": "Prescrição da Pretensão Punitiva (Art. 119 CP)",
        "fonte_oficial": "Súmula do Supremo Tribunal Federal (Sessão Plenária de 03/12/1969)",
        "natureza": "LITERAL",
        "verificacao": _verif("verificado", _URL_STF_SUMULAS),
    },
    {
        "id": "SUMULA-659-STJ",
        "tipo": "SUMULA_STJ",
        "categoria": "SUMULAS",
        "tribunal": "STJ",
        "referencia": "Súmula 659/STJ",
        "titulo": "Fração de Aumento da Continuidade Delitiva pelo Número de Infrações",
        "texto_literal": "A fração de aumento em razão da prática de crime continuado deve ser fixada de acordo com o número de delitos cometidos, aplicando-se 1/6 pela prática de duas infrações, 1/5 para três, 1/4 para quatro, 1/3 para cinco, 1/2 para seis e 2/3 para sete ou mais infrações.",
        "materia": "Concurso de Crimes e Dosimetria (Art. 71 CP)",
        "fonte_oficial": "Súmula da Terceira Seção do STJ (DJe 18/09/2023)",
        "natureza": "LITERAL",
        "verificacao": _verif("verificado", _URL_STJ_SUMULAS),
    },
    {
        "id": "SUMULA-636-STJ",
        "tipo": "SUMULA_STJ",
        "categoria": "SUMULAS",
        "tribunal": "STJ",
        "referencia": "Súmula 636/STJ",
        "titulo": "Folha de Antecedentes Criminais como Prova dos Maus Antecedentes e da Reincidência",
        "texto_literal": "A folha de antecedentes criminais é documento suficiente a comprovar os maus antecedentes e a reincidência.",
        "materia": "Dosimetria da Pena (Primeira e Segunda Fases)",
        "fonte_oficial": "STJ, Terceira Seção, Súmula 636, DJe 27/06/2019",
        "observacao": "Amostra do 1º grau do TJRN (2024–2026): precedente citado em 6 sentenças. A folha deve indicar o trânsito em julgado. A prova pode ser questionada se a folha estiver incompleta.",
        "natureza": "LITERAL",
        "verificacao": _verif("verificado", "https://scon.stj.jus.br/SCON/sumstj/toc.jsp?sumula=636.num."),
    },
    {
        "id": "TEMA-129-STF",
        "tipo": "TEMA_REPERCUSSAO_GERAL_STF",
        "categoria": "STF_VINCULANTE",
        "tribunal": "STF",
        "referencia": "Tema 129/STF",
        "titulo": "Inquéritos e Ações Penais sem Trânsito em Julgado na Dosimetria",
        "texto_literal": "A existência de inquéritos policiais ou de ações penais sem trânsito em julgado não pode ser considerada como maus antecedentes para fins de dosimetria da pena.",
        "materia": "Dosimetria da Pena (Primeira Fase)",
        "fonte_oficial": "STF, Tema 129 da repercussão geral, RE 591.054, j. 17/12/2014",
        "observacao": "Amostra do 1º grau do TJRN (2024–2026): precedente citado em 7 sentenças. Ver também a Súmula 444/STJ.",
        "natureza": "LITERAL",
        "verificacao": _verif("verificado", "https://noticias.stf.jus.br/?p=36083"),
    },
    {
        "id": "TJRN-1G-INSUFICIENCIA-PROBATORIA",
        "tipo": "PRATICA_1G_TJRN",
        "categoria": "TJRN",
        "tribunal": "TJRN",
        "referencia": "TJRN — Síntese do 1º grau — In Dubio Pro Reo na Valoração da Palavra da Vítima",
        "titulo": "In Dubio Pro Reo na Valoração da Palavra da Vítima",
        "texto_literal": "O in dubio pro reo é afastado quando a palavra da vítima encontra corroboração. Se o relato é isolado ou contraditório e a prova é insuficiente, aplica-se o art. 386, VII, do CPP.",
        "materia": "Valoração Probatória e Absolvição",
        "fonte_oficial": "Banco de Jurisprudência do TJRN, sentenças de 1º grau, amostra de 295 (2024–2026)",
        "observacao": "73 sentenças tratam do tema.",
        "natureza": "SINTESE_DO_ENTENDIMENTO",
        "verificacao": _verif("verificado", "https://jurisprudencia.tjrn.jus.br/"),
    },
    {
        "id": "TJRN-1G-EMBRIAGUEZ-VOLUNTARIA",
        "tipo": "PRATICA_1G_TJRN",
        "categoria": "TJRN",
        "tribunal": "TJRN",
        "referencia": "TJRN — Síntese do 1º grau — Embriaguez Voluntária e Imputabilidade",
        "titulo": "Embriaguez Voluntária e Imputabilidade",
        "texto_literal": "A embriaguez voluntária não exclui a imputabilidade (art. 28, II, do CP, actio libera in causa).",
        "materia": "Imputabilidade Penal",
        "fonte_oficial": "Banco de Jurisprudência do TJRN, sentenças de 1º grau, amostra de 295 (2024–2026)",
        "observacao": "41 sentenças tratam do tema.",
        "natureza": "SINTESE_DO_ENTENDIMENTO",
        "verificacao": _verif("verificado", "https://jurisprudencia.tjrn.jus.br/"),
    },
    {
        "id": "TJRN-1G-DESCLASSIFICACAO",
        "tipo": "PRATICA_1G_TJRN",
        "categoria": "TJRN",
        "tribunal": "TJRN",
        "referencia": "TJRN — Síntese do 1º grau — Desclassificação da Lesão Corporal",
        "titulo": "Desclassificação da Lesão Corporal",
        "texto_literal": "As sentenças examinam os pedidos de desclassificação à luz da prova pericial, como na passagem do § 13 para o § 9º do art. 129 do CP ou de lesão grave para leve na falta de laudo complementar.",
        "materia": "Lesão Corporal e Prova Pericial",
        "fonte_oficial": "Banco de Jurisprudência do TJRN, sentenças de 1º grau, amostra de 295 (2024–2026)",
        "observacao": "50 sentenças tratam do tema.",
        "natureza": "SINTESE_DO_ENTENDIMENTO",
        "verificacao": _verif("verificado", "https://jurisprudencia.tjrn.jus.br/"),
    },
    {
        "id": "TJRN-1G-BIS-IN-IDEM",
        "tipo": "PRATICA_1G_TJRN",
        "categoria": "TJRN",
        "tribunal": "TJRN",
        "referencia": "TJRN — Síntese do 1º grau — Bis in Idem na Dosimetria",
        "titulo": "Bis in Idem na Dosimetria",
        "texto_literal": "A motivação de gênero já contida na qualificadora (art. 147, § 1º, ou art. 129, § 13, do CP) não pode ser valorada novamente como circunstância judicial. Para a agravante do art. 61, II, f, do CP, ver o Tema 1.197/STJ.",
        "materia": "Dosimetria da Pena (Primeira Fase)",
        "fonte_oficial": "Banco de Jurisprudência do TJRN, sentenças de 1º grau, amostra de 295 (2024–2026)",
        "observacao": "37 sentenças tratam do tema.",
        "natureza": "SINTESE_DO_ENTENDIMENTO",
        "verificacao": _verif("verificado", "https://jurisprudencia.tjrn.jus.br/"),
    },
    {
        "id": "TJRN-1G-CIENCIA-MPU",
        "tipo": "PRATICA_1G_TJRN",
        "categoria": "TJRN",
        "tribunal": "TJRN",
        "referencia": "TJRN — Síntese do 1º grau — Ciência Prévia da Medida Protetiva",
        "titulo": "Ciência Prévia da Medida Protetiva",
        "texto_literal": "As sentenças examinadas exigem prova de ciência inequívoca da medida protetiva mediante intimação pessoal anterior ao fato.",
        "materia": "Descumprimento de Medida Protetiva (Art. 24-A da Lei Maria da Penha)",
        "fonte_oficial": "Banco de Jurisprudência do TJRN, sentenças de 1º grau, amostra de 295 (2024–2026)",
        "observacao": "Amostra sem contagem específica informada para o tema.",
        "natureza": "SINTESE_DO_ENTENDIMENTO",
        "verificacao": _verif("verificado", "https://jurisprudencia.tjrn.jus.br/"),
    },
    {
        "id": "TJRN-1G-CONCURSO-MATERIAL",
        "tipo": "PRATICA_1G_TJRN",
        "categoria": "TJRN",
        "tribunal": "TJRN",
        "referencia": "TJRN — Síntese do 1º grau — Concurso Material em Condutas Autônomas",
        "titulo": "Concurso Material em Condutas Autônomas",
        "texto_literal": "As sentenças aplicam o concurso material e somam as penas quando ameaça e dano ou lesão e ameaça decorrem de condutas autônomas (art. 69 do CP).",
        "materia": "Concurso de Crimes (Art. 69 do CP)",
        "fonte_oficial": "Banco de Jurisprudência do TJRN, sentenças de 1º grau, amostra de 295 (2024–2026)",
        "observacao": "Recorrência em várias sentenças, sem contagem específica informada.",
        "natureza": "SINTESE_DO_ENTENDIMENTO",
        "verificacao": _verif("verificado", "https://jurisprudencia.tjrn.jus.br/"),
    },
]

#: Enunciados FONAVID revogados (não podem constar no acervo vigente).
FONAVID_REVOGADOS: frozenset[int] = frozenset({2, 5, 6, 8, 10, 12, 27, 28, 34, 41, 55})

# Doutrina Especializada Consagrada — apenas referência ABNT e síntese do entendimento (NUNCA citação literal entre aspas)
DOUTRINA_VERIFICADA: List[Dict[str, Any]] = [
    {
        "id": "DOUT-01",
        "autor": "Maria Berenice Dias",
        "obra": "A Lei Maria da Penha na Justiça (Ed. JusPodivm)",
        "referencia_abnt": "DIAS, Maria Berenice. A Lei Maria da Penha na Justiça. 9. ed. rev., atual. e ampl. São Paulo: JusPodivm, 2024.",
        "tema": "Palavra da Vítima e Clandestinidade da Violência Doméstica",
        "natureza": "SINTESE_DO_ENTENDIMENTO",
        "sintese": (
            "Como os delitos de violência doméstica e familiar contra a mulher são praticados, em regra, no ambiente privado e sem testemunhas presenciais, "
            "a palavra da ofendida assume especial relevo probatório quando coerente e corroborada pelos demais elementos indiciários e periciais dos autos."
        ),
        "verificacao": _verif("verificado", _URL_BDJUR_STJ),
    },
    {
        "id": "DOUT-03",
        "autor": "Rogério Sanches Cunha e Ronaldo Batista Pinto",
        "obra": "Violência doméstica: Lei Maria da Penha (Lei 11.340/2006) comentada artigo por artigo (Ed. JusPodivm)",
        "referencia_abnt": "CUNHA, Rogério Sanches; PINTO, Ronaldo Batista. Violência doméstica: Lei Maria da Penha (Lei 11.340/2006) comentada artigo por artigo. 15. ed. São Paulo: JusPodivm, 2024.",
        "tema": "Ameaça em Contexto Doméstico e Eficácia Intimidatória",
        "natureza": "SINTESE_DO_ENTENDIMENTO",
        "sintese": (
            "No delito de ameaça praticado no contexto de violência doméstica e familiar, a alegação de ânimo exaltado ou discussão acalorada não afasta "
            "a tipicidade penal quando a promessa de mal injusto e grave se insere em contexto concreto de intimidação e mostra-se idônea para abalar a tranquilidade da vítima."
        ),
        "verificacao": _verif("verificado", _URL_BDJUR_STJ),
    },
    {
        "id": "DOUT-04",
        "autor": "Alice Bianchini et al.",
        "obra": "Crimes contra mulheres (Ed. JusPodivm)",
        "referencia_abnt": "BIANCHINI, Alice; BAZZO, M.; CHAKIAN, Silvia. Crimes contra mulheres. 6. ed. rev., atual. e ampl. São Paulo: JusPodivm, 2024.",
        "tema": "Ciclo da Violência e Consequências do Delito (Art. 59 do CP)",
        "natureza": "SINTESE_DO_ENTENDIMENTO",
        "sintese": (
            "A compreensão da dinâmica assimétrica das relações domésticas e da vulnerabilidade imposta pela violência de gênero exige análise contextualizada "
            "das provas, especialmente quando a agressão ocorre na presença de filhos comuns, circunstância que agrava a reprovabilidade da conduta na dosimetria (art. 59 do CP)."
        ),
        "verificacao": _verif("verificado", _URL_BDJUR_STJ),
    },
    {
        "id": "DOUT-08",
        "autor": "Maria Berenice Dias",
        "obra": "A Lei Maria da Penha e os novos avanços (Ed. JusPodivm)",
        "referencia_abnt": "DIAS, Maria Berenice. A Lei Maria da Penha e os novos avanços. 11. ed. São Paulo: JusPodivm, 2026.",
        "tema": "Atualizações Legislativas e Efetividade das Medidas Protetivas",
        "natureza": "SINTESE_DO_ENTENDIMENTO",
        "sintese": (
            "Analisa as atualizações recentes da Lei Maria da Penha e o fortalecimento dos mecanismos protetivos de urgência como tutela autônoma voltada à prevenção da violência de gênero."
        ),
        "verificacao": _verif("verificado", _URL_JUSPODIVM),
    },
]


def obter_item_oficial_por_id(item_id: str) -> Dict[str, Any]:
    """Localiza um item oficial em ``SUMULAS_E_TEMAS_VERIFICADOS`` por ID exato (sem I/O de disco)."""
    alvo = item_id.strip().upper()
    for item in SUMULAS_E_TEMAS_VERIFICADOS:
        if str(item.get("id", "")).upper() == alvo:
            return item
    raise KeyError(f"Item oficial não encontrado no acervo: {item_id!r}")


def obter_doutrina_por_id(item_id: str) -> Dict[str, Any]:
    """Localiza um verbete doutrinário em ``DOUTRINA_VERIFICADA`` por ID exato (sem I/O de disco)."""
    alvo = item_id.strip().upper()
    for item in DOUTRINA_VERIFICADA:
        if str(item.get("id", "")).upper() == alvo:
            return item
    raise KeyError(f"Item de doutrina não encontrado no acervo: {item_id!r}")


def pode_citar_literal(item: Dict[str, Any]) -> bool:
    """True somente se o item for ``LITERAL`` e tiver ``verificacao.status == 'verificado'``."""
    if not isinstance(item, dict):
        return False
    verif = item.get("verificacao")
    status = verif.get("status") if isinstance(verif, dict) else None
    return item.get("natureza") == "LITERAL" and status == "verificado"


def item_citavel_automaticamente(item: Dict[str, Any]) -> bool:
    """False para itens oficiais marcados como ``pendente`` (visíveis no painel, mas sem citação automática)."""
    if not isinstance(item, dict):
        return False
    verif = item.get("verificacao")
    if isinstance(verif, dict) and verif.get("status") == "pendente":
        return False
    return True


def formatar_citacao_acervo(item: Dict[str, Any], *, com_aspas: bool = True) -> str:
    """Formata o texto de um verbete do acervo respeitando ``natureza`` e ``verificacao``.

    - Itens ``pendente`` retornam string vazia (nunca são citados automaticamente).
    - Somente itens ``LITERAL`` com ``status == 'verificado'`` podem sair entre aspas.
    - Itens ``RESUMO``, ``parcial`` ou ``SINTESE_DO_ENTENDIMENTO`` saem sempre **sem aspas**.
    """
    if not item_citavel_automaticamente(item):
        return ""
    texto = str(item.get("texto_literal") or item.get("sintese") or "").strip()
    if not texto:
        return ""
    if com_aspas and pode_citar_literal(item):
        return f'"{texto}"'
    return texto


def _escrever_json_atomico(destino: Path, conteudo: Any) -> None:
    """Escrita atômica (arquivo temporário no mesmo diretório + ``os.replace``), com novas tentativas no Windows."""
    destino.parent.mkdir(parents=True, exist_ok=True)
    texto = json.dumps(conteudo, ensure_ascii=False, indent=2)
    fd, tmp = tempfile.mkstemp(prefix=f".{destino.name}.", suffix=".tmp", dir=str(destino.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(texto)
            fh.flush()
            os.fsync(fh.fileno())
        ultimo_erro: Optional[Exception] = None
        for tentativa in range(5):
            try:
                os.replace(tmp, destino)
                return
            except PermissionError as exc:  # Windows: arquivo momentaneamente aberto por outro processo
                ultimo_erro = exc
                time.sleep(0.05 * (tentativa + 1))
        if ultimo_erro is not None:
            raise ultimo_erro
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


def _payload_acervo() -> Dict[str, Any]:
    return {
        "sumulas_e_temas": SUMULAS_E_TEMAS_VERIFICADOS,
        "doutrina": DOUTRINA_VERIFICADA,
        "metadados": {
            "total_itens": len(SUMULAS_E_TEMAS_VERIFICADOS),
            "total_doutrina": len(DOUTRINA_VERIFICADA),
            "tribunais": sorted({item.get("tribunal", "STJ") for item in SUMULAS_E_TEMAS_VERIFICADOS}),
            "categorias": sorted({item.get("categoria", "SUMULAS") for item in SUMULAS_E_TEMAS_VERIFICADOS}),
        },
    }


def _salvar_acervo_em_disco(forcar: bool = False) -> bool:
    """
    Sincroniza o espelho JSON do acervo oficial (``data/knowledge_base/acervo_completo.json``).

    Só grava quando o arquivo está ausente, ilegível ou desatualizado em relação aos verbetes do
    código (ou quando ``forcar=True``), sempre com escrita atômica. Retorna ``True`` se gravou.
    """
    payload = _payload_acervo()
    try:
        if not forcar and ACERVO_COMPLETO_FILE.exists():
            try:
                atual = json.loads(ACERVO_COMPLETO_FILE.read_text(encoding="utf-8"))
                if atual == json.loads(json.dumps(payload, ensure_ascii=False)):
                    return False
            except (OSError, ValueError):
                pass  # ilegível/corrompido: regrava
        _escrever_json_atomico(ACERVO_COMPLETO_FILE, payload)
        return True
    except Exception as exc:  # nunca impede o uso do acervo em memória
        logger.warning("Não foi possível sincronizar o acervo em disco (%s): %s", ACERVO_COMPLETO_FILE, exc)
        return False


VIGENCIAS_VALIDAS = ("vigente", "modulada", "superado", "revogado", "nao_verificada")

_REGRAS_QUESTOES_JURIDICAS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("substituicao_pena", ("substituição", "substituicao", "restritiva de direitos", "súmula 588", "sumula 588")),
    ("suspensao_condicional", ("suspensão condicional", "suspensao condicional", "transação penal", "9.099", "súmula 536", "sursis")),
    ("acao_penal", ("ação penal", "acao penal", "incondicionada", "condicionada", "representação", "retratação", "súmula 542", "adi 4.424")),
    ("insignificancia", ("insignificância", "insignificancia", "bagatela", "súmula 589")),
    ("reparacao_danos", ("reparação", "reparacao", "dano moral", "valor mínimo", "tema 983", "art. 387, iv")),
    ("palavra_da_vitima", ("palavra da vítima", "palavra da vitima", "declarações da ofendida", "depoimento da vítima", "especial relevo")),
    ("medida_protetiva", ("medida protetiva", "medidas protetivas", "24-a", "descumprimento", "urgência")),
    ("legitima_defesa_honra", ("legítima defesa da honra", "legitima defesa da honra", "adpf 779")),
    ("perspectiva_de_genero", ("perspectiva de gênero", "perspectiva de genero", "resolução 492", "cnj 492")),
    ("dosimetria", ("dosimetria", "pena-base", "art. 59", "agravante", "atenuante", "súmula 231", "súmula 444", "61, ii, f")),
    ("prescricao", ("prescrição", "prescricao", "súmula 497", "art. 109", "art. 119")),
    ("competencia", ("competência", "competencia", "juizado", "coabitação", "coabitacao", "súmula 600", "âmbito doméstico")),
    ("violencia_psicologica", ("violência psicológica", "violencia psicologica", "147-b")),
    ("perseguicao", ("perseguição", "perseguicao", "stalking", "147-a")),
    ("lesao_corporal", ("lesão corporal", "lesao corporal", "129, § 9", "129, § 13")),
    ("prisao_preventiva", ("prisão preventiva", "prisao preventiva", "art. 20", "art. 313")),
)


def _normalizar_busca_kb(texto: str) -> str:
    sem_acento = unicodedata.normalize("NFKD", texto or "")
    limpo = "".join(ch for ch in sem_acento if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", limpo).strip().lower()


def _inferir_questoes_juridicas(item: Dict[str, Any]) -> List[str]:
    base = _normalizar_busca_kb(
        " ".join(
            str(item.get(k) or "")
            for k in ("id", "referencia", "titulo", "materia", "texto_literal", "sintese")
        )
    )
    encontradas = [tag for tag, chaves in _REGRAS_QUESTOES_JURIDICAS if any(_normalizar_busca_kb(c) in base for c in chaves)]
    if not encontradas:
        mat = _normalizar_busca_kb(str(item.get("materia") or "violencia_domestica")).replace(" ", "_")
        encontradas = [mat or "violencia_domestica"]
    return encontradas


def _enriquecer_item_acervo(item: Dict[str, Any]) -> None:
    """Garante ``proposicao_juridica``, ``proposicao_origem``, ``recorte_fatico``, ``vigencia``, ``data_revisao`` e ``questoes_juridicas``.

    Nunca decide vigência, recorte fático nem data de revisão por padrão: campos ausentes ficam
    ``"nao_verificada"`` / ``""``.
    """
    texto_base = str(item.get("texto_literal") or item.get("sintese") or item.get("titulo") or "").strip()
    if not item.get("proposicao_juridica"):
        primeira = re.split(r"(?<=[.!?])\s+", texto_base, maxsplit=1)[0].strip() if texto_base else ""
        item["proposicao_juridica"] = primeira if len(primeira) >= 15 else texto_base
        item["proposicao_origem"] = "primeira_frase" if item["proposicao_juridica"] else ""
    elif not item.get("proposicao_origem"):
        item["proposicao_origem"] = "revisada"
    if not item.get("recorte_fatico"):
        item["recorte_fatico"] = ""
    vig = str(item.get("vigencia") or "").strip().lower()
    item["vigencia"] = vig if vig in VIGENCIAS_VALIDAS else "nao_verificada"
    if not item.get("data_revisao"):
        verif = item.get("verificacao")
        data_conf = verif.get("conferido_em") if isinstance(verif, dict) else ""
        item["data_revisao"] = str(data_conf or "").strip()
    if not item.get("questoes_juridicas"):
        item["questoes_juridicas"] = _inferir_questoes_juridicas(item)


def _enriquecer_acervo_oficial() -> None:
    for item in SUMULAS_E_TEMAS_VERIFICADOS:
        _enriquecer_item_acervo(item)


_enriquecer_acervo_oficial()

# Sincroniza o espelho em disco apenas se ausente ou desatualizado (não regrava a cada import).
_salvar_acervo_em_disco()


def _ler_custom_items() -> List[Dict[str, str]]:
    if not CUSTOM_KB_FILE.exists():
        return []
    try:
        dados = json.loads(CUSTOM_KB_FILE.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("Acervo do gabinete ilegível (%s): %s", CUSTOM_KB_FILE, exc)
        return []
    return [it for it in dados if isinstance(it, dict)] if isinstance(dados, list) else []


def versao_acervo() -> str:
    """
    Identificador da versão atual do acervo (oficial + precedentes do gabinete).

    Muda sempre que um precedente é cadastrado por este processo ou quando o arquivo do
    gabinete é alterado em disco (mtime/tamanho). Usado pelo auditor para invalidar o cache do
    índice da Base Fechada.
    """
    try:
        st = CUSTOM_KB_FILE.stat()
        assinatura_arquivo = f"{st.st_mtime_ns}:{st.st_size}"
    except OSError:
        assinatura_arquivo = "ausente"
    return f"oficial:{len(SUMULAS_E_TEMAS_VERIFICADOS)}|local:{_VERSAO_LOCAL}|gabinete:{assinatura_arquivo}"


def _marcar_acervo_alterado() -> None:
    global _VERSAO_LOCAL
    _VERSAO_LOCAL += 1


def get_all_knowledge_base(
    categoria: Optional[str] = None,
    busca: Optional[str] = None,
    questao: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Retorna todo o acervo consolidado do gabinete (Súmulas STJ/STF, Temas Repetitivos, STF Vinculante,
    Teses STJ, Enunciados FONAVID, Precedentes TJRN, Doutrina e Precedentes customizados do gabinete).
    Suporta filtros opcionais por categoria, termo de busca e questão jurídica.
    """
    custom_items = _ler_custom_items()

    sumulas_e_temas = list(SUMULAS_E_TEMAS_VERIFICADOS)

    if categoria and categoria != "TODOS":
        sumulas_e_temas = [
            it for it in sumulas_e_temas
            if it.get("categoria", "").upper() == categoria.upper()
            or it.get("tipo", "").upper() == categoria.upper()
            or it.get("tribunal", "").upper() == categoria.upper()
        ]

    if questao:
        alvo_q = _normalizar_busca_kb(questao).replace(" ", "_")
        sumulas_e_temas = [
            it for it in sumulas_e_temas
            if alvo_q in {_normalizar_busca_kb(str(q)).replace(" ", "_") for q in (it.get("questoes_juridicas") or [])}
            or alvo_q.replace("_", " ") in _normalizar_busca_kb(
                f"{it.get('proposicao_juridica', '')} {it.get('recorte_fatico', '')} {it.get('titulo', '')}"
            )
        ]

    if busca:
        termo = busca.strip().lower()
        sumulas_e_temas = [
            it for it in sumulas_e_temas
            if termo in it.get("referencia", "").lower()
            or termo in it.get("titulo", "").lower()
            or termo in it.get("texto_literal", "").lower()
            or termo in it.get("materia", "").lower()
            or termo in str(it.get("proposicao_juridica", "")).lower()
            or termo in str(it.get("recorte_fatico", "")).lower()
        ]

    return {
        "sumulas_e_temas": sumulas_e_temas,
        "doutrina": DOUTRINA_VERIFICADA,
        "precedentes_gabinete": custom_items,
        "categorias_disponiveis": [
            {"id": "TODOS", "label": "Todos os Precedentes"},
            {"id": "SUMULAS", "label": "Súmulas STJ & STF"},
            {"id": "TEMAS_REPETITIVOS", "label": "Temas Repetitivos STJ"},
            {"id": "STF_VINCULANTE", "label": "STF (ADC / ADI / ADPF / Rep. Geral)"},
            {"id": "TESES_STJ", "label": "Jurisprudência em Teses STJ"},
            {"id": "FONAVID", "label": "Enunciados FONAVID"},
            {"id": "TJRN", "label": "Jurisprudência do TJRN"},
            {"id": "NORMAS_CNJ", "label": "Resoluções CNJ"},
            {"id": "GABINETE", "label": "Precedentes do Gabinete"},
        ],
        "tribunais_reconhecidos": sorted(TRIBUNAIS_RECONHECIDOS),
        "estatisticas": {
            "total_precedentes": len(SUMULAS_E_TEMAS_VERIFICADOS),
            "total_doutrina": len(DOUTRINA_VERIFICADA),
            "total_gabinete": len(custom_items),
        },
    }


def selecionar_por_questao_juridica(
    questao: str,
    *,
    apenas_vigentes: bool = True,
    incluir_nao_verificadas: bool = False,
    apenas_citaveis: bool = True,
    incluir_gabinete: bool = False,
    palavras_faticas: Optional[Sequence[str]] = None,
    limite: int = 10,
) -> List[Dict[str, Any]]:
    """Seleciona e ordena verbetes do acervo por questão jurídica, vigência e pertinência fática.

    ``nao_verificada`` nunca é tratada como ``vigente``: só entra quando ``incluir_nao_verificadas=True``
    ou ``apenas_vigentes=False``.
    """
    q_norm = _normalizar_busca_kb(questao)
    q_tag = q_norm.replace(" ", "_")
    q_frase = q_norm.replace("_", " ")
    fatos_norm = [_normalizar_busca_kb(p) for p in (palavras_faticas or []) if str(p).strip()]

    candidatos: List[Dict[str, Any]] = list(SUMULAS_E_TEMAS_VERIFICADOS)
    if incluir_gabinete:
        candidatos.extend(_ler_custom_items())

    ranqueados: List[tuple[int, str, Dict[str, Any]]] = []
    for it in candidatos:
        vigencia = str(it.get("vigencia") or "nao_verificada").strip().lower()
        if apenas_vigentes:
            permitidas = {"vigente", "nao_verificada"} if incluir_nao_verificadas else {"vigente"}
            if vigencia not in permitidas:
                continue
        if apenas_citaveis and not item_citavel_automaticamente(it):
            continue

        score = 0
        tags = {_normalizar_busca_kb(str(t)).replace(" ", "_") for t in (it.get("questoes_juridicas") or [])}
        if q_tag and q_tag in tags:
            score += 10
        prop = _normalizar_busca_kb(str(it.get("proposicao_juridica") or ""))
        recorte = _normalizar_busca_kb(str(it.get("recorte_fatico") or ""))
        titulo = _normalizar_busca_kb(f"{it.get('titulo', '')} {it.get('materia', '')}")
        texto = _normalizar_busca_kb(str(it.get("texto_literal") or it.get("sintese") or ""))

        if q_frase:
            if q_frase in prop:
                score += 5
            if q_frase in recorte:
                score += 4
            if q_frase in titulo:
                score += 3
            if q_frase in texto:
                score += 2

        if score == 0:
            continue

        for fat in fatos_norm:
            if fat in recorte:
                score += 4
            elif fat in prop or fat in texto:
                score += 2
        if vigencia == "vigente":
            score += 2
        if it.get("natureza") == "LITERAL":
            score += 1

        ranqueados.append((score, str(it.get("id", "")), it))

    ranqueados.sort(key=lambda x: (-x[0], x[1]))
    return [it for _, _, it in ranqueados[: max(1, int(limite))]]


def _escrever_bytes_atomico(destino: Path, conteudo: bytes) -> None:
    """Grava bytes em ``destino`` via arquivo temporário no mesmo diretório + ``fsync`` + ``os.replace``."""
    destino.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(destino.parent), prefix=f".{destino.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(conteudo)
            fh.flush()
            os.fsync(fh.fileno())
        ultimo_erro: Optional[Exception] = None
        for tentativa in range(5):
            try:
                os.replace(tmp, destino)
                return
            except PermissionError as exc:  # Windows: arquivo momentaneamente aberto por outro processo
                ultimo_erro = exc
                time.sleep(0.05 * (tentativa + 1))
        if ultimo_erro is not None:
            raise ultimo_erro
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


def _arquivo_backup_gabinete() -> Path:
    """Caminho do backup ``custom_precedentes.bak.json`` no mesmo diretório de ``CUSTOM_KB_FILE``."""
    return CUSTOM_KB_FILE.with_name("custom_precedentes.bak.json")


def _salvar_custom_items_com_backup(items: List[Dict[str, Any]]) -> Optional[Path]:
    """Cria/atualiza ``custom_precedentes.bak.json`` antes de gravar ``CUSTOM_KB_FILE`` atomicamente."""
    backup_criado: Optional[Path] = None
    if CUSTOM_KB_FILE.exists():
        destino_bak = _arquivo_backup_gabinete()
        _escrever_bytes_atomico(destino_bak, CUSTOM_KB_FILE.read_bytes())
        backup_criado = destino_bak
    _escrever_json_atomico(CUSTOM_KB_FILE, items)
    _marcar_acervo_alterado()
    return backup_criado


def _remover_acentos(texto: str) -> str:
    decomposed = unicodedata.normalize("NFKD", texto or "")
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def normalizar_tribunal(tribunal: Optional[str]) -> str:
    """Valida e normaliza a sigla do tribunal contra ``TRIBUNAIS_RECONHECIDOS``."""
    bruto = (tribunal or "").strip()
    if not bruto:
        raise ValueError("Tribunal do precedente é obrigatório.")
    canon = re.sub(r"[\s\-_/\.]+", "", _remover_acentos(bruto)).upper()
    if canon not in TRIBUNAIS_RECONHECIDOS:
        exemplos = "STF, STJ, TJRN, CNJ, FONAVID, TRF1–TRF6 ou TJ estadual (ex.: TJSP, TJMG)"
        raise ValueError(f"Tribunal não reconhecido: '{bruto}'. Informe uma sigla válida ({exemplos}).")
    return canon


def _extrair_tribunal_da_referencia(texto_sem_acento: str) -> Optional[str]:
    """Tenta identificar uma sigla de tribunal reconhecida ao final ou corpo da referência."""
    tokens = re.findall(r"[A-Za-z0-9]+", texto_sem_acento.upper())
    for tok in reversed(tokens):
        if tok in TRIBUNAIS_RECONHECIDOS:
            return tok
    return None


def normalizar_referencia_precedente(referencia: str, tribunal: Optional[str] = None) -> str:
    """Normaliza a referência para formato canônico legível (ex.: ``Súmula 231/STJ``, ``Tema 1197/STJ``).

    Preserva referências de formatos livres (como acórdãos ou enunciados próprios) apenas
    colapsando espaços redundantes.
    """
    limpa = re.sub(r"\s+", " ", (referencia or "").strip())
    if not limpa:
        return ""

    trib_fallback = None
    if tribunal:
        try:
            trib_fallback = normalizar_tribunal(tribunal)
        except ValueError:
            trib_fallback = None

    sem_acento = _remover_acentos(limpa)

    # Súmula Vinculante (STF)
    m_sv = re.fullmatch(
        r"(?:sumula\s+vinculante|sv)\s*(?:n[\.º°o]*)?\s*(\d+)(?:\s*(?:[/\-]|do|da|de)?\s*([a-z0-9\-]+))?",
        sem_acento,
        flags=re.IGNORECASE,
    )
    if m_sv:
        num = str(int(m_sv.group(1)))
        return f"Súmula Vinculante {num}/STF"

    # Súmula (STJ, STF, etc.)
    m_sum = re.fullmatch(
        r"sumula\s*(?:n[\.º°o]*)?\s*(\d+)(?:\s*(?:[/\-]|do|da|de)?\s*([a-z0-9\-]+))?",
        sem_acento,
        flags=re.IGNORECASE,
    )
    if m_sum:
        num = str(int(m_sum.group(1)))
        trib = _extrair_tribunal_da_referencia(m_sum.group(2) or "") or trib_fallback
        return f"Súmula {num}/{trib}" if trib else f"Súmula {num}"

    # Tema / Tema Repetitivo / Repercussão Geral
    m_tema = re.fullmatch(
        r"tema(?:\s+repetitivo|\s+de\s+repercussao\s+geral|\s+rg)?\s*(?:n[\.º°o]*)?\s*([\d\.]+)"
        r"(?:\s*(?:[/\-]|do|da|de)?\s*([a-z0-9\-]+))?",
        sem_acento,
        flags=re.IGNORECASE,
    )
    if m_tema:
        num = str(int(m_tema.group(1).replace(".", "")))
        trib = _extrair_tribunal_da_referencia(m_tema.group(2) or "") or trib_fallback
        return f"Tema {num}/{trib}" if trib else f"Tema {num}"

    # Ações de Controle Concentrado (ADC, ADI, ADPF, ACO)
    m_acao = re.fullmatch(
        r"(adc|adi|adpf|aco)\s*(?:n[\.º°o]*)?\s*([\d\.]+)(?:\s*(?:[/\-]|do|da|de)?\s*([a-z0-9\-]+))?",
        sem_acento,
        flags=re.IGNORECASE,
    )
    if m_acao:
        sigla = m_acao.group(1).upper()
        num = str(int(m_acao.group(2).replace(".", "")))
        trib = _extrair_tribunal_da_referencia(m_acao.group(3) or "") or trib_fallback or "STF"
        return f"{sigla} {num}/{trib}"

    # Enunciado FONAVID
    m_fon = re.fullmatch(
        r"enunciado(?:\s+fonavid)?\s*(?:n[\.º°o]*)?\s*(\d+)(?:\s*(?:[/\-]|do|da|de)?\s*fonavid)?",
        sem_acento,
        flags=re.IGNORECASE,
    )
    if m_fon and ("fonavid" in sem_acento.lower() or trib_fallback == "FONAVID"):
        num = str(int(m_fon.group(1)))
        return f"Enunciado {num}/FONAVID"

    # Resolução CNJ
    m_res = re.fullmatch(
        r"resolucao(?:\s+cnj)?\s*(?:n[\.º°o]*)?\s*(\d+(?:/\d{2,4})?)(?:\s*(?:[/\-]|do|da|de)?\s*cnj)?",
        sem_acento,
        flags=re.IGNORECASE,
    )
    if m_res and ("cnj" in sem_acento.lower() or trib_fallback == "CNJ"):
        return f"Resolução CNJ nº {m_res.group(1)}"

    return limpa


def _chave_duplicidade_referencia(referencia: str, tribunal: Optional[str] = None) -> str:
    """Chave canônica usada na detecção de duplicidade (insensível a acentos, caixa e variações)."""
    norm = normalizar_referencia_precedente(referencia, tribunal)
    sem_acento = _remover_acentos(norm).lower()
    chave = re.sub(r"[^a-z0-9]+", " ", sem_acento).strip()
    return chave


def _eh_item_oficial(item_id: str) -> bool:
    alvo = (item_id or "").strip().upper()
    if not alvo:
        return False
    return any(str(it.get("id", "")).upper() == alvo for it in SUMULAS_E_TEMAS_VERIFICADOS)


def _validar_dados_precedente(
    *,
    referencia: str,
    titulo: str,
    texto_literal: str,
    tipo: str,
    tribunal: str,
    materia: str,
) -> Dict[str, str]:
    """Valida campos obrigatórios, tribunal reconhecido e tamanho mínimo do texto literal.

    Nunca altera silenciosamente o conteúdo de ``texto_literal`` (apenas remove espaços nas pontas).
    """
    ref_bruta = (referencia or "").strip()
    if not ref_bruta:
        raise ValueError("Referência do precedente é obrigatória.")

    titulo_limpo = (titulo or "").strip()
    if not titulo_limpo:
        raise ValueError("Título do precedente é obrigatório.")

    texto_limpo = (texto_literal or "").strip()
    if not texto_limpo:
        raise ValueError("Texto literal do precedente é obrigatório.")
    if len(texto_limpo) < MIN_TEXTO_LITERAL_CHARS:
        raise ValueError(
            f"Texto literal muito curto (mínimo de {MIN_TEXTO_LITERAL_CHARS} caracteres; "
            f"informado: {len(texto_limpo)})."
        )

    tribunal_norm = normalizar_tribunal(tribunal)
    ref_norm = normalizar_referencia_precedente(ref_bruta, tribunal_norm)
    tipo_limpo = (tipo or "PRECEDENTE_TJ").strip() or "PRECEDENTE_TJ"
    materia_limpa = (materia or "Violência Doméstica e Familiar").strip() or "Violência Doméstica e Familiar"

    return {
        "referencia": ref_norm,
        "titulo": titulo_limpo,
        "texto_literal": texto_limpo,
        "tipo": tipo_limpo,
        "tribunal": tribunal_norm,
        "materia": materia_limpa,
    }


def _encontrar_duplicado(
    referencia: str,
    tribunal: str,
    custom_items: List[Dict[str, Any]],
    *,
    ignorar_id: Optional[str] = None,
) -> Optional[Tuple[Dict[str, Any], str]]:
    """Procura precedente equivalente no acervo oficial e no acervo do gabinete."""
    chave_alvo = _chave_duplicidade_referencia(referencia, tribunal)
    ignorar_upper = (ignorar_id or "").strip().upper()

    for oficial in SUMULAS_E_TEMAS_VERIFICADOS:
        if _chave_duplicidade_referencia(oficial.get("referencia", ""), oficial.get("tribunal")) == chave_alvo:
            return oficial, "oficial"

    for item in custom_items:
        if ignorar_upper and str(item.get("id", "")).upper() == ignorar_upper:
            continue
        if _chave_duplicidade_referencia(item.get("referencia", ""), item.get("tribunal")) == chave_alvo:
            return item, "gabinete"

    return None


def _proximo_id_gabinete(ids_existentes: set[str]) -> str:
    maior = 0
    for ident in ids_existentes:
        m = re.fullmatch(r"GAB-(\d+)", ident)
        if m:
            maior = max(maior, int(m.group(1)))
    proximo = maior + 1
    while f"GAB-{proximo:03d}" in ids_existentes:
        proximo += 1
    return f"GAB-{proximo:03d}"


def _verificar_citacao_em_versoes(
    item: Dict[str, Any], versoes_salvas: Optional[List[Dict[str, Any]]]
) -> List[Dict[str, Any]]:
    """Retorna metadados das versões salvas que citam a referência ou ID do precedente."""
    if not versoes_salvas:
        return []
    ref = str(item.get("referencia", "") or "").strip()
    item_id = str(item.get("id", "") or "").strip()
    ref_sem_acento = _remover_acentos(ref).lower()
    chave_ref = _chave_duplicidade_referencia(ref, item.get("tribunal"))
    citadas: List[Dict[str, Any]] = []
    for versao in versoes_salvas:
        texto = str(versao.get("texto_completo", "") or "")
        if not texto:
            continue
        texto_norm = _remover_acentos(texto).lower()
        texto_alnum = re.sub(r"[^a-z0-9]+", " ", texto_norm)
        encontrou = False
        if ref_sem_acento and ref_sem_acento in texto_norm:
            encontrou = True
        elif item_id and item_id.lower() in texto_norm:
            encontrou = True
        elif chave_ref and len(chave_ref) >= 6 and chave_ref in texto_alnum:
            encontrou = True
        if encontrou:
            citadas.append(
                {
                    "id": versao.get("id"),
                    "numero": versao.get("numero"),
                    "rotulo": versao.get("rotulo"),
                    "criado_em": versao.get("criado_em"),
                }
            )
    return citadas


def add_custom_precedente(
    referencia: str,
    titulo: str,
    texto_literal: str,
    tipo: str = "PRECEDENTE_TJ",
    tribunal: str = "TJRN",
    materia: str = "Violência Doméstica e Familiar",
    *,
    proposicao_juridica: Optional[str] = None,
    recorte_fatico: Optional[str] = None,
    vigencia: Optional[str] = None,
    data_revisao: Optional[str] = None,
    questoes_juridicas: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    """
    Adiciona um precedente ou enunciado customizado pelo magistrado à base JSON fechada.

    Valida campos obrigatórios, tribunal reconhecido, tamanho mínimo do texto literal e
    duplicidade contra o acervo oficial e o acervo do gabinete (``PrecedenteDuplicadoError``).
    O ID é único (``GAB-NNN``), cria backup ``custom_precedentes.bak.json`` antes de gravar
    atomicamente e invalida o cache do auditor (``versao_acervo`` muda).
    """
    validado = _validar_dados_precedente(
        referencia=referencia,
        titulo=titulo,
        texto_literal=texto_literal,
        tipo=tipo,
        tribunal=tribunal,
        materia=materia,
    )

    with _KB_LOCK:
        custom_items = _ler_custom_items()
        duplicado = _encontrar_duplicado(validado["referencia"], validado["tribunal"], custom_items)
        if duplicado is not None:
            item_existente, origem = duplicado
            local = "acervo oficial" if origem == "oficial" else "acervo do gabinete"
            raise PrecedenteDuplicadoError(
                f"Precedente '{validado['referencia']}' já existe no {local} ({item_existente.get('id')}).",
                item_existente=item_existente,
                origem=origem,
            )

        ids_existentes = {str(it.get("id", "")).upper() for it in custom_items}
        ids_existentes |= {str(it.get("id", "")).upper() for it in SUMULAS_E_TEMAS_VERIFICADOS}
        novo_id = _proximo_id_gabinete(ids_existentes)

        vig_norm = str(vigencia or "").strip().lower()
        novo: Dict[str, Any] = {
            "id": novo_id,
            "tipo": validado["tipo"],
            "categoria": "GABINETE",
            "tribunal": validado["tribunal"],
            "referencia": validado["referencia"],
            "titulo": validado["titulo"],
            "texto_literal": validado["texto_literal"],
            "materia": validado["materia"],
            "proposicao_juridica": (proposicao_juridica or "").strip() or validado["texto_literal"],
            "proposicao_origem": "revisada" if (proposicao_juridica or "").strip() else "texto_literal",
            "recorte_fatico": (recorte_fatico or "").strip(),
            "vigencia": vig_norm if vig_norm in VIGENCIAS_VALIDAS else "nao_verificada",
            "data_revisao": (data_revisao or "").strip(),
            "questoes_juridicas": [str(q).strip() for q in (questoes_juridicas or []) if str(q).strip()],
        }
        if not novo["questoes_juridicas"]:
            novo["questoes_juridicas"] = _inferir_questoes_juridicas(novo)
        custom_items.append(novo)
        _salvar_custom_items_com_backup(custom_items)
    return novo


def update_custom_precedente(
    item_id: str,
    *,
    referencia: Optional[str] = None,
    titulo: Optional[str] = None,
    texto_literal: Optional[str] = None,
    tipo: Optional[str] = None,
    tribunal: Optional[str] = None,
    materia: Optional[str] = None,
) -> Dict[str, str]:
    """Edita um precedente do gabinete. Recusa itens do acervo oficial (403)."""
    if _eh_item_oficial(item_id):
        raise PrecedenteOficialReadOnlyError(
            f"O item '{item_id}' pertence ao acervo oficial e é somente leitura; não pode ser editado."
        )

    alvo_upper = (item_id or "").strip().upper()
    with _KB_LOCK:
        custom_items = _ler_custom_items()
        indice_alvo: Optional[int] = None
        for idx, it in enumerate(custom_items):
            if str(it.get("id", "")).upper() == alvo_upper:
                indice_alvo = idx
                break
        if indice_alvo is None:
            raise PrecedenteNaoEncontradoError(f"Precedente do gabinete '{item_id}' não encontrado.")

        atual = custom_items[indice_alvo]
        validado = _validar_dados_precedente(
            referencia=referencia if referencia is not None else atual.get("referencia", ""),
            titulo=titulo if titulo is not None else atual.get("titulo", ""),
            texto_literal=texto_literal if texto_literal is not None else atual.get("texto_literal", ""),
            tipo=tipo if tipo is not None else atual.get("tipo", "PRECEDENTE_TJ"),
            tribunal=tribunal if tribunal is not None else atual.get("tribunal", "TJRN"),
            materia=materia if materia is not None else atual.get("materia", "Violência Doméstica e Familiar"),
        )

        duplicado = _encontrar_duplicado(
            validado["referencia"],
            validado["tribunal"],
            custom_items,
            ignorar_id=atual.get("id"),
        )
        if duplicado is not None:
            item_existente, origem = duplicado
            local = "acervo oficial" if origem == "oficial" else "acervo do gabinete"
            raise PrecedenteDuplicadoError(
                f"Precedente '{validado['referencia']}' já existe no {local} ({item_existente.get('id')}).",
                item_existente=item_existente,
                origem=origem,
            )

        atualizado = {
            **atual,
            "id": atual["id"],
            "categoria": "GABINETE",
            "tipo": validado["tipo"],
            "tribunal": validado["tribunal"],
            "referencia": validado["referencia"],
            "titulo": validado["titulo"],
            "texto_literal": validado["texto_literal"],
            "materia": validado["materia"],
        }
        custom_items[indice_alvo] = atualizado
        _salvar_custom_items_com_backup(custom_items)
    return atualizado


def delete_custom_precedente(
    item_id: str,
    *,
    versoes_salvas: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Exclui um precedente do gabinete. Recusa itens oficiais (403).

    Se o precedente for citado em versões salvas da sessão, não bloqueia a exclusão,
    mas retorna um ``aviso`` claro e a lista ``citado_em_versoes``.
    """
    if _eh_item_oficial(item_id):
        raise PrecedenteOficialReadOnlyError(
            f"O item '{item_id}' pertence ao acervo oficial e é somente leitura; não pode ser excluído."
        )

    alvo_upper = (item_id or "").strip().upper()
    with _KB_LOCK:
        custom_items = _ler_custom_items()
        indice_alvo: Optional[int] = None
        for idx, it in enumerate(custom_items):
            if str(it.get("id", "")).upper() == alvo_upper:
                indice_alvo = idx
                break
        if indice_alvo is None:
            raise PrecedenteNaoEncontradoError(f"Precedente do gabinete '{item_id}' não encontrado.")

        removido = custom_items.pop(indice_alvo)
        versoes_citadas = _verificar_citacao_em_versoes(removido, versoes_salvas)
        _salvar_custom_items_com_backup(custom_items)

    aviso: Optional[str] = None
    if versoes_citadas:
        ids_v = ", ".join(str(v.get("id") or f"v{v.get('numero')}") for v in versoes_citadas)
        aviso = (
            f"O precedente '{removido.get('referencia')}' está citado em versões salvas ({ids_v}). "
            "A auditoria anterior permanece válida no histórico, mas novas auditorias não o encontrarão na base."
        )
    return {
        "removido": removido,
        "citado_em_versoes": versoes_citadas,
        "aviso": aviso,
    }


def exportar_precedentes_gabinete() -> Dict[str, Any]:
    """Exporta os precedentes do gabinete em JSON versionado."""
    with _KB_LOCK:
        itens = _ler_custom_items()
    return {
        "formato_versao": FORMATO_EXPORTACAO_VERSAO,
        "exportado_em": datetime.now(timezone.utc).isoformat(),
        "total": len(itens),
        "itens": itens,
    }


def importar_precedentes_gabinete(
    payload: Dict[str, Any],
    *,
    confirmar: bool = False,
) -> Dict[str, Any]:
    """Valida e (quando ``confirmar=True``) importa precedentes do gabinete.

    Em modo prévia (``confirmar=False``), não grava nada em disco e lista ``novos``,
    ``duplicados_ignorados`` e ``invalidos``. Em modo confirmado, ignora duplicados e
    inválidos, gera backup ``custom_precedentes.bak.json`` e grava atomicamente os novos itens.
    """
    if not isinstance(payload, dict):
        raise ValueError("Payload de importação deve ser um objeto JSON.")

    versao = payload.get("formato_versao")
    if versao != FORMATO_EXPORTACAO_VERSAO:
        raise ValueError(
            f"Versão de formato não suportada: {versao!r} (esperado: {FORMATO_EXPORTACAO_VERSAO})."
        )

    itens_brutos = payload.get("itens")
    if not isinstance(itens_brutos, list):
        raise ValueError("Campo 'itens' deve ser uma lista de precedentes.")
    if len(itens_brutos) > 5000:
        raise ValueError(
            f"A importação excede o limite de 5.000 itens por vez (recebido: {len(itens_brutos)})."
        )

    with _KB_LOCK:
        custom_items = _ler_custom_items()
        novos_validados: List[Dict[str, str]] = []
        duplicados_ignorados: List[Dict[str, Any]] = []
        invalidos: List[Dict[str, Any]] = []
        chaves_lote: Dict[str, Dict[str, str]] = {}

        for idx, bruto in enumerate(itens_brutos):
            if not isinstance(bruto, dict):
                invalidos.append({"indice": idx, "referencia": "", "erro": "Item não é um objeto JSON."})
                continue
            try:
                validado = _validar_dados_precedente(
                    referencia=str(bruto.get("referencia", "") or ""),
                    titulo=str(bruto.get("titulo", "") or ""),
                    texto_literal=str(bruto.get("texto_literal", "") or ""),
                    tipo=str(bruto.get("tipo", "PRECEDENTE_TJ") or "PRECEDENTE_TJ"),
                    tribunal=str(bruto.get("tribunal", "TJRN") or "TJRN"),
                    materia=str(
                        bruto.get("materia", "Violência Doméstica e Familiar")
                        or "Violência Doméstica e Familiar"
                    ),
                )
            except ValueError as exc:
                invalidos.append(
                    {
                        "indice": idx,
                        "referencia": str(bruto.get("referencia", "") or ""),
                        "erro": str(exc),
                    }
                )
                continue

            dup = _encontrar_duplicado(validado["referencia"], validado["tribunal"], custom_items)
            if dup is not None:
                item_existente, origem = dup
                duplicados_ignorados.append(
                    {
                        "indice": idx,
                        "referencia": validado["referencia"],
                        "origem_existente": origem,
                        "item_existente": item_existente,
                    }
                )
                continue

            chave = _chave_duplicidade_referencia(validado["referencia"], validado["tribunal"])
            if chave in chaves_lote:
                duplicados_ignorados.append(
                    {
                        "indice": idx,
                        "referencia": validado["referencia"],
                        "origem_existente": "lote_importacao",
                        "item_existente": chaves_lote[chave],
                    }
                )
                continue

            chaves_lote[chave] = validado
            novos_validados.append(validado)

        if not confirmar:
            return {
                "modo": "previa",
                "gravado": False,
                "formato_versao": FORMATO_EXPORTACAO_VERSAO,
                "total_recebidos": len(itens_brutos),
                "novos": novos_validados,
                "duplicados_ignorados": duplicados_ignorados,
                "invalidos": invalidos,
            }

        adicionados: List[Dict[str, str]] = []
        if novos_validados:
            ids_existentes = {str(it.get("id", "")).upper() for it in custom_items}
            ids_existentes |= {str(it.get("id", "")).upper() for it in SUMULAS_E_TEMAS_VERIFICADOS}
            for item_v in novos_validados:
                novo_id = _proximo_id_gabinete(ids_existentes)
                ids_existentes.add(novo_id)
                registro = {
                    "id": novo_id,
                    "tipo": item_v["tipo"],
                    "categoria": "GABINETE",
                    "tribunal": item_v["tribunal"],
                    "referencia": item_v["referencia"],
                    "titulo": item_v["titulo"],
                    "texto_literal": item_v["texto_literal"],
                    "materia": item_v["materia"],
                }
                custom_items.append(registro)
                adicionados.append(registro)
            _salvar_custom_items_com_backup(custom_items)

        return {
            "modo": "confirmado",
            "gravado": bool(adicionados),
            "formato_versao": FORMATO_EXPORTACAO_VERSAO,
            "total_recebidos": len(itens_brutos),
            "adicionados": adicionados,
            "duplicados_ignorados": duplicados_ignorados,
            "invalidos": invalidos,
            "total_gabinete": len(custom_items),
        }


def buscar_precedente_por_id_ou_referencia(identificador: str) -> Optional[Dict[str, str]]:
    """Localiza qualquer item do acervo (oficial ou do gabinete) por ID ou por referência literal."""
    id_norm = identificador.strip().upper()
    kb = get_all_knowledge_base()
    todos = kb["sumulas_e_temas"] + kb["precedentes_gabinete"]
    for item in todos:
        if item.get("id", "").upper() == id_norm or item.get("referencia", "").upper() == id_norm:
            return item
    return None
