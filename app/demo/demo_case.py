from typing import Any, Dict, List


def get_demo_case_bundle() -> Dict[str, Any]:
    """
    Retorna um processo criminal completo e realista do Juizado de Violência Doméstica e Familiar contra a Mulher,
    cobrindo desde o Inquérito Policial e Medidas Protetivas até a Audiência de Instrução (com transcrições) e Alegações Finais.
    """
    raw_documents: List[Dict[str, Any]] = [
        {
            "doc_id": "ID-4100101",
            "folha": "fls. 02/05",
            "titulo": "Auto de Prisão em Flagrante e Boletim de Ocorrência nº 2025/084192 - DEAM",
            "data_peca": "2025-02-14",
            "fase": "INQUISITORIAL",
            "ocr_aplicado": True,
            "conteudo": (
                "BOLETIM DE OCORRÊNCIA Nº 2025/084192 - DELEGACIA ESPECIALIZADA DE ATENDIMENTO À MULHER (DEAM). "
                "Data do Fato: 14/02/2025, por volta das 21h30min. "
                "Comunicante/Ofendida: MARIANA ALVES FERREIRA. Indiciado: CARLOS EDUARDO ROCHA MENDES (ex-companheiro, conviveram em união estável por 6 anos, "
                "possuem uma filha comum de 5 anos). "
                "Histórico Policial: Compareceu a guarnição da Polícia Militar (Cb. PM Roberto Silva) acionada via 190 para atendimento de ocorrência de "
                "violência doméstica na residência da vítima. No local, encontraram a ofendida Mariana Alves Ferreira chorando, com hematoma visível na região "
                "zigomática esquerda e escoriações no antebraço direito. A vítima relatou na Delegacia que o ex-companheiro Carlos Eduardo Rocha Mendes invadiu o "
                "quintal do imóvel descumprindo Medida Protetiva de Urgência anteriormente deferida nos autos nº 0801122-44.2025.8.00.0001, desferiu-lhe um soco "
                "no rosto e segurou-a com força pelos braços, além de proferir ameaça de morte dizendo: 'Se você não retirar a queixa e voltar para mim, eu volto "
                "aqui e acabo com a sua vida'. O indiciado foi detido a duas quadras do local."
            ),
        },
        {
            "doc_id": "ID-4100108",
            "folha": "fls. 12/14",
            "titulo": "Laudo de Exame de Corpo de Delito - Lesão Corporal nº 1194/2025 - IML",
            "data_peca": "2025-02-15",
            "fase": "INQUISITORIAL",
            "ocr_aplicado": True,
            "conteudo": (
                "INSTITUTO MÉDICO LEGAL - LAUDO PERICIAL DE EXAME DE CORPO DE DELITO Nº 1194/2025. "
                "Periciada: MARIANA ALVES FERREIRA. Exame realizado em 15/02/2025 às 00h40min. "
                "Descrição das Lesões: Equimose violácea de 4,5 cm x 3,0 cm em região malar/zigomática esquerda, acompanhada de edema traumático local; "
                "escoriações lineares recentes em terço médio do antebraço direito. "
                "Quesitos: 1º Houve ofensa à integridade corporal ou à saúde da paciente? RESPOSTA: SIM. "
                "2º Qual o instrumento ou meio que produziu a ofensa? RESPOSTA: Ação contundente (compatível com golpe de punho/contusão direta e contenção manual)."
            ),
        },
        {
            "doc_id": "ID-4100115",
            "folha": "fls. 19/21",
            "titulo": "Decisão Concessiva de Medidas Protetivas de Urgência (Autos em Apenso nº 0801122-44.2025.8.00.0001)",
            "data_peca": "2025-01-20",
            "fase": "MEDIDA_PROTETIVA",
            "ocr_aplicado": False,
            "conteudo": (
                "DECISÃO DE MEDIDAS PROTETIVAS DE URGÊNCIA (Art. 22 da Lei nº 11.340/2006). "
                "Em 20/01/2025, este Juizado deferiu em favor de MARIANA ALVES FERREIRA as medidas protetivas de urgência consistentes em: "
                "(a) proibição de aproximação da ofendida e de seus familiares, fixando o limite mínimo de 300 (trezentos) metros de distância; "
                "(b) proibição de manter qualquer espécie de contato com a ofendida por qualquer meio de comunicação."
            ),
        },
        {
            "doc_id": "ID-4100118",
            "folha": "fls. 22/23",
            "titulo": "Mandado e Certidão Positiva de Intimação Pessoal do Requerido acerca das Medidas Protetivas",
            "data_peca": "2025-01-22",
            "fase": "MEDIDA_PROTETIVA",
            "ocr_aplicado": True,
            "conteudo": (
                "CERTIDÃO DO OFICIAL DE JUSTIÇA. Certifico e dou fé que, em 22/01/2025, às 14h15min, intimei pessoalmente o requerido "
                "CARLOS EDUARDO ROCHA MENDES do inteiro teor da decisão de ID-4100115 que concedeu as Medidas Protetivas de Urgência (proibição de aproximação a "
                "menos de 300 metros e proibição de contato com a vítima Mariana Alves Ferreira), tendo o requerido exarado sua assinatura de ciente no mandado "
                "e recebido contrafé, advertido das penas do crime de descumprimento de medida protetiva (art. 24-A da Lei nº 11.340/2006)."
            ),
        },
        {
            "doc_id": "ID-4100201",
            "folha": "fls. 35/39",
            "titulo": "Denúncia do Ministério Público Estadual",
            "data_peca": "2025-02-25",
            "fase": "JUDICIAL",
            "ocr_aplicado": False,
            "conteudo": (
                "MINISTÉRIO PÚBLICO ESTADUAL - PROMOTORIA DE JUSTIÇA DE VIOLÊNCIA DOMÉSTICA. "
                "Processo nº 0804590-12.2025.8.00.0001. "
                "O MINISTÉRIO PÚBLICO oferece DENÚNCIA contra CARLOS EDUARDO ROCHA MENDES, brasileiro, qualificado nos autos, pela prática dos seguintes fatos delituosos: "
                "1º FATO (Descumprimento de Medida Protetiva - art. 24-A da Lei nº 11.340/2006): No dia 14/02/2025, às 21h30min, o denunciado, de forma livre e consciente, "
                "descumpriu decisão judicial que deferiu medidas protetivas de urgência (ID-4100115), da qual fora pessoalmente intimado em 22/01/2025 (ID-4100118), "
                "ao dirigir-se até a residência da ex-companheira Mariana Alves Ferreira, aproximando-se a menos de 300 metros. "
                "2º FATO (Lesão Corporal contra a Mulher - art. 129, § 13, do Código Penal): Nas mesmas circunstâncias de tempo e local, na presença da filha menor do casal, "
                "o denunciado ofendeu a integridade corporal da vítima Mariana Alves Ferreira, desferindo-lhe um soco na face e apertando-lhe os braços, causando-lhe as lesões "
                "descritas no Laudo Pericial de ID-4100108. "
                "3º FATO (Ameaça contra a Mulher - art. 147, caput e § 1º, do Código Penal): Ato contínuo, o denunciado ameaçou a ofendida, por palavra, de causar-lhe mal "
                "injusto e grave, afirmando que voltaria para acabar com a vida dela caso não retirasse a queixa. "
                "PEDIDO EXPRESSO DE REPARAÇÃO DE DANOS: Requer a condenação do denunciado nas penas do art. 129, § 13, do CP, art. 147, § 1º, do CP e art. 24-A da Lei nº 11.340/2006, "
                "em concurso material (art. 69 do CP), bem como a fixação de valor mínimo para reparação dos danos morais sofridos pela ofendida (art. 387, IV, do CPP e Tema 983/STJ) "
                "em patamar não inferior a R$ 3.000,00 (três mil reais)."
            ),
        },
        {
            "doc_id": "ID-4100210",
            "folha": "fls. 41",
            "titulo": "Decisão de Recebimento da Denúncia",
            "data_peca": "2025-02-28",
            "fase": "JUDICIAL",
            "ocr_aplicado": False,
            "conteudo": (
                "DECISÃO: Presentes a prova da materialidade e indícios suficientes de autoria, não se vislumbrando as hipóteses do art. 395 do CPP, "
                "RECEBO A DENÚNCIA oferecida em face de CARLOS EDUARDO ROCHA MENDES em 28/02/2025. Cite-se o acusado para responder à acusação no prazo de 10 dias."
            ),
        },
        {
            "doc_id": "ID-4100230",
            "folha": "fls. 48/51",
            "titulo": "Citação Pessoal e Resposta à Acusação (Art. 396-A do CPP)",
            "data_peca": "2025-03-14",
            "fase": "JUDICIAL",
            "ocr_aplicado": False,
            "conteudo": (
                "RESPOSTA À ACUSAÇÃO. O acusado Carlos Eduardo Rocha Mendes, regularmente citado em 06/03/2025, apresenta Resposta à Acusação reservando-se ao "
                "enfrentamento do mérito após a instrução probatória e arrolando uma testemunha abonatória."
            ),
        },
        {
            "doc_id": "ID-4100300",
            "folha": "fls. 62/64",
            "titulo": "Termo de Audiência de Instrução e Julgamento (com Gravação Audiovisual PJe Mídias)",
            "data_peca": "2025-06-18",
            "fase": "JUDICIAL",
            "ocr_aplicado": False,
            "conteudo": (
                "TERMO DE AUDIÊNCIA DE INSTRUÇÃO E JULGAMENTO. Em 18/06/2025, aberta a audiência, foram colhidos, mediante registro audiovisual (PJe Mídias): "
                "1) Declarações da ofendida MARIANA ALVES FERREIRA (Mídia ID-4100301, duração 06min42s) - colhidas sem a presença física do réu na sala, "
                "assegurada a transmissão à defesa técnica (art. 217 do CPP e Resolução CNJ nº 492/2023); "
                "2) Depoimento da testemunha policial Cb. PM ROBERTO SILVA (Mídia ID-4100302, duração 04min15s); "
                "3) Depoimento da testemunha de defesa JOÃO BATISTA SOARES (Mídia ID-4100303, duração 02min50s); "
                "4) Interrogatório do acusado CARLOS EDUARDO ROCHA MENDES (Mídia ID-4100304, duração 05min30s)."
            ),
        },
        {
            "doc_id": "ID-4100350",
            "folha": "fls. 70/72",
            "titulo": "Certidão e Folha de Antecedentes Criminais (CAC / FAC)",
            "data_peca": "2025-07-02",
            "fase": "JUDICIAL",
            "ocr_aplicado": False,
            "conteudo": (
                "CERTIDÃO DE ANTECEDENTES CRIMINAIS (CAC/SINIC). Certifico que, revendo os assentamentos criminais em nome de CARLOS EDUARDO ROCHA MENDES, "
                "CONSTA apenas 01 (um) inquérito policial em andamento sem denúncia ou sentença transitada em julgado. O réu é tecnicamente PRIMÁRIO (Súmula 444/STJ)."
            ),
        },
        {
            "doc_id": "ID-4100401",
            "folha": "fls. 78/84",
            "titulo": "Alegações Finais por Memoriais do Ministério Público",
            "data_peca": "2025-08-05",
            "fase": "JUDICIAL",
            "ocr_aplicado": False,
            "conteudo": (
                "MEMORIAIS DO MINISTÉRIO PÚBLICO. Pugna pela integral procedência da pretensão punitiva estatal para condenar CARLOS EDUARDO ROCHA MENDES nas penas "
                "do art. 129, § 13, do CP (com exasperação da pena-base na 1ª fase pela culpabilidade acentuada, eis que a agressão ocorreu diante da filha de 5 anos do casal), "
                "art. 147, § 1º, do CP e art. 24-A da Lei nº 11.340/2006, em concurso material (art. 69 do CP), fixando-se indenização mínima por danos morais em R$ 3.000,00 "
                "(Tema 983/STJ) e mantendo-se as medidas protetivas de urgência."
            ),
        },
        {
            "doc_id": "ID-4100420",
            "folha": "fls. 88/95",
            "titulo": "Alegações Finais por Memoriais da Defesa Técnica",
            "data_peca": "2025-08-19",
            "fase": "JUDICIAL",
            "ocr_aplicado": False,
            "conteudo": (
                "MEMORIAIS DA DEFESA TÉCNICA. A Defesa suscita: "
                "PRELIMINAR 1 (Inépcia da Denúncia): alega descrição genérica dos fatos na peça acusatória e ausência de justa causa. "
                "MÉRITO - TESE 1 (Insuficiência de Provas / In Dubio Pro Reo - art. 386, VII, CPP): sustenta que não havia testemunhas presenciais oculares da agressão e da ameaça, "
                "baseando-se a acusação apenas na palavra da vítima e de policial militar. "
                "MÉRITO - TESE 2 (Atipicidade da Ameaça por Ânimo Exaltado): alega que eventual discussão se deu de cabeça quente (ira momentânea), sem seriedade idônea do mal prometido. "
                "MÉRITO - TESE 3 (Atipicidade do art. 24-A da Lei 11.340/2006 por Ausência de Dolo): sustenta que o acusado foi à residência apenas para ver a filha menor e falar sobre pensão, sem intenção de violar a medida protetiva. "
                "MÉRITO - TESE 4 (Subsidiariamente - Pena no Mínimo Legal, Reconhecimento da Atenuante da Confissão Parcial, Afastamento do Dano Moral e Substituição por Restritiva de Direitos)."
            ),
        },
    ]

    transcriptions: List[Dict[str, Any]] = [
        {
            "media_id": "ID-4100301",
            "arquivo": "01_Depoimento_Ofendida_Mariana_Alves_Ferreira.mp4",
            "depoente": "MARIANA ALVES FERREIRA",
            "papel": "OFENDIDA",
            "duracao": "06:42",
            "segmentos": [
                {
                    "timestamp": "00:18 - 01:45",
                    "texto_literal": "Doutor Juiz, eu já tinha pedido a medida protetiva em janeiro porque ele não aceitava o fim do nosso relacionamento de seis anos. O oficial de justiça intimou ele, mas no dia 14 de fevereiro, por volta de nove e meia da noite, ele pulou o portão da frente da minha casa.",
                },
                {
                    "timestamp": "01:46 - 03:30",
                    "texto_literal": "Minha filha de cinco anos estava na sala comigo e começou a gritar de medo. Ele veio para cima de mim exigindo que eu tirasse a medida protetiva. Quando eu disse que ia chamar a polícia, ele me segurou com muita força pelo braço direito e me deu um soco no lado esquerdo do rosto.",
                },
                {
                    "timestamp": "03:31 - 05:10",
                    "texto_literal": "Antes de sair correndo quando ouviu a sirene da viatura, ele apontou o dedo para mim e falou que se eu não retirasse a queixa e não voltasse com ele, ele voltaria para acabar com a minha vida. Eu fiquei apavorada, passei semanas sem conseguir dormir e tive que mudar as fechaduras.",
                },
            ],
        },
        {
            "media_id": "ID-4100302",
            "arquivo": "02_Testemunha_Policial_Cb_PM_Roberto_Silva.mp4",
            "depoente": "CB. PM ROBERTO SILVA",
            "papel": "TESTEMUNHA_POLICIAL",
            "duracao": "04:15",
            "segmentos": [
                {
                    "timestamp": "00:25 - 02:10",
                    "texto_literal": "Fomos acionados via COPOM para atender ocorrência de violência doméstica com descumprimento de medida protetiva. Ao chegarmos na residência, a vítima Mariana estava muito abalada, chorando junto com a filha pequena, e apresentava o rosto já inchado e avermelhado do lado esquerdo, além de marcas no braço.",
                },
                {
                    "timestamp": "02:11 - 03:55",
                    "texto_literal": "Ela nos mostrou no celular a decisão da medida protetiva vigente e relatou na hora que o ex-companheiro Carlos Eduardo tinha acabado de agredi-la e ameaçá-la de morte. Fizemos patrulhamento e localizamos o réu a cerca de duzentos metros dali, ainda alterado.",
                },
            ],
        },
        {
            "media_id": "ID-4100303",
            "arquivo": "03_Testemunha_Defesa_Joao_Batista_Soares.mp4",
            "depoente": "JOÃO BATISTA SOARES",
            "papel": "TESTEMUNHA_DEFESA",
            "duracao": "02:50",
            "segmentos": [
                {
                    "timestamp": "00:30 - 02:20",
                    "texto_literal": "Conheço o Carlos Eduardo do trabalho na oficina há três anos. Não presenciei os fatos do dia 14 de fevereiro, só sei dizer que no serviço ele sempre foi trabalhador.",
                },
            ],
        },
        {
            "media_id": "ID-4100304",
            "arquivo": "04_Interrogatorio_Reu_Carlos_Eduardo_Rocha_Mendes.mp4",
            "depoente": "CARLOS EDUARDO ROCHA MENDES",
            "papel": "INTERROGATORIO_REU",
            "duracao": "05:30",
            "segmentos": [
                {
                    "timestamp": "00:40 - 02:15",
                    "texto_literal": "Eu sabia que tinha a medida protetiva porque o oficial de justiça me entregou o papel em janeiro, mas no dia 14 de fevereiro eu fui até a casa dela só porque queria ver minha filha e conversar sobre o relacionamento.",
                },
                {
                    "timestamp": "02:16 - 04:50",
                    "texto_literal": "Nós acabamos discutindo de cabeça quente, eu segurei no braço dela durante a discussão, mas nego que tenha dado um soco no rosto ou ameaçado matar ela de verdade.",
                },
            ],
        },
    ]

    return {
        "is_demo": True,
        "case_id": "0804590-12.2025.8.00.0001",
        "numero_processo": "0804590-12.2025.8.00.0001",
        "autos_mpu_apenso": "0801122-44.2025.8.00.0001",
        "comarca": "1º Juizado de Violência Doméstica e Familiar contra a Mulher da Capital",
        "ofendida": "MARIANA ALVES FERREIRA",
        "reu": "CARLOS EDUARDO ROCHA MENDES",
        "vinculo_domestico": "Ex-companheiro (união estável por 6 anos, com uma filha comum de 5 anos - art. 5º, III, da Lei nº 11.340/2006)",
        "data_fato": "2025-02-14",
        "data_recebimento_denuncia": "2025-02-28",
        "raw_documents": raw_documents,
        "transcriptions": transcriptions,
    }
