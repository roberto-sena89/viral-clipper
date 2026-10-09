"""Optional web UI server for viral-clipper.

Serves web/index.html and exposes a small JSON API that drives the real
pipeline (viralclipper.pipeline). Stdlib only: no extra dependency, so
`python web/server.py` works from the repo root with the project venv.

Two namespaces, and the split is the contract:

  /...       pages. HTML only. These are what the user sees in the address
             bar, bookmarks and shares, so they change rarely and only with
             a redirect behind them.
  /api/...   data and actions. JSON only. Called by the JS this same server
             delivers, so they can be renamed freely -- no bookmark points
             here. Every one of them lives under the prefix; nothing that
             returns JSON sits at the root.

Pages:
  GET  /                    -> index.html (Estudio)
  GET  /biblioteca          -> scrap.html (Biblioteca). The FILE keeps the
                               name "scrap" because that is the action in the
                               code (viralclipper/scrap*.py); the ADDRESS is
                               what the user reads.
  GET  /ajustes             -> ajustes.html
  GET  /publicar            -> publicar.html (Publicar). Pos-render: a
                               headline e as hashtags de cada clip, prontas
                               para colar no campo de descricao da plataforma.
  GET  /docs                -> README.md rendered as HTML
  GET  /scrap               -> 301 to /biblioteca. The old address, kept
                               because the Biblioteca is the only way in to
                               search media and a saved bookmark must not
                               404.

Data (GET):
  /api/status               -> {jobs, clips} current state
  /api/health               -> the process itself: version, pid, port and the
                               free space on the volume a render writes to
  /api/run/progress         -> the live record the Estudio aside paints
  /api/ajustes              -> the settings the Ajustes page owns
  /api/publicacao           -> output/clips.json, recortado para o post:
                               headline, hashtags, tempos e o poster de cada
                               clip. Mesmo arquivo da galeria, outro recorte.
  /api/saida                -> the output folder listing (what the panel
                               calls "pasta de saida")
  /api/providers            -> the provider registry
  /api/providers/user       -> the providers added from the panel
  /api/prompts/curador      -> the curator prompt
  /api/browse/native        -> OS folder dialog on the server machine
  /api/thumb/<name>         -> cached thumbnail bytes
  /api/clips/<id>           -> a rendered clip, or its poster
  /api/scrap/thumb          -> a search result thumbnail (fetched on demand)
  /api/scrap/archive/thumb  -> a catalog item thumbnail
  /api/scrap/download/progress -> live record while a download runs
  /api/scrap/archive/progress  -> live record while a catalog import runs

Actions (POST -- they DO something, they do not replace a resource):
  /api/run                  -> {options: {...}, plan_only: bool}
                               -> {clips, log_lines} | {error}
  /api/scrap                -> search Instagram/TikTok/YouTube
  /api/scrap/thumb          -> fetch one result thumbnail
  /api/scrap/archive        -> walk a whole profile catalog
  /api/scrap/download       -> download the selected items
  /api/transcript/normalize -> clean up a pasted transcript
  /api/providers/save       -> add/update one provider
  /api/providers/remove     -> drop one provider
  /api/providers/test       -> probe one provider

Replacement (PUT -- the whole object, nothing else):
  /api/ajustes              -> replace the settings
  /api/prompts/curador      -> replace the curator prompt

  The verb carries the meaning. These two used to be POST on the SAME path
  as their GET, which meant the reader had to open the handler to learn
  whether the call replaced or appended -- and `POST /ajustes` was
  indistinguishable from "create a new setting".

Page contracts:
  /biblioteca -> /?url=<encoded>
      The only channel between pages. No page keeps state across a
      navigation, so the pick from the Biblioteca travels in the address and
      is consumed on boot by index.js (`acceptHandoff`), which fills the
      `#url` field, strips the parameter and toasts. `/` links back to
      `/biblioteca` from the Fonte card, so the round trip is closed in both
      directions -- it used to be one-way.
"""

from __future__ import annotations

import ctypes
import html
import json
import os
import shutil
import threading
import time
import unicodedata
import webbrowser
from ctypes import wintypes
from http import server as http_server
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse
from urllib.request import Request, urlopen

REPO_ROOT = Path(__file__).resolve().parent.parent
WEB_DIR = Path(__file__).resolve().parent

#: The curator prompt lives in a file, not in a database. A file is diffable,
#: revertible and travels with the repo to a server; a blob in a table is none
#: of those, and the project already says as much in ``archive.py``
#: ("resumability comes from the filesystem, not a database").
#:
#: The panel edits THIS path and no other. The path never comes from the
#: request, so a crafted payload cannot turn the endpoint into an arbitrary
#: file writer - which is the failure mode of every "save settings" route that
#: takes the destination from the client.
CURATOR_PROMPT_PATH = REPO_ROOT / "prompts" / "curador.txt"
#: Generous for a prompt, small enough that a runaway client cannot fill the
#: disk through this route.
MAX_CURATOR_PROMPT_BYTES = 64 * 1024

AJUSTES_PATH = REPO_ROOT / "ajustes.toml"

#: O caminho do arquivo que o cartao "Meus provedores" escreve NAO mora aqui:
#: e' de ``routes_providers``, que e' quem escreve e quem le. Uma copia neste
#: modulo seria uma segunda fonte de verdade -- e foi exatamente esse o defeito
#: que o comentario da definicao, la', registra.

#: Which video the saved transcript belongs to. Lives beside ``ajustes.toml``
#: rather than inside it, and deliberately so: every key in ``AJUSTES_KEYS``
#: must be both an argparse dest and a ``ClipConfig`` field, because the file
#: has to stay a valid ``--config``. This is neither -- it is bookkeeping the
#: panel keeps for itself, the same reasoning that keeps ``curator_prompt_file``
#: out of the tuple. Persisting a transcript without this tie is the hazard the
#: old ``test_the_page_does_not_own_the_transcript`` was guarding against: paste
#: the next video's URL and the run silently reuses the previous video's text.
TRANSCRIPT_SOURCE_PATH = REPO_ROOT / ".ajustes-transcript-source"

#: Every key the Ajustes page owns. This tuple is the whole contract: the page
#: may only write these, GET /api/ajustes only returns these, and index.js
#: merges exactly these into the POST /api/run payload. Spelled out rather than
#: derived from the CLI parser because it is a product decision -- what belongs
#: to the panel -- and a parser-derived list would grow silently every time a
#: flag is added, putting fields on the page nobody designed.
#:
#: Every name here is an argparse dest *and* a ClipConfig field, so the file
#: this produces is a valid ``--config`` and the merge needs no translation.
#: ``curator_prompt_file`` is deliberately absent: the page does not edit that
#: path, the server owns it (see ``_ajustes_payload``).

# Import the package itself; the server must run from the repo root so
# `viralclipper` resolves, but __file__ lets us be explicit.
import re
import sys
sys.path.insert(0, str(REPO_ROOT))

from viralclipper import __version__ as VIRALCLIPPER_VERSION  # noqa: E402
from viralclipper import config as config_mod  # noqa: E402
from viralclipper import config_file as config_file_mod  # noqa: E402
from viralclipper import download as download_mod  # noqa: E402
from viralclipper import ig_profile as ig_profile_mod  # noqa: E402
from viralclipper import pipeline, quality, report, transcript_import, util, viral_report  # noqa: E402
from viralclipper.util import ClipperError  # noqa: E402

#: Funcoes puras e estado compartilhado: ver ``web/state.py``. Re-exportados
#: para que ``server.<nome>`` continue resolvendo para quem ja' os alcancava.
#: ``WEB_DIR`` entra no path porque este modulo tambem e' importado como
#: ``web.server`` (pela suite), quando o diretorio do script nao esta' no path.
sys.path.insert(0, str(WEB_DIR))
from state import *  # noqa: E402,F401,F403
from state import (  # noqa: E402,F401
    AJUSTES_KEYS,
    _lock,
    _state,
    LIBRARY_SUFFIXES,
    _DOWNLOAD_SLOT,
    _ARCHIVE_SLOT,
    _RUN_SLOT,
    _RUN_STAGES,
    _UNSET,
    _CHANNEL_TABS,
    _ITEM_SEGMENTS,
    _BFFM_INITIALIZED,
    _BROWSE_CALLBACK,
    _LOCAL_HOSTNAMES,
    _archive_record,
    _as_float,
    _as_int,
    _browse_info,
    _browse_initialized,
    _cookies_file_from_args,
    _dedupe_entries,
    _docs_page,
    _download_record,
    _dump_ajustes,
    _export_saved_key,
    _host_is_local,
    _ig_session_from_payload,
    _ig_title,
    _is_profile_url,
    _mark_failed,
    _md_inline,
    _md_to_html,
    _normalise_title,
    _options_to_config,
    _publish_archive,
    _publish_download,
    _publish_run,
    _relative_to,
    _run_record,
    _segments,
    _toml_scalar,
    _viral_rank,
    _win32_askdirectory,
    _with_videos_tab,
    _write_atomically,
    _ytdlp_argv,
    esc,
    list_library,
    resolve_within,
    _BROWSEINFOW,
)

#: As rotas de /api/providers/*. O modulo e' o dono de ``USER_PROVIDERS_PATH``
#: (e aponta ``user_providers.USERS_PATH`` para ele no proprio import) e de
#: ``_providers_payload``. Aqui so' se importa: re-exportar os nomes criaria uma
#: SEGUNDA binding, e o ``mock.patch.object`` da suite alcancaria uma so' --
#: a outra seguiria lendo o valor real. Por isso ``do_GET`` chama
#: ``routes_providers._providers_payload()`` em vez de um nome local.
import routes_providers  # noqa: E402
import routes_scrap  # noqa: E402
import routes_transcript  # noqa: E402

HOST = "127.0.0.1"
PORT = 7755

#: Free space a render is allowed to START with. A 9:16 1080x1920 encode plus the
#: source download and the extracted audio runs into hundreds of MB, and the
#: failure this prevents is the expensive one: `Errno 28` in the middle of an
#: encode, which leaves `output/` with half-written clips and an error that
#: points at ffmpeg instead of at the disk. Half a GB is deliberately
#: conservative -- it is a floor for "there is room to try", not a quota.
MIN_FREE_MB = 512


def _disk_free_bytes(path: Path | None = None) -> int | None:
    """Free bytes on the volume holding ``path`` (the repo, by default).

    ``None`` means "could not measure", and every caller has to treat that as
    "do not block": refusing to start a job because the MEASUREMENT failed would
    be a worse failure than the one this guards against. The path walks up to
    the nearest existing parent because the output folder does not exist yet on
    a fresh clone.
    """
    target = Path(path or REPO_ROOT)
    while not target.exists() and target != target.parent:
        target = target.parent
    try:
        return int(shutil.disk_usage(str(target)).free)
    except OSError:
        return None

# Shared state. The HTTP handler runs on one thread; runs happen on a worker
# thread, so access goes through the lock.


class CollectingLogger(util.Logger):
    """Logger that records lines so the UI can replay them.

    Recording happens before the print: a dead server console raises
    ``OSError`` on stdout, and the line must survive it for the UI.
    """

    def __init__(self) -> None:
        super().__init__(quiet=False, verbose=False)
        self.lines: list[str] = []

    def _emit(self, prefix: str, message: str) -> None:
        self.lines.append(f"{prefix} {message}")
        super()._emit(prefix, message)


def _poster_path(video: Path) -> Path:
    """The sidecar image that belongs to ``video`` (``clip-01.mp4`` -> ``.jpg``).

    Broken out so the writer and the reader cannot disagree on the name: the
    renderer fills ``thumb`` and the gallery asks for that exact file, and a
    second literal somewhere else is how the panel ends up pointing at a poster
    that was never written.
    """
    return video.with_suffix(".jpg")


# O poster NAO e' o primeiro quadro do clipe.
#
# Medido nos cinco clipes de ``output/``: o quadro 0 esta' com os olhos fechados
# ou baixados em TODOS eles, e num deles os olhos so' abrem por volta de 5 s --
# o ponto de corte cai enquanto o apresentador ainda se acomoda na frase. Um
# piscar ou um olhar baixo nao e' quadro representativo, e e' exatamente o que
# o filtro `thumbnail` descarta: ele compara os quadros do lote e devolve o mais
# parecido com a media, entao um estado que dura 1 a 3 quadros em centenas nao
# ganha. Quadro 0 deu olhos abertos em 1 dos 5 clipes; o representativo dos
# keyframes, em 3 dos 5.
#
# Os keyframes entram como conjunto de candidatos porque sao baratos de
# decodificar e ja' vem espalhados pelo clipe inteiro: 375 ms para extrair,
# contra 10674 ms do representativo do clipe INTEIRO (que deu 4 de 5). Com cinco
# clipes nao da' para separar 3 de 4, e o custo entraria na primeira abertura da
# pagina -- por isso o caminho caro ficou de fora.
#
# Dois heurísticos foram testados e DESCARTADOS, para ninguem repetir o
# caminho: as cascatas de olho do OpenCV (marcaram 2 olhos num quadro de olhos
# fechados, e a nota final ficou sem separacao -- 0,03 entre o 1o e o 4o lugar)
# e a fracao de esclera clara na faixa dos olhos (o quadro fechado de um clipe
# marcou mais esclera que o aberto de outro).
#
# O lote e' maior que qualquer conjunto real de keyframes (5 a 8 num clipe de
# 35 a 57 s, GOP de ~7 s), entao o lote so' fecha no fim do arquivo e o unico
# quadro emitido e' o representativo de TODOS eles -- e nao o dos primeiros N.
POSTER_KEYFRAME_BATCH = 1000


def _write_poster(video: Path, logger: util.Logger | None = None) -> str | None:
    """Extract one frame of ``video`` into a sidecar ``.jpg``; return its name.

    Returns ``None`` (never raises) when the poster cannot be produced. A
    poster is a nicety of the gallery: a clip that rendered fine must not be
    reported as a failure because ffmpeg could not be found or the frame grab
    failed. The caller keeps ``rendered: True`` and simply has no ``thumb``.

    O quadro e' o representativo dos keyframes do clipe, e o primeiro quadro e'
    apenas a reserva -- o porque esta' em ``POSTER_KEYFRAME_BATCH``. Os dois sao
    quadros do proprio clipe, entao nenhum e' um segundo palpite sobre o
    instante que o renderizador ja' resolveu.
    """
    if not video.exists():
        return None
    poster = _poster_path(video)
    try:
        ffmpeg = util.require_binary("ffmpeg")
    except ClipperError:
        return None
    # Overwrite in place: a re-render of the same clip must not fail on an
    # existing poster, and the frame it would write is the newer one.
    #
    # Duas tentativas, na ordem. A primeira e' a escolha; a segunda e' o
    # primeiro quadro puro, que e' o que este modulo fazia antes e continua
    # valendo para um ffmpeg cujo decodificador nao aceite `-skip_frame` ou cujo
    # build nao traga o filtro `thumbnail` -- um poster ruim e' muito melhor do
    # que nenhum, porque o cartao vazio e' o defeito que isto conserta.
    # `-skip_frame` e' opcao de ENTRADA (antes do `-i`), `-vf` e' de saida.
    tentativas = (
        (["-skip_frame", "nokey"], ["-vf", f"thumbnail=n={POSTER_KEYFRAME_BATCH}"]),
        ([], []),
    )
    for antes, filtro in tentativas:
        cmd = [
            ffmpeg, "-y",
            *antes,
            "-i", str(video),
            *filtro,
            "-frames:v", "1",
            "-q:v", "3",
            "-loglevel", "error",
            str(poster),
        ]
        try:
            util.run(cmd, logger=logger, check=True)
        except Exception as exc:  # noqa: BLE001 - a missing poster is not a failed clip
            if logger:
                logger.warn(f"Poster nao gerado para {video.name}: {exc}")
            continue
        if poster.exists():
            break
    if not poster.exists():
        return None
    return poster.name


def _clip_to_payload(clip: report.ClipRecord, output_dir: Path) -> dict:
    path = Path(clip.file) if clip.file else None
    rel = None
    if path and path.exists():
        try:
            rel = str(path.relative_to(output_dir.resolve())).replace("\\", "/")
        except ValueError:
            rel = path.name
    # Read the poster rather than assume it: the file is what the browser will
    # ask for, and a `thumb` naming an image that does not exist is a broken
    # card in the gallery. Same rule the video already follows.
    thumb = None
    if rel and path is not None:
        poster = _poster_path(path)
        if poster.exists():
            try:
                thumb = str(poster.relative_to(output_dir.resolve())).replace("\\", "/")
            except ValueError:
                thumb = poster.name
    return {
        "title": f"Clip {clip.index}",
        "score": round(clip.score, 1),
        "start": clip.start_label,
        "end": clip.end_label,
        "duration": clip.duration,
        "video": rel,
        # False for a plan-only run: the cut was scored but no file was written,
        # so the UI must not offer it as a playable preview.
        "rendered": bool(rel),
        "thumb": thumb,
        "file": clip.file,
        "hook_terms": clip.hook_terms,
        "text": clip.text,
    }


def _download_worker(
    targets: list[download_mod.MediaTarget],
    root: Path,
    config: config_mod.ClipConfig,
    logger: CollectingLogger,
) -> None:
    """Run one batch off the request thread, publishing progress as it goes.

    Never raises: the record is where a caller looks, so a failure is written
    there instead of dying inside a thread where nothing can see it.
    """

    def progress(event) -> None:
        _publish_download(
            phase=event.phase,
            index=event.index,
            total=event.total,
            title=event.title,
            percent=0.0 if event.percent is None else event.percent,
            downloaded=event.downloaded,
            skipped=event.skipped,
            failed=event.failed,
        )

    try:
        summary = download_mod.download_many(
            targets, root, config, logger, on_progress=progress
        )
    except Exception as exc:  # noqa: BLE001 - a thread must not die silently
        _publish_download(
            active=False, state="erro", error=f"{exc!r}", lines=logger.lines
        )
        return

    _publish_download(
        active=False,
        state="concluido",
        phase="done",
        percent=100.0,
        total=summary.total,
        downloaded=summary.downloaded,
        skipped=summary.skipped,
        failed=summary.failed,
        root=_relative_to_repo(summary.root),
        lines=summary.lines(),
        errors=[{"item": name, "error": error} for name, error in summary.errors],
    )


def _curator_prompt_payload() -> dict:
    """The curator prompt as it is on disk, plus where it lives.

    Returns the path even when the file is missing, because the panel shows it:
    "salve para criar" is a different instruction from "edite", and the user
    cannot tell which applies without knowing the path.
    """
    path = CURATOR_PROMPT_PATH
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        text = ""
    return {"path": str(path), "text": text, "exists": path.is_file()}

def _mark_stage(index: int, *, skipped: bool = False) -> None:
    """Publish ``index`` as the running stage, the earlier ones as done.

    ``skipped`` exists for the render phase of a plan-only run: the step is
    reached but does no work, and calling it "feito" would tell the user clips
    were written when the whole point of plan-only is that none were.
    """
    stages = []
    for position, (key, label) in enumerate(_RUN_STAGES):
        if position < index:
            state = "feito"
        elif position == index:
            state = "pulado" if skipped else "agora"
        else:
            state = "pendente"
        stages.append({"key": key, "label": label, "state": state})
    _publish_run(stages=stages, stage=_RUN_STAGES[index][0],
                 stage_label=_RUN_STAGES[index][1], stage_index=index + 1)
    # O card da fila le `job.progress`, nao o `_run_record` da escada: sao duas
    # vistas do mesmo instante. Escrever a etapa aqui (e nao no meio do
    # `_run_job`) mantem uma fonte so — se a escada muda, o card muda junto.
    with _lock:
        ativos = [j for j in _state["jobs"] if j.get("status") == "running"]
        for ativo in ativos:
            ativo["progress"] = _RUN_STAGES[index][1]


# ---------- ajustes: o estado da pagina de Ajustes ----------
# The panel's settings live in one TOML file at the repo root. Reading goes
# through the same loader the CLI uses, so a file the panel wrote is a file
# ``--config`` can read and vice versa -- there is no second dialect.


def _load_ajustes() -> "tuple[dict, str]":
    """The saved settings, and WHY they could not be read (``""`` when fine).

    A missing file is not an error: it is the first run, and the caller falls
    back to the ClipConfig defaults, which are the same numbers the form ships
    with. A malformed file is not an error *for the endpoint* either -- it must
    not go down because of a bad file -- but the reason travels back so the page
    can SAY so.

    Swallowing it in silence cost real data: the page reported "nothing saved
    yet", which reads as "this is the first run", and the next save merged the
    one edited key into an empty dict and rewrote the file with defaults. The
    other 26 keys were gone without a single line of warning. The error is
    therefore preserved here and reported by ``GET /api/ajustes``.
    """
    if not AJUSTES_PATH.is_file():
        return {}, ""
    try:
        loaded = config_file_mod.load_config_file(AJUSTES_PATH, CollectingLogger())
    except Exception as exc:  # noqa: BLE001 - um arquivo torto nao derruba a rota
        return {}, f"{type(exc).__name__}: {exc}"
    if not isinstance(loaded, dict):
        return {}, "o arquivo nao descreve uma tabela"
    return {key: loaded[key] for key in AJUSTES_KEYS if key in loaded}, ""


def _read_ajustes() -> dict:
    """Just the settings. Callers that can warn should use ``_load_ajustes``."""
    return _load_ajustes()[0]








def _read_transcript_source() -> str:
    """The URL the saved transcript belongs to, or "" when untracked.

    A missing or unreadable sidecar is not an error: it means the transcript
    was saved by a version that did not track it, which is exactly the case the
    panel has to treat as "stale, ask before reusing".
    """
    try:
        return TRANSCRIPT_SOURCE_PATH.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _write_transcript_source(url: str) -> None:
    """Record which video the saved transcript belongs to.

    Best-effort on purpose: failing to write the sidecar must not fail the save
    that carries the transcript itself. The cost of a miss is one extra clear of
    a stale transcript, not a lost setting.
    """
    try:
        if url:
            TRANSCRIPT_SOURCE_PATH.write_text(url.strip(), encoding="utf-8")
        else:
            TRANSCRIPT_SOURCE_PATH.unlink(missing_ok=True)
    except OSError:
        pass


def _ajustes_payload(settings: "dict | None" = None) -> dict:
    """What ``GET /api/ajustes`` returns.

    ``curator_prompt_file`` rides along without being an AJUSTES_KEYS entry: the
    page never edits that path (it saves the prompt through
    ``/api/prompts/curador``), but index.js has to forward it into the run payload,
    and sending it here saves a second request. It is added *after* the merge in
    ``_handle_save_ajustes`` for the same reason -- the panel must not be able
    to point the curator at an arbitrary file.

    ``transcript_source_url`` rides along for the same reason: the Cortes page
    needs it to decide whether the persisted transcript still matches the URL
    about to be run, and it is not a setting either.

    ``settings`` lets the caller hand over a read it already did. The handler
    needs the settings AND the parse error, and reading the file twice could in
    principle report one and describe the other; here both come from the same
    read. A copy is taken because the two extra keys below are added in place.
    """
    settings = dict(_read_ajustes() if settings is None else settings)
    if CURATOR_PROMPT_PATH.is_file():
        settings["curator_prompt_file"] = str(CURATOR_PROMPT_PATH)
    settings["transcript_source_url"] = _read_transcript_source()
    return settings


def _run_job(options: dict, plan_only: bool) -> dict:
    """Execute one URL end to end. Runs off the request thread."""
    logger = RunLogger()
    url = str(options.get("url") or "")
    job = {"url": url, "status": "running",
           # `progress` nasce com a PRIMEIRA etapa, nao vazio. O card faz
           # `job.progress || job.meta` para escolher o texto da fase: sem isto
           # o campo caia no `meta` e o card mostrava "small" (o modelo de
           # transcricao) onde devia ler a etapa. A lista de etapas e a mesma
           # que a escada publica, entao o vocabulario nao diverge.
           "progress": _RUN_STAGES[0][1],
           "meta": ("plan-only · " if plan_only else "") + str(options.get("whisper_model", "small"))}
    started = time.time()
    _publish_run(active=True, state="rodando", url=url, plan_only=bool(plan_only),
                 started_at=started, elapsed=0.0, error="", lines=[],
                 stages=[{"key": k, "label": lbl, "state": "pendente"}
                         for k, lbl in _RUN_STAGES],
                 stage="", stage_label="Preparando…", stage_index=0)
    with _lock:
        # Um run por vez: o registro anterior que ficou `running` e fantasma —
        # a thread que o escreveu ja morreu (servidor reiniciado no meio, ou
        # excecao fora do try), e ele nunca mais vira `done`. Sem esta poda a
        # lista so cresce: medido ao vivo, `/status` devolvia 8 jobs identicos
        # em `running` para a mesma URL, com 1 so realmente rodando. So os
        # terminais sobrevivem a um run novo; eles tem resultado a mostrar.
        _state["jobs"] = [
            j for j in _state["jobs"]
            if j.get("status") in ("done", "fail")
        ]
        _state["jobs"].append(job)
        _state["clips"] = []

    def finish(state: str, error: str = "") -> None:
        """Close the ladder so a finished job does not look stuck on its last step.

        On success every stage is "feito" — except the render phase of a
        plan-only run, which is "pulado": calling it done would tell the user
        clips were written when the whole point of plan-only is that none were.

        On failure the ladder keeps where it got to and marks the stage that was
        running as "erro". Rewriting everything as pending would throw away the
        one useful piece of information a failed job has: which phase broke.
        """
        if state == "concluido":
            stages = [
                {"key": key, "label": label,
                 "state": "pulado" if (plan_only and key == "render") else "feito"}
                for key, label in _RUN_STAGES
            ]
        else:
            with _lock:
                anteriores = list((_state.get(_RUN_SLOT) or {}).get("stages") or [])
            stages = _mark_failed(anteriores)
        final = round(time.time() - started, 1)
        _publish_run(active=False, state=state, error=error, stages=stages,
                     elapsed=final)
        # O `elapsed` so vive na escada, e ela e zerada no proximo run. Sem gravar
        # o total no proprio job, um card "Concluido" perde a duracao no instante
        # em que o usuario dispara o proximo video — e a informacao mais util de
        # um job terminado e justamente quanto ele levou. `started_at` vai junto
        # para o card poder recalcular sozinho enquanto o run esta no ar, sem
        # depender do relogio da escada (que e do run, nao do job).
        with _lock:
            job["started_at"] = started
            job["elapsed"] = final

    try:
        config = _options_to_config(options)
        config.dry_run = bool(plan_only)
        # A key pasted into the card travels with the provider, not with the
        # form: this is where it reaches ``ranker.build_provider`` without the
        # panel inventing a second place a run reads a secret from.
        _export_saved_key(config)
    except (ValueError, TypeError) as exc:
        job["status"] = "fail"
        job["meta"] = f"erro: {exc}"
        finish("erro", str(exc))
        return {"error": str(exc), "log_lines": logger.lines}

    try:
        work = util.ensure_dir(config.work_path())
        _mark_stage(0)
        metadata, analysis, transcript, units = pipeline.analyse(config, work, logger)
        _mark_stage(1)
        windows = pipeline.select_windows(units, analysis, config, logger)
        _mark_stage(2)
        viral = pipeline.build_viral_report(windows, units, metadata, config, logger)
        _mark_stage(3, skipped=bool(plan_only))
        records = pipeline.render_windows(
            windows, metadata, analysis, transcript, config, work, logger
        )
        output_dir = util.ensure_dir(config.output_dir)
        run_report = report.RunReport(
            url=config.url,
            title=str(metadata.get("title") or ""),
            video_id=str(metadata.get("id") or ""),
            uploader=str(metadata.get("uploader") or metadata.get("channel") or ""),
            source_duration=round(analysis.duration, 2),
            language=transcript.language if transcript else "nao transcrito",
            model=transcript.model_name if transcript else "-",
            engine=config.engine,
            min_duration=config.min_duration,
            max_duration=config.max_duration,
            clips=records,
        )
        report.write_json(run_report, output_dir / "clips.json")
        report.write_markdown(run_report, output_dir / "clips.md")
        clips = [_clip_to_payload(c, output_dir) for c in records]
        for clip in clips:
            clip["source_url"] = config.url
        if not plan_only:
            for record, clip in zip(records, clips):
                if clip["rendered"] and record.file:
                    # Poster first: it is one cheap frame grab and it is what
                    # `_clip_to_payload` reads back. Doing it after the quality
                    # pass would leave the payload we already built holding the
                    # old `thumb: None`.
                    clip["thumb"] = _write_poster(Path(record.file), logger)
                    try:
                        clip["quality"] = quality.inspect_clip(
                            record.file, config, caption_text=record.text or ""
                        )
                    except Exception as exc:  # quality checks must never fail a finished render
                        clip["quality"] = {
                            "status": "attention", "score": 0,
                            "checks": [{
                                "key": "quality", "label": "Revisão automática",
                                "status": "warning",
                                "message": f"Não foi possível concluir a análise: {exc}",
                            }],
                        }
            warnings = sum(
                1 for clip in clips
                if (clip.get("quality") or {}).get("status") in {"attention", "critical"}
            )
            logger.info(f"Revisão de qualidade concluída: {warnings} corte(s) para revisar.")
        rendered = sum(1 for clip in clips if clip["rendered"])
        with _lock:
            _state["clips"] = clips
            job["title"] = run_report.title
            job["clips"] = clips[:3]
            job["status"] = "done"
            job["meta"] = (
                f"{len(clips)} cortes analisados (sem render)"
                if plan_only
                else f"{rendered} clips · {run_report.title[:40]}"
            )
        finish("concluido")
        return {"clips": clips, "log_lines": logger.lines,
                "title": run_report.title,
                "plan_only": bool(plan_only),
                "viral": [analysis.to_dict() for analysis in viral],
                "viral_markdown": viral_report.format_markdown(viral, run_report.title)}
    except ClipperError as exc:
        job["status"] = "fail"
        job["meta"] = f"erro: {exc}"
        finish("erro", str(exc))
        return {"error": str(exc), "log_lines": logger.lines}
    except Exception as exc:  # noqa: BLE001 - surface anything to the UI
        job["status"] = "fail"
        job["meta"] = f"erro: {exc!r}"
        finish("erro", f"{exc!r}")
        return {"error": f"{exc!r}", "log_lines": logger.lines}
    finally:
        if not bool(plan_only):
            try:
                import shutil
                shutil.rmtree(config.work_path(), ignore_errors=True)
            except Exception:  # noqa: BLE001
                pass




#: Content types for the files served straight out of ``web/``. Every response
#: carries ``X-Content-Type-Options: nosniff``, so a type that is only "close
#: enough" is refused: a script sent as ``octet-stream`` never runs, and the
#: hero preview video sent as ``octet-stream`` never plays. Keyed by lowercase
#: suffix because a suffix on disk can be in any case.
_ASSET_TYPES = {
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".mp4": "video/mp4",
    ".webm": "video/webm",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
    ".woff2": "font/woff2",
}


def asset_content_type(path: Path) -> str:
    """The Content-Type to serve a static asset with, by suffix.

    Unknown suffixes stay opaque on purpose: guessing ``text/html`` for a file
    nobody named would turn any uploaded asset into a script host.
    """
    return _ASSET_TYPES.get(path.suffix.lower(), "application/octet-stream")




# Video containers the library view lists. Anything else in the output folder
# (json/md/srt reports, work directories) is not a preview candidate.


#: Thumbnail bytes are proxied, not hot-linked: Instagram and YouTube serve
#: from CDNs that send no permissive CORS headers, and their signed URLs expire
#: in hours. Caching the bytes on disk is also what makes the card survive a
#: refresh.
_THUMB_DIR_NAME = "thumbs"
_THUMB_MAX_BYTES = 6 * 1024 * 1024

#: Teto de itens numa selecao para download. A lista na tela tem no maximo 100
#: itens (o limite da busca), entao o teto existe para uma requisicao forjada
#: nao transformar o servidor num downloader de mil URLs de uma vez so.
_MAX_SELECTED_DOWNLOADS = 200


def _relative_to_repo(path: Path | None) -> str:
    """A path as the UI shows it: inside the repo when it fits, absolute when not.

    ``relative_to`` raises for a folder outside the project (a symlinked output
    directory, an absolute ``--output``). Showing that path is still better than
    failing the whole response, so the absolute form is the fallback.
    """
    if path is None:
        return ""
    try:
        return str(path.relative_to(REPO_ROOT)).replace("\\", "/")
    except ValueError:
        return str(path).replace("\\", "/")


#: One batch at a time. Two selections writing into the same folder would fight
#: over the same file names and double the load on a site that already throttles;
#: the page posts, gets the job and then follows it.

#: Same single-slot rule for the profile archive: one catalogue at a time, so
#: two runs never interleave files in output/instagram/ nor double the GraphQL
#: pressure on Instagram. The page polls /scrap/archive/progress like it does
#: for the selection download.

#: Same single-slot rule for the "Cortes" run. The page posts /run, gets the job
#: and now FOLLOWS it: /run/progress is the record the aside paints while the
#: pipeline works. Before this slot existed the only reader of the run was the
#: blocking response, so a four-minute job showed one log line ("$ python -m
#: viralclipper ...") and a progress bar frozen at 0.

#: The pipeline's phases in order, with the label the panel shows.
#:
#: The keys are, one for one, the calls :func:`_run_job` makes. Naming them here
#: rather than in the page is what keeps the painted progress from drifting away
#: from the work that actually runs: a fifth phase would have to be added to
#: this tuple to appear on screen, and that is the same edit that adds the call.




#: Marca "deixe este campo como esta". Um `None` cru apagaria o valor no
#: merge abaixo (record.update), e nem todo publish quer tocar em todo campo.




def _archive_worker(
    profile: str,
    cookies_file: str,
    kinds: list[str],
    limit: int | None,
    logger: CollectingLogger,
    order: str = "recent",
) -> None:
    """Run one profile archive off the request thread, publishing progress.

    Never raises: like _download_worker, the record is the channel and a dead
    thread would leave the page stuck on "Baixando…".
    """
    from viralclipper import archive as archive_mod

    _publish_archive(active=True, state="listando", phase="listando",
                     title=f"@{profile}", percent=0.0, index=0, total=0)
    try:
        listing = ig_profile_mod.list_profile(profile, cookies_file, logger)
    except ClipperError as exc:
        _publish_archive(active=False, state="erro", error=str(exc),
                         lines=logger.lines)
        return
    except Exception as exc:  # noqa: BLE001 - a thread must not die silently
        _publish_archive(active=False, state="erro", error=f"{exc!r}",
                         lines=logger.lines)
        return

    chosen = archive_mod.select_items(listing, kinds=kinds, limit=limit, order=order)
    if not chosen:
        _publish_archive(
            active=False, state="erro",
            error="o filtro não deixou nenhum item para baixar",
            lines=logger.lines, username=listing.username,
        )
        return

    root = (REPO_ROOT / "output" / "instagram").resolve()
    destination = root / archive_mod.safe_slug(
        listing.username, fallback="perfil", limit=40
    )
    total = len(chosen)
    # Cards da pagina: um retrato por item (codigo, pasta, tem-capa?) mais o
    # estado de cada um. As URLs das capas ficam fora do record — CDN expira
    # e a pagina busca os bytes em /scrap/archive/thumb por indice.
    # getattr porque os fakes de teste nao tem todos os campos.
    snapshots = [
        {
            "code": getattr(item, "code", "") or getattr(item, "pk", ""),
            "folder": getattr(item, "folder", ""),
            "kind": getattr(item, "kind", ""),
            "thumb": bool(getattr(item, "thumbnail", "")),
        }
        for item in chosen
    ]
    with _lock:
        _state["archive_items_full"] = [
            {
                "code": snap["code"],
                "thumbnail": getattr(item, "thumbnail", "") or "",
            }
            for snap, item in zip(snapshots, chosen)
        ]
    states = [""] * total
    _publish_archive(active=True, state="baixando", phase="item", total=total,
                     index=0, percent=0.0, username=listing.username,
                     title=f"@{listing.username} · {total} itens",
                     root=_relative_to_repo(destination),
                     items=snapshots, item_states=list(states))

    archive_lines: list[str] = []
    #: Fracao 0..1 do item que esta na mao, lida das linhas do yt-dlp. O notify
    #: do archive marca `position` como o item ATUAL (nao o fechado), entao
    #: position/total sozinho adianta a barra e a faz pular de item em item.
    inner = 0.0

    def progress(position: int, _total: int, code: str, phase: str) -> None:
        # `position` e o item na mao, nao o fechado. Publicar o limite inferior
        # (itens ja fechados) e deixar o on_line somar o pedaco do item atual:
        # assim a barra nunca anda para tras quando um item novo comeca.
        nonlocal inner
        fresh = phase in ("item", "bytes")
        if fresh:
            inner = 0.0  # item novo: o progresso do anterior nao vale mais
        closed = max(0, position - 1) if fresh else position
        done = min(1.0, closed / total) if total else 0.0
        if 1 <= position <= len(states):
            states[position - 1] = phase
        # Em item novo a linha do yt-dlp tambem e nova: zerar o current_line
        # evita mostrar o "99.0% of ..." do item anterior junto com frac 0.
        _publish_archive(index=position, title=code, phase=phase,
                         percent=round(done * 100.0, 1),
                         item_fraction=round(inner, 4),
                         current_line="" if fresh else _UNSET,
                         item_states=list(states))

    def on_line(line: str) -> None:
        # yt-dlp rewrites its progress in place with carriage returns; echo the
        # latest line so the page can show "1 de 20 · 54.9% of 230.98MiB"
        # instead of freezing the bar while one long reel downloads. Keep every
        # line too: the collapsible log panel shows the whole transcript of the
        # download, not just the last one.
        nonlocal inner
        match = re.search(r"(\d+(?:[.,]\d+)?)\s*%", line or "")
        if match:
            try:
                inner = max(0.0, min(1.0, float(match.group(1).replace(",", ".")) / 100.0))
            except ValueError:
                pass
        archive_lines.append(line)
        # A barra tambem anda por dentro do item: republica o percentual sem
        # esperar o proximo notify, senao um reel longo congela a UI.
        idx = _state.get(_ARCHIVE_SLOT, {}).get("index", 0)
        if total and match:
            done = min(1.0, (max(0, idx - 1) + inner) / total)
            _publish_archive(percent=round(done * 100.0, 1), item_fraction=round(inner, 4),
                             current_line=line, archive_lines=list(archive_lines))
        else:
            _publish_archive(current_line=line, archive_lines=list(archive_lines))

    try:
        config = _options_to_config(
            {"url": "", "output": str(destination),
             "cookies_file": cookies_file}
        )
        summary = archive_mod.archive_profile(
            ig_profile_mod.ProfileListing(
                username=listing.username, title=listing.title,
                items=chosen, pages=listing.pages,
            ),
            destination, config, logger, on_progress=progress,
            on_line=on_line,
        )
    except ClipperError as exc:
        _publish_archive(active=False, state="erro", error=str(exc),
                         lines=logger.lines, archive_lines=list(archive_lines))
        return
    except Exception as exc:  # noqa: BLE001 - surface anything to the UI
        _publish_archive(active=False, state="erro", error=f"{exc!r}",
                         lines=logger.lines, archive_lines=list(archive_lines))
        return

    _publish_archive(
        active=False,
        state="concluido",
        phase="done",
        index=total,
        percent=100.0,
        total=summary.total,
        downloaded=summary.downloaded,
        skipped=summary.skipped,
        failed=summary.failed,
        photos=summary.photos,
        reels=summary.reels,
        posts=summary.posts,
        username=summary.username,
        root=_relative_to_repo(summary.root),
        lines=summary.lines(),
        errors=[{"item": name, "error": error} for name, error in summary.errors],
        archive_lines=list(archive_lines),
    )










class RunLogger(CollectingLogger):
    """Collecting logger that publishes each line as it is emitted.

    The lines were always collected; what was missing was a reader. ``/run``
    only returned them in its response, so during the job the page had nothing
    to show but the command it had just echoed. Publishing here is what turns
    the log box from a receipt into progress: the pipeline's own ``[>]`` steps
    ("Baixando o video", "Transcrevendo com whisper", "Renderizando 5/12")
    appear as they happen.

    Only the tail is published. A long job can log a few hundred lines, and the
    page shows the last screenful: sending the whole list once a second would be
    bytes nobody reads. The full list still comes back in the ``/run`` response.
    """

    #: How many trailing lines the poll carries. The box scrolls; older lines
    #: are only ever seen by someone who scrolls up, and the final response
    #: hands over the complete list anyway.
    TAIL = 200

    def _emit(self, prefix: str, message: str) -> None:
        super()._emit(prefix, message)
        _publish_run(lines=list(self.lines[-self.TAIL:]))



def _thumb_dir() -> Path:
    """Where thumbnail bytes are cached, under the server's own directory.

    Deliberately NOT under ``output/``: that directory is the pipeline's
    deliverable folder, and a cache of other people's video frames does not
    belong in the same tree as the user's rendered clips.
    """
    path = WEB_DIR / _THUMB_DIR_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def _fetch_thumb_bytes(item: dict) -> bytes | None:
    """Resolve a thumbnail URL for one scrap result and return its bytes.

    A stored ``thumb`` wins: the Instagram listing already carries the signed
    CDN URL from the GraphQL payload, so a 300-item profile would otherwise pay
    300 extractions just to decorate the rows. yt-dlp is the fallback, for the
    flat-playlist shapes that carry no image at all.
    """
    url = str(item.get("url") or item.get("webpage_url") or "").strip()
    if not url:
        return None
    thumb = str(item.get("thumb") or "").strip()
    if not thumb:
        config = config_mod.ClipConfig(url=url)
        args = item.get("_ytdlp_args")
        if isinstance(args, list):
            config.extra_ytdlp_args = [str(a) for a in args]
        config.cache_dir = _thumb_dir().parent / "cache"
        meta = download_mod.fetch_metadata(url, config)
        thumb = str(meta.get("thumbnail") or "").strip()
        if not thumb:
            thumbs = meta.get("thumbnails")
            if isinstance(thumbs, list) and thumbs:
                last = thumbs[-1]
                if isinstance(last, dict):
                    thumb = str(last.get("url") or "").strip()
    if not thumb:
        return None
    request = Request(thumb, headers={"User-Agent": "Mozilla/5.0", "Accept": "image/*"})
    with urlopen(request, timeout=20) as response:  # noqa: S310 - url comes from yt-dlp
        return response.read(_THUMB_MAX_BYTES)


def _scrap_thumb(item: dict) -> str | None:
    """Return the served path of one item's thumbnail, or None.

    Never raises. A thumbnail is decoration: a private post, an expired CDN
    signature or a slow host must leave the row intact and just without an
    image. Letting this bubble would turn a working list into "a busca falhou".
    """
    try:
        media_id = str(item.get("id") or "").strip()
        if not media_id:
            return None
        safe = "".join(ch for ch in media_id if ch.isalnum() or ch in "-_")[:64]
        if not safe:
            return None
        target = _thumb_dir() / f"{safe}.jpg"
        if not target.is_file():
            blob = _fetch_thumb_bytes(item)
            if not blob:
                return None
            tmp = target.with_suffix(".jpg.part")
            tmp.write_bytes(blob)
            tmp.replace(target)
        return f"/api/thumb/{quote(target.name)}"
    except Exception:  # noqa: BLE001 - decoration must never break the list
        return None


def _archive_thumb_path(position: int) -> Path | None:
    """Cached thumbnail file of the ``position``-th archived item, or None.

    Never raises: a capa e decoracao — um CDN expirado ou um item sem
    thumbnail deixa o card sem imagem, nunca derruba a lista.
    """
    try:
        with _lock:
            full = list(_state.get("archive_items_full") or [])
            username = (_state.get(_ARCHIVE_SLOT) or {}).get("username", "") or "perfil"
        if not 0 <= position < len(full):
            return None
        entry = full[position]
        url = str(entry.get("thumbnail") or "").strip()
        if not url.startswith("http"):
            return None
        safe = "".join(
            ch for ch in f"arch-{username}-{entry.get('code', '')}"
            if ch.isalnum() or ch in "-_"
        )[:64]
        if not safe:
            return None
        target = _thumb_dir() / f"{safe}.jpg"
        if not target.is_file():
            request = Request(
                url, headers={"User-Agent": "Mozilla/5.0", "Accept": "image/*"})
            with urlopen(request, timeout=20) as response:  # noqa: S310 - url came from Instagram
                blob = response.read(_THUMB_MAX_BYTES)
            if not blob:
                return None
            tmp = target.with_suffix(".jpg.part")
            tmp.write_bytes(blob)
            tmp.replace(target)
        return target
    except Exception:  # noqa: BLE001 - decoration must never break the list
        return None


#: A channel URL with no tab resolves to a playlist OF playlists — "Videos",
#: "Live", "Shorts" — so expanding it yields three sub-tabs and zero videos.
#: yt-dlp explains this itself: "The URL does not have a videos tab, but it has
#: videos. Use .../@handle/videos". Appending the tab is what makes a pasted
#: profile URL behave the way someone pasting it expects.

#: Path segments that mean "this URL names one item, not an account".










def _publicacao_payload() -> dict:
    """O que a aba de publicacao precisa para montar o post de cada clipe.

    Le o MESMO ``clips.json`` que a galeria ja consome -- nao ha uma segunda
    fonte, so um segundo recorte. A galeria quer os arquivos; a publicacao quer
    o texto que vai no campo de descricao da plataforma.

    O curador ja escreve ``headline`` e ``hashtags`` por clipe (ver
    ``docs/curador.md``); ate agora eles so apareciam no ``clips.md``, que e
    markdown e ninguem cola no TikTok. Aqui eles voltam estruturados.

    As hashtags NAO entram no video de proposito: TikTok e Reels as recebem
    como metadado do post, e uma hashtag desenhada no quadro e uma hashtag que
    a plataforma ignora.
    """
    base = (REPO_ROOT / "output").resolve()
    report_path = base / "clips.json"
    rel_report = _relative_to_repo(report_path)
    if not report_path.is_file():
        # Primeira execucao. Nao e erro: a pagina diz o que fazer.
        return {"exists": False, "path": rel_report, "clips": []}
    try:
        document = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"exists": True, "path": rel_report, "clips": [], "error": str(exc)}

    clips: list[dict] = []
    for clip in document.get("clips") or []:
        if not isinstance(clip, dict):
            continue
        # `file` chega ABSOLUTO do motor. A pagina so pode pedir o que esta sob
        # `output/` (e `/api/clips/` recusa o resto), entao o caminho e
        # relativizado aqui em vez de confiar no que veio do arquivo.
        rel = ""
        bruto = str(clip.get("file") or "")
        if bruto:
            try:
                rel = str(Path(bruto).resolve().relative_to(base)).replace("\\", "/")
            except (ValueError, OSError):
                rel = ""
        poster = ""
        if rel:
            clipe_em_disco = base / rel
            imagem = _poster_path(clipe_em_disco)
            if not imagem.is_file() and clipe_em_disco.is_file():
                # Reparo na leitura. Um clipe cujo poster nunca foi escrito --
                # execucao antiga, ou uma feita pela CLI, que nao escreve
                # poster nenhum -- mostraria a caixa 9:16 vazia para sempre,
                # porque nada re-renderiza um clipe que ja' esta' no disco. Uma
                # extracao, na primeira abertura da pagina, e o arquivo fica.
                _write_poster(clipe_em_disco)
            if imagem.is_file():
                poster = str(imagem.relative_to(base)).replace("\\", "/")
        clips.append({
            "index": clip.get("index"),
            "rel": rel,
            "poster": poster,
            "headline": str(clip.get("headline") or ""),
            "hashtags": str(clip.get("hashtags") or ""),
            "duration": clip.get("duration") or 0,
            "start_label": clip.get("start_label") or "",
            "end_label": clip.get("end_label") or "",
            "score": clip.get("score") or 0,
            "hook_terms": clip.get("hook_terms") or [],
            "text": str(clip.get("text") or ""),
        })
    return {
        "exists": True,
        "path": rel_report,
        "title": str(document.get("title") or ""),
        "uploader": str(document.get("uploader") or ""),
        "url": str(document.get("url") or ""),
        # A ficha da origem. Os tres ja estao no relatorio; a pagina nao tem
        # como deduzi-los e sem eles a unica forma de saber QUAL modelo escreveu
        # a legenda seria abrir o JSON a mao.
        "source_duration": document.get("source_duration") or 0,
        "model": str(document.get("model") or ""),
        "engine": str(document.get("engine") or ""),
        "clips": clips,
    }


def _tk_askdirectory() -> str:
    """Open the OS folder dialog on the server machine, return the path.

    The browser sandbox never reveals real local paths to the page, so no
    HTML picker can feed the server a folder. But the server runs on the
    user's own machine — tkinter (stdlib) opens the native dialog there, and
    the chosen path comes back through this endpoint.
    """
    import tkinter
    from tkinter import filedialog

    root = tkinter.Tk()
    try:
        root.withdraw()
        root.attributes("-topmost", True)
        return filedialog.askdirectory(title="Escolher pasta de saída")
    finally:
        try:
            root.destroy()
        except Exception:  # noqa: BLE001 - teardown must not mask the choice
            pass








#: Single callback instance: the struct only keeps the address, so a local
#: would be garbage-collected mid-dialog and the callback would crash it.






def _native_askdirectory() -> str:
    """The OS dialog by whatever means this Python has: tkinter, else Win32."""
    try:
        return _tk_askdirectory()
    except ImportError:
        pass
    if os.name != "nt":
        raise ImportError("tkinter indisponivel neste Python")
    return _win32_askdirectory()


def _browse_native(ask=None) -> dict:
    """Run the native folder dialog and answer with the chosen path.

    ``ask`` is injected by the tests so they never pop a real dialog:
    production passes nothing and gets :func:`_native_askdirectory`.
    """
    try:
        path = (ask or _native_askdirectory)()
    except ImportError:
        return {"error": "nenhum dialogo disponivel neste Python"}
    except Exception as exc:  # noqa: BLE001 - headless server
        return {"error": f"dialogo indisponivel: {exc}"}
    if not path:
        return {"cancelled": True}
    return {"path": str(path)}


class UiServer(http_server.ThreadingHTTPServer):
    """HTTP server that refuses a silent duplicate bind on Windows.

    ``SO_REUSEADDR`` (the ``socketserver`` default on non-Windows) lets two
    processes listen on the same port at once on Windows; connections then go
    to an arbitrary one of them, which is how three orphaned servers ended up
    sharing port 7755. ``SO_EXCLUSIVEADDRUSE`` makes the second bind fail
    loudly instead.
    """

    allow_reuse_address = False

    def server_bind(self):
        import socket

        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


#: Hostnames that mean "this machine". A request whose ``Host`` is one of these
#: is same-origin by definition, because the listener is bound to ``HOST``
#: (127.0.0.1) and only a local process could have addressed it.




class Handler(routes_providers.ProviderRoutes, routes_scrap.ScrapRoutes, routes_transcript.TranscriptRoutes, http_server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "ViralClipper/1.0"

    # Thumbnails are served from /thumb/, i.e. from this same origin, instead of
    # being hot-linked from Instagram's CDN. That is what keeps the policy tight:
    # no `img-src https:` wildcard, and no per-CDN allowlist that would silently
    # blank every image the day a host changes. The styles and scripts are
    # external files served from 'self', so script-src needs no 'unsafe-inline';
    # it stays on style-src because the markup still carries inline style
    # attributes. The font is self-hosted too (web/fonts), so no fonts origin
    # is allowlisted — the page renders with zero external requests.
    CSP = (
        "default-src 'self'; "
        "img-src 'self' data:; "
        "style-src 'self' 'unsafe-inline'; "
        "font-src 'self'; "
        "script-src 'self'; "
        "connect-src 'self'; "
        "media-src 'self' blob:; "
        "frame-ancestors 'none'; "
        "base-uri 'none'"
    )

    def end_headers(self) -> None:
        # Set on every response, JSON included: a policy applied only on the
        # HTML paths is one that a future route quietly escapes.
        self.send_header("Content-Security-Policy", self.CSP)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        super().end_headers()

    def _guard_origin(self) -> bool:
        """Refuse a request whose ``Host`` (or ``Origin``) is not this machine.

        Called at the top of ``do_GET`` and ``do_POST`` so the check cannot be
        forgotten by a route added later. A rejected request gets 403 with a
        one-line reason and never reaches a handler: the point is that
        ``/run`` and ``/ajustes`` are unreachable from a page the user merely
        has open. ``Origin`` is only inspected when present — a plain browser
        navigation does not send it, and its absence is not suspicious.
        """
        host = self.headers.get("Host") or ""
        # The port we are actually bound to, not a constant: the panel accepts
        # --port N, so a Host carrying the wrong port is not this server.
        real_port = self.server.server_address[1]
        if not _host_is_local(host, real_port):
            self._send_json({"error": "host not allowed"}, 403)
            return False
        origin = self.headers.get("Origin")
        if origin:
            parsed = urlparse(origin)
            # Compare on the authority the browser actually used, which is what
            # a rebinding page gets wrong. A same-origin fetch sends the real
            # one; anything else (a null origin from a sandboxed frame, a
            # foreign site) is not this panel.
            if not _host_is_local(parsed.netloc, real_port):
                self._send_json({"error": "origin not allowed"}, 403)
                return False
        return True

    def _send_json(self, payload: dict, code: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        # State, not content: /status and the progress endpoints are the same
        # URL with different bytes a second later, and a cached 200 would show
        # a job as "running" after it already finished.
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_redirect(self, location: str, code: int = 301) -> None:
        """301 para o endereco novo de uma rota que mudou de nome.

        301 (permanente) e nao 302: o nome mudou de vez, e o navegador deve
        guardar e parar de bater no endereco velho. O corpo vai vazio de
        proposito -- quem segue o `Location` nunca o le, e um corpo com HTML
        so criaria uma segunda pagina para manter.

        Existe para o caso de rota de PAGINA, que e' a que o usuario guarda em
        favoritos. As rotas de dados nao precisam: quem as chama e' o JS que o
        proprio servidor entrega, e esse ja vem com o endereco novo.
        """
        self.send_response(code)
        self.send_header("Location", location)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", "0")
        self.end_headers()

    #: Freshness for files whose name never changes (style.css, 1.mp4): five
    #: minutes absorbs a burst of reloads during a session, and a revalidation
    #: via ETag still picks up an edit immediately after.
    _CACHE_ASSET = "public, max-age=300"
    #: HTML is assembled per request and is what the app version lives in.
    _CACHE_PAGE = "no-cache"

    @staticmethod
    def _etag_for(path: Path) -> str:
        """Validator from mtime+size: no hashing of 10 MB videos per request."""
        st = path.stat()
        return '"%x-%x"' % (st.st_mtime_ns, st.st_size)

    def _send_file(self, data: bytes, content_type: str, code: int = 200,
                   cache: str | None = None, etag: str | None = None) -> None:
        # Range support so the <video> elements can seek and lazy-load: the
        # browser asks for "bytes=start-" chunks instead of whole files.
        range_header = self.headers.get("Range")
        # Revalidation first: without it every reload re-downloaded all assets
        # (≈10 MB of hero videos included) because no response ever carried a
        # validator the browser could ask about.
        if etag and self.headers.get("If-None-Match") == etag:
            self.send_response(304)
            if cache:
                self.send_header("Cache-Control", cache)
            self.send_header("ETag", etag)
            self.end_headers()
            return
        if code == 200 and range_header and range_header.startswith("bytes="):
            start_str, _, end_str = range_header[len("bytes="):].partition("-")
            try:
                start = int(start_str) if start_str else 0
                end = int(end_str) if end_str else len(data) - 1
            except ValueError:
                start, end = 0, len(data) - 1
            end = min(end, len(data) - 1)
            if 0 <= start <= end < len(data):
                chunk = data[start:end + 1]
                self.send_response(206)
                self.send_header("Content-Type", content_type)
                self.send_header("Accept-Ranges", "bytes")
                if cache:
                    self.send_header("Cache-Control", cache)
                if etag:
                    self.send_header("ETag", etag)
                self.send_header("Content-Range", f"bytes {start}-{end}/{len(data)}")
                self.send_header("Content-Length", str(len(chunk)))
                self.end_headers()
                self.wfile.write(chunk)
                return
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Accept-Ranges", "bytes")
        if cache:
            self.send_header("Cache-Control", cache)
        if etag:
            self.send_header("ETag", etag)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:  # noqa: N802
        if not self._guard_origin():
            return
        path = urlparse(self.path).path
        if path == "/" or path == "/index.html":
            index = WEB_DIR / "index.html"
            if index.exists():
                self._send_file(index.read_bytes(), "text/html; charset=utf-8",
                                cache=self._CACHE_PAGE, etag=self._etag_for(index))
            else:
                self._send_json({"error": "index.html missing"}, 404)
            return
        if path in {"/biblioteca", "/biblioteca.html"}:
            # O ARQUIVO continua `scrap.html`. "Scrap" e' o nome da acao no
            # codigo (viralclipper/scrap*.py) e o que a pagina faz; o endereco
            # e' o que o usuario le, guarda e ve na barra -- e ali o nome e'
            # "Biblioteca". Trocar o arquivo de nome arrastaria scrap.js e
            # scrap.css junto, sem ganho para quem usa.
            page = WEB_DIR / "scrap.html"
            if page.exists():
                self._send_file(page.read_bytes(), "text/html; charset=utf-8",
                                cache=self._CACHE_PAGE, etag=self._etag_for(page))
            else:
                self._send_json({"error": "scrap.html missing"}, 404)
            return
        if path in {"/scrap", "/scrap.html"}:
            # Endereco antigo. Redireciona em vez de sumir: a Biblioteca e' a
            # unica porta para buscar midia, e um favorito salvo nao pode virar
            # 404 -- a pagina sumiria para quem ja a conhecia.
            self._send_redirect("/biblioteca")
            return
        if path in {"/ajustes", "/ajustes.html"}:
            # Both spellings on purpose: the page links to /ajustes, but a
            # .html suffix is what people type. Without this route the request
            # would fall through to the static handler, where .html is not a
            # known suffix and the file would be *downloaded* instead of shown.
            page = WEB_DIR / "ajustes.html"
            if page.exists():
                self._send_file(page.read_bytes(), "text/html; charset=utf-8",
                                cache=self._CACHE_PAGE, etag=self._etag_for(page))
            else:
                self._send_json({"error": "ajustes.html missing"}, 404)
            return
        if path in {"/publicar", "/publicar.html"}:
            # Pos-render. Mesma dupla grafia de /ajustes, pelo mesmo motivo:
            # sem a rota, `/publicar.html` cairia no ramo estatico, onde
            # `.html` nao e' um sufixo conhecido, e o arquivo seria
            # *baixado* em vez de mostrado.
            page = WEB_DIR / "publicar.html"
            if page.exists():
                self._send_file(page.read_bytes(), "text/html; charset=utf-8",
                                cache=self._CACHE_PAGE, etag=self._etag_for(page))
            else:
                self._send_json({"error": "publicar.html missing"}, 404)
            return
        if path == "/docs":
            # The README, rendered here instead of sent to GitHub: the old
            # button opened a repo URL that 404s (private/renamed), so the
            # one help button in the product led to nothing.
            readme = REPO_ROOT / "README.md"
            if readme.exists():
                body = _docs_page(readme.read_text(encoding="utf-8"))
                self._send_file(body.encode("utf-8"), "text/html; charset=utf-8",
                                cache=self._CACHE_PAGE, etag=self._etag_for(readme))
            else:
                self._send_json({"error": "README.md missing"}, 404)
            return
        if path == "/api/scrap/thumb":
            # The <img> tag hits this directly: a GET, not the POST above.
            # Both exist because only the caller knows which it needs — the
            # page uses the GET from the tag and lets the server do the work,
            # while the POST is there for a caller that already holds the item
            # dict and wants the resolved path back.
            query = parse_qs(urlparse(self.path).query)
            index = (query.get("i") or [""])[0]
            try:
                position = int(index)
            except (TypeError, ValueError):
                self._send_json({"error": "bad index"}, 400)
                return
            self._send_thumb_at(position, (query.get("s") or [""])[0])
            return
        if path == "/api/scrap/archive/thumb":
            # Capa do N-esimo item do ultimo arquivamento, para os cards do
            # progresso. Posicao em vez de id: a pagina monta o <img> direto.
            query = parse_qs(urlparse(self.path).query)
            try:
                position = int((query.get("i") or [""])[0])
            except (TypeError, ValueError):
                self._send_json({"error": "bad index"}, 400)
                return
            target = _archive_thumb_path(position)
            if target is None or not target.is_file():
                self._send_json({"error": "no thumbnail"}, 404)
                return
            self._send_file(target.read_bytes(), "image/jpeg",
                            cache=self._CACHE_ASSET, etag=self._etag_for(target))
            return
        if path.startswith("/api/thumb/"):
            # Cached thumbnail bytes. The name is validated against the cache
            # directory itself rather than pattern-matched: ``..\..\`` and an
            # absolute path both resolve outside the folder, and the resolved
            # path is what decides.
            name = unquote(path[len("/api/thumb/"):])
            candidate = (_thumb_dir() / name).resolve()
            try:
                candidate.relative_to(_thumb_dir().resolve())
            except ValueError:
                self._send_json({"error": "not found"}, 404)
                return
            if candidate.is_file():
                self._send_file(candidate.read_bytes(), "image/jpeg",
                                cache=self._CACHE_ASSET, etag=self._etag_for(candidate))
            else:
                self._send_json({"error": "not found"}, 404)
            return
        if path == "/api/providers":
            self._send_json(routes_providers._providers_payload())
            return
        if path == "/api/providers/user":
            # The card's own listing. Same payload as /providers because the
            # dropdown and the card must never disagree about what exists; the
            # ``user`` flag is what the card filters on.
            self._send_json(routes_providers._providers_payload())
            return
        if path == "/api/prompts/curador":
            self._send_json(_curator_prompt_payload())
            return
        if path == "/api/status":
            with _lock:
                snapshot = dict(_state)
            # The download/archive records have their own endpoints; keeping them
            # out of /status leaves that payload the {jobs, clips} contract.
            snapshot.pop(_DOWNLOAD_SLOT, None)
            snapshot.pop(_ARCHIVE_SLOT, None)
            snapshot.pop(_RUN_SLOT, None)
            self._send_json(snapshot)
            return
        if path == "/api/health":
            # Sobre o PROCESSO, nao sobre o trabalho: `/api/status` responde "o
            # que esta rodando", este responde "esta de pe, qual versao, e cabe
            # um render?". A separacao e' o ponto -- um probe de saude nao pode
            # depender do estado dos jobs para dizer que o servidor subiu.
            self._send_json(self._health_payload())
            return
        if path == "/api/run/progress":
            with _lock:
                record = dict(_state.get(_RUN_SLOT) or _run_record())
            # `elapsed` is computed here rather than stored: a worker that died
            # mid-job would leave a frozen number behind, and the page shows
            # this one every second anyway.
            if record.get("active") and record.get("started_at"):
                record["elapsed"] = round(time.time() - record["started_at"], 1)
            self._send_json(record)
            return
        if path == "/api/ajustes":
            settings, malformed = _load_ajustes()
            self._send_json({
                "settings": _ajustes_payload(settings),
                "path": _relative_to_repo(AJUSTES_PATH),
                "exists": AJUSTES_PATH.is_file(),
                # Nao-vazio = o arquivo existe e nao foi possivel ler. A pagina
                # AVISA em vez de dizer "nada salvo ainda", que era o que fazia
                # o usuario concluir que era a primeira execucao.
                "malformed": malformed,
            })
            return
        if path == "/api/scrap/download/progress":
            with _lock:
                record = dict(_state.get(_DOWNLOAD_SLOT) or _download_record())
            self._send_json(record)
            return
        if path == "/api/scrap/archive/progress":
            with _lock:
                record = dict(_state.get(_ARCHIVE_SLOT) or _archive_record())
            self._send_json(record)
            return
        if path == "/api/publicacao":
            self._send_json(_publicacao_payload())
            return
        if path == "/api/saida":
            # Everything playable in the output folder, not just the last job.
            base = (REPO_ROOT / "output").resolve()
            self._send_json({"files": list_library(base)})
            return
        if path == "/api/browse/native":
            # Native OS dialog on the server machine: the only picker that
            # returns a real local path, since the browser hides them all.
            # The request hangs while the dialog is open — same as /run.
            self._send_json(_browse_native())
            return
        if path.startswith("/api/clips/"):
            # Serve a rendered clip from the output dir. The UI passes the
            # relative path it received from /run. The browser percent-encodes
            # accented file names ("nao" is fine, "não" arrives as "na%C3%A3o"),
            # so the segment must be decoded before it touches the filesystem.
            rel = unquote(path[len("/api/clips/"):])
            base = (REPO_ROOT / "output").resolve()
            candidate = resolve_within(base, rel)
            if candidate is None:
                # A traversal attempt and a missing file get the same answer:
                # neither reveals anything about the filesystem.
                self._send_json({"error": "not found"}, 404)
                return
            # Videos and their posters are both served from here, so the type
            # comes from the suffix table (the same one the static route uses)
            # instead of a hardcoded mp4 check: an image/jpeg sent as
            # octet-stream is refused by nosniff and the poster strip stays
            # blank.
            ctype = asset_content_type(candidate)
            self._send_file(candidate.read_bytes(), ctype,
                            cache=self._CACHE_ASSET, etag=self._etag_for(candidate))
            return
        # Static assets inside web/ (also percent-decoded, same reason).
        asset = resolve_within(WEB_DIR, unquote(path.lstrip("/")))
        if asset is not None:
            # Sufixos que as paginas carregam, e nada mais: `server.py` mora em
            # web/ junto com o CSS, entao sem este corte `GET /server.py` — e
            # qualquer `__pycache__/*.pyc` que uma edicao deixe para tras —
            # sairia servido pelo mesmo ramo que serve os assets. 404 igual a
            # arquivo inexistente: a resposta nao confirma o que existe.
            if asset.suffix.lower() not in _ASSET_TYPES:
                self._send_json({"error": "not found"}, 404)
                return
            # The pages keep their CSS, JS and preview media in sibling files,
            # so the type has to be named correctly: nosniff is on, and a
            # script served as octet-stream is refused by the browser while the
            # hero video just never plays.
            ctype = asset_content_type(asset)
            self._send_file(asset.read_bytes(), ctype,
                            cache=self._CACHE_ASSET, etag=self._etag_for(asset))
            return
        self._send_json({"error": "not found"}, 404)

    def _read_payload(self) -> dict | None:
        """Le o corpo JSON, ou responde 400 e devolve None.

        Existe porque POST e PUT precisam exatamente do mesmo tratamento de
        entrada, e duplicar o bloco faria as duas rotas divergirem na primeira
        mudanca -- uma delas responderia 400 e a outra estouraria.
        """
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            return json.loads(raw.decode("utf-8") or "{}")
        except json.JSONDecodeError:
            self._send_json({"error": "invalid json"}, 400)
            return None

    def do_PUT(self) -> None:  # noqa: N802
        """PUT = substituir o recurso inteiro. Sao dois endpoints, e so dois.

        `/api/ajustes` e `/api/prompts/curador` recebem o objeto COMPLETO --
        um arquivo de configuracao e um prompt. PUT e' o verbo que diz
        exatamente isso.

        Antes eram POST no MESMO caminho do GET, o que obrigava a ler o
        handler para saber se aquilo substituia ou acrescentava: `POST
        /ajustes` nao dava para distinguir de "criar um ajuste novo". Agora o
        verbo carrega a informacao, e `GET /api/ajustes` + `PUT /api/ajustes`
        e' o par ler/escrever do mesmo recurso.

        Nao existe PUT para acao: `POST /api/run` e `POST /api/scrap/*` fazem
        coisas, nao substituem recurso nenhum, e continuam POST.
        """
        if not self._guard_origin():
            return
        path = urlparse(self.path).path
        payload = self._read_payload()
        if payload is None:
            return
        if path == "/api/ajustes":
            self._handle_save_ajustes(payload)
            return
        if path == "/api/prompts/curador":
            self._handle_save_curator_prompt(payload)
            return
        self._send_json({"error": "not found"}, 404)

    def do_POST(self) -> None:  # noqa: N802
        """POST = executar uma acao. Nao substitui recurso nenhum.

        Tudo aqui tem efeito colateral que nao e' "o novo estado deste
        objeto": dispara o pipeline, busca no Instagram, baixa, normaliza. As
        duas unicas rotas que SUBSTITUIAM algo (config e prompt) sairam daqui
        para o `do_PUT`, que e' o verbo certo para elas.
        """
        if not self._guard_origin():
            return
        path = urlparse(self.path).path
        payload = self._read_payload()
        if payload is None:
            return
        if path == "/api/scrap":
            self._handle_scrap(payload)
            return
        if path == "/api/scrap/thumb":
            self._handle_thumb(payload)
            return
        if path == "/api/scrap/archive":
            self._handle_archive(payload)
            return
        if path == "/api/scrap/download":
            self._handle_selected_download(payload)
            return
        if path == "/api/transcript/normalize":
            self._handle_normalize(payload)
            return
        if path == "/api/providers/save":
            self._handle_save_provider(payload)
            return
        if path == "/api/providers/remove":
            self._handle_remove_provider(payload)
            return
        if path == "/api/providers/test":
            self._handle_test_provider(payload)
            return
        if path != "/api/run":
            self._send_json({"error": "not found"}, 404)
            return
        options = payload.get("options") or {}
        plan_only = bool(payload.get("plan_only"))
        if not str(options.get("url") or "").strip():
            self._send_json({"error": "url is required"}, 400)
            return
        free = _disk_free_bytes()
        if free is not None and free < MIN_FREE_MB * 1024 * 1024:
            # Recusar ANTES de comecar. Um render que morre no meio deixa
            # `output/` com clipes pela metade, e o erro real (`Errno 28`) sobe
            # de dentro do ffmpeg, longe da causa. O numero vai na resposta
            # porque "disco cheio" sem medida nao diz quanto liberar.
            #
            # Vem DEPOIS da validacao da url: um pedido sem url e' recusado por
            # si mesmo, e nao precisa consultar o disco para isso.
            self._send_json({
                "error": (
                    f"espaco em disco insuficiente: {free / (1024 * 1024):.0f} MB livres, "
                    f"{MIN_FREE_MB} MB necessarios para comecar um render"
                ),
                "free_mb": round(free / (1024 * 1024), 1),
                "min_free_mb": MIN_FREE_MB,
            }, 507)
            return
        result = _run_job(options, plan_only)
        self._send_json(result)

    def _health_payload(self) -> dict:
        """The process itself: version, pid, port and free space.

        The port comes from ``self.server.server_address`` and not from the
        ``PORT`` constant on purpose: the panel accepts ``--port N``, and a
        health check that reports the default while listening elsewhere is worse
        than no health check at all.

        ``free_mb`` is ``None`` when the volume could not be measured -- the
        honest answer, and the one that keeps ``room_for_a_render`` from turning
        a failed measurement into a false alarm.
        """
        free = _disk_free_bytes()
        return {
            "ok": True,
            "version": VIRALCLIPPER_VERSION,
            "pid": os.getpid(),
            "host": HOST,
            "port": self.server.server_address[1],
            "free_mb": None if free is None else round(free / (1024 * 1024), 1),
            "min_free_mb": MIN_FREE_MB,
            "room_for_a_render": free is None or free >= MIN_FREE_MB * 1024 * 1024,
        }

    def _handle_save_ajustes(self, payload: dict) -> None:
        """Persist the panel's settings to ``ajustes.toml``.

        Merges instead of replacing: a page that does not render every key must
        not delete the ones it does not know about, which is exactly what would
        happen the first time an older tab saved over a newer file. Keys outside
        AJUSTES_KEYS are ignored outright -- this endpoint must not become a way
        to write ``url`` or ``output_dir`` into the config from the browser.
        """
        incoming = payload.get("settings")
        if not isinstance(incoming, dict):
            self._send_json({"error": "settings must be an object"}, 400)
            return

        merged = dict(_read_ajustes())
        for key in AJUSTES_KEYS:
            if key in incoming:
                merged[key] = incoming[key]

        try:
            _write_atomically(AJUSTES_PATH, _dump_ajustes(merged).encode("utf-8"))
        except OSError as exc:
            self._send_json({"error": f"cannot write {AJUSTES_PATH}: {exc}"}, 500)
            return

        # The transcript's own "which video is this" stamp. Sent by the page
        # next to the transcript rather than derived here, because only the page
        # knows which URL the text on screen was pasted for. A cleared
        # transcript drops the stamp with it, so the pair never survives half.
        if "transcript_text" in incoming:
            texto = merged.get("transcript_text")
            _write_transcript_source(
                str(payload.get("transcript_source_url") or "") if texto else ""
            )

        # Answer with what was actually stored, not with what was sent: the page
        # reapplies this response, so a value dropped or normalised on the way
        # in shows up on screen instead of diverging silently from the file.
        self._send_json({
            "settings": _ajustes_payload(),
            "path": _relative_to_repo(AJUSTES_PATH),
        })

    def _handle_thumb(self, payload: dict) -> None:
        """Resolve one result's thumbnail after the list is already on screen.

        Two reasons this is a separate call rather than part of ``/scrap``:
        Instagram flat entries carry no image, so every thumbnail costs a full
        per-video metadata extraction — on a 100-item profile that would turn a
        fast list into a minute of waiting. And the failures are per item: one
        private post must not blank the other 99 rows.
        """
        item = payload.get("item")
        if not isinstance(item, dict):
            self._send_json({"error": "item is required"}, 400)
            return
        self._send_json({"thumb": _scrap_thumb(item)})

    def _send_thumb_at(self, position: int, _stamp: str) -> None:
        """Serve the thumbnail of the ``position``-th item of the last search.

        Keyed by position rather than by id so the page can put the URL straight
        into an ``<img src>`` without first knowing the id — and so the server,
        not the page, decides what "the item at row 3" currently means. ``_stamp``
        is only a cache-buster and is deliberately unused: the page bumps it to
        force a refetch when a new search reuses the same row numbers.
        """
        with _lock:
            last = list(_state.get("scrap") or [])
        if not 0 <= position < len(last):
            self._send_json({"error": "not found"}, 404)
            return
        served = _scrap_thumb(last[position])
        if not served:
            # 404, not an empty image: the page watches for the error event and
            # falls back to the placeholder, which is the honest outcome.
            self._send_json({"error": "no thumbnail"}, 404)
            return
        candidate = _thumb_dir() / Path(served).name
        if not candidate.is_file():
            self._send_json({"error": "no thumbnail"}, 404)
            return
        self._send_file(candidate.read_bytes(), "image/jpeg",
                        cache=self._CACHE_ASSET, etag=self._etag_for(candidate))

    def _handle_archive(self, payload: dict) -> None:
        """Baixa o catalogo de um perfil para ``reels/`` e ``posts/``.

        Diferente de ``/scrap`` (que so lista metadados) e de ``/run`` (que
        analisa UM video e produz clipes): aqui o produto e um arquivo da conta
        inteira. Responde o ACEITE e roda numa thread — um perfil com dezenas
        de itens leva minutos, e a pagina acompanha o record em
        ``/scrap/archive/progress`` com a barra de progresso.
        """
        profile = str(payload.get("profile") or "").strip()
        cookies_file = str(payload.get("cookies_file") or "").strip()
        if not profile:
            self._send_json({"error": "informe o perfil"}, 400)
            return
        if not cookies_file:
            self._send_json(
                {"error": "Informe o arquivo cookies.txt: o Instagram só devolve "
                          "o catálogo para uma sessão autenticada."},
                400,
            )
            return

        # Antes da thread: o worker le o MESMO arquivo, entao a sessao colada
        # tem de estar gravada nele quando o download comecar — e o yt-dlp, que
        # baixa a midia, tambem le esse arquivo.
        _ig_session_from_payload(payload)

        only = str(payload.get("only") or "").strip()
        kinds = [part.strip() for part in only.split(",") if part.strip()]
        valid = {ig_profile_mod.REELS_DIR, ig_profile_mod.POSTS_DIR}
        unknown = [k for k in kinds if k.lower() not in valid]
        if unknown:
            self._send_json(
                {"error": f"only aceita {', '.join(sorted(valid))}"}, 400
            )
            return

        try:
            limit = int(payload.get("max_items") or 0) or None
        except (TypeError, ValueError):
            limit = None

        order = str(payload.get("order") or "").strip().lower()
        if order not in ("", "recent", "viral"):
            self._send_json(
                {"error": "order aceita recent ou viral"}, 400
            )
            return
        if not order:
            order = "recent"

        with _lock:
            current = _state.get(_ARCHIVE_SLOT) or {}
            if current.get("active"):
                self._send_json(
                    {"error": "já existe um arquivamento em andamento; espere ele terminar"},
                    409,
                )
                return
            _state[_ARCHIVE_SLOT] = _archive_record(
                active=True, state="listando", phase="listando",
                title=f"@{profile}",
            )

        logger = CollectingLogger()
        threading.Thread(
            target=_archive_worker,
            args=(profile, cookies_file, kinds, limit, logger, order),
            name="scrap-archive",
            daemon=True,
        ).start()

        self._send_json({"started": True, "profile": profile})

    def _handle_selected_download(self, payload: dict) -> None:
        """Baixa exatamente os itens marcados na lista de resultados.

        Diferente de ``/scrap/archive`` (que le o catalogo de um perfil do
        Instagram a partir de cookies), aqui a lista ja esta na tela e o que
        chega e a selecao do usuario: cada URL e baixada por si, sem depender de
        sessao nem de o site ter um catalogo paginado. Serve para YouTube,
        TikTok e Instagram igualmente.

        Roda sincrono, como ``/scrap`` e ``/scrap/archive``: o resultado que
        importa e quantos arquivos foram escritos e onde.
        """
        raw_items = payload.get("items")
        if not isinstance(raw_items, list) or not raw_items:
            self._send_json({"error": "nenhum item selecionado"}, 400)
            return

        targets: list[download_mod.MediaTarget] = []
        for raw in raw_items[:_MAX_SELECTED_DOWNLOADS]:
            if not isinstance(raw, dict):
                continue
            url = str(raw.get("url") or "").strip()
            if not url:
                continue
            targets.append(
                download_mod.MediaTarget(
                    url=url,
                    media_id=str(raw.get("id") or ""),
                    title=str(raw.get("title") or ""),
                )
            )
        if not targets:
            self._send_json({"error": "nenhum item selecionado tem URL"}, 400)
            return

        # Uma pasta por busca, para duas buscas nao misturarem arquivos. O nome
        # vem do titulo da lista ("ANCAPSU - Vídeos"), que e o que o usuario ve.
        collection = str(payload.get("collection") or "").strip()
        slug = util.slugify(collection, fallback="selecionados", max_length=40)
        root = (REPO_ROOT / "output" / "downloads" / slug).resolve()

        cookies_file = str(payload.get("cookies_file") or "").strip()
        # A sessao colada tambem vale aqui: quem baixa a midia e o yt-dlp, e ele
        # le o MESMO jar. Sem gravar, uma busca autenticada seguida de download
        # baixaria os itens como anonimo.
        _ig_session_from_payload(payload)
        logger = CollectingLogger()
        try:
            config = _options_to_config(
                {"url": "", "output": str(root), "cookies_file": cookies_file}
            )
        except ClipperError as exc:
            self._send_json({"error": str(exc)}, 400)
            return

        # O download roda fora da thread da requisicao: uma selecao de vinte
        # videos leva minutos, e uma requisicao que fica minutos sem responder
        # nao tem como mostrar progresso nenhum. A pagina recebe o aceite e
        # acompanha o record em /scrap/download/progress.
        with _lock:
            current = _state.get(_DOWNLOAD_SLOT) or {}
            if current.get("active"):
                self._send_json(
                    {"error": "já existe um download em andamento; espere ele terminar"},
                    409,
                )
                return
            _state[_DOWNLOAD_SLOT] = _download_record(
                active=True,
                state="baixando",
                phase="item",
                total=len(targets),
                index=0,
                root=_relative_to_repo(root),
            )

        threading.Thread(
            target=_download_worker,
            args=(targets, root, config, logger),
            name="scrap-download",
            daemon=True,
        ).start()

        self._send_json(
            {
                "started": True,
                "total": len(targets),
                "root": _relative_to_repo(root),
            }
        )

    def _handle_save_curator_prompt(self, payload: dict) -> None:
        """Write the curator prompt to the server's own path.

        The destination is ``CURATOR_PROMPT_PATH`` and never anything the
        request names: a "save my settings" route that takes the path from the
        body is an arbitrary file writer with extra steps. The body carries the
        text and nothing else.

        An empty body is refused rather than written. ``load_curator_prompt``
        treats an empty file as fatal, so accepting it here would let one
        accidental click turn every later run into a hard failure with an error
        pointing at a file the user believes they filled in.
        """
        text = payload.get("text")
        if not isinstance(text, str):
            self._send_json({"error": "text is required"}, 400)
            return
        if not text.strip():
            self._send_json({"error": "o prompt nao pode ficar vazio"}, 400)
            return
        encoded = text.encode("utf-8")
        if len(encoded) > MAX_CURATOR_PROMPT_BYTES:
            self._send_json(
                {"error": f"prompt maior que {MAX_CURATOR_PROMPT_BYTES} bytes"}, 400
            )
            return
        # Grava com bytes e com o separador normalizado. `write_text` no Windows
        # converte todo `\n` em `\r\n`, e o arquivo versionado e LF: o resultado
        # era um diff de uma linha por linha do arquivo e um prompt que nao
        # batia com o que o git guarda. O painel e um editor de texto do repo;
        # ele tem de gravar no dialeto do repo.
        encoded = encoded.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
        path = CURATOR_PROMPT_PATH
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            # Atomico pelo mesmo motivo do ajustes.toml, e aqui pesa mais: um
            # arquivo truncado nao e "um prompt ruim", e um run que falha duro
            # mais tarde apontando para um arquivo que o usuario acha que
            # preencheu (``load_curator_prompt`` trata vazio como fatal).
            _write_atomically(path, encoded)
        except OSError as exc:
            self._send_json({"error": f"nao consegui gravar {path}: {exc}"}, 500)
            return
        self._send_json({"ok": True, "path": str(path), "bytes": len(encoded)})

    def log_message(self, *args) -> None:  # keep the console quiet
        return


def _parse_port(argv: list[str]) -> int:
    """Read ``--port N`` (or ``PORT=N``) from the command line.

    The port used to be a constant, which made a stale listener a hard stop with
    no way out except killing processes. It stays defaulting to 7755 so the
    documented URL never changes.
    """
    for index, arg in enumerate(argv):
        if arg in {"--port", "-p"} and index + 1 < len(argv):
            try:
                candidate = int(argv[index + 1])
            except ValueError:
                continue
            if 1 <= candidate <= 65535:
                return candidate
        elif arg.startswith("--port="):
            try:
                candidate = int(arg.split("=", 1)[1])
            except ValueError:
                continue
            if 1 <= candidate <= 65535:
                return candidate
    return PORT


def main(argv: list[str] | None = None) -> int:
    # Same rationale as viralclipper.cli: job logs carry video titles, and a
    # legacy code page would turn the first combining mark into a crash.
    util.configure_stdio()
    port = _parse_port(list(argv if argv is not None else sys.argv[1:]))
    addr = (HOST, port)
    try:
        httpd = UiServer(addr, Handler)
    except OSError as exc:
        print(f"[!] Nao foi possivel abrir a porta {port}: {exc}")
        print(f"[!] Ja existe uma viral-clipper web UI rodando na porta {port}?")
        print(f"[!] Use --port {port + 1} para subir numa porta livre.")
        return 1
    url = f"http://{HOST}:{port}/"
    print(f"[*] viral-clipper web UI: {url}")
    print("[*] CTRL+C para parar")
    try:
        webbrowser.open(url)
    except Exception:  # noqa: BLE001 - headless environments
        pass
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n[*] encerrando")
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
