"""Texto do CASO DEMONSTRATIVO — fonte ÚNICA.

Este módulo concentra tudo o que é específico do processo fictício de demonstração
(``app.demo.demo_case.get_demo_case_bundle``): os parâmetros de simulação da dosimetria, os IDs
fixos citados na redação e o texto das cinco seções. ``sentence_generator`` e
``sectional_pipeline`` consomem estas funções; nenhum outro módulo deve repetir o texto do demo.

Só é usado quando o bundle traz ``"is_demo": True`` (``app.core.case_flags.is_demo_bundle``) —
nunca por detecção de prefixo de ID.
"""

from __future__ import annotations

import datetime
from typing import Any, Dict, List, Tuple

from app.generation.date_formatter import formatar_data_br, formatar_moeda_com_extenso
from app.knowledge.knowledge_base import (
    DOUTRINA_VERIFICADA,
    SUMULAS_E_TEMAS_VERIFICADOS,
    formatar_citacao_acervo,
    obter_item_oficial_por_id,
)
from app.legal_engine.dosimetria_calculator import CircunstanciaArt59, CrimeDosimetriaInput

__all__ = [
    "DEMO_CRIMES",
    "DEMO_MEDIA_IDS",
    "DEMO_MEDIA_ALIASES",
    "DEMO_IDS_DOSIMETRIA",
    "DEMO_FUNDAMENTO_LIBERDADE",
    "construir_inputs_dosimetria_demo",
    "gerar_secoes_demo",
]

#: Crimes imputados no caso demonstrativo (ordem da denúncia).
DEMO_CRIMES: Tuple[str, ...] = ("art_24a_lmp", "art_129_p13_cp", "art_147_cp")

#: Mídias da AIJ do caso demonstrativo e seus aliases canônicos (PJe Mídias).
DEMO_MEDIA_IDS: Tuple[str, ...] = ("ID-4100301", "ID-4100302", "ID-4100303", "ID-4100304")
DEMO_MEDIA_ALIASES: Tuple[str, ...] = ("ID-MIDIA-001", "ID-MIDIA-002", "ID-MIDIA-003", "ID-MIDIA-004")

#: Referências às peças do demo usadas no capítulo da dosimetria.
DEMO_IDS_DOSIMETRIA: Dict[str, str] = {
    "id_cac": "ID-4100350 (fls. 70/72)",
    "id_den": "ID-4100201 (fls. 35/39)",
    "id_mp": "ID-4100401 (fls. 78/84)",
    "id_def": "ID-4100420 (fls. 88/95)",
}

#: No demo o réu foi preso em flagrante e não há alvará nos autos: a concessão do direito de recorrer
#: em liberdade não afirma que ele "respondeu solto".
DEMO_FUNDAMENTO_LIBERDADE = (
    "5. Concedo ao réu o direito de recorrer em liberdade (art. 387, § 1º, do CPP), salvo se por outro motivo estiver preso, "
    "desde que cumpra rigorosamente as medidas protetivas vigentes, sob pena de decretação da prisão preventiva (art. 313, inciso III, do CPP)."
)

# Datas fixas do enredo (usadas apenas se a peça correspondente não trouxer a data).
_DEMO_DATA_INTIMACAO_MPU = "2025-01-22"
_DEMO_DATA_AIJ = "2025-06-18"


def construir_inputs_dosimetria_demo(
    dt_fato: datetime.date,
    *,
    culpabilidade_lesao: bool,
    motivos_lesao: bool,
    circunstancias_lesao: bool,
    consequencias_lesao: bool,
    confissao_art24a: bool,
    reincidente: bool,
    agravante_art61f: bool,
) -> List[CrimeDosimetriaInput]:
    """Parâmetros de SIMULAÇÃO da dosimetria do caso demonstrativo (fatos do enredo fictício)."""
    return [
        CrimeDosimetriaInput(
            crime_code="art_24a_lmp",
            data_fato=dt_fato,
            circunstancias_art59={
                "antecedentes": CircunstanciaArt59(
                    desfavoravel=True,
                    possui_transito_em_julgado_anterior=False,
                    fundamentacao="Anotação sem trânsito em julgado (neutralizada pela Súmula 444/STJ).",
                )
            },
            agravantes_art61_f_violencia_domestica=agravante_art61f,
            confissao_espontanea=confissao_art24a,
            reincidente=reincidente,
        ),
        CrimeDosimetriaInput(
            crime_code="art_129_p13_cp",
            data_fato=dt_fato,
            circunstancias_art59={
                "culpabilidade": CircunstanciaArt59(
                    desfavoravel=culpabilidade_lesao,
                    fundamentacao="Agressão física perpetrada na presença da filha comum do casal, de apenas 5 anos de idade, gerando maior reprovabilidade social da conduta.",
                ),
                "motivos": CircunstanciaArt59(
                    desfavoravel=motivos_lesao,
                    fundamentacao="Inconformismo possessivo com o término do relacionamento e recusa da vítima em retirar medida protetiva.",
                ),
                "circunstancias": CircunstanciaArt59(
                    desfavoravel=circunstancias_lesao,
                    fundamentacao="Invasão noturna do domicílio da vítima mediante transposição do portão.",
                ),
                "consequencias": CircunstanciaArt59(
                    desfavoravel=consequencias_lesao,
                    fundamentacao="Abalo psicológico acentuado que exigiu troca imediata das fechaduras do imóvel.",
                ),
            },
            agravantes_art61_f_violencia_domestica=agravante_art61f,
            confissao_espontanea=False,
            reincidente=reincidente,
        ),
        CrimeDosimetriaInput(
            crime_code="art_147_cp",
            data_fato=dt_fato,
            circunstancias_art59={},
            agravantes_art61_f_violencia_domestica=agravante_art61f,
            confissao_espontanea=False,
            reincidente=reincidente,
        ),
    ]


def _data_da_peca(pecas: List[Any], doc_id: str, padrao: str) -> str:
    for p in pecas or []:
        if getattr(p, "doc_id", "") == doc_id and getattr(p, "data_peca", ""):
            return p.data_peca
    return padrao


def gerar_secoes_demo(
    *,
    meta: Any,
    pecas: List[Any],
    matriz_oral: Any,
    dosimetria_global: Any,
    prescricao: Any,
    criterio_1fase: str,
    style: Dict[str, Any],
    settings: Any,
    titulo_prescricao: str,
    texto_prescricao: str,
    data_sentenca: datetime.date,
) -> Tuple[str, str, str, str, str]:
    """Relatório, preliminares, mérito, dosimetria e dispositivo do caso demonstrativo."""
    # Import tardio: sentence_generator importa este módulo.
    from app.generation.sentence_generator import (
        formatar_secao_dosimetria_completa,
        texto_dano_moral_dispositivo,
        texto_multa_dispositivo,
        texto_regime_dispositivo,
    )

    dt_fato_extenso = formatar_data_br(meta.data_fato, por_extenso=True)
    dt_intimacao_extenso = formatar_data_br(
        meta.data_intimacao_mpu or _data_da_peca(pecas, "ID-4100118", _DEMO_DATA_INTIMACAO_MPU), por_extenso=True
    )
    dt_rec_extenso = formatar_data_br(meta.data_recebimento_denuncia, por_extenso=True)
    dt_audiencia_extenso = formatar_data_br(_data_da_peca(pecas, "ID-4100300", _DEMO_DATA_AIJ), por_extenso=True)
    valor_pedido = float(getattr(meta, "valor_dano_moral_pedido_mp", 0.0) or 0.0)
    pedido_mp_txt = (
        f"fixação de indenização mínima por danos morais no valor de {formatar_moeda_com_extenso(valor_pedido)} (Tema Repetitivo 983/STJ)"
        if valor_pedido > 0
        else "fixação de indenização mínima por danos morais (Tema Repetitivo 983/STJ)"
    )

    relatorio = (
        f"I – RELATÓRIO\n\n"
        f"{style['abertura_relatorio']}\n\n"
        f"O MINISTÉRIO PÚBLICO ESTADUAL ofereceu denúncia (ID-4100201, fls. 35/39) em face de {meta.nome_reu}, devidamente qualificado nos autos, "
        f"imputando-lhe a prática dos crimes previstos no art. 24-A da Lei nº 11.340/2006 (descumprimento de medida protetiva de urgência), "
        f"no art. 129, § 13, do Código Penal (lesão corporal contra a mulher por razões da condição do sexo feminino, com a redação dada pela Lei nº 14.994/2024) "
        f"e no art. 147, caput e § 1º, do Código Penal (ameaça contra a mulher, com a redação dada pela Lei nº 14.994/2024), na forma do art. 69 do Código Penal (concurso material), "
        f"requerendo ainda a fixação de valor mínimo para reparação dos danos morais sofridos pela ofendida {meta.nome_ofendida}, nos termos do art. 387, inciso IV, do Código de Processo Penal e do Tema Repetitivo 983/STJ.\n\n"
        f"Narra a peça acusatória que, no dia {dt_fato_extenso}, por volta das 21h30min, na residência da ofendida, o denunciado descumpriu deliberadamente decisão judicial "
        f"concessiva de medidas protetivas de urgência proferida nos autos em apenso (ID-4100115, fls. 19/21), da qual fora pessoalmente intimado em {dt_intimacao_extenso} (ID-4100118, fls. 22/23). "
        f"Segundo a exordial, após invadir o quintal do imóvel na presença da filha comum do casal, de 5 (cinco) anos de idade, o acusado segurou a vítima pelos braços e desferiu-lhe "
        f"um soco na face esquerda, causando-lhe as lesões corporais atestadas no Laudo de Exame de Corpo de Delito de ID-4100108 (fls. 12/14), além de ameaçá-la de morte afirmando "
        f"que voltaria para acabar com a vida dela caso não retirasse a queixa e não reatasse o relacionamento.\n\n"
        f"O inquérito policial teve início mediante Auto de Prisão em Flagrante e Boletim de Ocorrência nº 2025/084192 (ID-4100101, fls. 02/05), instruído com o Laudo Pericial do IML (ID-4100108) "
        f"e cópia da decisão e certidão de intimação das medidas protetivas de urgência (ID-4100115 e ID-4100118).\n\n"
        f"A denúncia foi regularmente recebida em {dt_rec_extenso} (ID-4100210, fls. 41). Citado pessoalmente, o réu apresentou Resposta à Acusação por intermédio de sua Defesa Técnica (ID-4100230, fls. 48/51).\n\n"
        f"Durante a instrução processual, em Audiência de Instrução e Julgamento realizada em {dt_audiencia_extenso} (Termo de ID-4100300, fls. 62/64), foram colhidas, pelo sistema audiovisual PJe Mídias, "
        f"as declarações da ofendida {meta.nome_ofendida} (Mídia ID-4100301), os depoimentos da testemunha policial Cb. PM Roberto Silva (Mídia ID-4100302) e da testemunha de defesa João Batista Soares (Mídia ID-4100303), "
        f"realizando-se, ao final, o interrogatório do acusado (Mídia ID-4100304). Juntou-se a Certidão de Antecedentes Criminais atualizada em ID-4100350 (fls. 70/72).\n\n"
        f"Em sede de alegações finais por memoriais (ID-4100401, fls. 78/84), o Ministério Público pugnou pela integral procedência da ação penal, com exasperação da pena-base do delito de lesão corporal "
        f"pela culpabilidade acentuada, {pedido_mp_txt} e manutenção das medidas protetivas.\n\n"
        f"A Defesa Técnica, em seus memoriais (ID-4100420, fls. 88/95), arguiu preliminar de inépcia da denúncia e, no mérito, postulou: (a) a absolvição por insuficiência de provas (art. 386, inciso VII, do CPP); "
        f"(b) a atipicidade do crime de ameaça por ter sido proferida em estado de ânimo exaltado; (c) a atipicidade do crime do art. 24-A da Lei nº 11.340/2006 por ausência de dolo específico; "
        f"e, subsidiariamente, (d) a fixação das penas no mínimo legal, o reconhecimento da atenuante da confissão espontânea, a substituição da pena privativa de liberdade por restritiva de direitos e o afastamento da indenização por danos morais.\n\n"
        f"{style['fechamento_relatorio']}"
    )

    preliminares = (
        f"II – FUNDAMENTAÇÃO\n\n"
        f"1. Das Questões Preliminares, do Direito Intertemporal e da Ação Penal\n\n"
        f"1.1. Rejeição da Preliminar de Inépcia da Denúncia (PREL-01 — Art. 41 e Art. 395 do CPP):\n"
        f"Não merece acolhimento a preliminar suscitada pela Defesa em seus memoriais (ID-4100420). A denúncia de ID-4100201 preenche integralmente os requisitos formais e materiais do art. 41 do Código de Processo Penal, "
        f"descrevendo de maneira clara, individualizada e circunstanciada o tempo ({dt_fato_extenso}, às 21h30min), o local (residência da ofendida) e o modus operandi das três condutas imputadas ao acusado, "
        f"permitindo-lhe o pleno exercício do contraditório e da ampla defesa durante toda a instrução criminal. Ademais, conforme jurisprudência pacífica dos Tribunais Superiores, a superveniência de sentença penal "
        f"após exaurida a instrução probatória esvazia eventual alegação de inépcia formal quando assegurada a plenitude de defesa. Rejeito, pois, a preliminar.\n\n"
        f"1.2. Da Natureza da Ação Penal e do Direito Intertemporal (Lei nº 14.994/2024 e Súmula 542/STJ):\n"
        f"Os fatos delituosos ocorreram em {dt_fato_extenso}, portanto já sob a plena vigência da Lei nº 14.994/2024 (publicada em 10 de outubro de 2024), aplicando-se a regra do tempus regit actum (art. 2º do Código Penal). "
        f"Tanto no crime de lesão corporal praticada contra a mulher por razões da condição do sexo feminino (art. 129, § 13, do CP e Súmula 542/STJ), quanto no crime de descumprimento de medida protetiva (art. 24-A da Lei nº 11.340/2006) "
        f"e no crime de ameaça contra a mulher (art. 147, § 2º, do CP, acrescentado pela Lei nº 14.994/2024), a ação penal é pública incondicionada. Ainda que assim não fosse, a ofendida manifestou inequívoco interesse "
        f"na persecução penal tanto na Delegacia (ID-4100101) quanto em juízo (Mídia ID-4100301). De igual modo, nos termos do art. 41 da Lei nº 11.340/2006, da Súmula 536/STJ, da ADC 19/STF e da ADI 4424/STF, são constitucionalmente inaplicáveis os institutos despenalizadores da Lei nº 9.099/1995.\n\n"
        f"1.3. {titulo_prescricao}:\n"
        f"{texto_prescricao}"
    )

    depoimentos_texto = "\n\n".join(
        f"• {d.depoente} ({d.papel} — {d.media_id}): {d.discurso_indireto_sentenca}" for d in matriz_oral.depoentes
    )
    doutrina_mbd = DOUTRINA_VERIFICADA[0]
    doutrina_vds = DOUTRINA_VERIFICADA[1]
    doutrina_rsc = DOUTRINA_VERIFICADA[2]

    merito = (
        f"2. Do Mérito: Materialidade, Autoria, Valoração da Prova sob Perspectiva de Gênero e Enfrentamento das Teses Defensivas\n\n"
        f"2.1. Da Incidência da Lei nº 11.340/2006 e da Resolução CNJ nº 492/2023:\n"
        f"Restou incontroverso nos autos que o acusado {meta.nome_reu} e a ofendida {meta.nome_ofendida} conviveram em união estável pelo período de 6 (seis) anos e possuem uma filha comum de 5 (cinco) anos de idade, "
        f"configurando-se a relação íntima de afeto e o âmbito familiar previstos no art. 5º, inciso III, da Lei nº 11.340/2006, sendo prescindível a coabitação à época dos fatos, consoante enuncia a Súmula 600/STJ: "
        f"{formatar_citacao_acervo(SUMULAS_E_TEMAS_VERIFICADOS[4])}, o Enunciado 1 FONAVID e o Enunciado 45 FONAVID, em conformidade com o entendimento do Superior Tribunal de Justiça (AgRg no AREsp 2.682.075/SE e APn 1.079/DF) e a vedação absoluta da legítima defesa da honra proclamada na ADPF 779/STF.\n\n"
        f"{matriz_oral.fundamentacao_sintese}\n\n"
        f"Nesse sentido, a Jurisprudência em Teses do STJ (Edição 41, Tese 13):\n\n"
        f"    {obter_item_oficial_por_id('TESE-STJ-ED41-13')['texto_literal']}\n\n"
        f"Nesse mesmo sentido, conforme a síntese doutrinária de {doutrina_mbd['autor']} ({doutrina_mbd.get('referencia_abnt', doutrina_mbd['obra'])}), {doutrina_mbd['sintese'][0].lower() + doutrina_mbd['sintese'][1:]}\n\n"
        f"2.2. Síntese da Prova Oral Produzida sob o Crivo do Contraditório (Transcrições Auditadas pelo Sistema):\n\n"
        f"{depoimentos_texto}\n\n"
        f"2.3. Do 1º Fato Delituoso — Descumprimento de Medida Protetiva de Urgência (Art. 24-A da Lei nº 11.340/2006) e Rejeição da Tese Defensiva MER-03:\n"
        f"A materialidade e a autoria do delito tipificado no art. 24-A da Lei nº 11.340/2006 restaram cabalmente demonstradas pela Decisão Concessiva de Medidas Protetivas de Urgência de ID-4100115 (fls. 19/21), "
        f"que proibiu o acusado de aproximar-se da ofendida a menos de 300 (trezentos) metros e de manter contato com ela, bem como pela Certidão Positiva do Oficial de Justiça de ID-4100118 (fls. 22/23), "
        f"que comprova a intimação pessoal do réu em {dt_intimacao_extenso}, demonstrando a ciência inequívoca da decisão judicial exigida pelo próprio tipo penal do art. 24-A da Lei nº 11.340/2006.\n\n"
        f"Não prospera a tese defensiva (MER-03, ID-4100420) de ausência de dolo sob o argumento de que o réu teria comparecido ao imóvel apenas para ver a filha menor. "
        f"O tipo penal do art. 24-A da Lei nº 11.340/2006 exige apenas o dolo genérico, consistente na vontade livre e consciente de desrespeitar as restrições impostas na decisão judicial da qual tinha plena ciência. "
        f"No próprio interrogatório judicial (Mídia ID-4100304, aos 00min40s), o réu confessou que sabia da ordem restritiva vigente e, ainda assim, dirigiu-se à residência da vítima às 21h30min, invadindo o quintal. "
        f"Na síntese doutrinária de {doutrina_vds['autor']} ({doutrina_vds.get('referencia_abnt', doutrina_vds['obra'])}), {doutrina_vds['sintese'][0].lower() + doutrina_vds['sintese'][1:]}\n\n"
        f"Impõe-se, portanto, a condenação do acusado nas penas do art. 24-A da Lei nº 11.340/2006.\n\n"
        f"2.4. Do 2º Fato Delituoso — Lesão Corporal contra a Mulher (Art. 129, § 13, do Código Penal) e Rejeição da Tese Defensiva MER-01:\n"
        f"A materialidade do delito de lesão corporal encontra-se positivada pelo Auto de Prisão em Flagrante e Boletim de Ocorrência (ID-4100101, fls. 02/05) e, de forma irretorquível, "
        f"pelo Laudo de Exame de Corpo de Delito elaborado pelo Instituto Médico Legal (ID-4100108, fls. 12/14), que atestou a presença de equimose violácea de 4,5 cm x 3,0 cm em região malar/zigomática esquerda, "
        f"acompanhada de edema traumático local, além de escoriações lineares recentes no terço médio do antebraço direito da ofendida, produzidas por ação contundente.\n\n"
        f"A autoria é igualmente certa e recai sobre o acusado. Rejeito a tese defensiva de insuficiência probatória (MER-01 — art. 386, inciso VII, do CPP, ID-4100420). "
        f"A palavra da ofendida {meta.nome_ofendida} manteve-se absolutamente coerente tanto na Delegacia (ID-4100101) quanto em juízo (Mídia ID-4100301, aos 01min46s e 02min30s), "
        f"descrevendo com precisão a dinâmica em que o acusado segurou seu braço direito com força e desferiu-lhe um soco no lado esquerdo do rosto — exatamente as duas regiões anatômicas nas quais os peritos oficiais "
        f"constataram as lesões poucas horas depois (ID-4100108). Além disso, o policial militar Cb. PM Roberto Silva confirmou em juízo (Mídia ID-4100302, aos 01min10s) que visualizou o rosto da vítima já inchado e avermelhado "
        f"imediatamente após o fato. Por sua vez, a testemunha arrolada pela Defesa, João Batista Soares (Mídia ID-4100303), nada presenciou dos fatos. Finalmente, recorde-se que a Súmula 589/STJ veda qualquer cogitação de bagatela ou insignificância: "
        f"{formatar_citacao_acervo(SUMULAS_E_TEMAS_VERIFICADOS[3])}. Caracterizado, assim, o crime do art. 129, § 13, do Código Penal.\n\n"
        f"2.5. Do 3º Fato Delituoso — Ameaça contra a Mulher (Art. 147, caput e § 1º, do Código Penal) e Rejeição da Tese Defensiva MER-02:\n"
        f"De igual modo, restaram comprovadas a materialidade e a autoria do crime de ameaça. A ofendida relatou de modo seguro (Mídia ID-4100301, aos 03min31s) que o réu, logo após agredi-la fisicamente, "
        f"apontou-lhe o dedo e afirmou que, caso ela não retirasse a queixa e não reatasse o relacionamento, voltaria para \"acabar com a sua vida\", provocando-lhe fundado temor.\n\n"
        f"Afasto a tese defensiva de atipicidade por ânimo exaltado ou ira momentânea (MER-02, ID-4100420). Conforme a jurisprudência da Corte Especial do Superior Tribunal de Justiça (processo sob segredo de justiça, j. 10/06/2024, Informativo Extraordinário 21) e a síntese doutrinária de {doutrina_rsc['autor']} ({doutrina_rsc.get('referencia_abnt', doutrina_rsc['obra'])}), {doutrina_rsc['sintese'][0].lower() + doutrina_rsc['sintese'][1:]}\n\n"
        f"No caso concreto, a promessa de morte foi proferida em meio à invasão domiciliar noturna, em descumprimento de medida protetiva e imediatamente após agressão física na face da vítima, "
        f"revelando nítida idoneidade intimidatória e dolo de ameaçar."
    )

    dosimetria_texto = formatar_secao_dosimetria_completa(
        dosimetria_global=dosimetria_global,
        criterio_1fase=criterio_1fase,
        ofendida_nome=meta.nome_ofendida,
        prescricao=prescricao,
        fundamento_liberdade=DEMO_FUNDAMENTO_LIBERDADE,
        **DEMO_IDS_DOSIMETRIA,
    )

    penas_partes: List[str] = []
    for especie, rotulo in (("reclusao", "reclusão"), ("detencao", "detenção"), ("prisao_simples", "prisão simples")):
        if especie in dosimetria_global.penas_por_especie:
            penas_partes.append(f"{dosimetria_global.penas_por_especie[especie].format_extenso()} de {rotulo}")
    penas_txt = " e ".join(penas_partes) or dosimetria_global.pena_total_unificada.format_extenso()
    multa_txt = texto_multa_dispositivo(dosimetria_global)
    regime_txt = texto_regime_dispositivo(dosimetria_global)
    dano_txt = texto_dano_moral_dispositivo(
        dosimetria_global,
        nome_ofendida=meta.nome_ofendida,
        data_fato_br=formatar_data_br(meta.data_fato),
        id_den="ID-4100201",
    )
    data_extenso = formatar_data_br(data_sentenca, por_extenso=True)

    dispositivo = (
        f"III – DISPOSITIVO\n\n"
        f"{style['abertura_dispositivo']}\n\n"
        f"a) CONDENAR o acusado {meta.nome_reu}, qualificado nos autos, como incurso nas sanções do art. 24-A da Lei nº 11.340/2006, do art. 129, § 13, do Código Penal "
        f"e do art. 147, caput e § 1º, do Código Penal, na forma do art. 69 do Código Penal, às penas definitivas de "
        f"{penas_txt}{multa_txt}, a serem cumpridas no {regime_txt}, "
        f"vedada a substituição por penas restritivas de direitos (Súmula 588/STJ);\n\n"
        f"b) {dano_txt};\n\n"
        f"c) MANTER integralmente em vigor as Medidas Protetivas de Urgência deferidas em favor da ofendida nos autos em apenso (ID-4100115), dada a persistência da situação de risco à sua integridade física e psicológica "
        f"(art. 19, § 6º, da Lei nº 11.340/2006, acrescentado pela Lei nº 14.550/2023, e Tema Repetitivo 1249/STJ);\n\n"
        f"d) CONCEDER ao réu o direito de recorrer em liberdade (art. 387, § 1º, do Código de Processo Penal), salvo se por outro motivo estiver preso e desde que cumpra rigorosamente as medidas protetivas vigentes, "
        f"sob pena de decretação da prisão preventiva (art. 313, inciso III, do CPP);\n\n"
        f"e) DETERMINAR a intimação pessoal da ofendida {meta.nome_ofendida} acerca desta sentença e de eventual ingresso ou saída do agressor da prisão, sem prejuízo da intimação de seu advogado ou da Defensoria Pública, "
        f"em estrito cumprimento ao comando cogente do art. 21 da Lei nº 11.340/2006;\n\n"
        f"f) CONDENAR o réu ao pagamento das custas processuais (art. 804 do Código de Processo Penal), ressalvada eventual concessão da gratuidade da justiça pelo Juízo da Execução Penal.\n\n"
        f"Após o trânsito em julgado:\n"
        f"1. Lance-se o nome do réu no rol dos culpados e proceda-se às comunicações ao Instituto de Identificação e ao SINIC;\n"
        f"2. Oficie-se à Justiça Eleitoral para os fins do art. 15, inciso III, da Constituição Federal;\n"
        f"3. Expeça-se a competente Guia de Execução Definitiva ao Juízo da Execução Penal (SEEU).\n\n"
        f"Publique-se. Registre-se. Intimem-se.\n\n"
        f"{settings.comarca_nome}, {data_extenso}.\n\n"
        f"{settings.magistrado_nome}\n"
        f"Juiz(a) de Direito — {settings.juizado_nome}"
    )

    return relatorio, preliminares, merito, dosimetria_texto, dispositivo
