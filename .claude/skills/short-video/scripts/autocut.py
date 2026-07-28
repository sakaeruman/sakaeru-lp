#!/usr/bin/env python3
"""無音区間を検出して詰める（ジャンプカット）。長尺ライブのテンポ改善用。

使い方:
    # 検出だけして区間を見る
    python3 autocut.py src.mp4

    # 無音を詰めて書き出す（0.35秒以上の無音をカット）
    python3 autocut.py src.mp4 --out tight.mp4

    # 指定区間だけ切り出してから詰める
    python3 autocut.py src.mp4 --start 252 --end 300 --out tight.mp4
"""

import argparse
import re
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path


def probe_duration(path: str) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", path],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    return float(out)


def detect_silence(path: str, noise_db: float, min_sil: float):
    """[(silence_start, silence_end), ...] を返す。"""
    r = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-i", path,
         "-af", f"silencedetect=noise={noise_db}dB:d={min_sil}",
         "-f", "null", "-"],
        capture_output=True, text=True,
    )
    starts = [float(m) for m in re.findall(r"silence_start:\s*(-?[\d.]+)", r.stderr)]
    ends = [float(m) for m in re.findall(r"silence_end:\s*(-?[\d.]+)", r.stderr)]
    dur = probe_duration(path)
    if len(ends) < len(starts):
        ends.append(dur)
    return [(max(0.0, s), min(dur, e)) for s, e in zip(starts, ends) if e > s]


def keep_segments(silences, duration, pad: float, min_keep: float):
    """無音の補集合＝残す区間。前後に pad 秒の余白を残して不自然さを防ぐ。"""
    segs, cur = [], 0.0
    for s, e in silences:
        end = min(s + pad, e)
        if end - cur >= min_keep:
            segs.append((cur, end))
        cur = max(cur, max(e - pad, s))
    if duration - cur >= min_keep:
        segs.append((cur, duration))
    return segs


def main() -> int:
    p = argparse.ArgumentParser(description="無音を詰めてテンポを上げる")
    p.add_argument("src")
    p.add_argument("--out", help="出力パス（省略時は検出結果の表示のみ）")
    p.add_argument("--start", type=float, default=None, help="切り出し開始秒")
    p.add_argument("--end", type=float, default=None, help="切り出し終了秒")
    p.add_argument("--noise", type=float, default=-32.0, help="無音とみなす音量 dB")
    p.add_argument("--min-silence", type=float, default=0.35, help="詰める無音の最短長")
    p.add_argument("--pad", type=float, default=0.08, help="発話前後に残す余白秒")
    p.add_argument("--min-keep", type=float, default=0.15, help="これ未満の残存区間は捨てる")
    args = p.parse_args()

    work = args.src
    tmpdir = tempfile.TemporaryDirectory()

    if args.start is not None or args.end is not None:
        work = str(Path(tmpdir.name) / "range.mp4")
        cmd = ["ffmpeg", "-y", "-v", "error"]
        if args.start is not None:
            cmd += ["-ss", str(args.start)]
        cmd += ["-i", args.src]
        if args.end is not None:
            cmd += ["-t", str(args.end - (args.start or 0.0))]
        cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
                "-c:a", "aac", work]
        subprocess.run(cmd, check=True)

    duration = probe_duration(work)
    silences = detect_silence(work, args.noise, args.min_silence)
    segs = keep_segments(silences, duration, args.pad, args.min_keep)
    kept = sum(e - s for s, e in segs)

    print(f"元尺 {duration:.2f}s / 無音 {len(silences)}箇所 / 残す区間 {len(segs)}本")
    print(f"詰め後 {kept:.2f}s（{duration - kept:.2f}s 削減, {kept / duration * 100:.0f}%）")
    for s, e in segs[:40]:
        print(f"  keep {s:8.2f} → {e:8.2f}  ({e - s:5.2f}s)")
    if len(segs) > 40:
        print(f"  … 他 {len(segs) - 40} 本")

    if not args.out:
        return 0
    if not segs:
        print("ERROR 残す区間がない。--noise を下げる", file=sys.stderr)
        return 1

    # filter_complex で trim/concat（フレーム単位で正確にカットする）
    parts, labels = [], []
    for i, (s, e) in enumerate(segs):
        parts.append(
            f"[0:v]trim=start={s:.3f}:end={e:.3f},setpts=PTS-STARTPTS[v{i}];"
            f"[0:a]atrim=start={s:.3f}:end={e:.3f},asetpts=PTS-STARTPTS[a{i}]"
        )
        labels.append(f"[v{i}][a{i}]")
    graph = ";".join(parts) + ";" + "".join(labels) + f"concat=n={len(segs)}:v=1:a=1[v][a]"

    script = Path(tmpdir.name) / "graph.txt"
    script.write_text(graph, encoding="utf-8")

    cmd = ["ffmpeg", "-y", "-v", "error", "-i", work,
           "-filter_complex_script", str(script), "-map", "[v]", "-map", "[a]",
           "-c:v", "libx264", "-preset", "slow", "-crf", "20", "-pix_fmt", "yuv420p",
           "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", args.out]
    print("$ " + " ".join(shlex.quote(c) for c in cmd))
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print(r.stderr[-2000:], file=sys.stderr)
        return r.returncode
    print(f"OK 書き出し: {args.out} ({probe_duration(args.out):.2f}s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
