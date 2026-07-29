#!/usr/bin/env python3
"""動画・音声をタイムスタンプ付きで文字起こしする。ネタ探しの起点。

使い方:
    python3 transcribe.py live.mp4                    # live.txt と live.json を出力
    python3 transcribe.py live.mp4 --model medium     # 速度優先
    python3 transcribe.py live.mp4 --out-dir ./work

事前準備:
    pip3 install faster-whisper

モデルは初回実行時に自動ダウンロードされる（large-v3 で約3GB）。
11分の素材で、Apple Silicon の CPU なら large-v3 で数分程度。
急ぐときは --model medium（精度は落ちるが固有名詞以外は十分実用になる）。
"""

import argparse
import json
import sys
from pathlib import Path


def fmt_ts(sec: float) -> str:
    m, s = divmod(int(sec), 60)
    h, m = divmod(m, 60)
    return f"{h:d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def main() -> int:
    p = argparse.ArgumentParser(description="タイムスタンプ付き文字起こし")
    p.add_argument("src", help="動画または音声ファイル")
    p.add_argument("--model", default="large-v3",
                   help="Whisper モデル (tiny/base/small/medium/large-v3)")
    p.add_argument("--language", default="ja", help="言語コード")
    p.add_argument("--out-dir", default=None, help="出力先（既定: 入力と同じ場所）")
    p.add_argument("--device", default="auto", help="cpu / cuda / auto")
    p.add_argument("--compute-type", default="int8",
                   help="int8 / int8_float16 / float16 / float32")
    args = p.parse_args()

    try:
        from faster_whisper import WhisperModel
    except ImportError:
        print("ERROR faster-whisper が未インストール。`pip3 install faster-whisper`",
              file=sys.stderr)
        return 1

    src = Path(args.src)
    if not src.exists():
        print(f"ERROR ファイルがない: {src}", file=sys.stderr)
        return 1

    out_dir = Path(args.out_dir) if args.out_dir else src.parent
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"モデル読み込み中: {args.model}（初回はダウンロードに数分かかる）",
          file=sys.stderr)
    model = WhisperModel(args.model, device=args.device,
                         compute_type=args.compute_type)

    print(f"文字起こし中: {src.name}", file=sys.stderr)
    segments, info = model.transcribe(
        str(src),
        language=args.language,
        vad_filter=True,                       # 無音を飛ばして誤認識を減らす
        vad_parameters={"min_silence_duration_ms": 350},
    )

    rows = []
    for seg in segments:                        # ジェネレータ。ここで実処理が走る
        text = seg.text.strip()
        if not text:
            continue
        rows.append({"start": round(seg.start, 2),
                     "end": round(seg.end, 2),
                     "text": text})
        print(f"  [{fmt_ts(seg.start)}] {text}", file=sys.stderr)

    if not rows:
        print("ERROR 発話を検出できなかった。音声トラックを確認する", file=sys.stderr)
        return 1

    json_path = out_dir / f"{src.stem}.json"
    txt_path = out_dir / f"{src.stem}.txt"
    json_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2),
                         encoding="utf-8")
    txt_path.write_text(
        "\n".join(f"[{fmt_ts(r['start'])}–{fmt_ts(r['end'])}] {r['text']}"
                  for r in rows),
        encoding="utf-8",
    )

    total = rows[-1]["end"]
    print(f"\nOK {len(rows)} 発話 / 全長 {fmt_ts(total)} "
          f"(検出言語: {info.language})", file=sys.stderr)
    print(f"OK {txt_path}", file=sys.stderr)
    print(f"OK {json_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
