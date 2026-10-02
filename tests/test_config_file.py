"""Unit tests for :mod:`viralclipper.config_file`.

Besides the parser itself, this covers the two shipped example files. A config
example that silently misconfigures the run is worse than no example at all:
the TOML one used to open a ``[whisper]`` table in the middle of the file, and
because a TOML table header applies to everything that follows it, every
setting below was being read as ``whisper.<key>``.
"""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from viralclipper import cli, config_file
from viralclipper.util import ClipperError, Logger

EXAMPLES = Path(__file__).resolve().parent.parent


class RecordingLogger(Logger):
    """Logger that keeps what it was told instead of printing it."""

    def __init__(self) -> None:
        super().__init__(quiet=True)
        self.warnings: list[str] = []
        self.infos: list[str] = []

    def warn(self, message: str) -> None:
        self.warnings.append(message)

    def info(self, message: str) -> None:
        self.infos.append(message)


class ConfigFileTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="config-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.log = RecordingLogger()

    def write(self, name: str, text: str) -> Path:
        path = self.tmp / name
        path.write_text(text, encoding="utf-8")
        return path

    def applied_defaults(self, flat: dict) -> dict:
        """Push a flat config through the parser and read the resulting values."""
        parser = cli.build_parser()
        config_file.apply_defaults(parser, flat, self.log)
        return vars(parser.parse_args(["https://example.test/v"]))


class LoadingTests(ConfigFileTestCase):
    def test_missing_file_raises(self):
        with self.assertRaises(ClipperError):
            config_file.load_config_file(self.tmp / "nope.yaml", self.log)

    def test_directory_raises(self):
        with self.assertRaises(ClipperError):
            config_file.load_config_file(self.tmp, self.log)

    def test_unknown_extension_raises(self):
        path = self.write("config.ini", "count = 3\n")
        with self.assertRaises(ClipperError):
            config_file.load_config_file(path, self.log)

    def test_invalid_yaml_raises(self):
        path = self.write("bad.yaml", "count: [unclosed\n")
        with self.assertRaises(ClipperError):
            config_file.load_config_file(path, self.log)

    def test_invalid_toml_raises(self):
        path = self.write("bad.toml", "count = \n")
        with self.assertRaises(ClipperError):
            config_file.load_config_file(path, self.log)

    def test_yaml_scalar_at_top_level_raises(self):
        path = self.write("scalar.yaml", "- just\n- a list\n")
        with self.assertRaises(ClipperError):
            config_file.load_config_file(path, self.log)

    def test_empty_yaml_is_an_empty_mapping(self):
        path = self.write("empty.yaml", "")
        self.assertEqual(config_file.load_config_file(path, self.log), {})


class FlattenTests(ConfigFileTestCase):
    def test_flat_yaml_keys_survive(self):
        path = self.write("flat.yaml", "count: 3\nmin_duration: 20\n")
        self.assertEqual(
            config_file.load_config_file(path, self.log),
            {"count": 3, "min_duration": 20},
        )

    def test_nested_yaml_table_becomes_a_prefixed_key(self):
        path = self.write("nested.yaml", "whisper:\n  model: medium\n  language: pt\n")
        flat = config_file.load_config_file(path, self.log)
        self.assertEqual(flat["whisper_model"], "medium")
        self.assertEqual(flat["whisper_language"], "pt")

    def test_deeper_nesting_keeps_joining_with_underscores(self):
        path = self.write("deep.yaml", "a:\n  b:\n    c: 1\n")
        self.assertEqual(config_file.load_config_file(path, self.log), {"a_b_c": 1})

    def test_toml_dotted_keys_stay_top_level(self):
        path = self.write("dotted.toml", 'whisper.model = "small"\ncount = 8\n')
        flat = config_file.load_config_file(path, self.log)
        self.assertEqual(flat["whisper_model"], "small")
        self.assertEqual(flat["count"], 8)

    def test_toml_table_swallows_every_following_key(self):
        """The trap that made the shipped TOML example wrong.

        A table header applies to the rest of the file, so a top-level key
        written after ``[whisper]`` silently becomes ``whisper.<key>``.
        """
        path = self.write("trap.toml", '[whisper]\nmodel = "small"\n\ncount = 8\n')
        flat = config_file.load_config_file(path, self.log)
        self.assertEqual(flat["whisper_model"], "small")
        self.assertEqual(flat["whisper_count"], 8)
        self.assertNotIn("count", flat)


class ApplyDefaultsTests(ConfigFileTestCase):
    def test_known_keys_become_parser_defaults(self):
        args = self.applied_defaults({"count": 9, "min_duration": 12.5})
        self.assertEqual(args["count"], 9)
        self.assertEqual(args["min_duration"], 12.5)

    def test_group_prefix_is_stripped_when_matching(self):
        args = self.applied_defaults({"whisper_model": "large-v3", "whisper_language": "en"})
        self.assertEqual(args["whisper_model"], "large-v3")
        self.assertEqual(args["language"], "en")

    def test_unknown_key_warns_and_is_dropped(self):
        args = self.applied_defaults({"not_a_real_option": 1})
        self.assertTrue(any("not_a_real_option" in w for w in self.log.warnings))
        self.assertNotIn("not_a_real_option", args)

    def test_dashes_are_normalized_to_underscores(self):
        args = self.applied_defaults({"min-duration": 15.0})
        self.assertEqual(args["min_duration"], 15.0)

    def test_explicit_cli_flags_beat_the_file(self):
        parser = cli.build_parser()
        config_file.apply_defaults(parser, {"count": 9}, self.log)
        args = parser.parse_args(["https://example.test/v", "-n", "2"])
        self.assertEqual(args.count, 2)


class ShippedExampleTests(ConfigFileTestCase):
    """The example files must load cleanly and set what they appear to set."""

    def load(self, name: str) -> dict:
        path = EXAMPLES / name
        self.assertTrue(path.exists(), f"{name} is missing")
        return config_file.load_config_file(path, self.log)

    def test_yaml_example_loads_without_warnings(self):
        self.load("config.example.yaml")
        self.assertEqual(self.log.warnings, [])

    def test_toml_example_loads_without_warnings(self):
        self.load("config.example.toml")
        self.assertEqual(self.log.warnings, [])

    def test_yaml_example_sets_every_declared_option(self):
        flat = self.load("config.example.yaml")
        args = self.applied_defaults(flat)
        for key in (
            "count",
            "auto_margin",
            "auto_ceiling",
            "max_duration_grace",
            "target_duration",
            "min_score",
            "layout",
            "workers",
            "threads",
        ):
            self.assertIn(key, flat, f"{key} missing from config.example.yaml")
        # O exemplo mostra o modo automatico, que e o padrao. A prova de que o
        # arquivo foi lido -- e nao so os defaults -- vem de target_duration,
        # que o exemplo muda para 45 s.
        self.assertEqual(args["count"], 0)
        self.assertEqual(args["auto_margin"], 15.0)
        self.assertEqual(args["auto_ceiling"], 200)
        self.assertEqual(args["max_duration_grace"], 30.0)
        self.assertEqual(args["target_duration"], 45.0)
        self.assertEqual(args["workers"], 2)
        self.assertEqual(args["threads"], 2)

    def test_toml_example_sets_every_declared_option(self):
        flat = self.load("config.example.toml")
        args = self.applied_defaults(flat)
        for key in ("count", "auto_margin", "auto_ceiling", "max_duration_grace"):
            self.assertIn(key, flat, f"{key} missing from config.example.toml")
        # Mesma prova do yaml: target_duration sai do default, o resto confirma
        # que as chaves do modo automatico chegaram ate o parser.
        self.assertEqual(args["count"], 0)
        self.assertEqual(args["auto_margin"], 15.0)
        self.assertEqual(args["auto_ceiling"], 200)
        self.assertEqual(args["max_duration_grace"], 30.0)
        self.assertEqual(args["target_duration"], 45.0)
        self.assertEqual(args["workers"], 2)
        self.assertEqual(args["threads"], 2)

    def test_toml_example_does_not_nest_top_level_keys_under_whisper(self):
        """Regression: ``[whisper]`` used to swallow the rest of the file."""
        flat = self.load("config.example.toml")
        for key in (
            "layout", "workers", "threads", "crf", "preset", "font",
            "min_score", "ranker", "caption_style", "download_mode",
        ):
            self.assertIn(key, flat, f"{key} was swallowed by a TOML table")
            self.assertNotIn(f"whisper_{key}", flat)

    def test_toml_example_still_sets_the_whisper_options(self):
        flat = self.load("config.example.toml")
        self.assertEqual(flat["whisper_model"], "small")
        self.assertEqual(flat["whisper_language"], "pt")

    def test_both_examples_agree_on_the_shared_options(self):
        yaml_flat = self.load("config.example.yaml")
        self.log.warnings.clear()
        toml_flat = self.load("config.example.toml")
        for key in ("count", "min_duration", "max_duration", "target_duration", "workers"):
            self.assertEqual(
                yaml_flat[key], toml_flat[key], f"{key} differs between the two examples"
            )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
