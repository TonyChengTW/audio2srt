#!/usr/bin/env python3
"""Convert a reviewed SRT file into an FCPXML project of Basic Title clips.

This is intentionally a separate second-stage tool: transcribe and correct the
SRT first, then run this program against that reviewed file. It makes no API
calls and uses only Python's standard library.
"""

import argparse
import re
import sys
import warnings
import xml.etree.ElementTree as ET
from fractions import Fraction
from pathlib import Path
from typing import List, Optional, Sequence, TypedDict


class SubtitleCue(TypedDict):
    """One validated SRT cue with integer millisecond times."""

    start_ms: int
    end_ms: int
    text: str


# Final Cut Pro 11.0.1 ships the matching 1.13 interchange DTD. Keep the
# version here aligned with the installed Final Cut Pro target application.
FCPXML_VERSION = "1.13"

# This is the built-in Basic Title Motion template's identifier in the current
# Final Cut Pro application bundle. Referencing it makes each item an editable
# Basic Title clip instead of a caption-track item.
BASIC_TITLE_UID = (
    ".../Titles.localized/Bumper:Opener.localized/"
    "Basic Title.localized/Basic Title.moti"
)

# Common project rates use exact rational values so NTSC-derived rates do not
# accumulate floating-point timing drift. The default is a broadly compatible
# 30 fps; use --fps to match the destination Final Cut Pro project when needed.
FRAME_RATES = {
    "23.976": Fraction(24000, 1001),
    "24": Fraction(24, 1),
    "25": Fraction(25, 1),
    "29.97": Fraction(30000, 1001),
    "30": Fraction(30, 1),
    "50": Fraction(50, 1),
    "59.94": Fraction(60000, 1001),
    "60": Fraction(60, 1),
}

# 字幕外觀預設值。這些參數經 FCP 實測確認：Final Cut Pro 會捨棄 inline 的
# text-style 屬性，只認 text-style-def 參照；而且缺少 fontColor 時會套用
# 黑色填色，在黑色畫面上就完全看不見字。
SUBTITLE_FONT = "DFPHanziPenW5-B5-TW"  # 華康翩翩體 W5 的 PostScript 名稱
SUBTITLE_FONT_FACE = "Regular"
SUBTITLE_FONT_SIZE = "58"
# FCP 匯入時會把 1.0 正規化為 0.999996，這個寫法才會被 FCP 接受為白色。
SUBTITLE_FONT_COLOR = "0.999996 1 1 1"
SUBTITLE_SHADOW_COLOR = "0 0 0 0.75"
SUBTITLE_SHADOW_OFFSET = "5"
SUBTITLE_SHADOW_BLUR_RADIUS = "3"
# 字幕在畫面上的垂直位置（像素，中心原點，負值代表往下）。
SUBTITLE_Y_POSITION = "-410"

# Use the common SRT time form, while accepting either comma or dot before the
# millisecond field. Optional trailing cue settings are ignored intentionally.
_TIMING_LINE = re.compile(
    r"^\s*(\d{2}:\d{2}:\d{2}[,.]\d{1,3})\s*-->\s*"
    r"(\d{2}:\d{2}:\d{2}[,.]\d{1,3})(?:\s+.*)?\s*$"
)


def _timestamp_to_milliseconds(timestamp: str) -> int:
    """Convert an SRT timestamp to integer milliseconds without float math."""
    normalized = timestamp.replace(".", ",")
    hours_text, minutes_text, seconds_and_ms = normalized.split(":")
    seconds_text, milliseconds_text = seconds_and_ms.split(",")
    milliseconds_text = milliseconds_text.ljust(3, "0")

    hours = int(hours_text)
    minutes = int(minutes_text)
    seconds = int(seconds_text)
    milliseconds = int(milliseconds_text)

    if minutes >= 60 or seconds >= 60:
        raise ValueError(f"Invalid SRT timestamp: {timestamp}")

    return (((hours * 60 + minutes) * 60) + seconds) * 1000 + milliseconds


def parse_srt(srt_text: str) -> List[SubtitleCue]:
    """Parse SRT blocks into cue dictionaries with millisecond timestamps.

    The parser tolerates a UTF-8 BOM, CRLF line endings, blank separator lines,
    and optional cue numbers. Subtitle text is preserved, including line breaks.
    """
    # Normalize platform-specific line endings before separating SRT blocks.
    normalized = srt_text.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    blocks = re.split(r"\n[ \t]*\n+", normalized.strip())
    cues: List[SubtitleCue] = []

    for block_number, block in enumerate(blocks, start=1):
        lines = block.splitlines()
        timing_index = next(
            (index for index, line in enumerate(lines) if _TIMING_LINE.match(line)),
            None,
        )
        if timing_index is None:
            # Ignore harmless non-cue blocks, but do not silently accept an SRT
            # containing no valid cues; that case is rejected after the loop.
            continue

        match = _TIMING_LINE.match(lines[timing_index])
        assert match is not None  # The search above guarantees a match.
        start_ms = _timestamp_to_milliseconds(match.group(1))
        end_ms = _timestamp_to_milliseconds(match.group(2))
        text = "\n".join(lines[timing_index + 1 :]).strip()

        if end_ms <= start_ms:
            raise ValueError(f"SRT cue {block_number} has a non-positive duration")
        if not text:
            # Empty timed cues carry no visible content. Skip them without
            # touching the source SRT, but tell the caller which block was lost.
            warnings.warn(
                f"SRT cue {block_number} has no subtitle text; skipping it",
                UserWarning,
                stacklevel=2,
            )
            continue

        cues.append({"start_ms": start_ms, "end_ms": end_ms, "text": text})

    if not cues:
        raise ValueError("The SRT file contains no valid subtitle cues")

    return cues


def _round_fraction_half_up(value: Fraction) -> int:
    """Round a positive rational number to the nearest integer frame."""
    # SRT times are non-negative. The integer expression implements half-up
    # rounding exactly and avoids Python's ties-to-even rounding behavior.
    return (2 * value.numerator + value.denominator) // (2 * value.denominator)


def _milliseconds_to_frames(milliseconds: int, frame_rate: Fraction) -> int:
    """Snap an SRT millisecond timestamp to the nearest sequence frame."""
    return _round_fraction_half_up(Fraction(milliseconds, 1000) * frame_rate)


def _frame_time(frame_number: int, frame_rate: Fraction) -> str:
    """Encode a frame index as an exact FCPXML rational time in seconds."""
    seconds = Fraction(frame_number, 1) / frame_rate
    if seconds.denominator == 1:
        return f"{seconds.numerator}s"
    return f"{seconds.numerator}/{seconds.denominator}s"


def _format_name(frame_rate: Fraction) -> str:
    """Return the standard Final Cut Pro name for a supported video rate."""
    # Final Cut Pro uses shortened labels for fractional NTSC rates rather than
    # a literal decimal suffix, for example 23.976 fps is named 1080p2398.
    format_suffixes = {
        "23.976": "2398",
        "24": "24",
        "25": "25",
        "29.97": "2997",
        "30": "30",
        "50": "50",
        "59.94": "5994",
        "60": "60",
    }
    for label, rate in FRAME_RATES.items():
        if rate == frame_rate:
            return f"FFVideoFormat1080p{format_suffixes[label]}"
    return "FFVideoFormat1080pCustom"


def build_fcpxml(
    cues: Sequence[SubtitleCue],
    fps: str = "30",
    project_name: str = "SRT Titles",
) -> str:
    """Build an FCPXML project containing one connected Basic Title per cue.

    The sequence uses a 1920x1080 format and a transparent-duration gap as its
    primary storyline. Each title is connected above that gap at the SRT cue's
    frame-aligned time, making it straightforward to copy into a video project.
    """
    if fps not in FRAME_RATES:
        supported = ", ".join(FRAME_RATES)
        raise ValueError(f"Unsupported fps {fps!r}; choose one of: {supported}")
    if not cues:
        raise ValueError("At least one SRT cue is required")

    frame_rate = FRAME_RATES[fps]
    frame_duration = _frame_time(1, frame_rate)

    # Convert all cue times first so the sequence and its title clips use the
    # exact same rational frame grid and the gap covers the last subtitle.
    frame_cues = []
    sequence_end_frame = 0
    for cue in cues:
        start_ms = int(cue["start_ms"])
        end_ms = int(cue["end_ms"])
        start_frame = _milliseconds_to_frames(start_ms, frame_rate)
        end_frame = _milliseconds_to_frames(end_ms, frame_rate)

        # A very short cue can round to zero frames; retain it as one visible
        # frame rather than emitting an invalid zero-duration title object.
        if end_frame <= start_frame:
            end_frame = start_frame + 1

        frame_cues.append((cue, start_frame, end_frame))
        sequence_end_frame = max(sequence_end_frame, end_frame)

    root = ET.Element("fcpxml", {"version": FCPXML_VERSION})
    resources = ET.SubElement(root, "resources")
    ET.SubElement(
        resources,
        "format",
        {
            "id": "r1",
            "name": _format_name(frame_rate),
            "frameDuration": frame_duration,
            "width": "1920",
            "height": "1080",
            "fieldOrder": "progressive",
        },
    )
    ET.SubElement(
        resources,
        "effect",
        {"id": "r2", "name": "Basic Title", "uid": BASIC_TITLE_UID},
    )

    library = ET.SubElement(root, "library")
    event = ET.SubElement(library, "event", {"name": project_name})
    project = ET.SubElement(event, "project", {"name": project_name})
    sequence = ET.SubElement(
        project,
        "sequence",
        {
            "format": "r1",
            "duration": _frame_time(sequence_end_frame, frame_rate),
            "tcStart": "0s",
            "tcFormat": "NDF",
        },
    )
    spine = ET.SubElement(sequence, "spine")
    gap = ET.SubElement(
        spine,
        "gap",
        {
            "name": "Subtitle title track",
            "offset": "0s",
            "duration": _frame_time(sequence_end_frame, frame_rate),
        },
    )

    for style_index, (cue, start_frame, end_frame) in enumerate(frame_cues, start=1):
        # Final Cut Pro requires a distinct text-style definition ID for each
        # title, so generate stable, local references in sequence order.
        style_id = f"ts{style_index}"
        # Set the timeline clip label to its subtitle instead of the template
        # name, so inspecting the timeline does not show only "Basic Title".
        title_name = " ".join(str(cue["text"]).split())
        title = ET.SubElement(
            gap,
            "title",
            {
                "ref": "r2",
                "name": title_name,
                "lane": "1",
                "offset": _frame_time(start_frame, frame_rate),
                "duration": _frame_time(end_frame - start_frame, frame_rate),
            },
        )

        # Reference a separate text-style definition. Final Cut Pro drops the
        # attributes of an inline text-style, and without an explicit font
        # colour it renders the glyphs black, which is invisible on a black
        # frame. The definition must therefore carry the colour itself.
        text_element = ET.SubElement(title, "text")
        styled_text = ET.SubElement(text_element, "text-style", {"ref": style_id})
        styled_text.text = str(cue["text"])

        style_definition = ET.SubElement(title, "text-style-def", {"id": style_id})
        ET.SubElement(
            style_definition,
            "text-style",
            {
                "font": SUBTITLE_FONT,
                "fontFace": SUBTITLE_FONT_FACE,
                "fontSize": SUBTITLE_FONT_SIZE,
                "fontColor": SUBTITLE_FONT_COLOR,
                "shadowColor": SUBTITLE_SHADOW_COLOR,
                "shadowOffset": SUBTITLE_SHADOW_OFFSET,
                "shadowBlurRadius": SUBTITLE_SHADOW_BLUR_RADIUS,
                "alignment": "center",
            },
        )

        # Set the clip transform to the requested lower-screen Y coordinate.
        ET.SubElement(title, "adjust-transform", {"position": f"0 {SUBTITLE_Y_POSITION}"})


    # Keep the generated XML compact. ElementTree's generic pretty-printer adds
    # whitespace inside mixed-content <text> elements, which would become real
    # characters displayed in the subtitle titles.
    xml_body = ET.tostring(root, encoding="unicode")
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        "<!DOCTYPE fcpxml>\n"
        f"{xml_body}\n"
    )


def convert_srt_file(
    srt_path: Path,
    output_path: Optional[Path] = None,
    fps: str = "30",
) -> Path:
    """Convert one reviewed SRT file and return the written FCPXML path."""
    srt_path = Path(srt_path)
    if not srt_path.is_file():
        raise FileNotFoundError(f"SRT file not found: {srt_path}")

    # Default to a sibling output so the SRT remains untouched for further edits.
    if output_path is None:
        output_path = srt_path.with_suffix(".fcpxml")
    else:
        output_path = Path(output_path)

    srt_text = srt_path.read_text(encoding="utf-8-sig")
    cues = parse_srt(srt_text)
    fcpxml_text = build_fcpxml(cues, fps=fps, project_name=srt_path.stem)
    output_path.write_text(fcpxml_text, encoding="utf-8")
    return output_path


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Run the independent SRT-to-FCPXML command-line conversion step."""
    parser = argparse.ArgumentParser(
        description=(
            "Convert a reviewed SRT into a Final Cut Pro XML project of "
            "editable Basic Title clips."
        )
    )
    parser.add_argument("srt_file", type=Path, help="Reviewed .srt input file")
    parser.add_argument(
        "--fps",
        choices=tuple(FRAME_RATES),
        default="30",
        help="Final Cut Pro sequence frame rate (default: 30)",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="Output FCPXML path (default: same filename with .fcpxml extension)",
    )
    args = parser.parse_args(argv)

    try:
        output_path = convert_srt_file(args.srt_file, args.output, args.fps)
    except (OSError, UnicodeError, ValueError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 2

    print(f"FCPXML 已儲存至：{output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
