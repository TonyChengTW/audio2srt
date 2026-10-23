# GNU License
# Author : Tony Cheng  tony.pig@gmail.com
# Version: 1.1
# audio2srt.py - 分段 + Whisper + 時間碼自動偏移 + 重編號 + 排版修正
#
# 1.1 修正紀錄：
#   - 分段偏移改用毫秒級精確值，避免每段累加秒數誤差造成字幕漂移
#   - 分段長度依「重新編碼後的位元率」計算，確保上傳檔案不超過 API 上限
#   - 毫秒時間碼改以整數運算，避免 datetime 的 %f 把毫秒當成微秒
#   - 每段長度限制在 Whisper 的長度上限內，避免尾端內容被 API 截斷

import os
import tempfile

import openai
from pydub import AudioSegment

openai.api_key = os.getenv("OPENAI_API_KEY")

MAX_FILE_SIZE = 24 * 1024 * 1024  # 24MB 安全範圍

# pydub 預設匯出 mp3 的位元率。分段長度必須依這個值計算，因為實際上傳的
# 是重新編碼後的檔案，不是原始音檔。
EXPORT_BITRATE_KBPS = 128

# 重新編碼時要保留的音訊參數。取樣率與聲道數若與原檔不同，聲音內容會
# 被重新縮放，導致語速或音色改變。
EXPORT_SAMPLE_RATE = 44100
EXPORT_CHANNELS = 2

# 每次送給 Whisper 的音訊長度（毫秒）。取 10 分鐘除了安全低於 API 的
# 25 分鐘長度上限，也能讓時間碼偏移累積的誤差維持在很小範圍。
CHUNK_DURATION_MS = 10 * 60 * 1000

# 剪輯軟體慣用 30 FPS，字幕時間碼以此為基準產生。
TARGET_FPS = 30


def chunk_duration_ms(max_bytes: int, export_bitrate_kbps: int) -> int:
    """計算單一上傳片段可用的長度（毫秒）。

    位元率越高，同樣長度的音訊檔案越大，因此可切分的長度越短。這裡用
    位元率換算位元組，再換算成毫秒，並保留 5% 安全餘裕避免超出上限。
    """
    if export_bitrate_kbps <= 0:
        raise ValueError("export_bitrate_kbps must be positive")

    # 每秒的位元組數。
    bytes_per_second = export_bitrate_kbps * 1000 / 8
    # 保留 5% 餘裕，避免編碼器額外加入的標頭資料讓檔案超標。
    usable_bytes = max_bytes * 0.95
    duration_ms = int(usable_bytes / bytes_per_second * 1000)
    return max(duration_ms, 1000)


def chunk_offsets(duration_ms: int, chunk_ms: int) -> list:
    """回傳每一段在原始音訊中的起點（秒，含毫秒精度）。

    原本使用 i // 1000 會把秒數向下取整，每切一段就累積一次誤差，
    字幕因此愈往後愈偏離聲音。這裡直接以毫秒位置換算成精確秒數。
    """
    if chunk_ms <= 0:
        raise ValueError("chunk_ms must be positive")

    offsets = []
    position = 0
    while position < duration_ms:
        offsets.append(position / 1000.0)
        position += chunk_ms
    return offsets


def split_audio_by_size(audio_path, max_bytes: int = MAX_FILE_SIZE):
    """把音訊切成數段，每段檔案不超過 API 上限。

    回傳 [(暫存檔路徑, 該段起始秒數), ...]，起始秒數含毫秒精度。
    """
    audio = AudioSegment.from_file(audio_path)
    duration_ms = len(audio)

    # 片段長度同時受限於位元率換算的上限與 Whisper 的長度上限，取較小者。
    by_size_ms = chunk_duration_ms(max_bytes, EXPORT_BITRATE_KBPS)
    chunk_ms = min(by_size_ms, CHUNK_DURATION_MS)

    chunks = []
    for start_ms in range(0, duration_ms, chunk_ms):
        chunk = audio[start_ms:start_ms + chunk_ms]
        temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".mp3")
        chunk.export(
            temp_file.name,
            format="mp3",
            bitrate=f"{EXPORT_BITRATE_KBPS}k",
            parameters=["-ar", str(EXPORT_SAMPLE_RATE), "-ac", str(EXPORT_CHANNELS)],
        )
        chunks.append((temp_file.name, start_ms / 1000.0))
    return chunks


def parse_srt(srt_text):
    """解析 SRT 區塊，回傳帶毫秒時間碼的字幕清單。"""
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


def _timestamp_to_milliseconds(timestamp: str) -> int:
    """把 HH:MM:SS,mmm 轉成整數毫秒。

    datetime.strptime 的 %f 會把 ,mmm 當成微秒（,500 會變成 500000 微秒），
    這是字幕漂移的隱藏來源，所以改用整數運算。
    """
    normalized = timestamp.replace(".", ",")
    try:
        hours_text, minutes_text, seconds_and_ms = normalized.split(":")
        seconds_text, ms_text = seconds_and_ms.split(",")
    except ValueError as error:
        raise ValueError(f"Invalid SRT timestamp: {timestamp}") from error

    hours = int(hours_text)
    minutes = int(minutes_text)
    seconds = int(seconds_text)
    milliseconds = int(ms_text.ljust(3, "0")[:3])

    if minutes >= 60 or seconds >= 60:
        raise ValueError(f"Invalid SRT timestamp: {timestamp}")

    return ((hours * 60 + minutes) * 60 + seconds) * 1000 + milliseconds


def _milliseconds_to_timestamp(total_ms: int) -> str:
    """把整數毫秒轉回 HH:MM:SS,mmm。"""
    total_ms = max(0, int(total_ms))
    hours, remainder = divmod(total_ms, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, milliseconds = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{milliseconds:03d}"


def shift_time_str(t_str: str, offset_seconds) -> str:
    """把時間碼加上偏移秒數，維持毫秒精度。"""
    total_ms = _timestamp_to_milliseconds(t_str)
    offset_ms = int(round(float(offset_seconds) * 1000))
    return _milliseconds_to_timestamp(total_ms + offset_ms)


def shift_and_renumber_srt(srt_text, offset_seconds, index_start):
    """套用偏移並重新編號，回傳 (SRT 文字, 下一個編號)。"""
    entries = parse_srt(srt_text)
    shifted = []
    for i, entry in enumerate(entries):
        shifted.append(f"{index_start + i}")
        shifted.append(
            f"{shift_time_str(entry['start'], offset_seconds)} --> "
            f"{shift_time_str(entry['end'], offset_seconds)}"
        )
        shifted.append(entry["text"])
        shifted.append("")  # 段落間空行
    return "\n".join(shifted), index_start + len(entries)


def transcribe(file_path):
    """呼叫 Whisper 取得 SRT 格式的轉錄結果。"""
    with open(file_path, "rb") as f:
        transcript = openai.audio.transcriptions.create(
            model="whisper-1",
            file=f,
            response_format="srt"
        )
    return transcript


def process_audio(file_path, max_bytes: int = MAX_FILE_SIZE):
    """轉錄音訊並合併各段結果，回傳完整的 SRT 文字。"""
    file_size = os.path.getsize(file_path)
    if file_size > max_bytes:
        print("檔案太大，開始自動切割處理...")
        chunks = split_audio_by_size(file_path, max_bytes)
    else:
        chunks = [(file_path, 0.0)]

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
