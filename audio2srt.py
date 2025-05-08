# GNU License
# Author : Tony Cheng  tony.pig@gmail.com
# Version: 1.0
# audio2srt.py - 分段 + Whisper + 時間碼自動偏移 + 重編號 + 排版修正

import os
import math
import tempfile
import re
from pydub import AudioSegment
from datetime import datetime, timedelta
import openai

openai.api_key = os.getenv("OPENAI_API_KEY")

MAX_FILE_SIZE = 24 * 1024 * 1024  # 24MB 安全範圍


def split_audio_by_size(audio_path):
    audio = AudioSegment.from_file(audio_path)
    file_size = os.path.getsize(audio_path)
    duration_ms = len(audio)
    bytes_per_ms = file_size / duration_ms
    max_bytes = MAX_FILE_SIZE
    max_duration_ms = int(max_bytes / bytes_per_ms)

    chunks = []
    for i in range(0, duration_ms, max_duration_ms):
        chunk = audio[i:i + max_duration_ms]
        temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".mp3")
        chunk.export(temp_file.name, format="mp3")
        chunks.append((temp_file.name, i // 1000))  # (file_path, start_sec)
    return chunks


def parse_srt(srt_text):
    entries = []
    parts = srt_text.strip().split("\n\n")
    for part in parts:
        lines = part.strip().splitlines()
        if len(lines) >= 3:
            time_line = lines[1]
            content = "\n".join(lines[2:])
            start_str, end_str = time_line.split(" --> ")
            entries.append({
                "start": start_str.strip(),
                "end": end_str.strip(),
                "text": content
            })
    return entries


def shift_time_str(t_str, offset_seconds):
    t = datetime.strptime(t_str, "%H:%M:%S,%f")
    t_shifted = t + timedelta(seconds=offset_seconds)
    return t_shifted.strftime("%H:%M:%S,%f")[:-3]


def shift_and_renumber_srt(srt_text, offset_seconds, index_start):
    entries = parse_srt(srt_text)
    shifted = []
    for i, entry in enumerate(entries):
        shifted.append(f"{index_start + i}")
        shifted.append(
            f"{shift_time_str(entry['start'], offset_seconds)} --> {shift_time_str(entry['end'], offset_seconds)}"
        )
        shifted.append(entry["text"])
        shifted.append("")  # 段落間空行
    return "\n".join(shifted), index_start + len(entries)


def transcribe(file_path):
    with open(file_path, "rb") as f:
        transcript = openai.audio.transcriptions.create(
            model="whisper-1",
            file=f,
            response_format="srt"
        )
    return transcript


def process_audio(file_path):
    file_size = os.path.getsize(file_path)
    if file_size > MAX_FILE_SIZE:
        print("檔案太大，開始自動切割處理...")
        chunks = split_audio_by_size(file_path)
    else:
        chunks = [(file_path, 0)]

    full_srt = ""
    index_offset = 1

    for idx, (chunk_file, start_sec) in enumerate(chunks):
        print(f"正在處理第 {idx + 1} 段：{chunk_file}")
        try:
            srt = transcribe(chunk_file)
            shifted_srt, index_offset = shift_and_renumber_srt(srt, start_sec, index_offset)
            full_srt += f"{shifted_srt.strip()}\n\n"
        finally:
            if chunk_file != file_path:
                os.remove(chunk_file)

    return full_srt.strip()


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("請提供音訊檔案作為參數")
        exit(1)

    input_file = sys.argv[1]
    srt_text = process_audio(input_file)

    output_file = os.path.splitext(input_file)[0] + ".srt"
    with open(output_file, "w", encoding="utf-8") as f:
        f.write(srt_text)

    print(f"字幕已儲存至：{output_file}")
