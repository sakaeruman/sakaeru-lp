#!/usr/bin/env python3
"""縦型ショート動画用のテロップを ASS 字幕として生成し、セーフゾーンを検証して焼き込む。

使い方:
    # 検証のみ（.ass を出力）
    python3 burn_telop.py telop.json --ass out.ass

    # 検証して動画に焼き込む
    python3 burn_telop.py telop.json --video base.mp4 --out final.mp4

telop.json:
    [
      {"start": 0.0, "end": 2.6, "text": "家の前の電線が\\n爆発しました",
       "style": "hook", "highlight": "爆発"},
      {"start": 2.6, "end": 5.0, "text": "移住3年目の\\nリアルな話です"}
    ]

style は "hook"（冒頭フック / 大きめ・キーワード黄色）と "main"（通常, 既定）。
highlight を指定すると、その文字列だけ黄色に変わる。
"""

import argparse
import json
import subprocess
import sys
import unicodedata
from pathlib import Path

# --- 9:16 (1080x1920) のセーフゾーン定義 -------------------------------------
PLAY_W, PLAY_H = 1080, 1920
SAFE_X = (80, 940)      # 右端はいいね/コメント/保存ボタンを避ける
SAFE_Y = (250, 1500)    # 上はアカウント名、下はキャプション/音源UI
CENTER_X = (SAFE_X[0] + SAFE_X[1]) // 2   # = 510

STYLES = {
    #            font  行間送り  既定のY(テキスト中心)  最大行数
    "hook": {"size": 86, "line_h": 108, "y": 1250, "max_lines": 2},
    "main": {"size": 62, "line_h": 82, "y": 1300, "max_lines": 2},
}

MAX_CHARS_PER_LINE = 13

YELLOW = r"{\c&H00D7FF&}"   # ASS は BGR 順。#FFD700 相当
WHITE = r"{\c&HFFFFFF&}"


def char_width(ch: str) -> float:
    """文字の表示幅を全角=1.0 / 半角=0.5 で見積もる。"""
    return 1.0 if unicodedata.east_asian_width(ch) in ("F", "W", "A") else 0.5


def line_px(line: str, font_size: int) -> float:
    return sum(char_width(c) for c in line) * font_size


def ass_time(sec: float) -> str:
    if sec < 0:
        raise ValueError(f"負の時刻: {sec}")
    cs = int(round(sec * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h:d}:{m:02d}:{s:02d}.{cs:02d}"


def validate(cues):
    """セーフゾーン・文字数・尺・重なりを検証し、(errors, warnings) を返す。"""
    errors, warnings = [], []
    prev_end = None

    for i, cue in enumerate(cues, 1):
        tag = f"[{i}] {cue['text'].splitlines()[0][:12]}…"
        style_name = cue.get("style", "main")
        if style_name not in STYLES:
            errors.append(f"{tag}: 未知の style '{style_name}'")
            continue
        st = STYLES[style_name]
        size = cue.get("size", st["size"])
        y_center = cue.get("y", st["y"])

        start, end = float(cue["start"]), float(cue["end"])
        dur = end - start
        if dur <= 0:
            errors.append(f"{tag}: end <= start")
        elif dur < 1.2:
            warnings.append(f"{tag}: 表示 {dur:.2f}s は短く読めない（推奨 1.2〜2.5s）")
        elif dur > 2.5:
            warnings.append(f"{tag}: 表示 {dur:.2f}s は長い。分割を検討（推奨 1.2〜2.5s）")

        if prev_end is not None and start < prev_end - 1e-6:
            errors.append(f"{tag}: 直前のテロップと時間が重なっている")
        prev_end = max(prev_end or 0.0, end)

        lines = cue["text"].split("\n")
        if len(lines) > st["max_lines"]:
            errors.append(f"{tag}: {len(lines)}行は多すぎる（最大 {st['max_lines']}行）")

        # 横方向のセーフゾーン
        for line in lines:
            if len(line) > MAX_CHARS_PER_LINE:
                warnings.append(
                    f"{tag}: 1行 {len(line)}文字（推奨 {MAX_CHARS_PER_LINE}文字以内）"
                )
            w = line_px(line, size)
            left, right = CENTER_X - w / 2, CENTER_X + w / 2
            if left < SAFE_X[0] or right > SAFE_X[1]:
                errors.append(
                    f"{tag}: 行「{line}」が幅 {w:.0f}px で安全域外 "
                    f"(x {left:.0f}〜{right:.0f} / 許容 {SAFE_X[0]}〜{SAFE_X[1]})。"
                    f"文字を減らすか size を下げる"
                )

        # 縦方向のセーフゾーン
        block_h = st["line_h"] * len(lines)
        top, bottom = y_center - block_h / 2, y_center + block_h / 2
        if top < SAFE_Y[0] or bottom > SAFE_Y[1]:
            errors.append(
                f"{tag}: y {top:.0f}〜{bottom:.0f} が安全域外 "
                f"(許容 {SAFE_Y[0]}〜{SAFE_Y[1]})。y を調整する"
            )

        if cue.get("highlight") and cue["highlight"] not in cue["text"]:
            warnings.append(f"{tag}: highlight「{cue['highlight']}」が text 中にない")

    if cues:
        first = min(float(c["start"]) for c in cues)
        if first > 0.05:
            errors.append(
                f"冒頭テロップが {first:.2f}s から。ショートは 0.0s からテロップを出す"
            )
    return errors, warnings


def render_text(cue) -> str:
    text = cue["text"]
    hl = cue.get("highlight")
    if hl and hl in text:
        text = text.replace(hl, f"{YELLOW}{hl}{WHITE}", 1)
    return text.replace("\n", r"\N")


def build_ass(cues, font: str) -> str:
    head = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {PLAY_W}
PlayResY: {PLAY_H}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: hook,{font},{STYLES['hook']['size']},&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,-1,0,0,0,100,100,2,0,3,8,0,5,0,0,0,1
Style: main,{font},{STYLES['main']['size']},&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,-1,0,0,0,100,100,1,0,3,6,0,5,0,0,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = []
    for cue in cues:
        style_name = cue.get("style", "main")
        st = STYLES[style_name]
        y = cue.get("y", st["y"])
        size = cue.get("size", st["size"])
        override = rf"{{\pos({CENTER_X},{y})\fs{size}}}"
        lines.append(
            f"Dialogue: 0,{ass_time(float(cue['start']))},{ass_time(float(cue['end']))},"
            f"{style_name},,0,0,0,,{override}{render_text(cue)}"
        )
    return head + "\n".join(lines) + "\n"


def pick_font(preferred: str | None) -> str:
    if preferred:
        return preferred
    try:
        out = subprocess.run(
            ["fc-list", ":lang=ja", "family"],
            capture_output=True, text=True, check=True,
        ).stdout
    except (FileNotFoundError, subprocess.CalledProcessError):
        return "sans-serif"
    families = [ln.split(",")[0].strip() for ln in out.splitlines() if ln.strip()]
    for want in ("Noto Sans JP", "Hiragino Sans", "Yu Gothic", "IPAGothic", "IPAPGothic"):
        for fam in families:
            if want.lower() in fam.lower():
                return fam
    return families[0] if families else "sans-serif"


def main() -> int:
    p = argparse.ArgumentParser(description="縦型ショート用テロップの検証・焼き込み")
    p.add_argument("telop_json", help="テロップ定義 JSON")
    p.add_argument("--video", help="焼き込み対象の 1080x1920 動画")
    p.add_argument("--out", help="出力動画パス")
    p.add_argument("--ass", help="生成した .ass の保存先")
    p.add_argument("--font", help="使用フォント名（既定: 日本語フォントを自動選択）")
    p.add_argument("--force", action="store_true", help="エラーがあっても続行する")
    args = p.parse_args()

    cues = json.loads(Path(args.telop_json).read_text(encoding="utf-8"))
    cues.sort(key=lambda c: float(c["start"]))

    errors, warnings = validate(cues)
    for w in warnings:
        print(f"WARN  {w}", file=sys.stderr)
    for e in errors:
        print(f"ERROR {e}", file=sys.stderr)

    if errors and not args.force:
        print(
            f"\n{len(errors)} 件のセーフゾーン違反。修正するか --force を付ける。",
            file=sys.stderr,
        )
        return 1

    font = pick_font(args.font)
    ass = build_ass(cues, font)
    ass_path = Path(args.ass) if args.ass else Path(args.telop_json).with_suffix(".ass")
    ass_path.write_text(ass, encoding="utf-8")
    print(f"OK    フォント: {font} / 字幕: {ass_path} ({len(cues)} 件)")

    if args.video:
        if not args.out:
            print("ERROR --video を使うなら --out も必要", file=sys.stderr)
            return 1
        escaped = str(ass_path).replace("\\", "/").replace(":", r"\:").replace("'", r"\'")
        cmd = [
            "ffmpeg", "-y", "-i", args.video,
            "-vf", f"subtitles='{escaped}':fontsdir=/usr/share/fonts",
            "-c:v", "libx264", "-preset", "slow", "-crf", "20",
            "-pix_fmt", "yuv420p", "-c:a", "copy",
            "-movflags", "+faststart", args.out,
        ]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            print(r.stderr[-2000:], file=sys.stderr)
            return r.returncode
        print(f"OK    書き出し: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
