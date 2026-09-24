"""Download an Instagram profile's whole catalogue, split by kind.

Why this module exists at all: yt-dlp's ``InstagramUserIE`` is disabled upstream
(``_WORKING = False``) and its ``_parse_graphql`` looks for a ``sharedData`` blob
that Instagram stopped emitting. The profile page still returns HTTP 200 with
the right title and the right post count, but the posts themselves are no longer
in the HTML — the React app fetches them afterwards with a ``POST /graphql/query``
carrying a fixed ``doc_id``. That call is what this module reproduces.

Two things make the reproduction non-obvious, and both cost real debugging time:

* ``doc_id`` is a build artefact, not a public identifier. It changes when
  Instagram ships a new bundle, and there is no way to discover the new value
  from the API itself. When it goes stale every request answers 403 or an HTML
  error page, so the failure has to name that possibility explicitly rather than
  surface as "o feed veio vazio".
* ``/graphql/query`` also demands an ``lsd`` anti-CSRF token and the
  ``csrftoken`` cookie echoed as ``X-CSRFToken``. Without both, the response is
  the page shell, not JSON.

The classification into reels and posts comes from ``product_type``, which the
GraphQL payload does carry: ``clips`` is a reel, everything else is a feed post.
That single field is what makes the folder split reliable — guessing from the
URL shape would mislabel a post shared through the reels tab.
"""

from __future__ import annotations

import http.cookiejar
import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from .util import ClipperError, Logger

#: ``doc_id`` of Instagram web's ``PolarisProfilePostsQuery``. Captured from the
#: live page in September 2026. There is no API that reports the current value;
#: when Instagram rotates it, this constant is what has to change.
PROFILE_DOC_ID = "29015124851429106"

#: The web app id Instagram's own bundle sends. Not a secret — it ships in the
#: public JS — but a request without it is treated as a bot.
APP_ID = "936619743392459"

GRAPHQL_URL = "https://www.instagram.com/graphql/query"

#: Friendly name of the operation. Instagram checks it against ``doc_id``.
FRIENDLY_NAME = "PolarisProfilePostsQuery"

#: Root field the caller expects back. Sent as ``X-Root-Field-Name``.
ROOT_FIELD = "xdt_api__v1__feed__user_timeline_graphql_connection"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

#: Instagram caps a page at a few dozen items; 50 is above the observed maximum
#: of 12-24 and still a single round trip.
PAGE_SIZE = 50

#: ``product_type`` values that mean "this is a reel", mapped to the folder the
#: file belongs in. Anything absent from here is a feed post.
REEL_PRODUCT_TYPES = frozenset({"clips"})

#: Instagram's own ``media_type`` enum. Only 2 carries a video stream; 1 is a
#: still, 8 a carousel whose entries decide for themselves.
MEDIA_TYPE_IMAGE = 1
MEDIA_TYPE_VIDEO = 2
MEDIA_TYPE_CAROUSEL = 8

#: Folder names. Kept as constants because the UI, the CLI help and the tests
#: all have to agree on them.
REELS_DIR = "reels"
POSTS_DIR = "posts"

#: The cursor repeats after the last page (Instagram loops back to the first
#: cursor instead of returning null), so termination is decided by seeing a
#: cursor twice rather than by its absence.
_MAX_PAGES = 200


@dataclass
class ProfileItem:
    """One post or reel of a profile, as listed by the GraphQL feed."""

    code: str
    url: str
    folder: str
    kind: str
    media_type: int | None = None
    pk: str = ""
    caption: str = ""
    like_count: int | None = None
    play_count: int | None = None
    comment_count: int | None = None
    taken_at: int | None = None
    duration: float | None = None
    thumbnail: str = ""
    #: True quando o payload traz vestígio de vídeo fora do ``media_type``
    #: (``video_versions``/``video_url`` no nó ou em algum filho do carrossel).
    #: É o que salva os vídeos da aba "posts": carrossel com vídeo vem com
    #: ``media_type == 8`` e seria pulado como "sem vídeo" sem este sinal.
    video_signal: bool = False

    @property
    def is_reel(self) -> bool:
        return self.folder == REELS_DIR

    @property
    def has_video(self) -> bool:
        """True when there is a video stream to download.

        ``media_type == 2`` é o sinal principal, mas carrossel com vídeo vem
        com ``media_type == 8`` — o vídeo está nos filhos (``carousel_media`` /
        ``children``) ou em ``video_versions``/``video_url`` do próprio nó.
        Sem checar isso, toda a aba "posts" com carrossel virava "sem vídeo"
        e nada baixava. Reels (``product_type == clips``) sempre têm vídeo.
        """
        if self.media_type == MEDIA_TYPE_VIDEO:
            return True
        if self.video_signal:
            return True
        return self.folder == REELS_DIR

    @property
    def is_photo(self) -> bool:
        return self.media_type == MEDIA_TYPE_IMAGE and not self.video_signal


@dataclass
class ProfileListing:
    """The whole catalogue of one profile plus what we learned about it."""

    username: str
    title: str = ""
    items: list[ProfileItem] = field(default_factory=list)
    pages: int = 0
    truncated: bool = False

    @property
    def reels(self) -> list[ProfileItem]:
        return [item for item in self.items if item.is_reel]

    @property
    def posts(self) -> list[ProfileItem]:
        return [item for item in self.items if not item.is_reel]

    def summary(self) -> str:
        return (
            f"@{self.username}: {len(self.items)} itens "
            f"({len(self.reels)} reels, {len(self.posts)} posts) "
            f"em {self.pages} pagina(s)"
        )


def _parse_cookie_file(path: str | Path) -> dict[str, str]:
    """Read a Netscape cookie jar into a name->value mapping.

    The jar yt-dlp writes is CRLF and tab separated. ``http.cookiejar`` handles
    it, but it also folds duplicate names across domains, and a value containing
    a tab would be truncated — reading the columns directly keeps every
    character of the session token.
    """
    source = Path(path)
    if not source.is_file():
        raise ClipperError(
            f"Arquivo de cookies não encontrado: {source}. Exporte os cookies do "
            f"Instagram para um cookies.txt e aponte --cookies para ele."
        )
    jar = http.cookiejar.MozillaCookieJar(str(source))
    try:
        jar.load(ignore_discard=True, ignore_expires=True)
    except (OSError, http.cookiejar.LoadError) as exc:
        raise ClipperError(f"Não consegui ler {source}: {exc}") from exc
    cookies = {cookie.name: cookie.value for cookie in jar}
    if "sessionid" not in cookies:
        raise ClipperError(
            f"{source} não tem o cookie 'sessionid': a sessão não está autenticada "
            f"e o Instagram só devolve o feed para quem está logado."
        )
    return cookies


def _normalise_username(raw: str) -> str:
    """Accept ``@name``, a full profile URL, or a bare name."""
    text = str(raw or "").strip().rstrip("/")
    if not text:
        raise ClipperError("Informe o perfil (ex.: @salmareis ou a URL do perfil).")
    if "instagram.com" in text:
        tail = text.split("instagram.com", 1)[1].strip("/")
        # /stories/<user>, /<user>/reels — the username is the first segment
        # that is not a known prefix.
        for segment in tail.split("/"):
            if segment and segment not in {"p", "reel", "reels", "tv", "stories", "explore"}:
                text = segment
                break
    return text.lstrip("@").split("?")[0]


def _http(cookies: dict[str, str], username: str) -> tuple[str, Exception | None]:
    """Fetch the profile page and return (html, error).

    The page is needed for one reason only: the ``lsd`` token. It lives in the
    HTML of the profile being scraped, so it cannot be fetched once and reused
    for a different account.
    """
    import urllib.error
    from urllib.request import Request, urlopen

    request = Request(
        f"https://www.instagram.com/{username}/",
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html",
            "Cookie": "; ".join(f"{k}={v}" for k, v in cookies.items()),
        },
    )
    try:
        with urlopen(request, timeout=30) as response:  # noqa: S310 - fixed host
            return response.read().decode("utf-8", errors="replace"), None
    except urllib.error.HTTPError as exc:
        return "", exc
    except (OSError, urllib.error.URLError) as exc:
        return "", exc


def _fetch_lsd(cookies: dict[str, str], username: str) -> tuple[str, str]:
    """Return ``(lsd_token, profile_html)``.

    ``/graphql/query`` answers 403 without the token. It is not a cookie, it is
    not stable across sessions, and it is different for every page load — a
    cheap GET of the profile is the price of every listing run. The HTML comes
    back from the same request, so the display name costs nothing extra.
    """
    text, error = _http(cookies, username)
    if error is not None:
        raise ClipperError(
            f"Não consegui abrir https://www.instagram.com/{username}/ ({error})."
        )
    match = re.search(r'"LSD",\[\],\{"token":"([^"]+)"', text)
    if not match:
        raise ClipperError(
            "Não encontrei o token anti-CSRF (LSD) na página do perfil. "
            "Isso acontece quando a sessão expirou ou o Instagram mudou a página. "
            "Reexporte os cookies e tente de novo."
        )
    return match.group(1), text


def _display_name(page_html: str) -> str:
    """Read the profile's display name out of its own page, best effort.

    Returns ``""`` when the page does not carry it. That is the common case for
    a logged-in session: Instagram serves the *shell* plus the React bundle, and
    fills the header from an API call afterwards, so ``<title>`` is just
    "Instagram" and there is no ``og:title``. Logged out (a crawler's view) the
    meta tags are present.

    A missing display name is not worth an extra request: the username already
    identifies the profile in every log line and folder name this module
    produces.
    """
    import html as html_mod

    match = re.search(
        r'<meta\s+property="og:title"\s+content="([^"]*)"', page_html, re.IGNORECASE
    )
    if not match:
        return ""
    title = html_mod.unescape(match.group(1))
    # "Salma Reis (@salmareis) • Instagram photos and videos" -> "Salma Reis"
    return re.split(r"\s*\(@|\s*[•|]\s*Instagram", title)[0].strip()


def _request_page(
    cookies: dict[str, str],
    lsd: str,
    username: str,
    after: str | None,
    page_size: int,
) -> dict:
    """One GraphQL page of the profile feed."""
    from urllib.error import HTTPError, URLError
    from urllib.parse import urlencode
    from urllib.request import Request, urlopen

    variables: dict = {
        "data": {
            "count": page_size,
            "include_reel_media_seen_timestamp": True,
            "include_relationship_info": True,
            "latest_besties_reel_media": True,
            "latest_reel_media": True,
        },
        "username": username,
        "__relay_internal__pv__PolarisMultiCaptionCarouselEnabledrelayprovider": True,
        "__relay_internal__pv__PolarisShortDramaEnabledrelayprovider": False,
        "__relay_internal__pv__PolarisReelsRecoDebugOverlayEnabledrelayprovider": False,
    }
    if after:
        variables["after"] = after

    body = urlencode(
        {
            "__a": "1",
            "__d": "www",
            "__user": "0",
            "server_timestamps": "true",
            "dpr": "1",
            "doc_id": PROFILE_DOC_ID,
            "variables": json.dumps(variables, separators=(",", ":")),
            "fb_api_req_friendly_name": FRIENDLY_NAME,
            "fb_api_caller_class": "RelayModern",
        }
    ).encode("utf-8")

    request = Request(
        GRAPHQL_URL,
        data=body,
        headers={
            "User-Agent": USER_AGENT,
            "X-IG-App-ID": APP_ID,
            "X-ASBD-ID": "359341",
            "X-FB-Friendly-Name": FRIENDLY_NAME,
            "X-FB-LSD": lsd,
            "X-CSRFToken": cookies.get("csrftoken", ""),
            "X-Root-Field-Name": ROOT_FIELD,
            "Origin": "https://www.instagram.com",
            "Referer": f"https://www.instagram.com/{username}/",
            "X-Requested-With": "XMLHttpRequest",
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "*/*",
            "Cookie": "; ".join(f"{k}={v}" for k, v in cookies.items()),
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=30) as response:  # noqa: S310 - fixed host
            raw = response.read().decode("utf-8", errors="replace")
    except HTTPError as exc:
        raise ClipperError(_graphql_error(exc.code, username)) from exc
    except (OSError, URLError) as exc:
        raise ClipperError(f"Falha de rede ao ler o feed de {username}: {exc}") from exc

    stripped = raw.lstrip()
    if not stripped.startswith("{"):
        # An HTML body here means Instagram refused to treat this as an API call.
        raise ClipperError(_graphql_error(0, username, raw))
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ClipperError(
            f"O Instagram devolveu algo que não é JSON para {username}: {exc}"
        ) from exc


def _graphql_error(status: int, username: str, body: str = "") -> str:
    """Explain a refused GraphQL call in terms of what can be fixed."""
    hint = ""
    if status == 403 or "<!DOCTYPE html>" in body[:200]:
        hint = (
            " O Instagram recusou a chamada. Normalmente é o 'doc_id' da operação "
            "PolarisProfilePostsQuery que mudou com uma atualização do site — "
            "nesse caso o extrator precisa ser atualizado."
        )
    elif status == 401 or status == 302:
        hint = " A sessão expirou: reexporte os cookies do Instagram."
    elif status == 429:
        hint = " Muitas requisições: espere alguns minutos e tente de novo."
    return f"O Instagram não devolveu o feed de {username} (HTTP {status or 'HTML'}).{hint}"


def _caption(node: dict) -> str:
    caption = node.get("caption")
    if isinstance(caption, dict):
        return str(caption.get("text") or "")
    return str(caption or "")


def _thumbnail(node: dict) -> str:
    """Best still for the item, accepting both spellings Instagram uses."""
    for key in ("image_versions2", "image_versions"):
        versions = node.get(key)
        if not isinstance(versions, dict):
            continue
        candidates = versions.get("candidates")
        if isinstance(candidates, list) and candidates and isinstance(candidates[0], dict):
            return str(candidates[0].get("url") or "")
        if versions.get("url"):
            return str(versions["url"])
    return str(node.get("thumbnail_url") or node.get("display_url") or "")


def _as_int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _as_float(value) -> float | None:
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return None


def _folder_for(product_type: str, media_type: int | None) -> tuple[str, str]:
    """Decide the destination folder and a human label for one item.

    ``product_type`` is authoritative when present: a reel is a reel even though
    it is also a video, and Instagram reports it as ``clips``. Only when it is
    missing does the media type decide, and then a video stream is the best
    available proxy for "reel" — a still has no business in ``reels/``.
    """
    kind = (product_type or "").strip().lower()
    if kind in REEL_PRODUCT_TYPES:
        return REELS_DIR, "reel"
    if not kind and media_type == MEDIA_TYPE_VIDEO:
        return REELS_DIR, "reel"
    return POSTS_DIR, kind or ("video" if media_type == MEDIA_TYPE_VIDEO else "post")


def _has_video_versions(payload: dict) -> bool:
    """True se o dicionário carrega stream de vídeo direta."""
    if not isinstance(payload, dict):
        return False
    versions = payload.get("video_versions")
    if isinstance(versions, list) and versions:
        return True
    if payload.get("video_url"):
        return True
    # Algumas respostas usam video_duration como vestígio de vídeo.
    try:
        if float(payload.get("video_duration") or 0) > 0:
            return True
    except (TypeError, ValueError):
        pass
    return False


def _carousel_has_video(payload: dict) -> bool:
    """True se algum filho do carrossel (media_type 8) tem vídeo."""
    if not isinstance(payload, dict):
        return False
    for key in ("carousel_media", "children", "carousel_media_count"):
        children = payload.get(key)
        if isinstance(children, dict):
            children = children.get("children") or children.get("items")
        if not isinstance(children, list):
            continue
        for child in children:
            if not isinstance(child, dict):
                continue
            inner = child.get("media") if isinstance(child.get("media"), dict) else child
            try:
                if int(inner.get("media_type")) == MEDIA_TYPE_VIDEO:
                    return True
            except (TypeError, ValueError):
                pass
            if _has_video_versions(inner):
                return True
            # Carrossel aninhado: um nível basta para o feed do Instagram.
            if _carousel_has_video(inner):
                return True
    return False


def _video_signal_from(node: dict, media: dict) -> bool:
    """Sinal complementar ao media_type para a aba posts."""
    for payload in (node, media):
        if _has_video_versions(payload):
            return True
        if _carousel_has_video(payload):
            return True
    return False


def _item_from_node(node: dict) -> ProfileItem | None:
    media = node.get("media") if isinstance(node.get("media"), dict) else node
    code = str(node.get("code") or media.get("code") or "").strip()
    pk = str(node.get("pk") or media.get("pk") or "").strip()
    if not code and not pk:
        return None
    raw_type = node.get("media_type")
    if raw_type is None:
        raw_type = media.get("media_type")
    media_type = _as_int(raw_type)
    folder, kind = _folder_for(
        str(media.get("product_type") or node.get("product_type") or ""), media_type
    )
    # The shortcode is what yt-dlp's InstagramIE accepts. /reel/ and /p/ both
    # resolve to the same media, so the kind only decides which spelling is
    # cosmetic — but keeping it faithful makes the URL recognisable in a log.
    segment = "reel" if folder == REELS_DIR else "p"
    video_signal = _video_signal_from(node, media)
    # Carrossel com vídeo na aba posts: mantém em posts/ mas com download.
    # Só promove para reels/ o vídeo solto sem product_type — carrossel
    # continua sendo post, mesmo com vídeo dentro.
    if folder == POSTS_DIR and video_signal and kind in ("post", "video"):
        kind = "video"
    return ProfileItem(
        code=code,
        pk=pk,
        url=f"https://www.instagram.com/{segment}/{code}/" if code else "",
        folder=folder,
        kind=kind,
        media_type=media_type,
        caption=_caption(node) or _caption(media),
        like_count=_as_int(node.get("like_count") or media.get("like_count")),
        play_count=_as_int(node.get("play_count") or media.get("play_count")),
        comment_count=_as_int(node.get("comment_count") or media.get("comment_count")),
        taken_at=_as_int(node.get("taken_at") or media.get("taken_at")),
        duration=_as_float(node.get("video_duration") or media.get("video_duration")),
        thumbnail=_thumbnail(node) or _thumbnail(media),
        video_signal=video_signal,
    )


def _page_items(document: dict) -> tuple[list[ProfileItem], str | None]:
    """Pull the items and the next cursor out of one page."""
    data = document.get("data") or {}
    connection = data.get(ROOT_FIELD)
    if not isinstance(connection, dict):
        keys = ", ".join(sorted(data)) or "(nenhuma)"
        raise ClipperError(
            f"Resposta do Instagram sem a lista de posts (campos: {keys}). "
            f"Provavelmente o doc_id da operação mudou."
        )
    items: list[ProfileItem] = []
    for edge in connection.get("edges") or []:
        if not isinstance(edge, dict):
            continue
        node = edge.get("node")
        if not isinstance(node, dict):
            continue
        item = _item_from_node(node)
        if item is not None:
            items.append(item)
    page_info = connection.get("page_info") or {}
    cursor = page_info.get("end_cursor")
    return items, (str(cursor) if cursor else None)


def list_profile(
    profile: str,
    cookies_file: str | Path,
    logger: Logger | None = None,
    *,
    limit: int | None = None,
    page_size: int = PAGE_SIZE,
    pause: float = 0.6,
) -> ProfileListing:
    """List every post and reel of ``profile``.

    ``pause`` is deliberate and not a tuning knob: Instagram rate limits a burst
    of paginated GraphQL calls, and a listing is not urgent enough to trade a
    429 for a few seconds.
    """
    username = _normalise_username(profile)
    cookies = _parse_cookie_file(cookies_file)
    lsd, page_html = _fetch_lsd(cookies, username)

    listing = ProfileListing(username=username, title=_display_name(page_html))
    seen_cursors: set[str] = set()
    cursor: str | None = None
    seen_codes: set[str] = set()

    for page_number in range(1, _MAX_PAGES + 1):
        document = _request_page(cookies, lsd, username, cursor, page_size)
        items, next_cursor = _page_items(document)
        listing.pages = page_number
        added = 0
        for item in items:
            key = item.pk or item.code
            if key in seen_codes:
                continue
            seen_codes.add(key)
            listing.items.append(item)
            added += 1

        if logger:
            logger.debug(
                f"página {page_number}: {added} novo(s) de {len(items)} "
                f"({'fim' if not next_cursor else 'segue'})"
            )

        if limit is not None and len(listing.items) >= limit:
            listing.items = listing.items[:limit]
            listing.truncated = True
            break
        # A page that brings nothing new means the feed looped; the cursor is
        # not a reliable terminator because Instagram returns the FIRST cursor
        # again after the last page instead of null.
        if not next_cursor or not added or (next_cursor in seen_cursors):
            break
        seen_cursors.add(next_cursor)
        cursor = next_cursor
        if pause:
            time.sleep(pause)

    if logger:
        logger.ok(listing.summary())
    return listing


__all__ = [
    "POSTS_DIR",
    "PROFILE_DOC_ID",
    "REELS_DIR",
    "ProfileItem",
    "ProfileListing",
    "list_profile",
]
