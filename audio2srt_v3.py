# Copyright 2025 All rights reserved
# Author : Tony Cheng  tony.pig@gmail.com
# Version: 0.8

import os
import math
import tempfile
from pydub import AudioSegment
import openai

openai.api_key = os.getenv("OPENAI_API_KEY")

MAX_FILE_SIZE = 25 * 1024 * 1024  # 25MB per chunk for Whisper


def split_audio(audio_path, max_size=MAX_FILE_SIZE):
    audio = AudioSegment.from_file(audio_path)
    bitrate = 32000  # assume 32 kbps MP3
    bytes_per_second = bitrate / 8
    duration_limit = max_size / bytes_per_second  # seconds

    chunks = []
    total_duration = len(audio) / 1000  # ms -> sec
    num_chunks = math.ceil(total_duration / duration_limit)

    for i in range(num_chunks):
        start_ms = int(i * duration_limit * 1000)
        end_ms = int(min((i + 1) * duration_limit * 1000, len(audio)))
        chunk = audio[start_ms:end_ms]
        temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".mp3")
        chunk.export(temp_file.name, format="mp3")
        chunks.append(temp_file.name)

    return chunks


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
        chunks = split_audio(file_path)
    else:
        chunks = [file_path]

    full_srt = ""
    for idx, chunk_file in enumerate(chunks):
        print(f"正在處理第 {idx + 1} 段：{chunk_file}")
        try:
            srt = transcribe(chunk_file)
            full_srt += f"\n{srt}"
        finally:
            if chunk_file != file_path:
                os.remove(chunk_file)

    return full_srt


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

