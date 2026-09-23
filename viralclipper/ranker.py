"""Optional semantic re-ranking of the heuristic's best candidates.

The heuristic in :mod:`viralclipper.score` is a *recall* stage: regex hooks,
speech density and loudness are cheap enough to run over every candidate window
in a two-hour video, but they cannot tell whether a window is a complete
thought. A window can match "segredo" and still be a fragment that starts
mid-sentence and ends before the payoff.

This module is the *precision* stage. It takes the ``top_n`` candidates the
heuristic liked most, asks a language model to judge each one as a standalone
short, and blends that judgement into the score.

Three properties keep it safe to run in a pipeline:

* **Off by default.** ``ranker = "none"`` means this module is never imported
  into the hot path and no network call is ever made.
* **Bounded cost.** Only ``top_n`` windows are sent, and every verdict is
  cached on disk keyed by the window text, the model and the prompt version,
  so re-running the same video while tuning options is free.
* **Never fatal.** Any transport, parsing or quota error leaves the heuristic
  scores untouched and logs a warning. A dead ranker degrades the selection; it
  does not fail the run.
"""

from __future__ import annotations

import hashlib
import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .config import ClipConfig
from .score import Window
from .util import ClipperError, Logger

# Bump when the prompt or the criteria change: the cache key includes it, so
# old verdicts are not reused against a different question.
PROMPT_VERSION = "1"

# Dimensions the model scores, each 0-10. Kept few and independent: a longer
# rubric makes the model average everything into the middle.
DIMENSIONS: tuple[str, ...] = (
    "autocontido",
    "gancho",
    "payoff",
    "compartilhavel",
    "final_completo",
)

SYSTEM_PROMPT = (
    "Você é um editor de vídeo sênior especializado em cortes curtos para "
    "Shorts, Reels e TikTok. Você recebe a transcrição de um trecho de um vídeo "
    "longo e decide se esse trecho funcionaria como um vídeo curto "
    "independente. Seja rigoroso: a maioria dos trechos não funciona. "
    "Responda apenas com JSON, sem texto em volta."
)

# The transcript may be in any language; the rubric stays in Portuguese because
# that is the target audience of the clips.
USER_TEMPLATE = """Trecho ({duration:.0f} segundos):

\"\"\"
{text}
\"\"\"

Avalie de 0 a 10 cada critério:
- autocontido: faz sentido completo sem nenhum contexto anterior?
- gancho: os primeiros 3 segundos fazem alguém parar de rolar?
- payoff: o trecho entrega uma conclusão, resposta ou virada?
- compartilhavel: alguém salvaria ou mandaria isso para outra pessoa?
- final_completo: termina em um ponto natural, sem cortar uma ideia no meio?

Responda exatamente neste formato JSON, com inteiros de 0 a 10:
{{"autocontido": 0, "gancho": 0, "payoff": 0, "compartilhavel": 0, "final_completo": 0, "motivo": "uma frase curta"}}"""


@dataclass(frozen=True)
class Verdict:
    """One model judgement about one candidate window."""

    scores: dict[str, float]
    reason: str = ""

    @property
    def overall(self) -> float:
        """Mean of the dimensions, on the same 0..10 scale as the model."""
        if not self.scores:
            return 0.0
        return sum(self.scores.values()) / len(self.scores)


class RankProvider(Protocol):
    """Anything that can turn a prompt into a completion."""

    name: str

    def complete(self, system: str, user: str) -> str:
        """Return the raw model response for one prompt pair."""
        ...


class HttpChatProvider:
    """OpenAI-compatible ``/chat/completions`` client.

    Deliberately generic: OpenAI, DeepSeek, Groq, Together, OpenRouter, a local
    Ollama or LM Studio all speak this shape, so the same provider covers every
    option without a vendor SDK.
    """

    name = "http"

    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str | None,
        *,
        timeout: float = 60.0,
        temperature: float = 0.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.timeout = timeout
        self.temperature = temperature

    def complete(self, system: str, user: str) -> str:
        payload = {
            "model": self.model,
            "temperature": self.temperature,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers=self._headers(),
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:300]
            raise ClipperError(f"Ranker HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise ClipperError(f"Ranker unreachable: {exc.reason}") from exc

        try:
            document = json.loads(body)
            return document["choices"][0]["message"]["content"] or ""
        except (json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
            raise ClipperError(f"Unexpected ranker response: {body[:200]}") from exc

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers


def _extract_json(text: str) -> dict:
    """Pull the first JSON object out of a model response.

    Models wrap JSON in prose or code fences often enough that a bare
    ``json.loads`` is not enough. Fences are stripped first, then the outermost
    balanced braces are taken; a brace inside a string literal would defeat the
    scan, which is acceptable because the rubric only asks for numbers and one
    short sentence.
    """
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("```")[1] if "```" in cleaned[3:] else cleaned[3:]
        if cleaned.startswith("json"):
            cleaned = cleaned[4:]
    cleaned = cleaned.strip()

    try:
        parsed = json.loads(cleaned)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    start = cleaned.find("{")
    if start == -1:
        raise ClipperError(f"No JSON object in the ranker response: {text[:160]}")
    depth = 0
    for index in range(start, len(cleaned)):
        char = cleaned[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                try:
                    parsed = json.loads(cleaned[start : index + 1])
                except json.JSONDecodeError as exc:
                    raise ClipperError(f"Malformed ranker JSON: {exc}") from exc
                if isinstance(parsed, dict):
                    return parsed
                break
    raise ClipperError(f"Unbalanced JSON in the ranker response: {text[:160]}")


def parse_verdict(text: str) -> Verdict:
    """Turn a raw model response into a :class:`Verdict`, clamped to 0..10."""
    document = _extract_json(text)
    scores: dict[str, float] = {}
    for dimension in DIMENSIONS:
        if dimension not in document:
            continue
        try:
            value = float(document[dimension])
        except (TypeError, ValueError):
            continue
        scores[dimension] = max(0.0, min(10.0, value))
    if not scores:
        raise ClipperError(f"No usable dimension in the ranker response: {text[:160]}")
    reason = str(document.get("motivo") or "").strip()
    return Verdict(scores=scores, reason=reason[:200])


def cache_key(window: Window, model: str) -> str:
    """Stable key for one window under one model and prompt version."""
    payload = "\u0000".join(
        [PROMPT_VERSION, model, window.text.strip(), f"{window.duration:.2f}"]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _load_cached(directory: Path, key: str, logger: Logger | None) -> Verdict | None:
    path = directory / f"{key}.json"
    if not path.exists():
        return None
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
        scores = {str(k): float(v) for k, v in dict(document["scores"]).items()}
        if not scores:
            raise ValueError("empty scores")
        return Verdict(scores=scores, reason=str(document.get("reason") or ""))
    except (json.JSONDecodeError, KeyError, TypeError, ValueError, OSError):
        if logger:
            logger.debug(f"Discarding unreadable rank cache entry {path.name}")
        path.unlink(missing_ok=True)
        return None


def _save_cached(directory: Path, key: str, verdict: Verdict) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{key}.json"
    temporary = target.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps({"scores": verdict.scores, "reason": verdict.reason}, ensure_ascii=False),
        encoding="utf-8",
    )
    temporary.replace(target)


def resolve_cache_dir(config: ClipConfig) -> Path:
    """Rank cache lives next to the transcript cache, outside the run scratch."""
    if config.cache_dir:
        return Path(config.cache_dir) / "rank"
    return config.work_path() / "cache" / "rank"


def build_provider(config: ClipConfig) -> RankProvider | None:
    """Build the configured provider, or ``None`` when ranking is disabled."""
    if config.ranker in {"none", "", None}:
        return None
    if config.ranker != "llm":
        raise ClipperError(f"Unknown ranker '{config.ranker}'. Use 'none' or 'llm'.")

    api_key = os.environ.get(config.ranker_api_key_env) if config.ranker_api_key_env else None
    if not api_key and config.ranker_requires_key:
        raise ClipperError(
            f"ranker='llm' needs an API key in ${config.ranker_api_key_env}. "
            f"Set it or run with --ranker none."
        )
    return HttpChatProvider(
        config.ranker_base_url,
        config.ranker_model,
        api_key,
        timeout=config.ranker_timeout,
    )


def _blend(heuristic: float, verdict: Verdict, weight: float) -> float:
    """Mix the absolute heuristic score with the model verdict.

    Both sides are on a 0..100 scale, so the blend stays comparable between
    videos, which is what keeps ``min_score`` meaningful with the ranker on.
    """
    weight = max(0.0, min(1.0, weight))
    return round(heuristic * (1.0 - weight) + verdict.overall * 10.0 * weight, 2)


def apply(
    candidates: list[Window],
    config: ClipConfig,
    logger: Logger,
    *,
    provider: RankProvider | None = None,
) -> int:
    """Re-rank the best candidates in place; return how many were judged.

    Only the ``top_n`` windows by heuristic score stay eligible for selection.
    Everything else is pushed below them, because mixing two different scoring
    functions in one ranking would make the result impossible to reason about.
    A shortlisted window whose model call failed keeps its heuristic score, so
    a flaky network never costs a good clip its place.
    """
    if config.ranker in {"none", "", None}:
        return 0

    if provider is None:
        provider = build_provider(config)
    if provider is None:
        return 0

    ordered = sorted(candidates, key=lambda window: window.score, reverse=True)
    top = ordered[: max(0, config.ranker_top_n)]
    if not top:
        return 0

    cache_dir = resolve_cache_dir(config)
    judged = 0
    failures = 0

    for window in top:
        key = cache_key(window, config.ranker_model)
        verdict = _load_cached(cache_dir, key, logger)
        if verdict is None:
            try:
                response = provider.complete(
                    SYSTEM_PROMPT,
                    USER_TEMPLATE.format(duration=window.duration, text=window.text),
                )
                verdict = parse_verdict(response)
                _save_cached(cache_dir, key, verdict)
            except Exception as exc:  # noqa: BLE001 - a dead ranker must not be fatal
                failures += 1
                if logger:
                    logger.warn(f"Ranker skipped one window ({type(exc).__name__}: {exc})")
                continue

        heuristic = window.score
        window.score = _blend(heuristic, verdict, config.ranker_weight)
        window.components["llm_overall"] = round(verdict.overall, 3)
        for dimension, value in verdict.scores.items():
            window.components[f"llm_{dimension}"] = round(value, 3)
        if verdict.reason:
            window.components["llm_motivo"] = verdict.reason
        window.components["heuristic_score"] = heuristic
        judged += 1

    if judged:
        if logger:
            logger.ok(
                f"Ranker ({config.ranker_model}) julgou {judged} de {len(top)} "
                f"candidatos; peso {config.ranker_weight:.2f}"
            )
    elif failures and logger:
        logger.warn("O ranker nao julgou nenhum candidato; usando so a heuristica.")

    if judged:
        # Push the candidates that were never sent to the model below every one
        # that was, so the selection stays driven by a single scoring function.
        # A candidate that *was* sent but whose call failed keeps its heuristic
        # score: the network is not the window's fault.
        shortlisted = {id(window) for window in top}
        for window in candidates:
            if id(window) not in shortlisted:
                window.components.setdefault("heuristic_score", window.score)
                window.score = -1.0
    return judged


__all__ = [
    "DIMENSIONS",
    "HttpChatProvider",
    "PROMPT_VERSION",
    "RankProvider",
    "Verdict",
    "apply",
    "build_provider",
    "cache_key",
    "parse_verdict",
    "resolve_cache_dir",
]
