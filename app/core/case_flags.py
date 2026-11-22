"""Sinalizadores de caso.

O caso demonstrativo é identificado EXCLUSIVAMENTE pela flag explícita ``bundle["is_demo"] is True``
(definida por ``app.demo.demo_case.get_demo_case_bundle``). É proibido inferir "demo" pelo
prefixo ``ID-4100``: IDs reais do PJe têm o formato ``ID-{NUM}`` e podem começar com 4100.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional

__all__ = ["is_demo_bundle", "is_demo_or_test_bundle"]

_PREFIXOS_DEMO_OU_TESTE = ("DEMO-", "TEST-", "E2E-")
_ORIGENS_DEMO_OU_TESTE = frozenset({"demo", "teste", "test", "simulacao", "simulação"})


def is_demo_bundle(bundle: Optional[Mapping[str, Any]]) -> bool:
    """Verdadeiro apenas se ``bundle.get("is_demo") is True``."""
    if not isinstance(bundle, Mapping):
        return False
    return bundle.get("is_demo") is True


def is_demo_or_test_bundle(bundle: Optional[Mapping[str, Any]]) -> bool:
    """Verdadeiro se ``bundle`` for demonstração, simulação ou artefato de teste.

    Nunca infere demo pelo prefixo ``ID-4100`` (IDs reais do PJe podem começar com 4100).
    """
    if not isinstance(bundle, Mapping):
        return False
    if bundle.get("is_demo") is True:
        return True
    if bundle.get("is_test") is True or bundle.get("is_simulation") is True:
        return True
    origem = str(bundle.get("origem") or "").strip().lower()
    if origem in _ORIGENS_DEMO_OU_TESTE:
        return True
    case_id = str(bundle.get("case_id") or "").strip().upper()
    if case_id.startswith(_PREFIXOS_DEMO_OU_TESTE):
        return True
    return False
