# audio2srt

把音訊檔轉成 SRT 字幕，人工校閱後，再轉成 Final Cut Pro 可以直接匯入的 fcpxml 字幕。

## 工作流程

```
音訊檔 (.mp3)
   │  ① python audio2srt.py xxx.mp3          ← 呼叫 OpenAI Whisper
   ▼
字幕檔 (.srt)
   │  ② 人工校閱、修正 SRT                    ← 必做，不可省略
   ▼
校閱後的 .srt
   │  ③ python srt_to_fcpxml.py xxx.srt       ← 不連網、不需 API Key
   ▼
Final Cut Pro 字幕專案 (.fcpxml)
```

兩支程式刻意分開：SRT 一定要人工校閱過，才轉成 fcpxml。

## 系統需求

| 項目 | 說明 |
|---|---|
| macOS | 匯入 fcpxml 需要 Final Cut Pro 11.0 以上（fcpxml 1.13） |
| Python | 3.9 以上（建議 Homebrew 版：`brew install python`） |
| ffmpeg | 音檔超過 24MB 時要用它切段：`brew install ffmpeg` |
| OpenAI API Key | 只有 `audio2srt.py` 需要 |

## 一次性設定：virtualenv

virtualenv（虛擬環境）讓這個專案的套件裝在專案自己的 `.venv` 資料夾，不會弄亂系統的 Python。`.venv` 已經列在 `.gitignore`，不會被 commit。每台電腦都要各自做一次。

### 1. 取得程式

```bash
git clone https://github.com/TonyChengTW/audio2srt.git
cd audio2srt
```

已經 clone 過的話，改成 `git pull origin main`。

### 2. 建立虛擬環境

```bash
python3 -m venv .venv
```

這會在專案裡建立 `.venv` 資料夾。

### 3. 啟用虛擬環境

```bash
source .venv/bin/activate
```

啟用後提示字元前面會出現 `(.venv)`。在這個狀態下，`python` 和 `pip` 都會指向 `.venv` 裡的版本。可以用下面這行確認：

```bash
which python
```

應該顯示 `.../audio2srt/.venv/bin/python`。

### 4. 安裝套件

```bash
pip install -r requirements.txt
```

Python 3.13 以上會自動多裝一個 `audioop-lts`（pydub 需要），不用另外處理。

### 5. 確認安裝成功

```bash
python -m unittest discover -s tests
```

最後出現 `OK` 就代表設定完成。

### 之後每次使用

開新的終端機視窗時，虛擬環境不會自動啟用，要先：

```bash
cd ~/private/audio2srt
source .venv/bin/activate
```

用完要離開虛擬環境：

```bash
deactivate
```

不想每次 activate 的話，也可以直接指定虛擬環境裡的 Python：

```bash
.venv/bin/python audio2srt.py xxx.mp3
```

### 重建虛擬環境

升級 Python、搬移專案資料夾，或套件壞掉時，刪掉重建最快：

```bash
rm -rf .venv
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## ① 音訊轉 SRT：audio2srt.py

```bash
python audio2srt.py xxx.mp3
```

- 執行時會出現「請輸入 OpenAI API Key (隱藏輸入) :」，貼上 Key 後按 Enter，畫面不會顯示 Key。
- Key 只在這次執行有效，程式結束就消失，不需要 `export` 或 `unset`。
- 如果已經設定環境變數 `OPENAI_API_KEY`，就直接使用，不會詢問。
- 輸出在音檔旁邊、同檔名的 `.srt`，例如 `xxx.mp3` → `xxx.srt`。
- 超過 24MB 的音檔會自動切段轉錄，時間碼會自動接續。
- 不加參數執行會顯示說明。

## ② 人工校閱 SRT

用任何文字編輯器打開 `.srt`，修正錯字、斷句與時間碼。存檔時請用 UTF-8 編碼。

## ③ SRT 轉 fcpxml：srt_to_fcpxml.py

```bash
python srt_to_fcpxml.py xxx.srt --fps 30
```

輸出在 SRT 旁邊、同檔名的 `.fcpxml`。每一句字幕都會變成一個可以個別編輯的 Basic Title。

| 參數 | 預設值 | 說明 |
|---|---|---|
| `--fps` | `30` | 幀速率，必須跟 FCP 專案一致。可選 `23.976` `24` `25` `29.97` `30` `50` `59.94` `60` |
| `--font` | `PingFang SC` | 字體家族名稱 |
| `--font-face` | `Semibold` | 字重，例如 `Regular` `Medium` `Semibold`（Semibold 就是「中粗體」） |
| `--font-size` | `62` | 字級 |
| `--x` | `0` | 位置 X（像素，畫面中心為 0） |
| `--y` | `-415` | 位置 Y（像素，負值往下） |
| `--z` | `0` | 位置 Z（像素） |
| `-o` | 同檔名 `.fcpxml` | 指定輸出路徑 |

字幕固定是白字、置中、帶陰影。

範例：

```bash
python srt_to_fcpxml.py interview.srt --fps 29.97
python srt_to_fcpxml.py interview.srt --fps 25 --font-size 70 --y -380
```

不加參數執行會顯示完整說明。

### 匯入 Final Cut Pro

FCP 選單「檔案 → 輸入 → XML…」，選擇產生的 `.fcpxml`。匯入後會出現一個和 SRT 同名的事件和專案，把裡面的字幕複製到你的影片專案即可。

## 常見問題

**`zsh: command not found: python`**
虛擬環境還沒啟用。先執行 `source .venv/bin/activate`，或改用 `python3`。

**`ModuleNotFoundError: No module named 'openai'`（或 `pydub`）**
虛擬環境沒啟用，或還沒裝套件。啟用後再執行一次 `pip install -r requirements.txt`。

**`ModuleNotFoundError: No module named 'audioop'`**
用 Python 3.13 以上，但套件是用舊版 `requirements.txt` 裝的。拉最新的程式後，重新執行 `pip install -r requirements.txt`。

**`FileNotFoundError: ... 'ffmpeg'` 或切段時出錯**
沒有安裝 ffmpeg：`brew install ffmpeg`。

**匯入 FCP 後看不到字幕**
確認已經拉到最新的程式（舊版的位置寫法會把字幕推到畫面外），重新產生 fcpxml 後再匯入。

## 執行測試

```bash
python -m unittest discover -s tests
```

測試不會呼叫 OpenAI API，也不需要 API Key。
