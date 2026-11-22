"""Exceções de domínio compartilhadas.

- ``DadosInsuficientesError``: falta dado essencial extraído dos autos (a API devolve HTTP 422
  com ``campos``). Nunca preencher o dado com valor fictício.
- ``DecisaoJudicialPendenteError``: falta veredicto explícito do juiz para algum crime imputado.
- ``EnvioExternoNaoAutorizadoError``: tentativa de enviar dado do processo para fora da máquina sem
  ``settings.permitir_envio_externo`` e consentimento explícito na requisição.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Union

__all__ = [
    "DadosInsuficientesError",
    "DecisaoJudicialPendenteError",
    "EnvioExternoNaoAutorizadoError",
]

_CamposArg = Union[Iterable[str], str, None]


def _como_lista(valor: _CamposArg) -> List[str]:
    if valor is None:
        return []
    if isinstance(valor, str):
        return [valor] if valor.strip() else []
    return [str(v) for v in valor if v is not None and str(v).strip()]


def _parece_lista(valor: Any) -> bool:
    return isinstance(valor, (list, tuple, set, frozenset))


class DadosInsuficientesError(ValueError):
    """Dados necessários para a geração não foram extraídos dos autos.

    Atributos:
        campos: nomes dos campos faltantes (ex.: ``["data_fato", "data_recebimento_denuncia"]``).
        mensagem: texto legível para o usuário.

    Aceita ``DadosInsuficientesError(campos, mensagem)`` e, por tolerância,
    ``DadosInsuficientesError(mensagem, campos)``.
    """

    def __init__(self, campos: _CamposArg = None, mensagem: Optional[Union[str, Iterable[str]]] = None) -> None:
        # Tolerância à ordem invertida (mensagem, campos).
        if isinstance(campos, str) and (mensagem is None or _parece_lista(mensagem)):
            campos, mensagem = mensagem, campos  # type: ignore[assignment]
        self.campos: List[str] = _como_lista(campos)  # type: ignore[arg-type]
        if not mensagem or not isinstance(mensagem, str):
            if self.campos:
                mensagem = "Dados insuficientes para gerar a sentença. Campos faltantes: " + ", ".join(self.campos) + "."
            else:
                mensagem = "Dados insuficientes para gerar a sentença."
        self.mensagem: str = mensagem
        super().__init__(self.mensagem)

    def to_dict(self) -> Dict[str, Any]:
        return {"erro": "dados_insuficientes", "mensagem": self.mensagem, "campos": list(self.campos)}


class DecisaoJudicialPendenteError(DadosInsuficientesError):
    """Algum crime imputado ainda não tem veredicto explícito do juiz.

    Atributos adicionais:
        crimes_pendentes: crimes sem veredicto (``condenar|absolver|desclassificar|extinguir``).

    ``campos`` assume, por padrão, ``["veredicto.<crime>", ...]``.
    """

    def __init__(
        self,
        crimes_pendentes: _CamposArg = None,
        mensagem: Optional[Union[str, Iterable[str]]] = None,
        campos: _CamposArg = None,
    ) -> None:
        if isinstance(crimes_pendentes, str) and (mensagem is None or _parece_lista(mensagem)):
            crimes_pendentes, mensagem = mensagem, crimes_pendentes  # type: ignore[assignment]
        self.crimes_pendentes: List[str] = _como_lista(crimes_pendentes)  # type: ignore[arg-type]
        if not mensagem or not isinstance(mensagem, str):
            if self.crimes_pendentes:
                mensagem = (
                    "Decisão judicial pendente: informe o veredicto (condenar, absolver, desclassificar "
                    "ou extinguir) para: " + ", ".join(self.crimes_pendentes) + "."
                )
            else:
                mensagem = "Decisão judicial pendente: informe o veredicto de cada crime imputado."
        campos_finais = _como_lista(campos) or [f"veredicto.{c}" for c in self.crimes_pendentes] or ["veredicto"]
        super().__init__(campos_finais, mensagem)

    def to_dict(self) -> Dict[str, Any]:
        base = super().to_dict()
        base["erro"] = "decisao_judicial_pendente"
        base["crimes_pendentes"] = list(self.crimes_pendentes)
        return base


class EnvioExternoNaoAutorizadoError(PermissionError):
    """Envio de dado do processo para serviço externo sem autorização/consentimento."""

    def __init__(self, mensagem: Optional[str] = None) -> None:
        self.mensagem: str = mensagem or (
            "Envio de dados do processo para serviço externo não autorizado. Habilite "
            "'permitir_envio_externo' nas configurações e confirme o consentimento na requisição."
        )
        super().__init__(self.mensagem)
