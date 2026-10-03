"""The panel's second provider table, and the probe that answers "does it work".

Split from ``test_curator.py`` because the subject is different. That file
covers the *reviewed* table in ``viralclipper/providers.py``. This one covers
the entries the user adds at run time (``viralclipper/user_providers.py``) and
the "Testar modelo" call (``viralclipper/provider_probe.py``) -- the two pieces
that exist because a catalog lists what exists and only a call says what works.
"""

from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from viralclipper import provider_probe, providers, user_providers
from viralclipper.providers import Provider
from viralclipper.ranker import HttpChatProvider


def _valid(**overrides) -> dict:
    """A minimal payload that passes validation, so each test varies one key."""
    entry = {
        "name": "meu-provedor",
        "label": "Meu Provedor",
        "base_url": "https://api.exemplo.test/v1",
        "model": "modelo-1",
        "api_key_env": "MEU_PROVEDOR_KEY",
        "requires_key": True,
        "note": "Grátis: 30 req/min. Testado em 03/10.",
    }
    entry.update(overrides)
    return entry


class _TempStore(unittest.TestCase):
    """Every test here writes to a throwaway file, never the repo's own.

    The directory is created once per class rather than per test. On this
    machine ``TemporaryDirectory.cleanup()`` costs ~5 s (a scanner walks the
    tree), so a per-test dir made a suite of 36 pure-logic assertions take
    almost a minute. The file is removed in ``setUp`` instead, which is the
    only part any test actually depends on.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls._dir = tempfile.TemporaryDirectory()
        cls._root = Path(cls._dir.name)

    @classmethod
    def tearDownClass(cls) -> None:
        cls._dir.cleanup()

    def setUp(self) -> None:
        self.path = self._root / "provedores-usuario.toml"
        # Each test starts from a missing file: "the first run" is a state
        # several of them assert on, and a leftover file would hide it.
        if self.path.exists():
            self.path.unlink()


class ValidateTests(unittest.TestCase):
    """Each rule exists because the alternative fails late and confusingly."""

    def test_a_complete_entry_becomes_a_provider(self):
        provider, reason = user_providers.validate(_valid())
        self.assertEqual(reason, "")
        self.assertIsNotNone(provider)
        self.assertEqual(provider.name, "meu-provedor")
        self.assertEqual(provider.base_url, "https://api.exemplo.test/v1")

    def test_the_name_is_restricted_to_what_a_shell_can_carry(self):
        # A space or a slash in the name reaches argparse as two arguments.
        _, reason = user_providers.validate(_valid(name="com espaco"))
        self.assertIn("hifen", reason)

    def test_the_endpoint_must_carry_a_scheme(self):
        for bad in ("ftp://x/v1", "api.exemplo.com/v1", "file:///etc/passwd"):
            with self.subTest(base_url=bad):
                _, reason = user_providers.validate(_valid(base_url=bad))
                self.assertIn("http", reason)

    def test_a_file_url_is_refused_so_the_probe_cannot_read_local_files(self):
        # The test route opens a connection to whatever URL it is given. The
        # scheme allowlist is what keeps that from being a file read.
        _, reason = user_providers.validate(_valid(base_url="file:///C:/Windows/win.ini"))
        self.assertIn("http", reason)

    def test_an_empty_model_is_refused(self):
        _, reason = user_providers.validate(_valid(model="   "))
        self.assertIn("modelo", reason)

    def test_a_blank_note_is_refused(self):
        # Same contract as the shipped table: the note is what says whether the
        # entry is worth picking, so a blank one is an entry nobody can judge.
        _, reason = user_providers.validate(_valid(note=""))
        self.assertIn("nota", reason)

    def test_a_key_less_local_endpoint_is_accepted(self):
        provider, reason = user_providers.validate(
            _valid(api_key_env="", requires_key=False)
        )
        self.assertEqual(reason, "")
        self.assertFalse(provider.requires_key)

    def test_a_requiring_entry_without_a_variable_is_refused(self):
        _, reason = user_providers.validate(_valid(api_key_env="", requires_key=True))
        self.assertIn("variavel", reason)

    def test_a_non_positive_timeout_is_refused(self):
        _, reason = user_providers.validate(_valid(timeout=0))
        self.assertIn("maior que zero", reason)


class StoreTests(_TempStore):
    def test_a_missing_file_is_an_empty_list_not_an_error(self):
        self.assertEqual(user_providers.load(self.path), [])

    def test_a_malformed_file_is_swallowed(self):
        self.path.write_bytes(b"isto nao e toml {{{")
        self.assertEqual(user_providers.load(self.path), [])

    def test_an_entry_round_trips(self):
        provider, _ = user_providers.validate(_valid())
        user_providers.save([provider], self.path)
        back = user_providers.load(self.path)
        self.assertEqual(len(back), 1)
        self.assertEqual(back[0], provider)

    def test_the_write_is_lf_even_on_windows(self):
        # write_text would turn every \n into \r\n, and a file the panel
        # rewrites on each save would then churn its own separators.
        provider, _ = user_providers.validate(_valid())
        user_providers.save([provider], self.path)
        raw = self.path.read_bytes()
        self.assertNotIn(b"\r\n", raw)

    def test_upsert_replaces_instead_of_appending(self):
        first, _ = user_providers.validate(_valid(model="modelo-1"))
        user_providers.save([first], self.path)
        replacement, _ = user_providers.validate(_valid(model="modelo-2"))
        entries = user_providers.upsert(replacement, self.path)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].model, "modelo-2")

    def test_remove_drops_only_the_named_entry(self):
        a, _ = user_providers.validate(_valid(name="a"))
        b, _ = user_providers.validate(_valid(name="b"))
        user_providers.save([a, b], self.path)
        left = user_providers.remove("a", self.path)
        self.assertEqual([p.name for p in left], ["b"])

    def test_a_saved_entry_survives_a_quote_in_the_note(self):
        # The note is free text typed by the user; an unescaped quote would
        # make the whole file unparseable, and load() would then return [] --
        # silently losing every provider, not just the one being saved.
        provider, _ = user_providers.validate(
            _valid(note='diz "grátis" e custa pouco')
        )
        user_providers.save([provider], self.path)
        back = user_providers.load(self.path)
        self.assertEqual(len(back), 1)
        self.assertEqual(back[0].note, 'diz "grátis" e custa pouco')


class MergeTests(_TempStore):
    """The merge is what makes a user entry indistinguishable downstream."""

    def setUp(self) -> None:
        super().setUp()
        # ``providers._table`` calls ``user_providers.load()`` with no argument,
        # so it reads the module global; patching the global is enough.
        patcher = mock.patch.object(user_providers, "USERS_PATH", self.path)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_a_user_entry_is_reachable_through_providers(self):
        provider, _ = user_providers.validate(_valid())
        user_providers.save([provider], self.path)
        self.assertEqual(providers.get_provider("meu-provedor"), provider)

    def test_a_user_entry_appears_in_the_listing(self):
        provider, _ = user_providers.validate(_valid())
        user_providers.save([provider], self.path)
        names = [p.name for p in providers.list_providers()]
        self.assertIn("meu-provedor", names)

    def test_a_user_entry_cannot_shadow_a_built_in_name(self):
        # The save route refuses this too, but the merge has to hold the line
        # on its own: a hand-edited file must not be able to redirect "openai".
        impostor, _ = user_providers.validate(
            _valid(name="openai", base_url="https://evil.test/v1")
        )
        user_providers.save([impostor], self.path)
        self.assertEqual(
            providers.get_provider("openai").base_url, "https://api.openai.com/v1"
        )
        self.assertEqual(providers.get_provider("openai"), providers.PROVIDERS["openai"])

    def test_apply_to_config_accepts_a_user_provider(self):
        provider, _ = user_providers.validate(_valid())
        user_providers.save([provider], self.path)
        from viralclipper.config import ClipConfig

        config = providers.apply_to_config(ClipConfig(url="u"), "meu-provedor")
        self.assertEqual(config.ranker_base_url, provider.base_url)
        self.assertEqual(config.ranker_model, provider.model)


class ProbePayloadTests(unittest.TestCase):
    def test_the_probe_payload_matches_the_real_client(self):
        # The two have to agree, and the property that matters is invisible
        # unless something asserts it: a reasoning model given a small
        # max_tokens spends it thinking and answers with a null content, so a
        # probe that set one would fail models that work fine in the real run.
        real = HttpChatProvider("https://x/v1", "m", None)
        captured = {}

        def fake_urlopen(request, timeout=None):
            captured["body"] = json.loads(request.data.decode("utf-8"))
            raise OSError("stop here")

        with mock.patch("urllib.request.urlopen", fake_urlopen):
            try:
                real.complete("s", "u")
            except Exception:
                pass

        probe = provider_probe.probe_payload("m", "s", "u")
        self.assertNotIn("max_tokens", captured["body"])
        self.assertNotIn("max_tokens", probe)
        self.assertEqual(
            sorted(probe.keys()), sorted(captured["body"].keys())
        )

    def test_the_probe_is_greedy_free(self):
        # Determinism: the same entry must give the same verdict twice.
        self.assertEqual(provider_probe.probe_payload("m", "s", "u")["temperature"], 0.0)


class ProbeGradingTests(unittest.TestCase):
    def test_an_empty_answer_is_its_own_verdict(self):
        # The diffusion-model failure: HTTP 200, content empty.
        verdict, reason = provider_probe._grade("OK", "")
        self.assertEqual(verdict, "empty")
        self.assertIn("vazio", reason)

    def test_an_echoed_instruction_is_caught(self):
        # The translator failure: the model restates the instruction.
        verdict, _ = provider_probe._grade(
            "OK", "Nao repita esta instrucao. Escreva apenas a palavra: funcionou"
        )
        self.assertEqual(verdict, "echo")

    def test_a_plain_answer_is_ok(self):
        self.assertEqual(provider_probe._grade("OK", "OK")[0], "ok")

    def test_an_answer_to_the_wrong_question_is_weak_not_a_failure(self):
        # Usable as a chat model, but it did not follow the instruction -- the
        # user should know, so it is neither "ok" nor an error.
        self.assertEqual(provider_probe._grade("OK", "talvez")[0], "weak")

    def test_a_multi_line_answer_passes_the_list_probe(self):
        self.assertEqual(
            provider_probe._grade("LIST", "Um\nDois\nTres")[0], "ok"
        )

    def test_a_single_line_answer_fails_the_list_probe(self):
        self.assertEqual(provider_probe._grade("LIST", "so uma linha")[0], "weak")


class _FakeResponse:
    def __init__(self, body: bytes, status: int = 200) -> None:
        self._body = body
        self.status = status

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class ProbeCallTests(unittest.TestCase):
    def _provider(self, **overrides) -> Provider:
        base = dict(
            name="p", label="P", base_url="https://x.test/v1", model="m",
            api_key_env="K", requires_key=True, note="n",
        )
        base.update(overrides)
        return Provider(**base)

    def test_a_good_answer_is_reported_with_its_latency(self):
        body = json.dumps(
            {"choices": [{"message": {"content": "OK"}}]}
        ).encode()
        result = provider_probe.test_provider(
            self._provider(), opener=lambda req, timeout=None: _FakeResponse(body)
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["verdict"], "ok")
        self.assertEqual(result["status"], 200)
        self.assertIn("seconds", result)

    def test_an_http_error_comes_back_as_a_result_not_an_exception(self):
        import urllib.error

        def boom(req, timeout=None):
            raise urllib.error.HTTPError(
                "https://x", 401, "unauthorized", {}, io.BytesIO(b"bad key")
            )

        result = provider_probe.test_provider(self._provider(), opener=boom)
        self.assertFalse(result["ok"])
        self.assertEqual(result["verdict"], "http")
        self.assertEqual(result["status"], 401)
        self.assertIn("chave", result["reason"])

    def test_a_404_explains_that_the_model_id_is_the_usual_cause(self):
        import urllib.error

        def boom(req, timeout=None):
            raise urllib.error.HTTPError(
                "https://x", 404, "not found", {}, io.BytesIO(b"")
            )

        result = provider_probe.test_provider(self._provider(), opener=boom)
        self.assertEqual(result["status"], 404)
        self.assertIn("modelo", result["reason"])

    def test_an_unreachable_endpoint_becomes_a_sentence(self):
        def boom(req, timeout=None):
            raise OSError("connection refused")

        result = provider_probe.test_provider(self._provider(), opener=boom)
        self.assertFalse(result["ok"])
        self.assertEqual(result["verdict"], "unreachable")
        self.assertIn("alcancei", result["reason"])

    def test_a_200_in_the_wrong_shape_is_reported_as_such(self):
        result = provider_probe.test_provider(
            self._provider(),
            opener=lambda req, timeout=None: _FakeResponse(b"<html>nao</html>"),
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["verdict"], "shape")
        self.assertIn("chat/completions", result["reason"])

    def test_the_key_travels_in_the_authorization_header(self):
        seen = {}

        def capture(req, timeout=None):
            seen["auth"] = req.get_header("Authorization")
            raise OSError("stop")

        provider_probe.test_provider(
            self._provider(), api_key="segredo-123", opener=capture
        )
        self.assertEqual(seen["auth"], "Bearer segredo-123")

    def test_a_key_less_provider_sends_no_authorization_header(self):
        seen = {}

        def capture(req, timeout=None):
            seen["auth"] = req.get_header("Authorization")
            raise OSError("stop")

        provider_probe.test_provider(
            self._provider(api_key_env="", requires_key=False), opener=capture
        )
        self.assertIsNone(seen["auth"])

    def test_the_request_lands_on_chat_completions(self):
        seen = {}

        def capture(req, timeout=None):
            seen["url"] = req.full_url
            raise OSError("stop")

        provider_probe.test_provider(
            self._provider(base_url="https://x.test/v1/"), opener=capture
        )
        self.assertEqual(seen["url"], "https://x.test/v1/chat/completions")


if __name__ == "__main__":
    unittest.main()
