"""Rotas de /api/transcript/*: limpeza de uma transcricao colada.

Extraido de ``server.py`` pela mesma regra de ``state.py``,
``routes_providers`` e ``routes_scrap``: sai o que nao deixa duas fontes de
verdade. O handler entra como MIXIN (``TranscriptRoutes``) para a suite e o
despacho em ``do_POST`` seguirem resolvendo ``self._handle_normalize(payload)``
pelo MRO, sem tocar no corpo de ``do_POST`` -- que e' onde o contrato de rotas
e' lido por ``inspect.getsource``.

Por que este corte e' seguro (fecho de chamadas medido em AST): o handler so'
usa ``transcript_import`` e ``ClipperError``, os dois imports, mais
``self._send_json``. Nao toca nenhum nome de ``server.py``.

A rota e' a familia ``/api/transcript/*`` -- hoje um endpoint, e o lugar onde o
proximo entra.
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from viralclipper import transcript_import  # noqa: E402
from viralclipper.util import ClipperError  # noqa: E402



class TranscriptRoutes:
    """O handler de /api/transcript/normalize. Mixin do ``Handler``.

    Nao guarda estado proprio e nao define ``__init__``: depende de
    ``self._send_json``, que vem do ``Handler``.
    """

    def _handle_normalize(self, payload: dict) -> None:
        """Clean a pasted transcript and return it minute-aligned."""
        raw = payload.get("transcript")
        if not isinstance(raw, str) or not raw.strip():
            self._send_json({"error": "transcript is required"}, 400)
            return
        try:
            result = transcript_import.normalize_transcript(raw)
        except ClipperError as exc:
            self._send_json({"error": str(exc)}, 400)
            return
        self._send_json(result.to_dict())
