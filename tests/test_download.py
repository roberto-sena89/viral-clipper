"""Unit tests for :mod:`viralclipper.download`.

Nothing here touches the network: yt-dlp is never invoked. What is covered is
the part that used to be the worst experience in the tool — turning a failure
into a sentence the user can act on — plus the two pure helpers the renderer
depends on, ``resolve_origin`` and ``_resolve_downloaded``.
"""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests._fixtures import make_config
from viralclipper import download
from viralclipper.util import ClipperError


class ExplainFailureTests(unittest.TestCase):
    def test_age_gate_points_at_the_cookies_flag(self):
        message = download.explain_failure(
            "ERROR: Sign in to confirm your age. This video may be inappropriate "
            "for some users."
        )
        self.assertIn("age restricted", message)
        self.assertIn("--cookies-from-browser", message)

    def test_bot_challenge_points_at_the_cookies_flag(self):
        message = download.explain_failure("ERROR: Sign in to confirm you're not a bot.")
        self.assertIn("--cookies-from-browser", message)

    def test_unavailable_video_lists_the_likely_causes(self):
        message = download.explain_failure(
            "ERROR: [youtube] AAAAAAAAAAA: This video is unavailable"
        )
        self.assertIn("unavailable", message)
        self.assertIn("private", message)

    def test_private_video(self):
        self.assertIn("private", download.explain_failure("ERROR: Private video"))

    def test_members_only_video(self):
        message = download.explain_failure("ERROR: This video is members-only")
        self.assertIn("members-only", message)

    def test_rate_limiting(self):
        message = download.explain_failure("ERROR: HTTP Error 429: Too Many Requests")
        self.assertIn("rate limiting", message)

    def test_format_problem_suggests_lowering_max_height(self):
        message = download.explain_failure("ERROR: Requested format is not available")
        self.assertIn("--max-height", message)

    def test_channel_url_is_explained(self):
        message = download.explain_failure("ERROR: Unsupported URL: https://x/y")
        self.assertIn("single video URL", message)

    def test_dpapi_failure_points_at_a_cookie_file_not_the_browser_flag(self):
        """Chrome 127+ App-Bound Encryption has no fix inside yt-dlp.

        The flag the user already passed is the thing that failed, so the hint
        must not tell them to pass it again. The only workaround is a
        ``cookies.txt`` file, which yt-dlp reads directly.
        """
        message = download.explain_failure(
            "ERROR: Failed to decrypt with DPAPI. See "
            "https://github.com/yt-dlp/yt-dlp/issues/10927 for more info"
        )
        self.assertIn("cookies.txt", message)
        self.assertIn("--cookies", message)
        self.assertNotIn("--cookies-from-browser", message)

    def test_locked_cookie_database_suggests_closing_the_browser(self):
        message = download.explain_failure("ERROR: Could not copy Chrome cookie database")
        self.assertIn("cookies.txt", message)

    def test_instagram_extraction_failure_names_curl_cffi(self):
        """Without curl_cffi, Instagram rejects the TLS fingerprint outright.

        The bare "Unable to extract data" reads like a broken extractor, so the
        hint has to name the missing optional dependency.
        """
        message = download.explain_failure(
            "ERROR: [instagram:user] nasa: Unable to extract data; please report "
            "this issue on https://github.com/yt-dlp/yt-dlp/issues"
        )
        self.assertIn("curl_cffi", message)

    def test_unknown_error_keeps_the_cause_and_the_url(self):
        message = download.explain_failure(
            "ERROR: something entirely new", "https://youtu.be/x"
        )
        self.assertIn("something entirely new", message)
        self.assertIn("https://youtu.be/x", message)

    def test_matching_is_case_insensitive(self):
        self.assertIn(
            "--cookies-from-browser",
            download.explain_failure("error: SIGN IN TO CONFIRM YOUR AGE"),
        )

    def test_the_last_error_line_wins(self):
        output = "ERROR: first problem\nWARNING: noise\nERROR: the real problem"
        self.assertIn("the real problem", download.explain_failure(output))
        self.assertNotIn("first problem", download.explain_failure(output))

    def test_json_noise_is_not_reported_as_the_cause(self):
        """yt-dlp dumps a literal ``null`` on failure; it is not an explanation."""
        message = download.explain_failure("null\nERROR: Video unavailable")
        self.assertNotIn("null", message)

    def test_output_without_an_error_line_falls_back_to_the_last_line(self):
        message = download.explain_failure("null\nsome unexpected failure")
        self.assertIn("some unexpected failure", message)

    def test_empty_output_gives_a_default_message(self):
        message = download.explain_failure("")
        self.assertIn("without a message", message)

    def test_output_of_only_json_noise_gives_a_default_message(self):
        self.assertIn("without a message", download.explain_failure("null\n{\n}"))

    def test_every_hint_is_reachable(self):
        """A hint that can never match is dead code."""
        for fragment, hint in download._HINTS:
            message = download.explain_failure(f"ERROR: {fragment}")
            self.assertIn(hint, message)


class ExtractJsonObjectTests(unittest.TestCase):
    def test_finds_the_object_among_noise(self):
        text = 'garbage {"id": "abc"} trailing'
        self.assertEqual(download._extract_json_object(text, "u"), '{"id": "abc"}')

    def test_handles_nested_objects(self):
        text = '{"a": {"b": 1}}'
        self.assertEqual(download._extract_json_object(text, "u"), text)

    def test_missing_object_raises(self):
        with self.assertRaises(ClipperError):
            download._extract_json_object("no json here", "u")

    def test_unbalanced_object_raises(self):
        with self.assertRaises(ClipperError):
            download._extract_json_object('{"a": 1', "u")


class ResolveOriginTests(unittest.TestCase):
    """The renderer seeks into the file it is handed, so this has to be right."""

    def test_full_download_needs_no_shift(self):
        self.assertEqual(download.resolve_origin("ffprobe", "media.mp4", 0.0), 0.0)

    def test_preserved_timestamps_need_no_shift(self):
        with patch.object(download.util, "probe_start_time", return_value=125.0):
            self.assertEqual(download.resolve_origin("ffprobe", "media.mp4", 121.0), 0.0)

    def test_reset_timeline_is_shifted_by_the_section_offset(self):
        with patch.object(download.util, "probe_start_time", return_value=0.0):
            self.assertEqual(download.resolve_origin("ffprobe", "media.mp4", 121.0), 121.0)

    def test_a_tiny_nonzero_start_counts_as_reset(self):
        with patch.object(download.util, "probe_start_time", return_value=0.2):
            self.assertEqual(download.resolve_origin("ffprobe", "media.mp4", 121.0), 121.0)

    def test_negative_requested_start_is_clamped_to_zero(self):
        with patch.object(download.util, "probe_start_time", return_value=0.0):
            self.assertEqual(download.resolve_origin("ffprobe", "media.mp4", -5.0), 0.0)


class ResolveDownloadedTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="download-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_returns_the_path_when_it_exists(self):
        target = self.tmp / "audio.m4a"
        target.write_bytes(b"x")
        self.assertEqual(download._resolve_downloaded(target), target)

    def test_finds_the_file_yt_dlp_renamed(self):
        (self.tmp / "audio.webm").write_bytes(b"x")
        self.assertEqual(
            download._resolve_downloaded(self.tmp / "audio.mp4"),
            self.tmp / "audio.webm",
        )

    def test_picks_the_largest_candidate(self):
        (self.tmp / "audio.webm").write_bytes(b"x" * 10)
        (self.tmp / "audio.mkv").write_bytes(b"x" * 500)
        self.assertEqual(
            download._resolve_downloaded(self.tmp / "audio.mp4"),
            self.tmp / "audio.mkv",
        )

    def test_partial_files_are_ignored(self):
        (self.tmp / "audio.webm.part").write_bytes(b"x" * 999)
        (self.tmp / "audio.webm").write_bytes(b"x")
        self.assertEqual(
            download._resolve_downloaded(self.tmp / "audio.webm"),
            self.tmp / "audio.webm",
        )

    def test_no_candidate_raises(self):
        with self.assertRaises(ClipperError):
            download._resolve_downloaded(self.tmp / "audio.mp4")


class BaseArgsTests(unittest.TestCase):
    def test_no_playlist_is_always_set(self):
        self.assertIn("--no-playlist", download._base_args(make_config()))

    def test_cookies_are_added_only_when_configured(self):
        self.assertNotIn(
            "--cookies-from-browser", download._base_args(make_config())
        )
        args = download._base_args(make_config(cookies_from_browser="chrome"))
        index = args.index("--cookies-from-browser")
        self.assertEqual(args[index + 1], "chrome")

    def test_extra_ytdlp_args_are_appended(self):
        args = download._base_args(make_config(extra_ytdlp_args=["--no-check-certificate"]))
        self.assertIn("--no-check-certificate", args)


class MetadataLanguageTests(unittest.TestCase):
    """The request language is what keeps titles in pt-BR.

    YouTube localizes every field to the language of the request, so the same
    video comes back as "LULA HAS LOST CONTROL OF THE GOVERNMENT" without this
    and as "LULA PERDEU CONTROLE do GOVERNO" with it.
    """

    @staticmethod
    def _extractor_args(args: list[str]) -> list[str]:
        """Every ``--extractor-args`` value in an argv list, in both spellings."""
        values = []
        for index, arg in enumerate(args):
            if arg.startswith("--extractor-args="):
                values.append(arg.split("=", 1)[1])
            elif arg == "--extractor-args":
                values.append(args[index + 1])
        return values

    def test_the_language_is_pt_by_default(self):
        args = download._base_args(make_config())
        self.assertEqual(self._extractor_args(args), ["youtube:lang=pt"])

    def test_a_configured_language_replaces_the_default(self):
        args = download._base_args(make_config(metadata_language="es"))
        self.assertEqual(self._extractor_args(args), ["youtube:lang=es"])

    def test_an_empty_language_leaves_ytdlp_alone(self):
        args = download._base_args(make_config(metadata_language=""))
        self.assertEqual(self._extractor_args(args), [])

    def test_a_language_the_user_pinned_wins(self):
        """An explicit ``youtube:lang=`` is an instruction, not a default."""
        args = download._base_args(
            make_config(extra_ytdlp_args=["--extractor-args", "youtube:lang=en"])
        )
        self.assertEqual(self._extractor_args(args), ["youtube:lang=en"])

    def test_the_equals_spelling_is_understood_too(self):
        args = download._base_args(
            make_config(extra_ytdlp_args=["--extractor-args=youtube:lang=en"])
        )
        self.assertEqual(self._extractor_args(args), ["youtube:lang=en"])

    def test_a_second_youtube_flag_would_erase_the_first(self):
        """One flag only: yt-dlp keeps the last and drops the earlier one.

        Passing the language as its own flag next to the user's
        ``player_client`` silently switched the titles back to English, because
        the second flag for the same extractor replaces the first.
        """
        args = download._base_args(
            make_config(extra_ytdlp_args=["--extractor-args", "youtube:player_client=web"])
        )
        self.assertEqual(self._extractor_args(args), ["youtube:lang=pt;player_client=web"])
        self.assertEqual(args.count("--extractor-args"), 1)

    def test_the_users_own_keys_survive_the_merge(self):
        args = download._base_args(
            make_config(
                extra_ytdlp_args=[
                    "--extractor-args",
                    "youtube:player_client=web;youtube_include_dash_manifest=False",
                ]
            )
        )
        self.assertEqual(
            self._extractor_args(args),
            ["youtube:lang=pt;player_client=web;youtube_include_dash_manifest=False"],
        )

    def test_the_same_key_twice_is_kept(self):
        """Repeating a key with another value is how yt-dlp takes two clients."""
        args = download._base_args(
            make_config(
                extra_ytdlp_args=["--extractor-args", "youtube:player_client=web;player_client=tv"]
            )
        )
        self.assertEqual(
            self._extractor_args(args), ["youtube:lang=pt;player_client=web;player_client=tv"]
        )

    def test_an_identical_repeat_is_dropped(self):
        args = download._base_args(
            make_config(
                extra_ytdlp_args=[
                    "--extractor-args",
                    "youtube:player_client=web",
                    "--extractor-args",
                    "youtube:player_client=web",
                ]
            )
        )
        self.assertEqual(self._extractor_args(args), ["youtube:lang=pt;player_client=web"])

    def test_another_extractor_is_left_untouched(self):
        args = download._base_args(
            make_config(extra_ytdlp_args=["--extractor-args", "instagram:api=graphql"])
        )
        self.assertIn("instagram:api=graphql", self._extractor_args(args))
        self.assertIn("youtube:lang=pt", self._extractor_args(args))

    def test_plain_flags_keep_their_place(self):
        config = make_config(
            extra_ytdlp_args=[
                "--cookies",
                "c.txt",
                "--extractor-args",
                "youtube:player_client=web",
            ]
        )
        args = download._base_args(config)
        # The merged flag goes last, so the caller's flags stay next to their own
        # values.
        self.assertEqual(args[args.index("--cookies") + 1], "c.txt")
        self.assertEqual(self._extractor_args(args), ["youtube:lang=pt;player_client=web"])

    def test_a_separator_in_the_language_is_refused(self):
        """The code is spliced into ``youtube:lang=<code>``."""
        for hostile in ("pt;player_client=web", "pt:en", "pt en", "pt=1"):
            with self.subTest(language=hostile):
                with self.assertRaises(ValueError):
                    make_config(metadata_language=hostile).validate()

    def test_a_plain_code_survives_validation(self):
        config = make_config(metadata_language="  pt  ")
        config.validate()
        self.assertEqual(config.metadata_language, "pt")


class VisibleDownloadFailureTests(unittest.TestCase):
    """A failing download must report what yt-dlp said, not a bare exit code."""

    def _run_with(self, code: int, output: str) -> None:
        with patch.object(
            download.util, "run_streaming_captured", return_value=(code, output)
        ):
            download._run_visible(["yt-dlp", "url"], "https://youtu.be/x", None)

    def test_failure_reports_the_reason_and_the_fix(self):
        with self.assertRaises(ClipperError) as ctx:
            self._run_with(1, "ERROR: HTTP Error 403: Forbidden")
        message = str(ctx.exception)
        self.assertIn("403", message)
        self.assertIn("--cookies-from-browser chrome", message)

    def test_interrupted_download_mentions_retrying(self):
        with self.assertRaises(ClipperError) as ctx:
            self._run_with(1, "ERROR: unable to download video data: HTTP Error 403")
        self.assertIn("Retry", str(ctx.exception))

    def test_unknown_cause_keeps_the_url(self):
        with self.assertRaises(ClipperError) as ctx:
            self._run_with(1, "ERROR: something nobody predicted")
        message = str(ctx.exception)
        self.assertIn("https://youtu.be/x", message)
        self.assertIn("something nobody predicted", message)

    def test_silent_failure_falls_back_to_the_exit_code(self):
        with self.assertRaises(ClipperError) as ctx:
            self._run_with(1, "")
        self.assertIn("exit code 1", str(ctx.exception))

    def test_success_is_silent(self):
        self._run_with(0, "everything fine")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
