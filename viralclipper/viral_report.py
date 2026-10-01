"""Viralization analysis for a clip, built from the signals the scorer already has.

The scorer answers "which window is the best cut". This module answers the
question a creator asks next: *why* would this specific cut travel. Every
metric is a documented heuristic over the window's own components plus a small
PT/EN term lexicon - fully deterministic, no network, no model. It is a
structured judgment aid, not a prediction: retention, comments, shares and
controversy are editorial signals, not a forecast of TikTok's algorithm.

Components used (0..1, produced by :func:`viralclipper.score.score_windows`):
``hook_start``, ``hook_peak``, ``hook_density``, ``question``, ``speech``,
``energy``, ``boundary``, ``length``, ``clean``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .ranker import METRIC_TO_REPORT
from .score import Unit, Window
from .util import fmt_clock

# Lexicons. Each entry is matched case-insensitively as a substring, which is
# language tolerant enough for PT-BR speech with English borrowings.
_CONTROVERSY_TERMS: tuple[str, ...] = (
    "absurdo", "mentira", "mentiroso", "errado", "polemic", "chocante",
    "inacreditavel", "inacreditável", "ridiculo", "ridículo", "proibid",
    "vergonha", "crime", "escandalo", "escândalo", "nunca deveria",
    "absurd", "lies", "wrong", "scam",
)
_SHARE_TERMS: tuple[str, ...] = (
    "formas", "maneiras", "passos", "dicas", "erros", "motivos", "estrategia",
    "estratégia", "tecnicas", "técnicas", "passo a passo", "como fazer",
    "guia", "lista", "tutorial", "salva esse", "manda para",
    "ways", "steps", "tips", "mistakes", "reasons", "how to",
)
_COMMENT_TERMS: tuple[str, ...] = (
    "eu acho", "na minha opini", "concorda", "comenta", "discorda",
    "voce acha", "você acha", "eu prefiro", "vamos discutir",
    "i think", "i believe", "agree", "comment",
)
_SECRET_TERMS: tuple[str, ...] = (
    "segredo", "ninguem", "ninguém", "pouca gente", "a verdade", "revelac",
    "revelação", "vazou", "vazamento", "secret", "nobody", "leak",
)
# Whisper usually writes numbers as words ("tres dicas", not "3 dicas"), so the
# list detector accepts both spellings.
_NUMBER_RE = re.compile(
    r"\b(\d+|um|uma|dois|duas|três|tres|quatro|cinco|seis|sete|oito|nove|dez|"
    r"one|two|three|four|five|six|seven|eight|nine|ten)\s+"
    r"(formas|maneiras|passos|dicas|erros|motivos|razões|razoes|estrategias|"
    r"estratégias|tecnicas|técnicas|ways|steps|tips|mistakes|reasons|strategies|tricks)\b",
    re.I,
)

# Weights of the editorial metrics. Retention is the primary driver on short
# video platforms, so it carries the most weight in the final potential.
_POTENTIAL_WEIGHTS: dict[str, float] = {
    "retention": 0.45,
    "shares": 0.20,
    "comments": 0.20,
    "controversy": 0.15,
}


def _hits(text: str, terms: tuple[str, ...]) -> int:
    lowered = text.lower()
    return sum(1 for term in terms if term in lowered)


def _term_score(hits: int) -> float:
    """Saturating 0..1 score: three hits already look like the pattern."""
    return min(1.0, hits / 3.0)


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def opening_label(text: str, components: dict[str, float]) -> str:
    """Classify the clip's opening, the way an editor would name the hook."""
    head = " ".join(text.split()[:14]).lower()
    if _hits(head, _SECRET_TERMS):
        return "REVELACAO / SEGREDO"
    if _NUMBER_RE.search(head):
        return "LISTA NUMERADA"
    if head.endswith("?") or head.startswith(("por que", "porque", "como", "quando", "quanto", "qual", "quem", "o que")):
        return "PERGUNTA DIRETA"
    if components.get("hook_start", 0.0) >= 0.6:
        return "ABERTURA IMPACTANTE"
    if components.get("hook_start", 0.0) >= 0.25:
        return "ABERTURA COM GANCHO MODERADO"
    return "ABERTURA NEUTRA"


def _why(components: dict[str, float], units: list[Unit]) -> str:
    """Explain, in plain language, which signals carry this cut."""
    reasons: list[str] = []
    if components.get("hook_start", 0.0) >= 0.5:
        reasons.append("gancho forte nos primeiros segundos, decisivo para retencao em video curto")
    if components.get("hook_density", 0.0) >= 0.5:
        reasons.append("densidade alta de frases de impacto ao longo do corte")
    if components.get("speech", 0.0) >= 0.8:
        reasons.append("fala continua, sem trechos mortos")
    if components.get("energy", 0.0) >= 0.7:
        reasons.append("entrega vocal acima da media, que sustenta a atencao")
    if components.get("boundary", 0.0) >= 0.5:
        reasons.append("comeca e termina em pausa natural, sem cortar ideia no meio")
    if components.get("question", 0.0) >= 1.0:
        reasons.append("termina em pergunta, o que puxa comentarios")
    if not reasons:
        reasons.append("trecho coerente, porem sem sinal forte de gancho ou pico de energia")
    if components.get("clean", 0.0) < 0.6:
        reasons.append("atencao: ha muletas de fala que reduzem o ritmo")
    return "; ".join(reasons) + "."



@dataclass
class ViralAnalysis:
    """The viralization report for one selected clip."""

    index: int
    headline: str
    start: float
    end: float
    duration: float
    subject: str
    why: str
    hook: str
    peak: str
    conclusion: str
    retention: int
    comments: int
    shares: int
    controversy: int
    viral_potential: int
    hook_terms: list[str] = field(default_factory=list)
    score: float = 0.0
    # Ready-to-paste tags, when the LLM ranker produced them. Appended at the
    # end so every existing positional construction keeps working.
    hashtags: str = ""
    #: Second hook option, same origin as ``hashtags``.
    headline_alternate: str = ""
    #: "curator" when the four metrics came from the model, "" when they are the
    #: heuristic's. Without this the same clip reports different numbers on two
    #: runs and nothing on the page says why.
    metrics_source: str = ""

    def to_dict(self) -> dict:
        return {
            "index": self.index,
            "headline": self.headline,
            "start": self.start,
            "end": self.end,
            "start_label": fmt_clock(self.start),
            "end_label": fmt_clock(self.end),
            "duration": self.duration,
            "subject": self.subject,
            "why": self.why,
            "hook": self.hook,
            "peak": self.peak,
            "conclusion": self.conclusion,
            "retention": self.retention,
            "comments": self.comments,
            "shares": self.shares,
            "controversy": self.controversy,
            "viral_potential": self.viral_potential,
            "hook_terms": list(self.hook_terms),
            "score": self.score,
            "hashtags": self.hashtags,
            "headline_alternate": self.headline_alternate,
            "metrics_source": self.metrics_source,
        }

    def to_markdown(self) -> str:
        lines = [
            f"#{self.index} - {self.headline}",
            f"  Minutagem: {fmt_clock(self.start)}-{fmt_clock(self.end)}",
            f"  Duracao: {self.duration:.0f}s",
            f"  Assunto: {self.subject}",
            f"  Por que pode viralizar: {self.why}",
            f'  Gancho: "{self.hook}"',
            f"  Momento mais forte: {self.peak}",
            f"  Conclusao: {self.conclusion}",
            f"  Retencao: {self.retention}/100",
            f"  Comentarios: {self.comments}/100",
            f"  Compartilhamentos: {self.shares}/100",
            f"  Polemica: {self.controversy}/100",
            f"  Potencial de viralizacao: {self.viral_potential}%",
        ]
        # Only when there is something to paste: an empty "Hashtags:" line on
        # every clip of a run that never enabled the model is noise.
        if self.hashtags:
            lines.append(f"  Hashtags: {self.hashtags}")
        return "\n".join(lines)


def _excerpt(text: str, max_words: int = 22) -> str:
    words = text.split()
    if not words:
        return "(sem texto)"
    if len(words) <= max_words:
        return " ".join(words)
    return " ".join(words[:max_words]) + "..."


def analyse_window(window: Window, units: list[Unit], *, index: int) -> ViralAnalysis:
    """Build the viralization report for one already-selected window."""
    slice_units = units[window.unit_start : window.unit_end + 1] or []
    components = window.components or {}
    text = window.text or " ".join(unit.text for unit in slice_units)

    first = slice_units[0].text if slice_units else text
    last = slice_units[-1].text if slice_units else text
    peak_unit = max(slice_units, key=lambda unit: unit.hook_score, default=None)

    # Retention: the opening carries most of it, then speech density and
    # pacing; a clip near the target duration retains better than a very short
    # or very long one.
    retention = 100.0 * _clamp01(
        0.35 * components.get("hook_start", 0.0)
        + 0.20 * components.get("hook_density", 0.0)
        + 0.15 * components.get("speech", 0.0)
        + 0.10 * components.get("clean", 0.0)
        + 0.10 * components.get("energy", 0.0)
        + 0.10 * components.get("length", 0.0)
    )

    # Comments: questions and opinions pull replies; filler pushes them away.
    comments = 100.0 * _clamp01(
        0.30 * (1.0 if components.get("question", 0.0) >= 1.0 else 0.0)
        + 0.20 * _term_score(_hits(text, _COMMENT_TERMS))
        + 0.20 * components.get("hook_peak", 0.0)
        + 0.15 * _term_score(_hits(text, _CONTROVERSY_TERMS))
        + 0.15 * components.get("clean", 0.0)
    )

    # Shares: utility and identity are what get a clip forwarded - lists,
    # how-tos, strong takes.
    shares = 100.0 * _clamp01(
        0.35 * _term_score(_hits(text, _SHARE_TERMS))
        + 0.25 * components.get("hook_peak", 0.0)
        + 0.20 * components.get("clean", 0.0)
        + 0.20 * components.get("length", 0.0)
    )

    # Controversy: a dedicated lexicon; a single strong hit already moves it.
    controversy = 100.0 * _clamp01(
        0.7 * _term_score(_hits(text, _CONTROVERSY_TERMS))
        + 0.3 * components.get("hook_peak", 0.0)
    )

    metrics = {
        "retention": retention,
        "shares": shares,
        "comments": comments,
        "controversy": controversy,
    }
    # The curator's numbers win when there are any. They are the same four
    # metrics by construction, measured by a model that read the transcript
    # rather than by a term lexicon - so blending the two would produce a third
    # number that is neither one, and nobody could say which run it came from.
    # Applied per metric: a model that answered only for retention still
    # improves retention and leaves the rest to the heuristic.
    used_curator = False
    for source_key, report_key in METRIC_TO_REPORT.items():
        value = (window.llm_metrics or {}).get(source_key)
        if value is None:
            continue
        metrics[report_key] = round(float(value), 1)
        used_curator = True
    potential = sum(_POTENTIAL_WEIGHTS[key] * value for key, value in metrics.items())

    return ViralAnalysis(
        index=index,
        # The model's hook when there is one, otherwise the derived opening.
        # The report is what gets read while posting, so it has to show the same
        # headline the viewer sees burned on the clip.
        headline=(getattr(window, "headline", "") or "").strip()
        or opening_label(first, components),
        start=window.start,
        end=window.end,
        duration=round(window.end - window.start, 2),
        subject=_excerpt(text),
        why=_why(components, slice_units),
        hook=_excerpt(first, max_words=32),
        hashtags=(getattr(window, "hashtags", "") or "").strip(),
        peak=_excerpt(peak_unit.text, max_words=26) if peak_unit else _excerpt(first),
        conclusion=_excerpt(last, max_words=26),
        retention=int(round(retention)),
        comments=int(round(comments)),
        shares=int(round(shares)),
        controversy=int(round(controversy)),
        viral_potential=int(round(potential)),
        hook_terms=list(window.hook_terms),
        score=window.score,
    )


def analyse_windows(windows: list[Window], units: list[Unit]) -> list[ViralAnalysis]:
    """Analyse every selected clip, in clip order."""
    return [
        analyse_window(window, units, index=position)
        for position, window in enumerate(windows, start=1)
    ]


def format_markdown(analyses: list[ViralAnalysis], title: str = "") -> str:
    """Render the reports as the plain-text shape creators already use."""
    lines: list[str] = []
    if title:
        lines.append(f"# Analise de viralizacao: {title}")
        lines.append("")
    for analysis in analyses:
        lines.append(analysis.to_markdown())
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def write_markdown(analyses: list[ViralAnalysis], destination, title: str = ""):
    """Write the report to ``destination`` and return the path."""
    from pathlib import Path

    path = Path(destination)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(format_markdown(analyses, title), encoding="utf-8")
    return path

    return " ".join(words[:max_words]) + "..."
