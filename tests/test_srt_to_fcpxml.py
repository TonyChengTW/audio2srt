"""Behavioral tests for converting reviewed SRT subtitles to FCPXML titles.

These tests keep the SRT-to-FCPXML workflow independent from audio transcription,
so a corrected subtitle file can be converted without calling the OpenAI API.
"""

import unittest
import xml.etree.ElementTree as ET

from srt_to_fcpxml import build_fcpxml, parse_srt


class SrtToFcpxmlTests(unittest.TestCase):
    """Verify the generated XML represents editable Basic Title clips."""

    def test_creates_timed_basic_title_objects_from_srt_cues(self):
        # This fixture covers two sequential cues and Unicode text, matching the
        # structure emitted by audio2srt.py while remaining free of API calls.
        srt_text = (
            "1\n"
            "00:00:00,000 --> 00:00:01,000\n"
            "第一句字幕\n\n"
            "2\n"
            "00:00:01,000 --> 00:00:02,500\n"
            "第二句字幕\n"
        )

        # Parse the reviewed SRT, then build an independent FCPXML project.
        cues = parse_srt(srt_text)
        fcpxml_text = build_fcpxml(cues, fps="30")
        root = ET.fromstring(fcpxml_text)

        # Each SRT cue must become its own editable FCP title clip with the
        # same text and frame-aligned start and duration values.
        self.assertEqual(root.tag, "fcpxml")
        self.assertEqual(root.attrib["version"], "1.13")
        titles = root.findall(".//title")
        self.assertEqual(len(titles), 2)
        title_texts = ["".join(title.find("text").itertext()) for title in titles]
        self.assertEqual(title_texts, ["第一句字幕", "第二句字幕"])
        self.assertEqual(titles[0].attrib["name"], "第一句字幕")
        self.assertEqual(titles[0].attrib["offset"], "0s")
        self.assertEqual(titles[0].attrib["duration"], "1s")
        self.assertEqual(titles[1].attrib["offset"], "1s")
        self.assertEqual(titles[1].attrib["duration"], "3/2s")

    def test_uses_style_definition_reference_with_white_text_colour(self):
        # Final Cut Pro drops an inline text style and keeps the title black.
        # It only honours a text-style-def reference, and without an explicit
        # font colour the glyphs render invisible against a black frame.
        cue = {"start_ms": 0, "end_ms": 1000, "text": "顏色測試"}
        root = ET.fromstring(build_fcpxml([cue], fps="30"))
        title = root.find(".//title")

        self.assertIsNotNone(title)
        text_style = title.find("text/text-style")
        style_definition = title.find("text-style-def")
        self.assertIsNotNone(text_style)
        self.assertIsNotNone(style_definition)
        self.assertEqual(text_style.attrib["ref"], style_definition.attrib["id"])
        self.assertEqual(text_style.text, "顏色測試")

        style = style_definition.find("text-style")
        self.assertEqual(style.attrib["fontColor"], "0.999996 1 1 1")

    def test_applies_requested_font_size_and_vertical_position(self):
        # The requested look is 華康翩翩體 at size 58, sitting at Y = -410.
        cue = {"start_ms": 0, "end_ms": 1000, "text": "樣式測試"}
        root = ET.fromstring(build_fcpxml([cue], fps="30"))
        title = root.find(".//title")
        style = title.find("text-style-def/text-style")

        self.assertEqual(style.attrib["fontSize"], "58")
        self.assertEqual(style.attrib["font"], "DFPHanziPenW5-B5-TW")
        self.assertEqual(title.find("adjust-transform").attrib["position"], "0 -410")

    def test_requests_a_drop_shadow_on_the_text(self):
        # Drop Shadow is expressed with the shadow attributes of the text style.
        cue = {"start_ms": 0, "end_ms": 1000, "text": "陰影測試"}
        root = ET.fromstring(build_fcpxml([cue], fps="30"))
        style = root.find(".//title/text-style-def/text-style")

        self.assertEqual(style.attrib["shadowColor"], "0 0 0 0.75")
        self.assertEqual(style.attrib["shadowOffset"], "5")
        self.assertIn("shadowBlurRadius", style.attrib)

    def test_skips_empty_subtitle_cues_with_a_warning(self):
        # Whisper can emit an empty timed block. It has no visible title to
        # generate, so conversion should continue without changing the SRT.
        srt_text = (
            "1\n00:00:00,000 --> 00:00:01,000\n第一句\n\n"
            "2\n00:00:01,000 --> 00:00:02,000\n\n"
            "3\n00:00:02,000 --> 00:00:03,000\n第三句\n"
        )

        with self.assertWarnsRegex(UserWarning, "cue 2.*no subtitle text"):
            cues = parse_srt(srt_text)

        self.assertEqual([cue["text"] for cue in cues], ["第一句", "第三句"])

    def test_uses_final_cut_pro_standard_name_for_fractional_rate(self):
        # FCPXML names this common NTSC-derived project rate as 2398, while
        # timing itself must retain the exact 24000/1001 rational rate.
        cue = {
            "start_ms": 0,
            "end_ms": 1000,
            "text": "Frame-rate test",
        }
        root = ET.fromstring(build_fcpxml([cue], fps="23.976"))
        video_format = root.find("./resources/format")

        self.assertIsNotNone(video_format)
        self.assertEqual(video_format.attrib["name"], "FFVideoFormat1080p2398")
        self.assertEqual(video_format.attrib["frameDuration"], "1001/24000s")


if __name__ == "__main__":
    unittest.main()
