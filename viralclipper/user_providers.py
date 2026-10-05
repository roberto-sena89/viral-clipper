"""Providers the user adds by hand, kept out of the versioned table.

:mod:`viralclipper.providers` is a table of entries that were *measured* against
real endpoints and reviewed before shipping. That is the right bar for what the
project recommends by default, and the wrong bar for a user who already pays for
something else: making them wait for a commit to try their own endpoint is how a
panel loses to editing a file.

So this module holds the *second* table -- the one the panel writes. It is a
separate file for a specific reason: ``providers.py`` is tracked by git, and a
web route that edits tracked source is a route that turns ``git pull`` into a
merge conflict on the user's own data. A file the panel owns never conflicts.

The merge happens in :func:`all_providers`, so :func:`providers.get_provider`
and :func:`providers.list_providers` see the user's entries exactly like the
built-in ones -- the rest of the codebase cannot tell the difference, which is
the property the original module promised ("adding a provider is a dict entry").

The file is TOML with an array of tables, because the panel already has a TOML
reader and this project prefers a diffable text file to a database
(``archive.py``: "resumability comes from the filesystem, not a database").
"""

from __future__ import annotations

import tomllib
from pathlib import Path

from .providers import Provider
from .util import ClipperError

#: Where the panel keeps the entries the user added. Sibling of
#: ``ajustes.toml`` and, like it, untracked bookkeeping rather than config the
#: CLI must understand -- it is data for the *merge*, not a ``--config`` file.
#:
#: ``web/server.py`` imports this and re-exports it as ``USER_PROVIDERS_PATH``
#: rather than declaring its own copy. Two constants for one file is how the
#: panel ends up saving to path A while the merge reads path B -- which is
#: exactly what happened the first time this module was written, and why the
#: save route answered 200 while the list it returned was unchanged.
USERS_PATH = Path("provedores-usuario.toml")

#: A provider name has to survive being typed on the command line, so it is
#: restricted to what ``argparse`` and a shell agree on. The same shape the
#: built-in names use (``nemotron-super``, ``deepseek-flash``).
_ALLOWED_NAME = set("abcdefghijklmnopqrstuvwxyz0123456789-_")

#: Refuse an endpoint that is not ``http(s)``. A ``file://`` URL here would make
#: the test route read a local file and, worse, be a scheme the probe's
#: ``urlopen`` would happily follow. Bare hostnames are rejected too: the panel
#: has no way to guess the scheme, and a wrong guess is a confusing failure.
_ALLOWED_SCHEMES = ("http://", "https://")


def validate(entry: dict) -> tuple[Provider | None, str]:
    """Turn one panel payload into a :class:`Provider`, or explain the refusal.

    Returns ``(provider, "")`` on success and ``(None, reason)`` on failure, so
    the caller can put the reason in front of the user instead of a traceback.
    Every rule here exists because the alternative fails *late*: a name with a
    space breaks the CLI, a URL without a scheme fails inside ``urlopen`` with a
    message about an unknown protocol, and an empty model is a 404 from a vendor
    the user cannot inspect from here.
    """
    name = str(entry.get("name") or "").strip()
    if not name:
        return None, "o nome do provedor e obrigatorio"
    if not set(name.lower()) <= _ALLOWED_NAME:
        return None, (
            "o nome so pode ter letras, numeros, hifen e sublinhado "
            "(ex.: meu-provedor)"
        )

    base_url = str(entry.get("base_url") or "").strip()
    if not base_url.lower().startswith(_ALLOWED_SCHEMES):
        return None, "o endpoint tem de comecar com http:// ou https://"

    model = str(entry.get("model") or "").strip()
    if not model:
        return None, "o nome do modelo e obrigatorio"

    label = str(entry.get("label") or "").strip() or name

    # ``api_key_env`` may be blank: that is a local endpoint that takes any key,
    # the same shape as the built-in ``local`` entry. It is only a problem when
    # the provider *requires* a key, because then there is a variable name the
    # run needs and the user has not given one.
    api_key_env = str(entry.get("api_key_env") or "").strip()
    # A key pasted into the panel. Kept apart from ``api_key_env`` because they
    # are two ways to say the same thing: the *name* of a variable the machine
    # already exports, or the secret itself for a machine that exports nothing.
    # A blank string is "not given", never "the key is empty" -- sending an empty
    # Authorization header is a 401 the user cannot read.
    api_key = str(entry.get("api_key") or "").strip()
    requires_key = bool(entry.get("requires_key", True))
    # Either one satisfies the requirement: the point of the check is that a
    # *run* has a key to send, not that it arrives by a particular channel.
    if requires_key and not api_key_env and not api_key:
        return None, "provedor que exige chave precisa da variavel de ambiente ou da chave colada"

    # ``or 180.0`` would swallow a legitimate ``0`` and turn it into the default,
    # which is the falsy-zero trap: the entry would be accepted with a timeout
    # nobody asked for. Test for absence, not falsiness.
    raw_timeout = entry.get("timeout")
    if raw_timeout is None or raw_timeout == "":
        timeout = 180.0
    else:
        try:
            timeout = float(raw_timeout)
        except (TypeError, ValueError):
            return None, "o timeout tem de ser um numero"
    if timeout <= 0:
        return None, "o timeout tem de ser maior que zero"

    note = str(entry.get("note") or "").strip()
    if not note:
        # Same contract as the built-in table, and for the same reason: the note
        # is what tells the next person whether the entry is worth picking. A
        # blank one makes the entry indistinguishable from a guess.
        return None, "escreva uma nota: o que este modelo custa e onde voce o testou"

    return (
        Provider(
            name=name,
            label=label,
            base_url=base_url,
            model=model,
            api_key_env=api_key_env,
            api_key=api_key,
            requires_key=requires_key,
            timeout=timeout,
            note=note,
        ),
        "",
    )


def _escape(text: str) -> str:
    """Escape a TOML basic string, the same way ``_dump_ajustes`` does.

    Duplicated rather than imported: the server owns its own writer for
    ``ajustes.toml``, and reaching into ``web/`` from the package would make the
    CLI depend on the panel. The two functions are three lines each and are
    tested independently.
    """
    return (
        text.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\r", "\\r")
        .replace("\n", "\\n")
        .replace("\t", "\\t")
    )


def load(path: Path | None = None) -> list[Provider]:
    """The providers saved by the panel, in file order.

    A missing file is the first run and a malformed one is a file the user can
    edit back into shape; neither is worth taking the panel down for, so both
    yield an empty list. This mirrors ``_read_ajustes``: the panel reports
    "nothing saved yet" instead of the endpoint failing.
    """
    target = Path(path) if path is not None else USERS_PATH
    if not target.is_file():
        return []
    try:
        with target.open("rb") as handle:
            document = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError):
        return []
    rows = document.get("provider")
    if not isinstance(rows, list):
        return []
    found: list[Provider] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        provider, reason = validate(row)
        if provider is not None:
            found.append(provider)
    return found


def dump(entries: list[Provider]) -> str:
    """Serialise the user's providers as TOML, one ``[[provider]]`` per entry.

    Iterating the dataclass fields instead of a dict keeps the key order stable
    between saves, so the diff of a hand-edit is one line and not the file.
    """
    lines = [
        "# Provedores que voce adicionou pela pagina Cortes (o card",
        "# 'Meus provedores'). Este arquivo e do painel: ele o reescreve por",
        "# inteiro a cada save. Os provedores de fabrica vivem em",
        "# viralclipper/providers.py e nao aparecem aqui.",
        "",
    ]
    for provider in entries:
        lines.append("[[provider]]")
        lines.append(f'name = "{_escape(provider.name)}"')
        lines.append(f'label = "{_escape(provider.label)}"')
        lines.append(f'base_url = "{_escape(provider.base_url)}"')
        lines.append(f'model = "{_escape(provider.model)}"')
        lines.append(f'api_key_env = "{_escape(provider.api_key_env)}"')
        # Only written when set: a line for every provider would put an empty
        # ``api_key = ""`` in files that never used the field, and a reader
        # skimming the TOML would have to know blank means absent. The field is
        # a secret in a gitignored file -- ``dump`` is not where it leaks, the
        # browser is, and the payload in ``web/server.py`` does not ship it.
        if provider.api_key:
            lines.append(f'api_key = "{_escape(provider.api_key)}"')
        lines.append(
            "requires_key = " + ("true" if provider.requires_key else "false")
        )
        lines.append(f"timeout = {provider.timeout!r}")
        lines.append(f'note = "{_escape(provider.note)}"')
        lines.append("")
    return "\n".join(lines)


def save(entries: list[Provider], path: Path | None = None) -> Path:
    """Write ``entries`` to the store, returning the path written.

    Bytes, not ``write_text``: on Windows ``write_text`` turns every ``\\n`` into
    ``\\r\\n``, and a file the panel rewrites on each save would then churn its
    own separators. The project's convention for panel-written text is LF.
    """
    target = Path(path) if path is not None else USERS_PATH
    data = dump(entries).encode("utf-8")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    except OSError as exc:
        raise ClipperError(f"nao consegui gravar {target}: {exc}") from exc
    return target


def upsert(provider: Provider, path: Path | None = None) -> list[Provider]:
    """Add ``provider``, replacing an entry of the same name, and persist.

    Replace rather than append: the panel's form is also the edit form, and two
    entries with the same name would make ``get_provider`` depend on file order.
    """
    entries = [p for p in load(path) if p.name != provider.name]
    entries.append(provider)
    save(entries, path)
    return entries


def remove(name: str, path: Path | None = None) -> list[Provider]:
    """Drop the entry called ``name`` and persist. Absent is not an error."""
    entries = [p for p in load(path) if p.name != name]
    save(entries, path)
    return entries


__all__ = [
    "USERS_PATH",
    "dump",
    "load",
    "remove",
    "save",
    "upsert",
    "validate",
]
