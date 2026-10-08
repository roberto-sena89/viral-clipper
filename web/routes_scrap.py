"""Rotas de /api/scrap/*: a busca do ViceScrap e o motor que a responde.

Extraido de ``server.py`` pela mesma regra de ``state.py`` e ``routes_providers``:
sai o que nao deixa duas fontes de verdade. Este modulo e' o DONO de
``_ig_profile_results``, ``_scrap_results`` e do handler ``_handle_scrap``.

O handler entra como MIXIN (``ScrapRoutes``) para a suite continuar chamando
tanto ``server.Handler._handle_scrap(obj, payload)`` (nao-ligado) quanto
``self._handle_scrap(payload)`` (ligado) -- e para o despacho em ``do_POST``
seguir valendo SEM tocar no corpo dele, que e' onde o contrato de rotas e' lido
por ``inspect.getsource``.

Por que este corte e' seguro (fecho de chamadas medido em AST): as tres funcoes
nao referenciam nenhum nome de ``server.py`` que o resto do arquivo use. O que
elas precisam vem de fora e tem um so' dono: ``ClipperError`` de
``viralclipper.util``, ``download_mod``/``ig_profile_mod`` do pacote, e de
``state`` os helpers puros (``_options_to_config``, ``_ytdlp_argv``,
``_ig_title``, ``_dedupe_entries``, ``_viral_rank``, ``_as_float``, ``_as_int``,
``_with_videos_tab``, ``_cookies_file_from_args``, ``_ig_session_from_payload``)
mais o estado compartilhado (``_lock``, ``_state``).

``_state`` e' o MESMO dict: este modulo o importa de ``state`` e nunca o
rebinda, e a suite o injeta com ``mock.patch.dict(server._state, ...)`` -- que
muta EM PLACE. Um so' objeto, um so' alvo.
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_WEB_DIR = Path(__file__).resolve().parent
for _caminho in (str(_REPO_ROOT), str(_WEB_DIR)):
    if _caminho not in sys.path:
        sys.path.insert(0, _caminho)

from viralclipper import download as download_mod  # noqa: E402
from viralclipper import ig_profile as ig_profile_mod  # noqa: E402
from viralclipper.util import ClipperError  # noqa: E402

from state import (  # noqa: E402
    _as_float,
    _as_int,
    _cookies_file_from_args,
    _dedupe_entries,
    _ig_session_from_payload,
    _ig_title,
    _lock,
    _options_to_config,
    _state,
    _viral_rank,
    _with_videos_tab,
    _ytdlp_argv,
)


def _ig_profile_results(
    username: str, payload: dict, cookies_file: str, argv: list[str], session: str = ""
) -> tuple[list[dict], str, int]:
    """List an Instagram account through the same call the site itself makes.

    Deliberately NOT through yt-dlp: ``InstagramUserIE`` is disabled upstream
    and its ``_parse_graphql`` looks for a ``sharedData`` blob Instagram stopped
    emitting, so the flat playlist answers "Unable to extract data" for an
    account that is perfectly reachable in a browser. ``ig_profile`` reproduces
    the ``POST /graphql/query`` the React app issues instead.

    Keeping this beside the yt-dlp branch — rather than replacing it — is what
    leaves the YouTube path untouched: only an Instagram profile URL is
    diverted here.

    ``session`` is the sessionid the page pasted; it is already in the jar, and
    travels in memory too so a jar that could not be written still lists.
    """
    if not cookies_file:
        raise ClipperError(
            "Listar um perfil do Instagram exige um cookies.txt: o catálogo só é "
            "devolvido para uma sessão autenticada. Escolha os cookies por "
            "arquivo — a opção do navegador não serve para este caminho."
        )

    limit = payload.get("limit")
    try:
        limit = max(1, min(int(limit), 100)) if limit is not None else 20
    except (TypeError, ValueError):
        limit = 20
    viral = bool(payload.get("viral"))
    # "Mais viralizados": lista até o teto e ordena por views ANTES de cortar.
    # Sem isso o corte traria os N primeiros do feed, não os N maiores.
    fetch_end = 100 if viral else limit

    listing = ig_profile_mod.list_profile(
        username, cookies_file, limit=fetch_end, session=session
    )
    items = list(listing.items)
    if viral:
        items.sort(
            key=lambda item: item.play_count or item.like_count or 0, reverse=True
        )
    items = items[:limit]

    results = []
    for index, item in enumerate(items, start=1):
        results.append(
            {
                "index": index,
                "id": item.code or item.pk,
                "title": _ig_title(item),
                "url": item.url,
                "duration": item.duration,
                "uploader": listing.username,
                # Reels carry play_count, photo posts only like_count. Sending
                # whichever exists keeps the row informative instead of blank.
                "view_count": (
                    item.play_count if item.play_count is not None else item.like_count
                ),
                # The CDN URL from the GraphQL payload. The row never uses it as
                # a src (CSP forbids a foreign origin); it is what lets
                # /scrap/thumb skip a per-item yt-dlp round trip.
                "thumb": item.thumbnail,
                # Same classification the archiver uses, so the chip on the row
                # and the folder a download lands in cannot disagree.
                "folder": item.folder,
                "kind": item.kind,
                # False for a photo or a photo-only carousel: the row can say so
                # instead of letting the download fail with "no video in this
                # post" after a network round trip.
                "has_video": item.has_video,
                "_ytdlp_args": list(argv),
            }
        )
    return results, listing.username, 0


def _scrap_results(payload: dict) -> tuple[list[dict], str, int]:
    """Expand a link or a profile into a list of downloadable videos.

    Returns (results, title, removed): ``removed`` counts the repeated videos
    dropped from a profile listing (same id, or same title + duration).

    Two modes, and the difference is one yt-dlp flag:

    - ``link``  -> a single post/reel/video. Answered by ``fetch_metadata``,
      which already exists and is already what the CLI uses.
    - ``profile`` -> the whole feed of an account. Here the flat playlist is
      read instead: one entry per item, no per-video extraction, which is the
      only shape that stays fast when the account has hundreds of posts.
      An Instagram profile never takes that route — see ``_ig_profile_results``.

    ``--playlist-end`` is appended through ``extra_ytdlp_args``, which
    ``download._base_args`` appends LAST — that is what lifts the
    ``--no-playlist`` hardcoded at the top of the same list. So profile
    expansion needs no change to ``download.py`` at all.
    """
    url = str(payload.get("url") or "").strip()
    mode = str(payload.get("mode") or "link").strip()
    if not url:
        raise ClipperError("Informe o link do vídeo ou o perfil.")

    config = _options_to_config({"url": url, "output": payload.get("output") or "output"})
    # ``extra_ytdlp_args`` is a list of argv words, one flag per element. A bare
    # string is accepted as a convenience and split on whitespace, because
    # ``list("--cookies x.txt")`` silently becomes 21 one-character arguments
    # and yt-dlp then fails with a baffling "unrecognized arguments" list.
    # A whole command line as one element (``["--cookies x.txt"]``) is split
    # too, for the same reason.
    config.extra_ytdlp_args = _ytdlp_argv(payload.get("extra_ytdlp_args"))

    if mode == "link":
        # A single item: reuse the metadata path verbatim, so the answer the
        # panel shows is the same one a run would act on.
        meta = download_mod.fetch_metadata(url, config)
        entries = [meta]
        title = str(meta.get("title") or "")
        removed = 0
    else:
        # Instagram first, and only Instagram: its profile extractor is disabled
        # upstream, so the flat-playlist route below cannot answer for an
        # account. Diverting here — instead of inside download.py — is what
        # leaves the YouTube branch exactly as it was.
        ig_user = ig_profile_mod.profile_username(url)
        if ig_user:
            return _ig_profile_results(
                ig_user,
                payload,
                _cookies_file_from_args(config.extra_ytdlp_args),
                config.extra_ytdlp_args,
                _ig_session_from_payload(payload),
            )
        # Only reach here for a profile-shaped URL. A bare profile name is
        # accepted too, but a full URL is what yt-dlp can resolve without
        # guessing the site.
        limit = payload.get("limit")
        try:
            limit = max(1, min(int(limit), 100)) if limit is not None else 20
        except (TypeError, ValueError):
            limit = 20
        # "Mais viralizados": lista até o teto (100) e ordena por views antes
        # de cortar no limite pedido — sem isso o corte traria os N primeiros
        # do feed, não os N maiores. Sem a flag o custo é o de sempre.
        viral = bool(payload.get("viral"))
        fetch_end = 100 if viral else limit
        config.extra_ytdlp_args += ["--flat-playlist", "--playlist-end", str(fetch_end)]
        tab_url = _with_videos_tab(url)
        info = download_mod.fetch_metadata(tab_url, config)
        entries = list(info.get("entries") or [])
        title = str(info.get("title") or info.get("uploader") or "")
        # One more level: a tab that itself holds playlists. Flatten it, or the
        # list would offer "Videos" as if it were a video.
        flattened: list[dict] = []
        for entry in entries:
            if isinstance(entry, dict) and entry.get("_type") == "playlist":
                flattened.extend(e for e in (entry.get("entries") or []) if isinstance(e, dict))
            elif isinstance(entry, dict):
                flattened.append(entry)
        entries = flattened[:fetch_end]
        # Repetidos fora antes de qualquer corte: o extrator repete o mesmo
        # id entre páginas e reposts dividem título + duração. Sem isso o
        # limite de N itens vinha com furos e a ordem viral ranqueava cópias.
        entries, removed = _dedupe_entries(entries)
        # The request language that keeps the titles readable also localizes the
        # counts, and yt-dlp reads "57 mi de visualizações" as 57. The repair is
        # one extra listing in English, merged by id; when it fails the list
        # still works, just with the numbers YouTube wrote in words. Repair
        # runs BEFORE the viral sort, or the ranking would use broken numbers.
        download_mod.repair_view_counts(entries, tab_url, config)
        if viral:
            entries.sort(key=_viral_rank)
        entries = entries[:limit]

    results = []
    for index, entry in enumerate(entries, start=1):
        if not isinstance(entry, dict):
            continue
        # ``image`` is the largest still the extractor exposes. Instagram flat
        # entries have no ``thumbnail`` key; YouTube shorts do. Accept both
        # rather than picking one site's spelling.
        thumb = str(entry.get("thumbnail") or entry.get("image") or "").strip()
        results.append(
            {
                "index": index,
                "id": str(entry.get("id") or ""),
                "title": str(entry.get("title") or entry.get("id") or "(sem título)"),
                "url": str(entry.get("webpage_url") or entry.get("url") or ""),
                "duration": _as_float(entry.get("duration")),
                "uploader": str(entry.get("uploader") or entry.get("channel") or ""),
                # Flat extraction may not carry a duration (live, some
                # Instagram shapes). The UI shows what it has rather than a 0.
                "view_count": _as_int(entry.get("view_count")),
                "thumb": thumb,
                # The cookie flags that made THIS search work, carried per item
                # so the thumbnail fetch can repeat the same authenticated
                # request. Without them a private feed lists fine and every
                # image comes back empty.
                "_ytdlp_args": list(config.extra_ytdlp_args),
            }
        )
    return results, title, removed


class ScrapRoutes:
    """O handler de /api/scrap. Mixin do ``Handler``.

    Nao guarda estado proprio e nao define ``__init__``: depende de
    ``self._send_json``, que vem do ``Handler``, e do estado em ``state``.
    """

    def _handle_scrap(self, payload: dict) -> None:
        """Answer the ViceScrap search box.

        Read-only: it enumerates what a URL yields and hands the list back. It
        runs on the request thread and is a metadata fetch, not a download, so
        there is no job to queue — the pick from the list is what feeds /run.

        Failures go back as the same one-line cause the CLI prints, so an
        expired cookie or a private post reads the same in both places.
        """
        try:
            results, title, removed = _scrap_results(payload)
        except ClipperError as exc:
            self._send_json({"error": str(exc)}, 400)
            return
        except Exception as exc:  # noqa: BLE001 - surface anything to the UI
            self._send_json({"error": f"{exc!r}"}, 400)
            return
        self._send_json({"title": title, "count": len(results), "results": results,
                         "removed": removed})
        # Kept so /scrap/thumb can serve the k-th row by index. The page holds
        # the same list, but re-deriving it there means sending every item back
        # through the API, and the URL has to be buildable from the row alone.
        # ``_ytdlp_args`` travels with it because the thumbnail fetch has to
        # repeat the same authenticated request the search made.
        with _lock:
            _state["scrap"] = results
