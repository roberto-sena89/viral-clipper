"""The Ajustes page contract: what the panel owns, and how it round-trips.

The page is a thin layer over one file (``ajustes.toml``) and one tuple
(``AJUSTES_KEYS``). Everything that can silently drift lives at those two
seams, so that is what these tests pin down:

* a key that is not a real argparse dest or not a real ClipConfig field would
  be dropped on the way to the engine without a word;
* a control on the page with no key behind it would look editable and change
  nothing;
* a value that loses its type on the way through TOML would turn a toggle into
  the number 1.

None of these fail loudly at runtime, which is exactly why they are worth a
test.
"""

from __future__ import annotations

import re
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest import mock

from viralclipper import cli
from viralclipper import config as config_mod
from web import server

AJUSTES_HTML = server.WEB_DIR / "ajustes.html"


def page_source(name: str) -> str:
    return (server.WEB_DIR / name).read_text(encoding="utf-8")


def control_ids(markup: str) -> set[str]:
    """Every control id in ``markup``.

    Three shapes count: ``<input>``/``<select>``/``<textarea>``, and the
    ``<button class="toggle">`` switches. The switches are matched by class on
    purpose -- a plain ``<button>`` is an action (save, reload the prompt), not
    a value, and folding those in would make the parity test meaningless.
    """
    campos = set(re.findall(r"<(?:input|select|textarea)[^>]*\sid=\"([^\"]+)\"", markup))
    toggles = set(re.findall(
        r"<button[^>]*\bclass=\"[^\"]*\btoggle\b[^\"]*\"[^>]*\sid=\"([^\"]+)\"", markup,
    ))
    return campos | toggles


#: Control id on the page -> key in ``AJUSTES_KEYS``.
#:
#: ``None`` means the control carries no value of its own: either it is a
#: toggle that *derives* another field (``headline-on`` decides whether
#: ``headline_seconds`` is 0 or its own number, ``ranker-llm`` turns ``ranker``
#: into "llm" or "none"), or it belongs to something the server owns
#: (``curator-prompt`` is saved through ``/prompts/curador``, not through this
#: file, and ``curator-prompt-path`` is read-only output).
CONTROLES = {
    "min-duration": "min_duration",
    "max-duration": "max_duration",
    "target-duration": "target_duration",
    "min-score": "min_score",
    "min-gap": "min_gap",
    "engine": "engine",
    "auto-margin": "auto_margin",
    "auto-ceiling": "auto_ceiling",
    "max-grace": "max_duration_grace",
    "whisper-model": "whisper_model",
    "language": "language",
    "beam-size": "beam_size",
    "cache-dir": "cache_dir",
    "vad-filter": "vad_filter",
    "transcript-cache": "transcript_cache",
    "layout": "layout",
    "caption-preset": "caption_preset",
    "caption-style": "caption_style",
    "font-size": "font_size",
    "crf": "crf",
    "target-lufs": "target_lufs",
    "workers": "workers",
    "headline-on": None,
    "headline-seconds": "headline_seconds",
    "progress-bar-on": "progress_bar",
    "jump-cut": "jump_cut",
    "loudnorm": "loudnorm",
    "ranker-llm": "ranker",
    "ranker-provider": "ranker_provider",
    "ranker-api-key-env": "ranker_api_key_env",
    "ranker-model": "ranker_model",
    "ranker-base-url": "ranker_base_url",
    "ranker-top-n": "ranker_top_n",
    "ranker-weight": "ranker_weight",
    "curator-prompt": None,
    "curator-prompt-path": None,
}


def sample_settings() -> dict:
    """One value per key, each of a type the form can actually produce."""
    return {
        "min_duration": 30.0,
        "max_duration": 60.0,
        "target_duration": 42.0,
        "min_score": 0.0,
        "min_gap": 6.0,
        "engine": "hybrid",
        "auto_margin": 15.0,
        "auto_ceiling": 200,
        "max_duration_grace": 30.0,
        "whisper_model": "small",
        "language": "pt",
        "beam_size": 1,
        "cache_dir": "output/cache/transcripts",
        "vad_filter": True,
        "transcript_cache": False,
        "layout": "focus",
        "caption_preset": "karaoke",
        "caption_style": "karaoke",
        "font_size": None,
        "crf": 20,
        "target_lufs": -14.0,
        "workers": 2,
        "headline_seconds": 0.0,
        "progress_bar": False,
        "jump_cut": False,
        "loudnorm": True,
        "ranker": "none",
        "ranker_provider": "",
        "ranker_api_key_env": "OPENAI_API_KEY",
        "ranker_model": "gpt-4o-mini",
        "ranker_base_url": "https://api.openai.com/v1",
        "ranker_top_n": 24,
        "ranker_weight": 0.6,
    }


class AjustesKeysAreRealTests(unittest.TestCase):
    """Every key has to survive the trip to the engine."""

    @classmethod
    def setUpClass(cls):
        cls.parser = cli.build_parser()
        cls.dests = {a.dest for a in cls.parser._actions if a.dest and a.dest != "help"}
        cls.fields = {f.name for f in __import__("dataclasses").fields(config_mod.ClipConfig)}

    def test_every_key_is_an_argparse_dest(self):
        # Not cosmetic: apply_defaults() only forwards keys that match a real
        # argument, so a typo here means the value is dropped with a warning
        # nobody reads, and the --config promise in the file header is a lie.
        faltando = [k for k in server.AJUSTES_KEYS if k not in self.dests]
        self.assertEqual(faltando, [], f"sem argumento na CLI: {faltando}")

    def test_every_key_is_a_clipconfig_field(self):
        # _options_to_config drops unknown keys silently. A name that is not a
        # dataclass field would look saved and never reach the render.
        faltando = [k for k in server.AJUSTES_KEYS if k not in self.fields]
        self.assertEqual(faltando, [], f"sem campo no ClipConfig: {faltando}")

    def test_the_keys_build_a_valid_config(self):
        settings = sample_settings()
        self.assertEqual(set(settings), set(server.AJUSTES_KEYS))
        cfg = server._options_to_config({**settings, "url": "https://example.test/v"})
        cfg.validate()
        self.assertEqual(cfg.auto_margin, 15.0)
        self.assertEqual(cfg.target_lufs, -14.0)

    def test_curator_prompt_file_is_not_a_page_key(self):
        # The page never edits that path: it saves the prompt through
        # /prompts/curador. If it were writable here, the panel could point the
        # curator at any file on disk.
        self.assertNotIn("curator_prompt_file", server.AJUSTES_KEYS)


class AjustesTomlTests(unittest.TestCase):
    """Round-trip through the file, including the types."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.path = self.tmp / "ajustes.toml"
        self._patch = mock.patch.object(server, "AJUSTES_PATH", self.path)
        self._patch.start()
        self.addCleanup(self._patch.stop)

    def test_missing_file_is_not_an_error(self):
        self.assertFalse(self.path.exists())
        self.assertEqual(server._read_ajustes(), {})

    def test_broken_file_is_not_an_error(self):
        # A hand-edited file with a syntax error must degrade to "nothing
        # saved", not take the endpoint down: the user can fix it from the page.
        self.path.write_text("min_duration = = 30\n", encoding="utf-8")
        self.assertEqual(server._read_ajustes(), {})

    def test_round_trip_preserves_value_and_type(self):
        settings = sample_settings()
        self.path.write_text(server._dump_ajustes(settings), encoding="utf-8")
        volta = server._read_ajustes()

        # font_size is None in the sample and None is deliberately not written,
        # so it is the one key allowed to come back missing.
        esperadas = {k: v for k, v in settings.items() if v is not None}
        self.assertEqual(set(volta), set(esperadas))
        for key, esperado in esperadas.items():
            self.assertEqual(volta[key], esperado, f"{key} mudou de valor")
            self.assertIs(type(volta[key]), type(esperado), f"{key} mudou de tipo")

    def test_toggles_survive_as_booleans(self):
        # The bug this pins: bool is a subclass of int in Python, so a
        # serialiser that checks int first writes true as 1, and the toggle
        # reads back as a number - which is truthy in JS, so it would look
        # right until someone turned it off.
        settings = {**sample_settings(), "vad_filter": False, "loudnorm": False}
        texto = server._dump_ajustes(settings)
        self.assertIn("vad_filter = false", texto)
        self.assertIn("loudnorm = false", texto)
        volta = tomllib.loads(texto)
        self.assertIs(volta["vad_filter"], False)
        self.assertIs(volta["loudnorm"], False)

    def test_none_is_omitted_rather_than_guessed(self):
        # font_size None means "inherit the caption preset". TOML has no null,
        # so the honest move is to leave the key out and let ClipConfig keep its
        # own default - not to write 0 or "" and pin the field forever.
        texto = server._dump_ajustes({**sample_settings(), "font_size": None})
        self.assertNotIn("font_size", texto)
        self.assertNotIn("font_size", tomllib.loads(texto))

    def test_strings_are_escaped(self):
        settings = {**sample_settings(), "engine": 'we"ird\\path'}
        self.path.write_text(server._dump_ajustes(settings), encoding="utf-8")
        self.assertEqual(server._read_ajustes()["engine"], 'we"ird\\path')

    def test_keys_outside_the_contract_are_ignored(self):
        # A stale file (or one written by a newer version) must not smuggle
        # url/output_dir into the panel's settings.
        self.path.write_text(
            "min_duration = 33.0\nurl = \"https://evil.test\"\noutput_dir = \"/tmp\"\n",
            encoding="utf-8",
        )
        self.assertEqual(server._read_ajustes(), {"min_duration": 33.0})

    def test_the_file_follows_the_key_order(self):
        # Stable order keeps the file diff-friendly between saves. Keys with a
        # None value are absent, so they are absent from the expectation too.
        settings = sample_settings()
        texto = server._dump_ajustes(settings)
        ordem = [
            linha.split(" = ")[0]
            for linha in texto.splitlines()
            if linha and not linha.startswith("#")
        ]
        esperado = [k for k in server.AJUSTES_KEYS if settings[k] is not None]
        self.assertEqual(ordem, esperado)

    def test_a_written_file_is_a_valid_cli_config(self):
        # The whole point of putting it at the repo root: `--config
        # ajustes.toml` has to work, or the panel and the CLI drift apart.
        from viralclipper import config_file

        class Quiet:
            def info(self, *a, **k): pass
            def warn(self, *a, **k): pass
            def error(self, *a, **k): pass

        self.path.write_text(server._dump_ajustes(sample_settings()), encoding="utf-8")
        carregado = config_file.load_config_file(self.path, Quiet())
        self.assertEqual(carregado["target_lufs"], -14.0)
        self.assertEqual(carregado["caption_preset"], "karaoke")

        # And the keys really do land on the parser, not just in the dict.
        parser = cli.build_parser()
        config_file.apply_defaults(parser, carregado, Quiet())
        args = parser.parse_args(["https://example.test/v"])
        self.assertEqual(args.target_lufs, -14.0)
        self.assertEqual(args.auto_ceiling, 200)
        self.assertEqual(args.caption_preset, "karaoke")


class AjustesPageTests(unittest.TestCase):
    """The page and the tuple have to describe the same thing."""

    @classmethod
    def setUpClass(cls):
        cls.markup = page_source("ajustes.html")
        cls.ids = control_ids(cls.markup)

    def test_every_control_on_the_page_is_declared(self):
        # A control nobody declared is a field the user can move that changes
        # nothing - the most expensive kind of bug, because it looks like it
        # worked.
        orfaos = sorted(self.ids - set(CONTROLES))
        self.assertEqual(orfaos, [], f"controles sem contrato: {orfaos}")

    def test_every_declared_control_exists_on_the_page(self):
        faltando = sorted(set(CONTROLES) - self.ids)
        self.assertEqual(faltando, [], f"contrato aponta para controles ausentes: {faltando}")

    def test_the_controls_cover_every_key_exactly_once(self):
        mapeadas = [k for k in CONTROLES.values() if k is not None]
        self.assertEqual(sorted(mapeadas), sorted(server.AJUSTES_KEYS))

    def test_the_page_does_not_own_the_transcript(self):
        # Deliberate: the transcript is per-run data. Persisted, the run of the
        # next video would silently reuse the previous video's transcript.
        self.assertNotIn("transcript", self.ids)
        self.assertNotIn("transcript_text", server.AJUSTES_KEYS)
        self.assertNotIn("transcript_text", CONTROLES.values())

    def test_the_page_has_no_duplicate_ids(self):
        todos = re.findall(r"\sid=\"([^\"]+)\"", self.markup)
        self.assertEqual(len(todos), len(set(todos)), "id repetido em ajustes.html")

    def test_every_aria_reference_resolves(self):
        ids = set(re.findall(r"\sid=\"([^\"]+)\"", self.markup))
        quebrados = []
        for attr in ("aria-describedby", "aria-labelledby", "aria-controls"):
            for m in re.finditer(attr + r"=\"([^\"]+)\"", self.markup):
                quebrados += [r for r in m.group(1).split() if r not in ids]
        self.assertEqual(quebrados, [], f"referencias aria quebradas: {quebrados}")

    def test_every_label_points_at_a_real_control(self):
        ids = set(re.findall(r"\sid=\"([^\"]+)\"", self.markup))
        for m in re.finditer(r"<label[^>]*\sfor=\"([^\"]+)\"", self.markup):
            self.assertIn(m.group(1), ids, f"label for={m.group(1)} sem controle")

    def test_the_provider_select_defers_its_enhancement(self):
        # enhanceSelect copies the <option> elements at the moment it runs, and
        # this select is filled by /providers afterwards. Without the marker the
        # rich panel would be born empty.
        self.assertRegex(
            self.markup,
            r"<select[^>]*id=\"ranker-provider\"[^>]*data-defer-enhance",
        )


class AjustesLufsTests(unittest.TestCase):
    """The loudness field, which never reached the engine before."""

    def test_the_old_spelling_still_works(self):
        # A cached page (or any other caller) sending the old name must not be
        # silently ignored any more.
        cfg = server._options_to_config({"url": "x", "lufs": -9.5})
        self.assertEqual(cfg.target_lufs, -9.5)

    def test_the_real_field_name_works(self):
        cfg = server._options_to_config({"url": "x", "target_lufs": -9.5})
        self.assertEqual(cfg.target_lufs, -9.5)

    def test_the_new_name_wins_when_both_are_sent(self):
        cfg = server._options_to_config({"url": "x", "lufs": -9.5, "target_lufs": -20.0})
        self.assertEqual(cfg.target_lufs, -20.0)

    def test_a_string_is_coerced(self):
        cfg = server._options_to_config({"url": "x", "target_lufs": "-9.5"})
        self.assertEqual(cfg.target_lufs, -9.5)


class AjustesAutomaticModeTests(unittest.TestCase):
    """The three knobs the automatic mode added, as strings."""

    def test_they_survive_being_sent_as_strings(self):
        # auto_ceiling is compared with >= against a list length. A str would
        # raise there instead of failing validation, so it has to be coerced.
        cfg = server._options_to_config({
            "url": "x",
            "auto_ceiling": "250",
            "auto_margin": "20",
            "max_duration_grace": "45",
        })
        self.assertEqual(cfg.auto_ceiling, 250)
        self.assertIsInstance(cfg.auto_ceiling, int)
        self.assertEqual(cfg.auto_margin, 20.0)
        self.assertEqual(cfg.max_duration_grace, 45.0)

    def test_the_grace_widens_the_hard_ceiling(self):
        cfg = server._options_to_config({"url": "x", "max_duration": 60, "max_duration_grace": 30})
        self.assertEqual(cfg.hard_max_duration, 90.0)


class AjustesPayloadTests(unittest.TestCase):
    """What GET /ajustes.json hands to the Cortes page."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.path = self.tmp / "ajustes.toml"
        self._patch = mock.patch.object(server, "AJUSTES_PATH", self.path)
        self._patch.start()
        self.addCleanup(self._patch.stop)

    def test_it_carries_the_curator_prompt_path(self):
        # index.js has to forward this into the run payload, and it is not on
        # the page, so it has to come from here or not at all.
        payload = server._ajustes_payload()
        self.assertIn("curator_prompt_file", payload)
        self.assertTrue(payload["curator_prompt_file"].endswith("curador.txt"))

    def test_it_is_empty_before_the_first_save(self):
        payload = server._ajustes_payload()
        self.assertEqual(
            {k: v for k, v in payload.items() if k != "curator_prompt_file"}, {},
        )

    def test_the_saved_values_win_over_the_defaults(self):
        self.path.write_text("min_duration = 12.5\nauto_ceiling = 40\n", encoding="utf-8")
        payload = server._ajustes_payload()
        self.assertEqual(payload["min_duration"], 12.5)
        self.assertEqual(payload["auto_ceiling"], 40)


if __name__ == "__main__":
    unittest.main()
