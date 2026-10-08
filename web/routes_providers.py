"""Rotas de /api/providers/*: o registro de provedores de LLM do painel.

Extraido de ``server.py``. O corte segue a mesma regra de ``state.py``: sai o
que nao deixa duas fontes de verdade.

Este modulo e' o DONO de ``USER_PROVIDERS_PATH`` e de ``_providers_payload``.
``server.py`` importa o modulo em vez de redefinir os dois, e chama
``routes_providers._providers_payload()`` em vez de um nome local. O motivo e'
o mesmo de ``state.py``: a suite injeta dependencia com ``mock.patch.object``,
e um nome re-exportado seria uma SEGUNDA binding -- o patch cairia numa delas e
a outra continuaria lendo o valor real. Um so' dono, um so' alvo de patch.

Os tres handlers entram como MIXIN (``ProviderRoutes``), e nao como funcoes
soltas, porque a suite os chama como metodo: ``server.Handler.
_handle_remove_provider(obj, payload)`` (nao-ligado) e
``handler._handle_save_provider({...})`` (ligado). Como mixin eles continuam
resolvendo pelo MRO do ``Handler``, e o despacho em ``do_POST`` nao muda --
``self._handle_save_provider(payload)`` segue valendo sem tocar no corpo de
``do_POST``, que e' onde o contrato de rotas e' lido por ``inspect.getsource``.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from viralclipper import user_providers as _user_providers_mod  # noqa: E402
from viralclipper.util import ClipperError  # noqa: E402

#: The store the card writes, resolved from the module that owns the format.
#: One path, one owner: when this file was a second constant, the save route
#: wrote one path and the merge read the other -- so every save answered 200
#: with a list that had not changed.
USER_PROVIDERS_PATH = (_REPO_ROOT / _user_providers_mod.USERS_PATH).resolve()
# Point the module's own default at the same file: ``providers._table`` calls
# ``user_providers.load()`` with no argument, so it reads that global.
_user_providers_mod.USERS_PATH = USER_PROVIDERS_PATH


def _providers_payload() -> dict:
    """The named LLM providers the panel offers in its dropdown.

    ``user`` marks the entries that came from the panel's own file rather than
    from ``viralclipper/providers.py``. Two things depend on it: the card shows
    a Remove button only for those, and the save route refuses to overwrite a
    built-in name. Compare by name against ``providers.PROVIDERS`` -- the merged
    table is available through ``list_providers`` but does not say which side an
    entry came from.
    """
    from viralclipper import providers

    builtin = set(providers.PROVIDERS)
    return {
        "default": providers.DEFAULT_PROVIDER,
        "providers": [
            {
                "name": provider.name,
                "label": provider.label,
                "base_url": provider.base_url,
                "model": provider.model,
                "api_key_env": provider.api_key_env,
                # Whether a key is already saved, never the key itself. The
                # browser needs to say "there is one" so the edit form does not
                # look empty; a reload that echoed the secret back into an
                # <input> would put it in the DOM and the devtools history for
                # no gain. A pasted key is write-only from the panel's side.
                "has_key": bool(provider.api_key),
                "requires_key": provider.requires_key,
                "note": provider.note,
                "timeout": provider.timeout,
                "user": provider.name not in builtin,
            }
            for provider in providers.list_providers()
        ],
    }


class ProviderRoutes:
    """As tres rotas de escrita de /api/providers/*.

    Mixin do ``Handler``: nao guarda estado proprio e nao define ``__init__``,
    so' os handlers. Depende de ``self._send_json``, que vem do ``Handler``.
    """

    def _handle_save_provider(self, payload: dict) -> None:
        """Add or replace one entry in the panel's provider file.

        The name is not allowed to shadow a built-in. ``providers._table``
        resolves a collision in favour of the built-in, so accepting the save
        would produce an entry the panel lists, lets the user edit, and that
        silently does nothing at run time -- the worst of the three possible
        behaviours. Refusing here is what makes that rule visible instead of
        mysterious.
        """
        from viralclipper import providers, user_providers

        if "openai" not in providers.PROVIDERS:  # pragma: no cover - sanity only
            self._send_json({"error": "tabela de provedores ausente"}, 500)
            return

        entry = payload.get("provider")
        if not isinstance(entry, dict):
            self._send_json({"error": "provider must be an object"}, 400)
            return

        name = str(entry.get("name") or "").strip()
        if name in providers.PROVIDERS:
            self._send_json(
                {"error": f"'{name}' e um provedor de fabrica e nao pode ser sobrescrito. "
                          f"Use outro nome."},
                400,
            )
            return

        # The panel never receives the saved key back (only `has_key`), so an
        # edit that does not retype it sends an empty ``api_key``. Empty means
        # "leave it alone", not "delete it": without this, correcting a typo in
        # the note of a provider would wipe the secret and the next run would
        # fail with a 401 the form gives no clue about. Removing the key is
        # turning "Exige chave" off, which is a different, visible action.
        #
        # Folded into the ENTRY, before ``validate``: validate is what enforces
        # "a provider that requires a key must have one", so a preservation done
        # after it would arrive too late and the edit would be refused with the
        # very message the merge exists to prevent.
        entry = dict(entry)
        if not str(entry.get("api_key") or "").strip():
            anterior = next(
                (p for p in user_providers.load(USER_PROVIDERS_PATH)
                 if p.name == name),
                None,
            )
            if anterior is not None and anterior.api_key:
                entry["api_key"] = anterior.api_key

        provider, reason = user_providers.validate(entry)
        if provider is None:
            self._send_json({"error": reason}, 400)
            return

        try:
            user_providers.save(
                [p for p in user_providers.load(USER_PROVIDERS_PATH)
                 if p.name != provider.name] + [provider],
                USER_PROVIDERS_PATH,
            )
        except ClipperError as exc:
            self._send_json({"error": str(exc)}, 500)
            return

        # The full merged list goes back, not just the saved entry: the panel
        # re-renders both the card and the Curador dropdown from one response,
        # so a page that was open while another tab added a provider converges.
        self._send_json({"ok": True, "saved": provider.name,
                         **_providers_payload()})

    def _handle_remove_provider(self, payload: dict) -> None:
        """Drop one user provider. Removing a built-in is refused, not ignored."""
        from viralclipper import providers, user_providers

        name = str(payload.get("name") or "").strip()
        if not name:
            self._send_json({"error": "name is required"}, 400)
            return
        if name in providers.PROVIDERS:
            self._send_json(
                {"error": f"'{name}' e um provedor de fabrica e nao pode ser removido."},
                400,
            )
            return
        try:
            user_providers.remove(name, USER_PROVIDERS_PATH)
        except ClipperError as exc:
            self._send_json({"error": str(exc)}, 500)
            return
        self._send_json({"ok": True, "removed": name, **_providers_payload()})

    def _handle_test_provider(self, payload: dict) -> None:
        """Fire one real request at the endpoint and report what came back.

        Takes the entry from the body rather than a name, and that is
        deliberate: the user has to be able to test a provider **before** saving
        it, otherwise the only way to find out a URL is wrong is to write it to
        a file first. The same endpoint then serves "test what I typed".

        This does mean the server will open a connection to a URL the request
        names. That is what the feature *is* -- it is a probe the user asked
        for, on a server bound to 127.0.0.1 and already behind
        ``_guard_origin``. The scheme is restricted to http(s) by
        ``user_providers.validate``, so no ``file://`` read is reachable.
        """
        from viralclipper import provider_probe, user_providers

        entry = payload.get("provider")
        if not isinstance(entry, dict):
            self._send_json({"error": "provider must be an object"}, 400)
            return
        provider, reason = user_providers.validate(entry)
        if provider is None:
            self._send_json({"error": reason}, 400)
            return

        api_key = None
        if provider.api_key:
            # The key pasted into the panel wins only over *absence*: an
            # exported variable still overrides it below, so a machine that has
            # the real secret in the environment is not shadowed by a key saved
            # from a browser on the same machine.
            api_key = provider.api_key
        if provider.api_key_env:
            api_key = os.environ.get(provider.api_key_env) or api_key
        if not api_key and provider.requires_key:
            # A missing key is a result the user needs, not a server error: the
            # probe cannot get past it, and saying so now is faster than a 401
            # the user has to decode.
            alvo = (
                f"${provider.api_key_env}" if provider.api_key_env
                else "a chave colada no painel"
            )
            self._send_json({
                "ok": False, "kind": "OK", "verdict": "no-key", "status": 0,
                "seconds": 0.0, "answer": "",
                "reason": f"nao ha chave em {alvo} neste servidor. "
                          f"Exporte a variavel (e reinicie o painel) ou cole a chave "
                          f"no campo \"Chave de API\" e salve.",
            })
            return

        self._send_json(provider_probe.test_provider(provider, api_key=api_key))
