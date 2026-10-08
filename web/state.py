"""Funcoes puras e estado compartilhado do painel web.

Extraido de ``server.py`` SEM mudanca de comportamento. O corte nao e'
estetico: so' saiu daqui o que nao toca (nem transitivamente) um nome que a
suite re-atribui com ``mock.patch.object(server, X)``. Python resolve global
por modulo, entao mover a definicao de um nome patcheado faria o ``patch``
no ``server`` nao chegar ao leitor. O ``Handler``, o despacho e as funcoes
que leem nomes patcheados continuam em ``server.py``, que re-exporta isto.
"""

from __future__ import annotations

import ctypes
import html
import json
import os
import re
import shutil
import sys
import threading
import time
import unicodedata
import webbrowser
from ctypes import wintypes
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse
from urllib.request import Request, urlopen

# O pacote do motor: as funcoes puras daqui o consultam (``config_mod``,
# ``transcript_import``, ``user_providers``...). O bootstrap e' o mesmo de
# ``server.py`` para que este modulo funcione importado sozinho.
_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from viralclipper import config as config_mod  # noqa: E402
from viralclipper import config_file as config_file_mod  # noqa: E402
from viralclipper import download as download_mod  # noqa: E402
from viralclipper import ig_profile as ig_profile_mod  # noqa: E402
from viralclipper import pipeline, quality, report, transcript_import, util, viral_report  # noqa: E402
from viralclipper import user_providers as _user_providers_mod  # noqa: E402
from viralclipper.util import ClipperError  # noqa: E402


AJUSTES_KEYS = (
    "min_duration", "max_duration", "target_duration", "min_score", "min_gap",
    "engine",
    # The three numbers of the automatic mode. They live here rather than on the
    # Cortes page because they are set once; what changes per run is the clip
    # count, which stayed there.
    "auto_margin", "auto_ceiling", "max_duration_grace",
    "whisper_model", "language", "beam_size", "cache_dir",
    "vad_filter", "transcript_cache",
    # The pasted transcript. It lives here because it is typed once and reused
    # across runs of the same video; the run payload still carries it (see
    # ``_options_to_config``), and the Cortes page reads it back from
    # ``state.ajustes`` instead of owning a second copy of the textarea.
    "transcript_text",
    "layout", "caption_preset", "caption_style", "font_size",
    "crf", "target_lufs", "workers",
    "headline_seconds", "progress_bar", "jump_cut", "loudnorm",
    # The seven ranker_* keys are deliberately absent: the AI curator is
    # configured on the Cortes page, which owns the card and sends its values
    # in the run payload. They are still real ClipConfig fields and real CLI
    # dests -- they are just not settings this page persists any more.
)

_lock = threading.Lock()

_state: dict = {"jobs": [], "clips": [], "scrap": [], "download": None}

LIBRARY_SUFFIXES: frozenset[str] = frozenset({".mp4", ".mkv", ".webm", ".mov"})

_DOWNLOAD_SLOT = "download"

_ARCHIVE_SLOT = "archive"

_RUN_SLOT = "run"

_RUN_STAGES = (
    ("analise", "Baixando e transcrevendo"),
    ("selecao", "Escolhendo os melhores trechos"),
    ("relatorio", "Montando o relatorio"),
    ("render", "Renderizando os clips"),
)

_UNSET = object()

_CHANNEL_TABS = ("videos", "shorts", "streams", "live", "playlists", "featured")

_ITEM_SEGMENTS = ("watch", "shorts", "embed", "v", "p", "reel", "tv", "video", "photo")

_BFFM_INITIALIZED = 1

_LOCAL_HOSTNAMES = frozenset({"127.0.0.1", "localhost", "::1", "[::1]", "0.0.0.0"})



class _BROWSEINFOW(ctypes.Structure):
    """Param block for ``SHBrowseForFolderW``. Module level so tests build it.

    A plain unicode array does NOT fit the ``LPWSTR`` field — assigning it
    raises ``incompatible types`` — hence the :func:`ctypes.cast` in
    :func:`_browse_info`, which is exactly the line that broke the picker.
    """

    _fields_ = [
        ("hwndOwner", wintypes.HWND),
        ("pidlRoot", ctypes.c_void_p),
        ("pszDisplayName", wintypes.LPWSTR),
        ("lpszTitle", wintypes.LPCWSTR),
        ("ulFlags", wintypes.UINT),
        ("lpfn", ctypes.c_void_p),
        ("lParam", ctypes.c_void_p),
        ("iImage", ctypes.c_int),
    ]


def _options_to_config(options: dict) -> config_mod.ClipConfig:
    """Map the JSON payload onto ClipConfig, coercing types like the CLI does."""
    payload = dict(options or {})
    payload["url"] = str(payload.get("url") or "")
    output = payload.pop("output", "output") or "output"
    payload["output_dir"] = Path(output)
    cache_dir = payload.pop("cache_dir", None)
    if not cache_dir:
        payload["cache_dir"] = Path(output, "cache", "transcripts")

    def num(key, cast=float, default=0.0):
        try:
            return cast(payload.get(key))
        except (TypeError, ValueError):
            return default

    # The panel used to send ``lufs``; ClipConfig names the field
    # ``target_lufs``. The old name matched no dataclass field, so the
    # unknown-key filter at the end of this function dropped it and the
    # loudness target never reached the config at all. Accept both spellings so
    # a cached page cannot silently keep the old behaviour.
    if "lufs" in payload and "target_lufs" not in payload:
        payload["target_lufs"] = payload.pop("lufs")
    for key in ("min_duration", "max_duration", "target_duration", "min_score",
                "min_gap", "target_lufs"):
        if key in payload:
            payload[key] = num(key)
    for key in ("count", "beam_size", "crf", "workers"):
        if key in payload:
            payload[key] = num(key, cast=int, default=1)
    # The automatic-mode knobs arrive as JSON numbers, but a hand-edited
    # ajustes.toml or a string from a future caller must not reach the dataclass
    # uncoerced: auto_ceiling is compared with ``<`` against 1 during validation
    # and with ``>=`` against a list length when picking, where a str raises
    # TypeError instead of failing validation.
    for key in ("auto_margin", "max_duration_grace"):
        if key in payload:
            payload[key] = num(key, default=0.0)
    if "auto_ceiling" in payload:
        payload["auto_ceiling"] = num("auto_ceiling", cast=int, default=200)
    # font_size may come as null from the UI: None means "inherit the preset".
    if "font_size" in payload and payload["font_size"] is not None:
        payload["font_size"] = num("font_size", cast=int, default=84)

    # Supplied transcript: accept pasted text and/or a file path. Either one
    # makes the pipeline skip whisper entirely.
    text = payload.get("transcript_text")
    if isinstance(text, str) and text.strip():
        payload["transcript_text"] = text.strip()
    else:
        payload.pop("transcript_text", None)
    transcript_file = payload.get("transcript_file")
    if isinstance(transcript_file, str) and transcript_file.strip():
        payload["transcript_file"] = Path(transcript_file.strip())
    else:
        payload.pop("transcript_file", None)

    # The panel sends the curator prompt path as a string; ClipConfig declares
    # a Path. Empty must become None and not Path("") - Path("") is Path("."),
    # which EXISTS, so validate() would accept it and the ranker would then try
    # to read a directory as its prompt. Same trap as render.py's ``image`` zone.
    curator_file = payload.get("curator_prompt_file")
    if isinstance(curator_file, str) and curator_file.strip():
        payload["curator_prompt_file"] = Path(curator_file.strip())
    else:
        payload.pop("curator_prompt_file", None)

    # A cookies file is not a ClipConfig field: it is sugar for
    # ``--ytdlp-arg --cookies <path>``. Folding it in here, before the unknown
    # keys are dropped, keeps the UI able to offer the one cookie route that
    # still works on Chrome/Edge 127+ (App-Bound Encryption sealed the other).
    # It wins over ``cookies_from_browser`` so both can stay selected in the
    # form without producing two competing sets of cookie flags.
    cookies_file = payload.pop("cookies_file", None)
    if isinstance(cookies_file, str) and cookies_file.strip():
        payload["cookies_from_browser"] = None
        payload["extra_ytdlp_args"] = [
            *_ytdlp_argv(payload.get("extra_ytdlp_args")),
            "--cookies", cookies_file.strip(),
        ]

    # Drop keys the dataclass does not declare, mirroring config_from_args.
    known = {field.name for field in __import__("dataclasses").fields(config_mod.ClipConfig)}
    clean = {k: v for k, v in payload.items() if k in known}

    # burn_captions is derived from caption_style in the UI: "none" means no
    # captions at all, anything else burns them.
    style = payload.get("caption_style")
    if style == "none":
        clean["caption_style"] = "none"
        clean["burn_captions"] = False

    cfg = config_mod.ClipConfig(**clean)
    cfg.validate()
    return cfg

def _export_saved_key(config) -> str | None:
    """Expose a provider's pasted key to the run, and say whether we set it.

    ``ranker.build_provider`` reads the key from ``os.environ`` under
    ``config.ranker_api_key_env`` -- that is the CLI contract, and the panel
    must not fork it. So a key the user pasted into the card is published into
    the environment for the duration of the run, which is enough because the
    pipeline runs in this process (``_run_job`` calls ``pipeline.analyse``
    directly, no subprocess).

    Two rules keep this from surprising anyone:

    * an already-exported variable wins -- the same precedence the test route
      uses, so "test what I typed" and "run it" cannot disagree;
    * nothing is set when the provider needs no key, or the config names no
      provider at all.

    Returns the variable name that was set (so a test can assert it), or
    ``None`` when nothing changed. The variable is left behind on purpose: a
    second run in the same process re-reads it, and scrubbing it would make the
    behaviour depend on which run came first.
    """
    import os as os_mod

    name = getattr(config, "ranker_provider", "") or ""
    if not name:
        return None
    try:
        from viralclipper import providers
        provider = providers.get_provider(name)
    except Exception:  # noqa: BLE001 - an unknown provider is reported by the run itself
        return None
    if not provider.api_key or not provider.requires_key:
        return None
    # The variable the *run* will read. ``providers.apply_to_config`` copies
    # ``provider.api_key_env`` onto the config when it is set and otherwise
    # leaves whatever the panel sent, so the config is the authority and not a
    # guess made here. Falling back to a constant would be how a key pasted for
    # a keyless-named provider lands on ``OPENAI_API_KEY`` and shadows a real
    # OpenAI key used by a different entry.
    var = config.ranker_api_key_env or provider.api_key_env
    if not var:
        return None
    if os_mod.environ.get(var):
        return None
    os_mod.environ[var] = provider.api_key
    return var

def _mark_failed(anteriores: list) -> list:
    """Marca como "erro" a etapa que estava em "agora", conservando o resto.

    Quebrada para fora de :func:`_run_job` para poder ser testada: o valor de
    um job falho nao e o pipeline, e a informacao de onde ele quebrou — e sem
    este ponto de entrada, so rodando o ffmpeg ate falhar alguem a exerceria.
    """
    if not anteriores:
        anteriores = [{"key": k, "label": lbl, "state": "pendente"}
                      for k, lbl in _RUN_STAGES]
    return [
        {**stage, "state": "erro" if stage.get("state") == "agora"
         else stage.get("state", "pendente")}
        for stage in anteriores
    ]

def _write_atomically(path: Path, data: bytes) -> None:
    """Grava ``data`` em ``path`` sem deixar o arquivo pela metade.

    ``Path.write_text`` trunca e depois escreve, entao um travamento no meio --
    ou o servidor morto por um Ctrl-C, ou o disco cheio -- deixa um arquivo
    parcial. O leitor seguinte ve um TOML invalido e, ate agora, perdia tudo em
    silencio. O ``.part`` no MESMO diretorio mais o ``replace`` (atomico no mesmo
    sistema de arquivos) faz o leitor ver a versao antiga ou a nova, nunca um
    pedaco. E o mesmo padrao que o cache de thumbnails ja usava.
    """
    tmp = path.with_name(path.name + ".part")
    tmp.write_bytes(data)
    tmp.replace(path)

def _toml_scalar(value) -> "str | None":
    """Render one TOML value, or None for anything TOML cannot express.

    ``bool`` is checked before ``int`` on purpose: in Python ``True`` is an
    ``int``, so the reverse order would write ``true`` as ``1`` and the panel
    would read the toggle back as a number.
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, str):
        # Escape the control characters TOML forbids inside a basic string.
        # ``\n`` is the one that matters in practice: the pasted transcript is
        # multi-line, and a raw newline inside a basic string makes the whole
        # file unparseable -- ``_read_ajustes`` swallows the error and returns
        # ``{}``, so the user would silently lose every saved setting, not just
        # the transcript.
        #
        # A multi-line basic string (``"""``) would read better in the file, but
        # it has its own traps: a backslash at the end of a line is a line
        # continuation, and a leading newline right after the opening quotes is
        # trimmed. Escaping keeps one code path for every string.
        escaped = (
            value.replace("\\", "\\\\")
            .replace('"', '\\"')
            .replace("\r", "\\r")
            .replace("\n", "\\n")
            .replace("\t", "\\t")
        )
        return f'"{escaped}"'
    return None

def _dump_ajustes(settings: dict) -> str:
    """Serialise ``settings`` as TOML, in ``AJUSTES_KEYS`` order.

    Written by hand because the stdlib ships a TOML reader but no writer. The
    file is flat by construction -- every value is a scalar the form produced --
    so a serialiser that only handles scalars is the whole job, and iterating
    AJUSTES_KEYS instead of the dict keeps the file diff-friendly between saves.
    """
    lines = [
        "# Escrito pela pagina de Ajustes (web/ajustes.html).",
        "# E um --config valido: `python -m viralclipper --config ajustes.toml`",
        "# roda com exatamente estes valores. Editar a mao funciona, mas a",
        "# proxima vez que a pagina salvar o arquivo e reescrito por inteiro.",
        "",
    ]
    for key in AJUSTES_KEYS:
        if key not in settings:
            continue
        rendered = _toml_scalar(settings[key])
        if rendered is None:
            continue
        lines.append(f"{key} = {rendered}")
    return "\n".join(lines) + "\n"

def _relative_to(path: Path, base: Path) -> str:
    """``path`` as seen from ``base``, with forward slashes.

    Falls back to the file name when ``path`` is not under ``base``: the callers
    pass a folder that may be a temporary one (the tests), and raising there would
    make a read-only listing fail on an unrelated path question.
    """
    try:
        return path.relative_to(base).as_posix()
    except ValueError:
        return path.name

def resolve_within(base: Path, rel: str) -> Path | None:
    """Resolve a client-supplied relative path under ``base``.

    Returns the existing file path, or ``None`` when the path escapes ``base``
    (path traversal, already percent-decoded by the caller) or does not exist.
    """
    candidate = (base / rel).resolve()
    try:
        candidate.relative_to(base)
    except ValueError:
        return None
    return candidate if candidate.is_file() else None

def _ytdlp_argv(raw: object) -> list[str]:
    """Normalise a client-supplied yt-dlp argument list into argv words.

    ``download._base_args`` splices this straight into the command line, so each
    element has to be exactly one word. Three shapes arrive in practice:

    * ``["--cookies", "C:/x.txt"]`` — already argv; used as is.
    * ``"--cookies C:/x.txt"`` — a whole line. ``list()`` on it yields 18
      single-character arguments, so the failure surfaces as yt-dlp complaining
      about every letter of the alphabet rather than about the real mistake.
    * ``["--cookies C:/x.txt"]`` — one element holding a whole line, the shape
      someone reaches for when the docs show a command rather than a list.

    The last two are split on whitespace. Paths with spaces in them cannot be
    expressed through the convenience form; pass the pre-split list instead.
    """
    if raw is None:
        return []
    items = [raw] if isinstance(raw, str) else list(raw)
    argv: list[str] = []
    for item in items:
        text = str(item).strip()
        if text:
            argv.extend(text.split())
    return argv

def _cookies_file_from_args(argv: list[str]) -> str:
    """Path given as ``--cookies`` in the argv the page sent, or "".

    ``ig_profile`` reads a Netscape jar from disk, so ``--cookies-from-browser``
    is of no use to it — and on Chrome/Edge 127+ that route is sealed by
    App-Bound Encryption anyway, which is why the page offers the file first.
    """
    for index, word in enumerate(argv):
        if word == "--cookies" and index + 1 < len(argv):
            return str(argv[index + 1])
        if word.startswith("--cookies="):
            return word.split("=", 1)[1]
    return ""

def _ig_session_from_payload(payload: dict) -> str:
    """Persist the pasted sessionid into the jar and return the normalised value.

    The value is a secret the page never sends twice: once it is in the jar,
    every later step — the listing, the archive and the yt-dlp download of the
    media — reads it from the same file. Writing it here, on the request thread,
    is what keeps the archive worker's signature unchanged.

    The jar path arrives in two shapes, because the two routes need it for
    different reasons: ``/scrap`` folds it into the yt-dlp argv (yt-dlp is what
    resolves a single item), while ``/scrap/archive`` sends it as its own field.
    """
    path = str(payload.get("cookies_file") or "").strip()
    if not path:
        path = _cookies_file_from_args(_ytdlp_argv(payload.get("extra_ytdlp_args")))
    if not path:
        return ""
    return ig_profile_mod.prepare_session(path, str(payload.get("ig_session") or ""))

def _ig_title(item) -> str:
    """First non-empty line of the caption, capped.

    The panel gives the title one line, and an Instagram caption is routinely
    paragraphs long with the useful sentence at the top. Capping here rather
    than in CSS keeps the JSON payload small for a 300-item listing.
    """
    for line in str(getattr(item, "caption", "") or "").splitlines():
        text = line.strip()
        if text:
            return text[:120]
    return getattr(item, "code", "") or getattr(item, "pk", "") or "(sem título)"

def _normalise_title(text: object) -> str:
    """Canonical title for repost detection: no accents, no case, no tags.

    Links, @mentions and #hashtags are dropped because a repost usually keeps
    the sentence and changes the tags. "Parte 2" vs "parte 3" still differ,
    so episodes of a series are NOT merged — only true reposts are.
    """
    base = unicodedata.normalize(
        "NFKD", str(text if text is not None else ""))
    base = "".join(ch for ch in base if not unicodedata.combining(ch))
    base = base.lower()
    base = re.sub(r"https?://\S+|@\w+|#\w+", " ", base)
    base = re.sub(r"[^a-z0-9 ]+", " ", base)
    return re.sub(r"\s+", " ", base).strip()

def _dedupe_entries(entries: list[dict]) -> tuple[list[dict], int]:
    """Drop repeated videos from a profile listing.

    Two levels, in order:

    1. Same id (or same URL when there is no id): the extractor repeated the
       item across pages/tabs. Keeps the first occurrence.
    2. Same normalised title AND same whole-second duration: a repost under
       another id. Keeps the highest view_count, so the surviving take is the
       one that performed best.

    Returns (unique_entries, removed_count). Non-dict items pass through.
    """
    unique: list[dict] = []
    removed = 0
    seen_ids: set[str] = set()
    seen_content: dict[tuple[str, int | None], int] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            unique.append(entry)
            continue
        key = str(entry.get("id") or entry.get("webpage_url") or entry.get("url") or "")
        if key and key in seen_ids:
            removed += 1
            continue
        if key:
            seen_ids.add(key)
        title = _normalise_title(entry.get("title"))
        duration = entry.get("duration")
        bucket = round(float(duration)) if isinstance(duration, (int, float)) else None
        if title:
            sig = (title, bucket)
            if sig in seen_content:
                removed += 1
                previous = unique[seen_content[sig]]
                if isinstance(previous, dict):
                    cur_views = entry.get("view_count")
                    prev_views = previous.get("view_count")
                    cur_num = cur_views if isinstance(cur_views, (int, float)) else None
                    prev_num = prev_views if isinstance(prev_views, (int, float)) else None
                    if cur_num is not None and (prev_num is None or cur_num > prev_num):
                        unique[seen_content[sig]] = entry
                continue
            seen_content[sig] = len(unique)
        unique.append(entry)
    return unique, removed

def _viral_rank(entry: dict) -> float:
    """Sort key for "most viral first": negated view count, unknowns last.

    A missing count ranks below an explicit zero — zero means "flopped",
    missing means "the extractor did not say".
    """
    views = entry.get("view_count") if isinstance(entry, dict) else None
    if not isinstance(views, (int, float)):
        return 1.0
    return -views

def _as_float(value) -> float | None:
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return None

def _as_int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None

def esc(text: object) -> str:
    """Escape text for an HTML attribute or text node.

    The panel builds cards as HTML strings (there is no template engine and no
    build step), so every value that reaches the DOM has to pass through here.
    Titles and uploader names are attacker-controlled: a caption containing a
    double quote would otherwise close the ``data-item`` attribute and let the
    rest of the string become markup.
    """
    return html.escape(str(text if text is not None else ""), quote=True)

def _md_inline(text: str) -> str:
    """Inline markdown → HTML. The text arrives escaped, so the patterns only
    see literal backticks/asterisks/brackets — never markup to preserve."""
    out = esc(text)
    out = re.sub(r"`([^`]+)`", lambda m: "<code>%s</code>" % m.group(1), out)
    out = re.sub(r"\*\*([^*]+)\*\*", lambda m: "<strong>%s</strong>" % m.group(1), out)
    out = re.sub(r"\[([^\]]+)\]\(([^)\s]+)\)",
                 lambda m: '<a href="%s">%s</a>' % (m.group(2), m.group(1)), out)
    return out

def _md_to_html(md: str) -> str:
    """Render the README subset this repo actually uses: ATX headings, fenced
    code, pipe tables, dash/numbered lists, paragraphs.

    Deliberately not a full markdown engine — no dependency, no build step,
    and the surface is the README itself (a bug here would hide the docs the
    button sends you to). Everything is escaped before any tag is added, so a
    README line containing ``<script>`` renders as text.
    """
    lines = md.splitlines()
    out: list[str] = []
    para: list[str] = []
    list_tag: str | None = None

    def flush_para() -> None:
        if para:
            out.append("<p>" + _md_inline(" ".join(para)) + "</p>")
            para.clear()

    def close_list() -> None:
        nonlocal list_tag
        if list_tag:
            out.append("</%s>" % list_tag)
            list_tag = None

    def open_list(tag: str) -> None:
        nonlocal list_tag
        if list_tag != tag:
            close_list()
            list_tag = tag
            out.append("<%s>" % tag)

    i = 0
    while i < len(lines):
        line = lines[i]

        if line.startswith("```"):
            flush_para()
            close_list()
            i += 1
            block: list[str] = []
            while i < len(lines) and not lines[i].startswith("```"):
                block.append(lines[i])
                i += 1
            i += 1  # closing fence (or EOF)
            out.append("<pre><code>" + esc("\n".join(block)) + "</code></pre>")
            continue

        heading = re.match(r"^(#{1,6})\s+(.*)$", line)
        if heading:
            flush_para()
            close_list()
            level = len(heading.group(1))
            out.append("<h%d>%s</h%d>" % (level, _md_inline(heading.group(2)), level))
            i += 1
            continue

        if (line.lstrip().startswith("|") and i + 1 < len(lines)
                and re.match(r"^\s*\|[\s:|-]+\|\s*$", lines[i + 1])
                and "-" in lines[i + 1]):
            flush_para()
            close_list()

            def cells(row: str) -> list[str]:
                return [c.strip() for c in row.strip().strip("|").split("|")]

            out.append("<table><thead><tr>"
                       + "".join("<th>%s</th>" % _md_inline(c) for c in cells(line))
                       + "</tr></thead><tbody>")
            i += 2
            while i < len(lines) and lines[i].lstrip().startswith("|"):
                out.append("<tr>"
                           + "".join("<td>%s</td>" % _md_inline(c) for c in cells(lines[i]))
                           + "</tr>")
                i += 1
            out.append("</tbody></table>")
            continue

        if re.match(r"^- \S", line):
            flush_para()
            open_list("ul")
            out.append("<li>" + _md_inline(line[2:]) + "</li>")
            i += 1
            continue
        ordered = re.match(r"^\d+\.\s+(.*)$", line)
        if ordered:
            flush_para()
            open_list("ol")
            out.append("<li>" + _md_inline(ordered.group(1)) + "</li>")
            i += 1
            continue

        if not line.strip():
            flush_para()
            close_list()
            i += 1
            continue

        para.append(line.strip())
        i += 1

    flush_para()
    close_list()
    return "\n".join(out)

def _docs_page(md: str) -> str:
    """README as a standalone page: same dark shell as the panel, no external
    font (docs must render offline — that is what the button is for)."""
    return (
        "<!DOCTYPE html>\n"
        '<html lang="pt-BR"><head><meta charset="UTF-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1.0">\n'
        "<title>Documentação · Viral Clipper</title>\n"
        "<style>\n"
        "body{margin:0;background:#0b0d12;color:#f3f4f6;"
        "font:16px/1.65 -apple-system,'Segoe UI',Roboto,Arial,sans-serif;}\n"
        ".doc-bar{position:sticky;top:0;background:#14171f;border-bottom:1px solid #262b36;"
        "padding:12px 20px;font-size:.9rem;z-index:5;}\n"
        ".doc-bar a{color:#a5b4fc;text-decoration:none;}\n"
        ".doc-bar a:hover{text-decoration:underline;}\n"
        ".doc{max-width:860px;margin:0 auto;padding:28px 20px 60px;}\n"
        "h1,h2,h3{line-height:1.25;} h1{font-size:1.9rem;}\n"
        "h2{font-size:1.35rem;margin-top:2em;border-bottom:1px solid #262b36;padding-bottom:.3em;}\n"
        "a{color:#a5b4fc;} code{background:#1b1f2a;border:1px solid #262b36;border-radius:5px;"
        "padding:.1em .35em;font:.9em Consolas,'Courier New',monospace;}\n"
        "pre{background:#14171f;border:1px solid #262b36;border-radius:8px;padding:14px 16px;"
        "overflow-x:auto;} pre code{background:none;border:0;padding:0;}\n"
        "table{border-collapse:collapse;width:100%;font-size:.92rem;}\n"
        "th,td{border:1px solid #262b36;padding:7px 10px;text-align:left;vertical-align:top;}\n"
        "th{background:#14171f;} tr:nth-child(even) td{background:rgba(22,26,35,.5);}\n"
        "li{margin:.25em 0;} strong{color:#f3f4f6;}\n"
        "</style></head><body>\n"
        '<div class="doc-bar"><a href="/">&larr; Voltar ao painel</a></div>\n'
        '<main class="doc">' + _md_to_html(md) + "</main>\n"
        "</body></html>"
    )

def _archive_record(**fields) -> dict:
    """The state the arquivar-box paints while a profile archive runs.

    Mirrors _download_record on purpose: the page polls it the same way and
    reads the same core keys (active/state/phase/total/index/title/percent),
    plus the archive counters (reels/posts/photos/skipped/failed/downloaded).
    """
    record = {
        "active": False,
        "state": "ocioso",  # ocioso | listando | baixando | concluido | erro
        "phase": "",  # listando | item | bytes | skipped | photo | failed | done
        "total": 0,
        "index": 0,
        "title": "",
        "percent": 0.0,
        "item_fraction": 0.0,  # 0..1 progress inside the item being downloaded
        "current_line": "",  # the last yt-dlp line, so the page can echo it
        "archive_lines": [],  # every yt-dlp line, in order, for the log panel
        "items": [],  # one {code, folder, kind, thumb} per chosen item, for the cards
        "item_states": [],  # parallel phases ("", item, done, skipped, photo, failed)
        "downloaded": 0,
        "skipped": 0,
        "failed": 0,
        "photos": 0,
        "reels": 0,
        "posts": 0,
        "username": "",
        "root": "",
        "lines": [],
        "errors": [],
        "error": "",
    }
    record.update(fields)
    return record

def _publish_archive(**fields) -> None:
    """Merge fields into the archive record, under the lock."""
    clean = {k: v for k, v in fields.items() if v is not _UNSET}
    with _lock:
        record = dict(_state.get(_ARCHIVE_SLOT) or _archive_record())
        record.update(clean)
        _state[_ARCHIVE_SLOT] = record

def _download_record(**fields) -> dict:
    """The state the scrap page paints while a batch runs.

    One flat record with every field always present: the page polls it twice a
    second and reads it directly, so a missing key would be a rendering bug
    rather than a smaller payload.
    """
    record = {
        "active": False,
        "state": "ocioso",  # ocioso | baixando | concluido | erro
        "phase": "",  # item | bytes | skipped | failed | done
        "total": 0,
        "index": 0,
        "title": "",
        "percent": 0.0,
        "downloaded": 0,
        "skipped": 0,
        "failed": 0,
        "root": "",
        "lines": [],
        "errors": [],
        "error": "",
    }
    record.update(fields)
    return record

def _publish_download(**fields) -> None:
    """Merge fields into the download record, under the lock.

    The worker thread writes and the request thread reads, so this goes through
    ``_lock`` like every other piece of shared state.
    """
    with _lock:
        record = dict(_state.get(_DOWNLOAD_SLOT) or _download_record())
        record.update(fields)
        _state[_DOWNLOAD_SLOT] = record

def _run_record(**fields) -> dict:
    """The state the Cortes aside paints while the pipeline works.

    Same shape rule as :func:`_download_record`: every field is always present,
    because the page polls this once a second and reads it directly — a missing
    key would be a rendering bug rather than a smaller payload.

    ``stages`` comes pre-filled as "pendente" instead of empty so the page can
    draw the whole ladder on the first poll, before the pipeline has announced
    anything. A bar that starts as four grey steps and fills in is honest about
    what remains; a bar that starts at 0% and jumps to 100% is not.
    """
    record = {
        "active": False,
        "state": "ocioso",  # ocioso | rodando | concluido | erro | offline
        "stage": "",
        "stage_label": "",
        "stage_index": 0,
        "stage_total": len(_RUN_STAGES),
        "stages": [{"key": key, "label": label, "state": "pendente"}
                   for key, label in _RUN_STAGES],
        "lines": [],
        "url": "",
        "plan_only": False,
        "started_at": 0.0,
        "elapsed": 0.0,
        "error": "",
    }
    record.update(fields)
    return record

def _publish_run(**fields) -> None:
    """Merge fields into the run record, under the lock."""
    with _lock:
        record = dict(_state.get(_RUN_SLOT) or _run_record())
        record.update(fields)
        _state[_RUN_SLOT] = record

def _segments(url: str) -> tuple[str, list[str]]:
    """Split a URL into (host, path segments), ignoring query and fragment."""
    without_query = url.split("?", 1)[0].split("#", 1)[0].rstrip("/")
    parts = [p for p in without_query.split("/") if p]
    if len(parts) < 2 or ":" not in parts[0]:
        return "", []
    return parts[1].lower(), parts[2:]

def _is_profile_url(url: str) -> bool:
    """True for an account URL rather than a single post."""
    host, tail = _segments(url)
    if not tail:
        return False
    # Any URL that names an item is a single video, whatever the site.
    if any(seg in _ITEM_SEGMENTS for seg in tail):
        return False
    if "tiktok.com" in host:
        return tail[0].startswith("@")
    if host.endswith("instagram.com"):
        return len(tail) == 1
    if "youtube.com" in host:
        return tail[0].startswith("@") or tail[0] in {"c", "user", "channel"}
    return False

def _with_videos_tab(url: str) -> str:
    """Point a YouTube channel URL at its videos tab when it has none."""
    host, tail = _segments(url)
    if "youtube.com" not in host or not tail:
        return url
    # /watch?v=ID has no item segment in the path — the id rides in the query.
    if "watch" in url.split("?", 1)[0].split("/") or "v=" in url:
        return url
    if any(seg in _CHANNEL_TABS for seg in tail):
        return url
    base = url.split("?", 1)[0].split("#", 1)[0].rstrip("/")
    return base + "/videos"

def list_library(base: Path, limit: int = 200) -> list[dict]:
    """List the videos inside ``base``, newest first.

    The gallery only knows about the clips of the last job; this view exists so
    a file that was renamed by hand, produced by the CLI, or left over from an
    earlier run is still visible and playable.
    """
    if not base.is_dir():
        return []
    found: list[dict] = []
    for path in base.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in LIBRARY_SUFFIXES:
            continue
        try:
            stat = path.stat()
        except OSError:  # pragma: no cover - file vanished mid-scan
            continue
        found.append(
            {
                "name": path.name,
                "rel": str(path.relative_to(base)).replace("\\", "/"),
                "size": stat.st_size,
                "modified": int(stat.st_mtime),
            }
        )
    found.sort(key=lambda item: item["modified"], reverse=True)
    return found[:limit]

def _browse_initialized(hwnd, msg, _lp, _data):
    """Bring the folder dialog forward the moment it opens.

    Without an owner it lands BEHIND a maximized browser: the page stays on
    "Aguardando…" and there is nothing to choose. A plain TOPMOST is refused
    here (SetWindowPos returns 0), so this uses the attach-input trick — the
    user just clicked for this dialog, which is what makes stealing foreground
    legitimate — plus a taskbar flash as fallback. It dies with the choice,
    so nothing is forced afterwards.
    """
    if msg != _BFFM_INITIALIZED:
        return 0
    try:
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        if user32.GetForegroundWindow() != hwnd:
            cur = kernel32.GetCurrentThreadId()
            ftid = user32.GetWindowThreadProcessId(user32.GetForegroundWindow(), None)
            user32.AttachThreadInput(cur, ftid, True)
            try:
                user32.SetForegroundWindow(hwnd)
                user32.BringWindowToTop(hwnd)
            finally:
                user32.AttachThreadInput(cur, ftid, False)
        user32.FlashWindowW(hwnd, True)
    except Exception:  # noqa: BLE001 - a missed flash must not kill the dialog
        pass
    return 0

def _browse_info(buf, title: str) -> "_BROWSEINFOW":
    """Fill the dialog param block: display buffer (cast), title, flags."""
    info = _BROWSEINFOW()
    info.hwndOwner = None
    info.pszDisplayName = ctypes.cast(buf, wintypes.LPWSTR)
    info.lpszTitle = title
    info.ulFlags = 0x00000001 | 0x00000040  # BIF_RETURNONLYFSDIRS | BIF_NEWDIALOGSTYLE
    info.lpfn = ctypes.cast(_BROWSE_CALLBACK, ctypes.c_void_p).value
    return info

def _win32_askdirectory() -> str:
    """Same native dialog via Win32, for Pythons without tkinter (venvs).

    ``ctypes`` is stdlib everywhere, so this needs no install: it calls
    ``SHBrowseForFolderW`` straight from shell32. Empty string = cancelled,
    same contract as the tkinter path above.
    """
    shell32 = ctypes.windll.shell32
    shell32.SHBrowseForFolderW.restype = ctypes.c_void_p
    shell32.SHGetPathFromIDListW.restype = wintypes.BOOL
    ole32 = ctypes.windll.ole32
    buf = ctypes.create_unicode_buffer(260)
    info = _browse_info(buf, "Escolher pasta de saída")
    ole32.CoInitialize(None)
    try:
        pidl = shell32.SHBrowseForFolderW(ctypes.byref(info))
        if not pidl:
            return ""
        try:
            out = ctypes.create_unicode_buffer(32767)
            return out.value if shell32.SHGetPathFromIDListW(pidl, out) else ""
        finally:
            ole32.CoTaskMemFree(pidl)
    finally:
        ole32.CoUninitialize()

def _host_is_local(value: str, port: int | None = None) -> bool:
    """True when a ``Host``/``Origin`` header addresses this machine.

    Why this exists: the server listens on 127.0.0.1, so it is unreachable from
    the network — but *not* from a web page the user has open. DNS rebinding
    makes a domain the attacker controls resolve to 127.0.0.1, and the browser
    then treats requests to it as same-origin, which lets that page POST to
    /run and read /status. ``frame-ancestors`` does not help (this is not a
    frame) and CORS does not help (the request is not cross-origin *for the
    browser*). The one thing the attacker cannot forge is the ``Host`` header:
    the browser writes the name it actually connected to.

    ``port`` is the port this server is listening on. When given, a request
    whose explicit port differs is refused — that is what makes the check
    survive ``--port``. When None, any port is accepted, because the caller
    could not tell us and refusing would break the panel.

    Parsing is deliberately manual instead of ``urlparse``: the value is
    ``host[:port]`` and nothing else, and IPv6 arrives as ``[::1]:7755``.

    Returns False for an empty value — a request with no ``Host`` at all is
    HTTP/1.0 legacy, and refusing it costs nothing since the only client is a
    browser that always sends one.
    """
    text = (value or "").strip()
    if not text:
        return False
    # IPv6 literal: "[::1]" or "[::1]:7755".
    if text.startswith("["):
        name, _, tail = text.partition("]")
        name = name + "]"
        port_text = tail.lstrip(":")
    else:
        name, sep, port_text = text.rpartition(":")
        if not sep:
            # No colon: bare hostname, no port.
            name, port_text = text, ""
        elif ":" in name:
            # More than one colon and no brackets: a bare IPv6 literal such as
            # "::1", which has no port.
            name, port_text = text, ""
    if name.lower() not in _LOCAL_HOSTNAMES:
        return False
    # An explicit port must be the one we are actually listening on. No port at
    # all is allowed: the browser omits it on port 80, and a same-origin fetch
    # from the panel always carries the real one anyway.
    if not port_text or port is None:
        return True
    return port_text.isdigit() and int(port_text) == port



_BROWSE_CALLBACK = ctypes.WINFUNCTYPE(
    ctypes.c_int, wintypes.HWND, wintypes.UINT, wintypes.LPARAM, wintypes.LPARAM
)(_browse_initialized)

