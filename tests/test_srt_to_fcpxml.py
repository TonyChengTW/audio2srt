"""Behavioral tests for converting reviewed SRT subtitles to FCPXML titles.

These tests keep the SRT-to-FCPXML workflow independent from audio transcription,
so a corrected subtitle file can be converted without calling the OpenAI API.
"""

import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from srt_to_fcpxml import SubtitleStyle, build_fcpxml, main, parse_srt


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

    def test_applies_default_font_size_and_position(self):
        # The requested look is PingFang SC Semibold at size 62, placed with
        # the Basic Title Position parameter at X=0, Y=-415, Z=0 pixels.
        cue = {"start_ms": 0, "end_ms": 1000, "text": "樣式測試"}
        root = ET.fromstring(build_fcpxml([cue], fps="30"))
        title = root.find(".//title")
        style = title.find("text-style-def/text-style")

        self.assertEqual(style.attrib["fontSize"], "62")
        self.assertEqual(style.attrib["font"], "PingFang SC")
        self.assertEqual(style.attrib["fontFace"], "Semibold")
        position = title.find("param[@name='Position']")
        self.assertIsNotNone(position)
        self.assertEqual(position.attrib["key"], "9999/999166631/999166633/1/100/101")
        self.assertEqual(position.attrib["value"], "0 -415 0")
        # adjust-transform positions are a percentage of frame height, so a
        # pixel value there pushed the text far off screen in Final Cut Pro.
        self.assertIsNone(title.find("adjust-transform"))
        # Final Cut Pro expects title parameters before the text content.
        self.assertEqual(title[0].tag, "param")

    def test_accepts_custom_font_and_position(self):
        cue = {"start_ms": 0, "end_ms": 1000, "text": "自訂樣式"}
        style = SubtitleStyle(
            font="HanziPen TC",
            font_face="Bold",
            font_size=48,
            x=12.5,
            y=-300,
            z=10,
        )
        root = ET.fromstring(build_fcpxml([cue], fps="30", style=style))
        title = root.find(".//title")
        text_style = title.find("text-style-def/text-style")

        self.assertEqual(text_style.attrib["font"], "HanziPen TC")
        self.assertEqual(text_style.attrib["fontFace"], "Bold")
        self.assertEqual(text_style.attrib["fontSize"], "48")
        self.assertEqual(title.find("param[@name='Position']").attrib["value"], "12.5 -300 10")

    def test_cli_passes_style_options_to_the_output(self):
        with tempfile.TemporaryDirectory() as directory:
            srt_path = Path(directory) / "sample.srt"
            srt_path.write_text("1\n00:00:00,000 --> 00:00:01,000\n命令列\n", encoding="utf-8")

            exit_code = main(
                [str(srt_path), "--fps", "25", "--font-size", "70", "--y", "-380"]
            )

            self.assertEqual(exit_code, 0)
            root = ET.parse(srt_path.with_suffix(".fcpxml")).getroot()
        title = root.find(".//title")
        self.assertEqual(root.find("./resources/format").attrib["frameDuration"], "1/25s")
        self.assertEqual(title.find("text-style-def/text-style").attrib["fontSize"], "70")
        self.assertEqual(title.find("param[@name='Position']").attrib["value"], "0 -380 0")

    def test_requests_a_drop_shadow_on_the_text(self):
        # Drop Shadow is expressed with the shadow attributes of the text style.
        cue = {"start_ms": 0, "end_ms": 1000, "text": "陰影測試"}
        root = ET.fromstring(build_fcpxml([cue], fps="30"))
        style = root.find(".//title/text-style-def/text-style")

        self.assertEqual(style.attrib["shadowColor"], "0 0 0 0.75")
        # FCP stores the offset as "distance angle"; a bare distance is not
        # the form it writes when Drop Shadow is enabled.
        self.assertEqual(style.attrib["shadowOffset"], "5 315")
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
