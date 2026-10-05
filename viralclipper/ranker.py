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
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from .config import ClipConfig
from .score import Window
from .util import ClipperError, Logger

# No topo, e nao no ``build_provider``: ``_headers`` precisa da constante, e
# ``providers`` so importa ``config``/``util`` -- nao este modulo --, entao nao
# ha ciclo. A importacao tardia que resta ali e para ``apply_to_config``.
from . import providers

# Bump when the prompt or the criteria change: the cache key includes it, so
# old verdicts are not reused against a different question.
#
# v2: the contract also asks for a headline and hashtags. A cached v1 verdict
# has no headline, and reusing it would silently ship a run with no on-screen
# hook while the log claimed the model had judged every window.
# v3: adds the alternate headline and the four editorial metrics.
PROMPT_VERSION = "3"

# Dimensions the model scores, each 0-10. Kept few and independent: a longer
# rubric makes the model average everything into the middle.
DIMENSIONS: tuple[str, ...] = (
    "autocontido",
    "gancho",
    "payoff",
    "compartilhavel",
    "final_completo",
)

# The text fields the same response carries alongside the numbers. They are not
# dimensions: they do not enter the score, they are the deliverables.
HEADLINE_KEY = "headline"
ALTERNATE_HEADLINE_KEY = "headline_alternativa"
HASHTAGS_KEY = "hashtags"

# The four editorial metrics the curator reports, on a 0..100 scale. They are
# the same four `viral_report` already computes from the heuristic, which is why
# they are reported rather than blended: the model's opinion replaces the
# heuristic's number in the report when the ranker is on, instead of being
# averaged with it and producing a value that is neither.
#
# Portuguese keys on purpose. They are what the curator prompt asks for, and
# translating them at the boundary would mean two names for one number.
METRIC_KEYS: tuple[str, ...] = (
    "retencao",
    "comentarios",
    "compartilhamentos",
    "polemica",
)
# The report's own names for the same four, in the same order.
METRIC_TO_REPORT: dict[str, str] = {
    "retencao": "retention",
    "comentarios": "comments",
    "compartilhamentos": "shares",
    "polemica": "controversy",
}

# What the model is asked for, and the ceiling it is held to. Two numbers on
# purpose: the ask is what keeps a headline on two lines, and the ceiling is the
# truncation point for a model that ignores the ask. Collapsing them into one
# would mean either truncating headlines that were fine, or shipping a
# three-line banner that eats the footage.
HEADLINE_TARGET_CHARS = 70
MAX_HEADLINE_CHARS = 90
# Enough to cover reach, topic and niche without looking like spam. TikTok
# stops weighting tags after a handful, so more is not more reach.
MAX_HASHTAGS = 10

#: The JSON schema, appended by the code to whatever prompt is in use. This is
#: the whole reason a free-form prompt file can be dropped in verbatim: the
#: author writes the rules, the code owns the response format. Without this
#: split, every prompt edit would also have to get the schema right, and a
#: prompt that returns prose would fail the parse with an error that names
#: nothing the author could act on.
RESPONSE_CONTRACT = f"""Responda EXATAMENTE com um unico objeto JSON, sem texto em
volta, sem cercas de codigo e sem comentarios:

{{"autocontido": 0, "gancho": 0, "payoff": 0, "compartilhavel": 0, "final_completo": 0, "retencao": 0, "comentarios": 0, "compartilhamentos": 0, "polemica": 0, "{HEADLINE_KEY}": "", "{ALTERNATE_HEADLINE_KEY}": "", "{HASHTAGS_KEY}": "", "motivo": ""}}

- As cinco notas autocontido/gancho/payoff/compartilhavel/final_completo sao
  inteiros de 0 a 10.
- retencao, comentarios, compartilhamentos e polemica sao inteiros de 0 a 100,
  e medem o MESMO trecho por outro angulo: retencao (prende do inicio ao fim),
  comentarios (provoca opiniao), compartilhamentos (alguem mandaria para outra
  pessoa) e polemica (gera debate). Podem divergir entre si - e esperado.
- {HEADLINE_KEY}: no maximo 10 palavras e no maximo {HEADLINE_TARGET_CHARS} caracteres,
  em caixa alta, sem aspas, sem ponto final e sem emoji.
- {ALTERNATE_HEADLINE_KEY}: uma segunda headline, mesmo limite, por um angulo
  diferente da principal. Se a principal afirma, esta pergunta, e vice-versa.
- {HASHTAGS_KEY}: de 5 a {MAX_HASHTAGS} hashtags numa unica string, separadas por
  espaco, cada uma comecando com #, sem acento e sem espaco dentro.
- motivo: uma frase curta explicando a nota."""

SYSTEM_PROMPT = (
    "Você é um editor de vídeo sênior especializado em cortes curtos para "
    "Shorts, Reels e TikTok. Você recebe a transcrição de um trecho de um vídeo "
    "longo e decide se esse trecho funcionaria como um vídeo curto "
    "independente. Seja rigoroso: a maioria dos trechos não funciona. "
    "Responda apenas com JSON, sem texto em volta."
)

# The transcript may be in any language; the rubric stays in Portuguese because
# that is the target audience of the clips.
#
# This is the built-in prompt, used when no ``curator_prompt_file`` is set. It
# carries the rubric; the response contract is appended by ``build_messages``
# after formatting, never concatenated here - these braces go through
# ``str.format``, and a JSON schema pasted in would be read as replacement
# fields and raise ``KeyError: '"autocontido"'``.
USER_TEMPLATE = """Trecho ({duration:.0f} segundos):

\"\"\"
{text}
\"\"\"

Avalie de 0 a 10 cada critério:
- autocontido: faz sentido completo sem nenhum contexto anterior?
- gancho: os primeiros 3 segundos fazem alguém parar de rolar?
- payoff: o trecho entrega uma conclusão, resposta ou virada?
- compartilhavel: alguém salvaria ou mandaria isso para outra pessoa?
- final_completo: termina em um ponto natural, sem cortar uma ideia no meio?"""

#: Used when a curator prompt file is set: the file already carries the rubric
#: in the system message, so the user message is only the material to judge.
#: Sending the rubric twice would have the model weigh it twice.
USER_TEMPLATE_EXCERPT = """Trecho ({duration:.0f} segundos):

\"\"\"
{text}
\"\"\""""


@dataclass(frozen=True)
class Verdict:
    """One model judgement about one candidate window.

    ``headline`` and ``hashtags`` are the deliverables that ride along with the
    score. Both default to empty because a model that answers with only the
    numbers is still a usable verdict: the window keeps its place in the
    ranking and the render falls back to its own derived hook. Refusing the
    whole judgement over a missing headline would throw away the expensive
    part to punish the cheap one.
    """

    scores: dict[str, float]
    reason: str = ""
    headline: str = ""
    headline_alternate: str = ""
    hashtags: str = ""
    #: The four editorial metrics, on 0..100. Reported, not blended: they are
    #: the same four the report already shows, so they replace the heuristic's
    #: number there instead of being averaged into a third value.
    metrics: dict[str, float] = field(default_factory=dict)

    @property
    def overall(self) -> float:
        """Mean of the dimensions, on the same 0..10 scale as the model."""
        if not self.scores:
            return 0.0
        return sum(self.scores.values()) / len(self.scores)


def load_curator_prompt(path: str | Path) -> str:
    """Read the curator prompt file, or raise naming the path.

    An unreadable or empty file is fatal rather than a fallback to the built-in
    prompt. Silent fallback is the worst outcome here: the user edits the file,
    sees the run succeed, and never learns that their rules were never sent.
    """
    target = Path(path)
    try:
        text = target.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise ClipperError(f"Cannot read curator prompt {target}: {exc}") from exc
    if not text:
        raise ClipperError(f"Curator prompt {target} is empty.")
    return text


def prompt_fingerprint(text: str) -> str:
    """Short stable hash of a prompt, for the cache key.

    This is what makes editing the prompt invalidate the cache automatically.
    Relying on a human to bump ``PROMPT_VERSION`` after every wording change is
    a rule that gets forgotten exactly once, and the symptom is a run that
    quietly reuses verdicts from the previous prompt.
    """
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def build_messages(config: ClipConfig, window: Window) -> tuple[str, str]:
    """Return the ``(system, user)`` pair for one window.

    With a curator prompt file: the file is the system message (plus the
    response contract the code appends) and the excerpt is the user message.
    Without one: the built-in system prompt and the rubric-carrying template,
    which is the behaviour every existing install already has.
    """
    path = getattr(config, "curator_prompt_file", None)
    if path:
        system = f"{load_curator_prompt(path)}\n\n{RESPONSE_CONTRACT}"
        user = USER_TEMPLATE_EXCERPT.format(duration=window.duration, text=window.text)
        return system, user
    # Formatted first, contract appended after: see the note on USER_TEMPLATE.
    user = USER_TEMPLATE.format(duration=window.duration, text=window.text)
    return SYSTEM_PROMPT, f"{user}\n\n{RESPONSE_CONTRACT}"


def prompt_cache_salt(config: ClipConfig) -> str:
    """The prompt fingerprint to fold into the cache key, or ``""``.

    Empty for the built-in prompt on purpose: that text only changes when the
    code changes, and a code change ships with a new ``PROMPT_VERSION``, so a
    second guard would be redundant. The fingerprint exists for the case the
    version number cannot cover - a prompt file the user edits between runs.
    """
    path = getattr(config, "curator_prompt_file", None)
    if not path:
        return ""
    return prompt_fingerprint(load_curator_prompt(path))


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

    The payload carries no ``max_tokens``, and that is load-bearing for the
    reasoning models. A budget low enough to look polite (16 was enough to
    empty `content`) goes entirely into ``reasoning_content``: the model
    thinks, runs out of room, and answers with a null. Measured on
    ``deepseek-ai/deepseek-v4.1-flash``: 16 gave `content: None` after 13 s,
    while no cap gave `"OK"` after 36 s. The caller that asked for a short
    answer would see a None it cannot explain, so the cap stays unset and the
    latency budget lives in ``ranker_timeout`` instead.
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
        headers = {
            "Content-Type": "application/json",
            # Sem isto o urllib se identifica como ``Python-urllib/3.x`` e o
            # Cloudflare -- que fica na frente de varios destes provedores --
            # responde 403 "error code: 1010" antes de a chave ser olhada. Vale
            # para o run, nao so para o teste.
            "User-Agent": providers.LLM_USER_AGENT,
        }
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


def clean_headline(value: object) -> str:
    """Normalise a headline into one short, single-line string.

    Models wrap these in quotes, in markdown emphasis and in stray newlines
    often enough that passing the raw value through means a burned caption with
    a literal asterisk in it. The length cap is the real fix: libass will
    happily draw a headline wider than the frame, and the failure is silent.
    """
    if value is None:
        return ""
    text = str(value)
    text = text.replace("\r", " ").replace("\n", " ")
    # Strip the wrappers a model adds on its own initiative.
    text = text.strip().strip("\"'").strip("*_` ").strip()
    text = " ".join(text.split())
    if len(text) > MAX_HEADLINE_CHARS:
        text = text[:MAX_HEADLINE_CHARS].rstrip(" ,;:-")
    return text


def clean_hashtags(value: object) -> str:
    """Extract hashtags from whatever shape the model answered with.

    Accepts a single string or a JSON array, because both are common and the
    difference is not worth an error. ``\\w`` under ``re.UNICODE`` is the only
    pattern the stdlib engine offers that matches a network hashtag: a tag has
    no accent and no space, so ``#\\w+`` cuts exactly where it matters.
    """
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        raw = " ".join(str(item) for item in value)
    else:
        raw = str(value)
    found: list[str] = []
    seen: set[str] = set()
    for tag in re.findall(r"#\w+", raw, re.UNICODE):
        lowered = tag.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        found.append(lowered)
        if len(found) >= MAX_HASHTAGS:
            break
    return " ".join(found)


def clean_metrics(document: dict) -> dict[str, float]:
    """Pull the four editorial metrics, clamped to 0..100.

    Clamped, not dropped, on an out-of-range value: a model that answers
    ``120`` meant "very high", and discarding it would silently report nothing
    for the metric it was most confident about. A non-numeric value is dropped,
    because there is no reading of ``"alta"`` that is honest.
    """
    metrics: dict[str, float] = {}
    for key in METRIC_KEYS:
        if key not in document:
            continue
        try:
            value = float(document[key])
        except (TypeError, ValueError):
            continue
        metrics[key] = max(0.0, min(100.0, value))
    return metrics


def parse_verdict(text: str) -> Verdict:
    """Turn a raw model response into a :class:`Verdict`, clamped to 0..10.

    The dimension requirement is unchanged: a response with no numeric
    dimension is still an error, because a verdict that cannot move the score
    is not a verdict. A missing headline, hashtag or metric is not - see
    :class:`Verdict`.
    """
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
    return Verdict(
        scores=scores,
        reason=reason[:200],
        headline=clean_headline(document.get(HEADLINE_KEY)),
        headline_alternate=clean_headline(document.get(ALTERNATE_HEADLINE_KEY)),
        hashtags=clean_hashtags(document.get(HASHTAGS_KEY)),
        metrics=clean_metrics(document),
    )


def cache_key(window: Window, model: str, prompt_salt: str = "") -> str:
    """Stable key for one window under one model, prompt version and prompt.

    ``prompt_salt`` is optional so the two-argument call keeps working and keeps
    producing the same key it always did. It only carries a value when the
    prompt comes from a file the user can edit.
    """
    payload = "\u0000".join(
        [PROMPT_VERSION, model, prompt_salt, window.text.strip(), f"{window.duration:.2f}"]
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
        return Verdict(
            scores=scores,
            reason=str(document.get("reason") or ""),
            headline=str(document.get("headline") or ""),
            headline_alternate=str(document.get("headline_alternate") or ""),
            hashtags=str(document.get("hashtags") or ""),
            metrics={
                str(k): float(v)
                for k, v in dict(document.get("metrics") or {}).items()
            },
        )
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
        json.dumps(
            {
                "scores": verdict.scores,
                "reason": verdict.reason,
                "headline": verdict.headline,
                "headline_alternate": verdict.headline_alternate,
                "hashtags": verdict.hashtags,
                "metrics": verdict.metrics,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    temporary.replace(target)


def resolve_cache_dir(config: ClipConfig) -> Path:
    """Rank cache lives next to the transcript cache, outside the run scratch.

    It used to be ``work_path()/cache/rank`` — inside the scratch, which
    ``cli.py`` deletes in its ``finally``. With ``--ranker llm`` that meant the
    LLM scoring calls were paid for again on every run of the same video, since
    nothing ever survived to be reused.
    """
    if config.cache_dir:
        return Path(config.cache_dir) / "rank"
    return Path(config.output_dir) / "cache" / "rank"


def build_provider(config: ClipConfig) -> RankProvider | None:
    """Build the configured provider, or ``None`` when ranking is disabled.

    A named ``ranker_provider`` fills in the endpoint, the model and the key
    variable from the table in :mod:`viralclipper.providers` before anything
    else reads them. That is what lets the panel offer a dropdown: the user
    picks "nemotron-super", not a URL they have to remember exactly.
    """
    if config.ranker in {"none", "", None}:
        return None
    if config.ranker != "llm":
        raise ClipperError(f"Unknown ranker '{config.ranker}'. Use 'none' or 'llm'.")

    if getattr(config, "ranker_provider", ""):
        # Imported here, not at module level: ``providers`` imports this
        # module's sibling ``config``, and a top-level import would be circular.
        from . import providers

        config = providers.apply_to_config(config, config.ranker_provider)

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
    # Computed once: the prompt does not change between windows of one run, and
    # reading the file per window would be a syscall per candidate for a value
    # that is already in hand.
    salt = prompt_cache_salt(config)
    judged = 0
    failures = 0

    for window in top:
        key = cache_key(window, config.ranker_model, salt)
        verdict = _load_cached(cache_dir, key, logger)
        if verdict is None:
            try:
                system, user = build_messages(config, window)
                response = provider.complete(system, user)
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
        # The two text deliverables go on their own fields, not into
        # ``components``: that dict is numeric and every consumer averages it.
        # Only overwritten when the model actually wrote something, so a model
        # that skipped the headline cannot erase one a previous run produced.
        if verdict.headline:
            window.headline = verdict.headline
        if verdict.headline_alternate:
            window.headline_alternate = verdict.headline_alternate
        if verdict.hashtags:
            window.hashtags = verdict.hashtags
        if verdict.metrics:
            window.llm_metrics.update(verdict.metrics)
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
    "ALTERNATE_HEADLINE_KEY",
    "DIMENSIONS",
    "HEADLINE_KEY",
    "HEADLINE_TARGET_CHARS",
    "HASHTAGS_KEY",
    "HttpChatProvider",
    "MAX_HASHTAGS",
    "MAX_HEADLINE_CHARS",
    "METRIC_KEYS",
    "METRIC_TO_REPORT",
    "PROMPT_VERSION",
    "RESPONSE_CONTRACT",
    "RankProvider",
    "USER_TEMPLATE",
    "USER_TEMPLATE_EXCERPT",
    "Verdict",
    "apply",
    "build_messages",
    "build_provider",
    "cache_key",
    "clean_hashtags",
    "clean_headline",
    "clean_metrics",
    "load_curator_prompt",
    "parse_verdict",
    "prompt_cache_salt",
    "prompt_fingerprint",
    "resolve_cache_dir",
]
