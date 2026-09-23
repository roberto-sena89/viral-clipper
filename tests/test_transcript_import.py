"""Unit tests for :mod:`viralclipper.transcript_import`.

The parser is a pure function over text, so the whole matrix of shapes users
paste - SRT, WebVTT, the YouTube caption panel, plain prose - is covered here
without touching the network or a whisper model.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from viralclipper import transcript_import
from viralclipper.util import ClipperError

SRT = """1
00:00:03,000 --> 00:00:06,500
Abertura chocante sobre o vazamento

2
00:00:06,500 --> 00:00:09,000
segunda linha aqui
"""

VTT = """WEBVTT
Kind: captions

00:00:03.000 --> 00:00:06.500
Abertura chocante sobre o vazamento
"""

YOUTUBE_PANEL = """0:03
Abertura chocante sobre o vazamento
0:21
O cara nao e o pica das magias
1:05:12
trecho final do video
"""

PLAIN = "Primeira frase do video. Segunda frase, mais longa que a primeira. Terceira."


class ParseTimestampTests(unittest.TestCase):
    def test_accepts_the_common_shapes(self):
        self.assertEqual(transcript_import.parse_timestamp("0:03"), 3.0)
        self.assertEqual(transcript_import.parse_timestamp("00:03"), 3.0)
        self.assertEqual(transcript_import.parse_timestamp("1:02:03"), 3723.0)
        self.assertEqual(transcript_import.parse_timestamp("00:00:05,500"), 5.5)
        self.assertEqual(transcript_import.parse_timestamp("00:00:05.250"), 5.25)

    def test_rejects_junk(self):
        self.assertIsNone(transcript_import.parse_timestamp("abc"))
        self.assertIsNone(transcript_import.parse_timestamp(""))


class ParseTranscriptTests(unittest.TestCase):
    def test_srt_blocks(self):
        cues = transcript_import.parse_transcript(SRT)
        self.assertEqual(len(cues), 2)
        self.assertAlmostEqual(cues[0].start, 3.0)
        self.assertAlmostEqual(cues[0].end, 6.5)
        self.assertIn("Abertura chocante", cues[0].text)

    def test_webvtt_with_header(self):
        cues = transcript_import.parse_transcript(VTT)
        self.assertEqual(len(cues), 1)
        self.assertAlmostEqual(cues[0].end, 6.5)

    def test_youtube_panel_with_bare_timestamps(self):
        cues = transcript_import.parse_transcript(YOUTUBE_PANEL)
        self.assertEqual(len(cues), 3)
        self.assertAlmostEqual(cues[0].start, 3.0)
        self.assertAlmostEqual(cues[1].start, 21.0)
        self.assertAlmostEqual(cues[2].start, 3912.0)
        self.assertIn("magias", cues[1].text)

    def test_timestamp_and_text_on_the_same_line(self):
        cues = transcript_import.parse_transcript("[0:05] oi tudo bem\n[0:10] segunda parte")
        self.assertEqual(len(cues), 2)
        self.assertAlmostEqual(cues[0].start, 5.0)
        self.assertEqual(cues[0].text, "oi tudo bem")
        self.assertAlmostEqual(cues[1].start, 10.0)

    def test_plain_prose_gets_estimated_timings(self):
        cues = transcript_import.parse_transcript(PLAIN)
        self.assertEqual(len(cues), 3)
        self.assertEqual(cues[0].start, 0.0)
        self.assertGreater(cues[1].start, cues[0].start)
        self.assertGreater(cues[2].start, cues[1].start)
        self.assertTrue(all(cue.end > cue.start for cue in cues))

    def test_missing_end_falls_back_to_next_start(self):
        cues = transcript_import.parse_transcript("0:10\ntexto solto")
        self.assertEqual(len(cues), 1)
        self.assertGreater(cues[0].end, cues[0].start)

    def test_empty_input_raises(self):
        with self.assertRaises(ClipperError):
            transcript_import.parse_transcript("   \n  ")


class CuesToWordsTests(unittest.TestCase):
    def test_words_cover_the_cue_span_in_order(self):
        cues = [transcript_import.Cue(start=2.0, end=4.0, text="um dois tres")]
        words = transcript_import.cues_to_words(cues)
        self.assertEqual([w.text for w in words], ["um", "dois", "tres"])
        self.assertAlmostEqual(words[0].start, 2.0)
        self.assertAlmostEqual(words[-1].end, 4.0)
        self.assertTrue(all(words[i].end <= words[i + 1].start + 0.001 for i in range(len(words) - 1)))


class TranscriptFromTextTests(unittest.TestCase):
    def test_builds_a_pipeline_transcript(self):
        transcript, cues = transcript_import.transcript_from_text(YOUTUBE_PANEL)
        self.assertEqual(len(cues), 3)
        self.assertFalse(transcript.empty)
        self.assertEqual(transcript.language, "manual")
        self.assertIn("Abertura chocante", transcript.text)

    def test_file_roundtrip(self):
        tmp = Path(tempfile.mkdtemp(prefix="vc_ti_"))
        path = tmp / "transcript.srt"
        path.write_text(SRT, encoding="utf-8")
        self.assertEqual(transcript_import.load_transcript_file(path), SRT)
        transcript, cues = transcript_import.transcript_from_file(path)
        self.assertEqual(len(cues), 2)
        self.assertFalse(transcript.empty)

    def test_missing_file_raises(self):
        with self.assertRaises(ClipperError):
            transcript_import.load_transcript_file("nao_existe.srt")


class NormalizeTranscriptTests(unittest.TestCase):
    """The cleanup that turns a messy paste into an aligned cue list."""

    def test_removes_consecutive_duplicates(self):
        raw = "0:05\noi tudo bem\n0:05\noi tudo bem\n0:10\nproxima fala"
        result = transcript_import.normalize_transcript(raw)
        self.assertEqual(len(result.cues), 2)
        self.assertEqual(result.duplicates_removed, 1)

    def test_merges_a_broken_sentence(self):
        raw = "0:05\no cara nao e o pica das\n0:06\nmagias de tecnologia"
        result = transcript_import.normalize_transcript(raw)
        self.assertEqual(len(result.cues), 1)
        self.assertEqual(result.fragments_merged, 1)
        self.assertIn("magias", result.cues[0].text)

    def test_sorted_output_and_reorder_count(self):
        raw = "0:30\nsegunda fala\n0:10\nprimeira fala"
        result = transcript_import.normalize_transcript(raw)
        self.assertEqual([round(cue.start) for cue in result.cues], [10, 30])
        # both lines changed position, so both are reported
        self.assertEqual(result.reordered, 2)
        self.assertLess(result.normalized.index("Primeira"), result.normalized.index("Segunda"))

    def test_text_is_cleaned_and_capitalized(self):
        result = transcript_import.normalize_transcript("0:05\n  oi   ,   tudo,  bem ,,  ")
        self.assertEqual(result.cues[0].text, "Oi, tudo, bem,")

    def test_normalized_layout_is_minute_aligned(self):
        raw = "0:05\noi\n10:00\nfala longa depois de dez minutos"
        result = transcript_import.normalize_transcript(raw)
        lines = result.normalized.split("\n")
        self.assertEqual(len(lines), 2)
        self.assertTrue(lines[0].startswith("0:05"))
        self.assertTrue(lines[1].startswith("10:00"))
        # both labels padded to the same width, so the speech starts in the
        # same column
        self.assertEqual(lines[0].index("Oi"), lines[1].index("Fala"))

    def test_normalized_output_round_trips(self):
        raw = "0:05\nprimeira fala do video\n0:12\nsegunda fala do video"
        first = transcript_import.normalize_transcript(raw)
        second = transcript_import.normalize_transcript(first.normalized)
        self.assertEqual(first.normalized, second.normalized)
        self.assertEqual(len(second.cues), 2)

    def test_dict_payload_shape(self):
        payload = transcript_import.normalize_transcript("0:05\nola tudo bem").to_dict()
        self.assertIn("normalized", payload)
        self.assertEqual(payload["stats"]["cues"], 1)
        self.assertEqual(payload["cues"][0]["label"], "0:05")
        self.assertGreater(payload["stats"]["words"], 0)

    def test_short_clock(self):
        self.assertEqual(transcript_import.short_clock(5), "0:05")
        self.assertEqual(transcript_import.short_clock(65), "1:05")
        self.assertEqual(transcript_import.short_clock(3725), "1:02:05")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

