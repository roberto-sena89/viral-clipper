"""Transcription with faster-whisper, including word level timestamps.

The word timestamps matter twice: they let the scorer split text into clean
sentences and cut at natural boundaries, and they drive the karaoke style
captions burned into the final clips.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .config import ClipConfig
from .util import ClipperError, Logger


class TranscriptionOutOfMemory(ClipperError):
    """Transcription failed because the machine could not allocate memory.

    A type of its own, and not just a message, because this is the one
    transcription failure worth RETRYING: it is a property of the machine's
    state at that instant (a concurrent build, a browser with fifty tabs), not
    of the input. A corrupt wav or a missing checkpoint fails identically the
    second time, so those must not pay for a retry.

    Classificado por :func:`is_memory_error` — o mesmo predicado que decide o
    degrau de modelo — para não existirem duas noções divergentes de "acabou a
    memória".
    """


@dataclass
class Word:
    start: float
    end: float
    text: str
    probability: float = 1.0


@dataclass
class Transcript:
    """Flat word list plus the metadata needed for reporting."""

    language: str
    language_probability: float
    model_name: str
    words: list[Word] = field(default_factory=list)

    @property
    def text(self) -> str:
        return " ".join(word.text for word in self.words).strip()

    @property
    def empty(self) -> bool:
        return not self.words

    def words_between(self, start: float, end: float) -> list[Word]:
        """Words whose midpoint falls inside ``[start, end)``."""
        picked = []
        for word in self.words:
            midpoint = (word.start + word.end) / 2.0
            if start - 0.01 <= midpoint < end:
                picked.append(word)
        return picked

    def text_between(self, start: float, end: float) -> str:
        return " ".join(word.text for word in self.words_between(start, end)).strip()


# Whisper checkpoints ordered from largest to smallest. When the configured
# model cannot be loaded - usually because the machine ran out of memory - the
# loader walks down this ladder instead of failing the whole run.
MODEL_LADDER: tuple[str, ...] = (
    "large-v3",
    "large-v2",
    "large",
    "medium",
    "small",
    "base",
    "tiny",
)

# ctranslate2/MKL reports an exhausted heap with these fragments. They are the
# difference between "you are out of RAM" and "your install is broken".
_MEMORY_MARKERS: tuple[str, ...] = (
    "mkl_malloc",
    "out of memory",
    "failed to allocate",
    "unable to allocate",
    "cannot allocate memory",
    "not enough memory",
    "bad_alloc",
)


@dataclass(frozen=True)
class LoadedModel:
    """A whisper model plus the checkpoint name it actually resolved to.

    ``name`` matters: the configured model may not be the one that loaded, and
    both the report and the transcript cache key have to reflect reality.
    """

    model: object
    name: str
    device: str
    compute_type: str


def is_memory_error(exc: BaseException) -> bool:
    """True when ``exc`` looks like an allocation failure rather than a bad install.

    Dois critérios, e o estrutural vem primeiro. Antes só havia os marcadores de
    texto, e eles **não reconheciam a falha que realmente aconteceu aqui**:

        Unable to allocate 241. MiB for an array with shape (1, 78957, 400)
        and data type float64

    "failed to allocate" estava na lista, "unable to allocate" não — e o
    ``is_memory_error`` devolvia False para a exceção inteira. Qualquer
    ``MemoryError`` sem a redação da lista também passava batido. Medido antes de
    corrigir: ``is_memory_error(MemoryError(''))`` era False.

    O tipo resolve isso sem depender de redação: o erro de alocação do numpy
    (``numpy._core._exceptions._ArrayMemoryError``) herda de ``MemoryError``, e
    ``MemoryError`` sem mensagem nenhuma também é, por definição, falta de
    memória. Os marcadores continuam porque o CTranslate2/MKL embrulha a falha
    num ``RuntimeError`` comum, onde não há tipo para consultar.
    """
    if isinstance(exc, MemoryError):
        return True
    text = str(exc).lower()
    return any(marker in text for marker in _MEMORY_MARKERS)


def smaller_models(name: str) -> list[str]:
    """Checkpoints strictly smaller than ``name``, largest first.

    An unknown name returns an empty list, so a typo in ``--model`` fails fast
    instead of silently loading something the user did not ask for.
    """
    key = (name or "").strip().lower()
    if key not in MODEL_LADDER:
        return []
    return list(MODEL_LADDER[MODEL_LADDER.index(key) + 1 :])


def _load_failure_message(requested: str, exc: BaseException | None) -> str:
    detail = f"{type(exc).__name__}: {exc}" if exc is not None else "no checkpoint loaded"
    if exc is not None and is_memory_error(exc):
        return (
            f"Could not load a whisper model for '{requested}': {detail}. "
            "The machine is out of memory. Close other programs, pass a smaller "
            "--model (base or tiny), or skip transcription with --engine audio."
        )
    return (
        f"Could not load a whisper model for '{requested}': {detail}. "
        "Check the --model name, the faster-whisper install, and the network "
        "access needed to download the checkpoint, or run with --engine audio."
    )


def load_model(config: ClipConfig, logger: Logger | None = None) -> LoadedModel:
    """Create a ``WhisperModel``, stepping down the ladder when loading fails.

    A checkpoint that does not fit in RAM is the normal failure on an 8 GB
    laptop, so the loader retries with progressively smaller checkpoints and
    reports the one it settled on. It only raises once every candidate failed.
    """
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise ClipperError(
            "faster-whisper is not installed. Run: pip install faster-whisper"
        ) from exc

    device = config.whisper_device
    compute_type = config.whisper_compute_type
    requested = config.whisper_model
    candidates = [requested, *smaller_models(requested)]
    last_error: BaseException | None = None

    for index, name in enumerate(candidates):
        if logger:
            logger.step(f"Loading whisper model '{name}' on {device} ({compute_type})")
        try:
            model = WhisperModel(name, device=device, compute_type=compute_type)
        except Exception as exc:  # noqa: BLE001 - ctranslate2 raises many types
            last_error = exc
            # A CUDA or float attempt can still work on CPU int8.
            if device != "cpu" and compute_type != "int8":
                if logger:
                    logger.warn(f"Retrying '{name}' on CPU int8: {exc}")
                try:
                    model = WhisperModel(name, device="cpu", compute_type="int8")
                except Exception as cpu_exc:  # noqa: BLE001
                    last_error = cpu_exc
                else:
                    return LoadedModel(
                        model=model, name=name, device="cpu", compute_type="int8"
                    )
            if index + 1 < len(candidates):
                if logger:
                    logger.warn(
                        f"Could not load '{name}' ({exc}); trying "
                        f"'{candidates[index + 1]}' instead"
                    )
                continue
            break
        else:
            return LoadedModel(
                model=model, name=name, device=device, compute_type=compute_type
            )

    raise ClipperError(_load_failure_message(requested, last_error))


def transcribe(
    wav_path: str | Path,
    config: ClipConfig,
    logger: Logger | None = None,
    model: LoadedModel | None = None,
) -> Transcript:
    """Transcribe a mono 16 kHz WAV file into words with timestamps.

    ``model`` lets the caller reuse an already loaded checkpoint, so the caller
    can know the resolved checkpoint name before deciding on a cache key.
    """
    loaded = model if model is not None else load_model(config, logger)
    if logger:
        logger.step(f"Transcribing {Path(wav_path).name}")
    try:
        segments, info = loaded.model.transcribe(
            str(wav_path),
            language=config.language,
            beam_size=config.beam_size,
            word_timestamps=True,
            vad_filter=config.vad_filter,
            condition_on_previous_text=False,
        )
        words: list[Word] = []
        for segment in segments:
            for item in segment.words or []:
                text = (item.word or "").strip()
                if not text:
                    continue
                words.append(
                    Word(
                        start=float(item.start),
                        end=float(item.end),
                        text=text,
                        probability=float(getattr(item, "probability", 1.0)),
                    )
                )
    except Exception as exc:  # noqa: BLE001 - transcription failures vary
        # A falta de memória sobe como tipo próprio: é a única falha daqui que
        # vale uma segunda tentativa (ver `TranscriptionOutOfMemory`). Usa o
        # `is_memory_error` que já existia — o mesmo que decide o degrau de
        # modelo — em vez de um `isinstance` paralelo, que seria mais estreito.
        if is_memory_error(exc):
            raise TranscriptionOutOfMemory(f"Transcription failed: {exc}") from exc
        raise ClipperError(f"Transcription failed: {exc}") from exc

    words.sort(key=lambda word: word.start)
    transcript = Transcript(
        language=getattr(info, "language", "") or "unknown",
        language_probability=float(getattr(info, "language_probability", 0.0) or 0.0),
        model_name=loaded.name,
        words=words,
    )
    if logger:
        logger.ok(
            f"Transcribed {len(words)} words with '{loaded.name}' "
            f"(language={transcript.language}, p={transcript.language_probability:.2f})"
        )
    return transcript
