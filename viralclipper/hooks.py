"""Detection of hook phrases and rhetorical patterns that drive retention.

The lists are deliberately conservative: every pattern maps to a phrase that a
human editor would recognise as a strong opening or a curiosity spike. Both
Brazilian Portuguese and English markers are covered because most clipped
videos mix them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Weight means: how much this pattern, on its own, looks like a viral opener.
# 1.0 = the strongest hooks, 0.4 = a mild curiosity nudge.
_PATTERN_WEIGHTS: list[tuple[str, float]] = [
    # Strong curiosity / secrecy hooks.
    (r"\b(segredo|secret)\w*", 1.0),
    (r"\bningu[eé]m\s+(te\s+)?(conta|falou|disse|sabe|ensina|mostra)\b", 1.0),
    (r"\bnobody\s+(tells|told|talks|knows|shows)\b", 1.0),
    (r"\bo\s+que\s+(ningu[eé]m|poucos|quase\s+ningu[eé]m)\b", 0.95),
    (r"\bnunca\s+(conte|fale|diga|fa[cç]a|use|compre)\b", 1.0),
    (r"\bnever\s+(tell|do|use|buy|say)\b", 1.0),
    (r"\b(erro|erros)\s+(fatal|grave|comum|que|de)\b", 0.9),
    (r"\bmistake\s+(that|everyone|most)\b", 0.9),
    (r"\bverd?ade\s+(sobre|que|incomoda)\b", 0.9),
    (r"\bthe\s+truth\s+about\b", 0.9),
    (r"\bpouca\s+gente\s+sabe\b", 0.9),
    (r"\bchocante\b|\bpol[eê]mico\b|\babsurdo\b", 0.75),
    # Numbers and lists.
    (r"\b\d+\s+(formas|maneiras|passos|dicas|erros|motivos|estrat[eé]gias|t[eé]cnicas)\b", 0.9),
    (r"\b\d+\s+(ways|steps|tips|mistakes|reasons|strategies)\b", 0.9),
    (r"\b(primeiro|segundo|terceiro)\s+(passo|erro|motivo)\b", 0.6),
    # Calls to attention.
    (r"\b(aten[cç][aã]o|olha\s+isso|presta\s+aten[cç][aã]o|ouve\s+isso)\b", 0.8),
    (r"\b(watch\s+this|listen\s+(to\s+)?this|pay\s+attention)\b", 0.8),
    (r"\bimagina\s+que\b|\bimagine\s+(if|this)\b", 0.65),
    (r"\bn[eé]\??\s*$", 0.35),
    # Questions.
    (r"^(por\s+que|porque|como|quando|quanto|qual|quem|o\s+que)\b", 0.7),
    (r"^(why|how|when|how\s+much|which|who|what)\b", 0.7),
    (r"\?\s*$", 0.55),
    # Transformation / money / result.
    (r"\b(dinheiro|grana|lucro|fat[ou]r|renda\s+extra|milh[aoã]o|mil\s+reais)\b", 0.75),
    (r"\b(money|profit|revenue|income|million|dollars)\b", 0.75),
    (r"\b(resultado|antes\s+e\s+depois|transforma[cç][aã]o|virou)\b", 0.6),
    (r"\b(result|before\s+and\s+after|transformation)\b", 0.6),
    # Urgency / scarcity.
    (r"\b(agora|hoje|r[aá]pido|urgente|antes\s+que)\b", 0.35),
    (r"\b(right\s+now|today|fast|urgent|before\s+it)\b", 0.35),
    # Contrast and superlatives.
    (r"\b(o\s+maior|a\s+maior|o\s+melhor|a\s+melhor|o\s+pior|gigante|enorme)\b", 0.5),
    (r"\b(the\s+biggest|the\s+best|the\s+worst|huge)\b", 0.5),
    (r"\bmas\s+o\s+que\s+(realmente|ningu[eé]m)\b", 0.6),
    # Personal authority / proof.
    (r"\beu\s+(descobri|aprendi|testei|perdi|ganhei|fiz)\b", 0.55),
    (r"\bi\s+(found|learned|tested|lost|made)\b", 0.55),
]

_COMPILED: list[tuple[re.Pattern[str], str, float]] = [
    (re.compile(pattern, re.IGNORECASE), pattern, weight)
    for pattern, weight in _PATTERN_WEIGHTS
]


@dataclass
class HookHit:
    """One matched hook pattern inside a piece of text."""

    pattern: str
    weight: float
    text: str


@dataclass
class HookResult:
    """Aggregated hook information for a text span."""

    score: float = 0.0
    hits: list[HookHit] = field(default_factory=list)
    is_question: bool = False

    @property
    def matched_terms(self) -> list[str]:
        return [hit.text for hit in self.hits]


def score_text(text: str) -> HookResult:
    """Score one sentence.

    The final value saturates at 1.0 so that a single very strong hook is not
    drowned out by a sentence that happens to match many weak patterns.
    """
    result = HookResult()
    if not text or not text.strip():
        return result

    normalized = " ".join(text.split())
    total = 0.0
    for pattern, source, weight in _COMPILED:
        match = pattern.search(normalized)
        if not match:
            continue
        total += weight
        result.hits.append(HookHit(pattern=source, weight=weight, text=match.group(0).strip()))

    result.is_question = normalized.rstrip().endswith("?")
    # Saturating combination: 1 - exp(-total) keeps strong singles dominant.
    result.score = round(1.0 - pow(2.718281828459045, -total), 4)
    return result


def opening_bonus(text: str) -> float:
    """Extra weight when the very first words of a clip are a hook.

    Retention lives or dies in the first two seconds, so a clip that opens on a
    hook is worth more than the same hook buried mid clip.
    """
    if not text:
        return 0.0
    head = " ".join(text.split()[:12])
    return score_text(head).score


def filler_ratio(text: str) -> float:
    """Ratio of filler words, used as a small penalty for weak stretches."""
    words = text.lower().split()
    if not words:
        return 0.0
    filler = {
        "eh",
        "hm",
        "hmm",
        "tipo",
        "assim",
        "entao",
        "então",
        "bom",
        "ai",
        "aí",
        "ne",
        "né",
        "uh",
        "um",
        "aham",
        "okay",
        "yeah",
    }
    hits = sum(1 for word in words if word.strip(".,!?") in filler)
    return hits / len(words)
