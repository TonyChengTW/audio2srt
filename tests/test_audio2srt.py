"""Behavioral tests for the audio2srt chunking and timing arithmetic.

These tests use no API calls. They pin the timing behaviour that decides
whether the generated subtitles stay aligned with the spoken audio.
"""

import unittest
from datetime import datetime

import audio2srt


class ShiftTimeTests(unittest.TestCase):
    """Verify the millisecond arithmetic used to offset chunk transcripts."""

    def test_preserves_millisecond_precision(self):
        # A timestamp with a non-zero millisecond field must survive unchanged.
        self.assertEqual(audio2srt.shift_time_str("00:00:10,500", 0), "00:00:10,500")
        self.assertEqual(audio2srt.shift_time_str("00:00:10,999", 0), "00:00:10,999")

    def test_adds_offset_seconds_to_milliseconds(self):
        # The offset must be applied with millisecond accuracy, not by
        # truncating the sub-second part of either timestamp.
        self.assertEqual(audio2srt.shift_time_str("00:00:10,500", 5), "00:00:15,500")
        self.assertEqual(audio2srt.shift_time_str("00:00:10,250", 782.622), "00:13:12,872")

    def test_handles_carry_into_next_second(self):
        # Adding a millisecond offset that crosses a second boundary must
        # carry correctly instead of dropping the overflow.
        self.assertEqual(audio2srt.shift_time_str("00:00:10,900", 0.2), "00:00:11,100")

    def test_rejects_invalid_timestamps(self):
        # A malformed timestamp must fail loudly rather than silently shifting.
        with self.assertRaises(ValueError):
            audio2srt.shift_time_str("00:00:10", 0)


class ChunkOffsetTests(unittest.TestCase):
    """Verify chunk offsets keep sub-second precision."""

    def test_chunk_offsets_keep_millisecond_precision(self):
        # Chunk offsets are used to shift the transcript of every chunk after
        # the first. Truncating them to whole seconds makes the subtitles
        # drift further behind the audio with every chunk.
        offsets = audio2srt.chunk_offsets(4000, 1000)
        self.assertEqual(offsets, [0.0, 1.0, 2.0, 3.0])

    def test_chunk_offsets_are_derived_from_millisecond_positions(self):
        # The helper works in milliseconds and returns exact seconds.
        offsets = audio2srt.chunk_offsets(1_086_668, 782_622)
        self.assertEqual(offsets[0], 0)
        self.assertAlmostEqual(offsets[1], 782.622, places=6)


class ChunkSizeTests(unittest.TestCase):
    """Verify the chunk duration honours the API upload limit."""

    def test_chunk_duration_uses_export_bitrate_not_source_bitrate(self):
        # The uploaded file is a freshly encoded mp3, so the chunk duration
        # must be derived from the export bitrate instead of the source.
        # 128 kbps means 16 KB per second, so a 24 MB budget allows about
        # 1500 s, minus the 5% safety margin.
        duration_ms = audio2srt.chunk_duration_ms(
            max_bytes=24 * 1024 * 1024,
            export_bitrate_kbps=128,
        )
        self.assertEqual(duration_ms, 1_494_220)

    def test_chunk_duration_never_exceeds_the_upload_limit(self):
        # Whatever the bitrate, the exported chunk must stay under the limit.
        for bitrate in (64, 96, 128, 192, 320):
            duration_ms = audio2srt.chunk_duration_ms(
                max_bytes=24 * 1024 * 1024,
                export_bitrate_kbps=bitrate,
            )
            estimated_bytes = bitrate * 1000 / 8 * duration_ms / 1000
            self.assertLessEqual(estimated_bytes, 24 * 1024 * 1024)


class SrtAssemblyTests(unittest.TestCase):
    """Verify the final SRT timeline is continuous across chunk borders."""

    def test_renumbering_continues_across_chunks(self):
        # Chunk transcripts are renumbered into one continuous sequence.
        srt_text, next_index = audio2srt.shift_and_renumber_srt(
            "1\n00:00:00,000 --> 00:00:01,000\n甲\n", 0, 1
        )
        self.assertEqual(next_index, 2)
        self.assertIn("1\n00:00:00,000 --> 00:00:01,000\n甲", srt_text)

    def test_second_chunk_keeps_millisecond_offset(self):
        # The second chunk's transcript must be shifted by its exact offset.
        srt_text, next_index = audio2srt.shift_and_renumber_srt(
            "1\n00:00:00,000 --> 00:00:01,000\n乙\n", 782.622, 10
        )
        self.assertEqual(next_index, 11)
        self.assertIn("00:13:02,622 --> 00:13:03,622", srt_text)


if __name__ == "__main__":
    unittest.main()
