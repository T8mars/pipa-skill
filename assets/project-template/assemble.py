# -*- coding: utf-8 -*-
"""按 总体使用规则.md 第 15 条组装最终成片。

只读 完成分段 里的 mp4，按 补齐生成用/原始帧数.txt 裁到原始帧数后按编号拼接，
整条换成 41888056471-1-192.mp3，成品只写到 完成分段/最终组装。
少一段就停，不跳过、不拿别的段补。
"""
import os
import subprocess
import sys
import shutil
import hashlib
import json
import argparse
from collections import Counter
from fractions import Fraction
from project_config import frame_table, require_current_reviews

ROOT = os.path.dirname(os.path.abspath(__file__))
CLIPS = os.path.join(ROOT, "完成分段")
TABLE = os.path.join(ROOT, "补齐生成用", "原始帧数.txt")
AUDIO = os.path.join(ROOT, "41888056471-1-192.mp3")
OUTDIR = os.path.join(CLIPS, "最终组装")
LOCAL_FFMPEG = os.path.join(ROOT, "制作分析", "工具依赖", "imageio_ffmpeg", "binaries", "ffmpeg-win-x86_64-v7.1.exe")
FFMPEG = LOCAL_FFMPEG if os.path.isfile(LOCAL_FFMPEG) else shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")


def probe(path, *args):
    command = [FFPROBE, "-v", "error", "-cpuflags", "0", "-threads", "1", *args, path]
    for attempt in range(3):
        r = subprocess.run(command, capture_output=True, text=True)
        if r.returncode == 0:
            return r.stdout.strip()
        if r.returncode in (3221225477, -1073741819) and attempt < 2:
            print("Local FFprobe process crashed; retrying.", flush=True)
            continue
        sys.stderr.write(r.stderr[-6000:])
        raise subprocess.CalledProcessError(r.returncode, command)


def parse_table():
    return frame_table(TABLE)


def encode(args):
    # The local Windows build can occasionally crash with an access violation.
    # Retry that process failure once; do not hide media or command errors.
    args = [args[0], "-cpuflags", "0", *args[1:]]
    for attempt in range(2):
        result = subprocess.run(args, capture_output=True)
        if result.returncode == 0:
            return
        if attempt == 0 and result.returncode in (3221225477, -1073741819):
            print("Local FFmpeg process crashed; retrying once.", flush=True)
            continue
        sys.stderr.write(result.stderr.decode("utf-8", "replace")[-6000:])
        raise subprocess.CalledProcessError(result.returncode, args)


def source_signature(src,keep,fps,size):
    digest=hashlib.sha256()
    with open(src,'rb') as f:
        for chunk in iter(lambda:f.read(1024*1024),b''):digest.update(chunk)
    return {'source_sha256':digest.hexdigest(),'keep':keep,'fps':str(Fraction(fps)),
            'size':list(size),'encoder':os.path.abspath(FFMPEG),'settings':'libx264/crf10/yuv420p/trim-scale-v2'}


def main():
    global AUDIO, OUTPUT_NAME
    if '--backend-av' in sys.argv:
        from assemble_av import main as independent_main
        independent_main([a for a in sys.argv[1:] if a != '--backend-av'])
        return
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--resume', action='store_true')
    ap.add_argument('--output', default='琵琶曲_完整版.mp4')
    ap.add_argument('--audio', default=AUDIO)
    args = ap.parse_args()
    if os.path.basename(args.output) != args.output or not args.output.lower().endswith('.mp4'):
        raise SystemExit('--output 只接受最终组装中的 MP4 文件名')
    AUDIO = os.path.abspath(args.audio); OUTPUT_NAME = args.output
    if os.path.exists(os.path.join(OUTDIR, OUTPUT_NAME)):
        raise SystemExit('已有同名成片须先归档，不能覆盖。')
    if not FFMPEG or not FFPROBE:
        raise SystemExit("缺少 FFmpeg 或 FFprobe。")
    rows = parse_table()
    require_current_reviews(ROOT, rows)
    expected = {name for name, _ in rows}
    actual = {name for name in os.listdir(CLIPS) if name.lower().endswith(".mp4")}
    if len(expected) != len(rows) or actual != expected:
        raise SystemExit(f"分段文件与帧数表不一致，缺少 {sorted(expected-actual)}，多出 {sorted(actual-expected)}。")
    missing = []
    for name, _ in rows:
        p = os.path.join(CLIPS, name)
        if not os.path.isfile(p):
            missing.append(name)
    if missing:
        print("停：完成分段里缺这些文件，不组装：")
        for n in missing:
            print(" ", n)
        sys.exit(1)

    os.makedirs(OUTDIR, exist_ok=True)
    work = os.path.join(OUTDIR, "_work")
    os.makedirs(work, exist_ok=True)

    sizes = []
    trimmed = []
    rates = []
    source_info = []
    for name, keep in rows:
        src = os.path.join(CLIPS, name)
        frames = int(probe(src, "-select_streams", "v:0",
                           "-count_frames", "-show_entries",
                           "stream=nb_read_frames", "-of", "csv=p=0"))
        if frames < keep:
            print(f"停：{name} 实际 {frames} 帧，少于原始帧数 {keep}，不补帧。")
            sys.exit(1)
        fps = probe(src, "-select_streams", "v:0",
                    "-show_entries", "stream=r_frame_rate", "-of", "csv=p=0")
        w,h=probe(src,'-select_streams','v:0','-show_entries','stream=width,height','-of','csv=p=0').split(',')
        sizes.append((int(w),int(h)));rates.append(Fraction(fps))
        source_info.append((src,fps,(int(w),int(h))))
    (tw,th),n=Counter(sizes).most_common(1)[0]
    if len(set(rates))!=1:
        raise SystemExit('分段帧率不一致；不能直接流复制拼接。')
    print(f'majority size {tw}x{th} ({n}/{len(sizes)})',flush=True)
    for (name,keep),(src,fps,source_size) in zip(rows,source_info):
        dest = os.path.join(work, name)
        signature=source_signature(src,keep,fps,(tw,th))
        cache_meta=dest+'.cache.json'
        cached = False
        if "--resume" in sys.argv and os.path.isfile(dest) and os.path.isfile(cache_meta):
            try:
                with open(cache_meta,encoding='utf-8') as meta:matches=json.load(meta)==signature
                cached = (matches and int(probe(dest, "-select_streams", "v:0", "-count_frames",
                                    "-show_entries", "stream=nb_read_frames", "-of", "csv=p=0")) == keep
                          and Fraction(probe(dest, "-select_streams", "v:0", "-show_entries",
                                    "stream=r_frame_rate", "-of", "csv=p=0")) == Fraction(fps))
            except (OSError, ValueError, subprocess.CalledProcessError):
                cached = False
        if not cached:
            filters=f'trim=end_frame={keep},setpts=PTS-STARTPTS'
            if source_size!=(tw,th):filters+=f',scale={tw}:{th}:flags=lanczos'
            encode([
                FFMPEG, "-y", "-threads", "1", "-i", src,
                "-filter_threads", "1", "-vf", filters,
                "-an", "-c:v", "libx264", "-crf", "10", "-pix_fmt", "yuv420p",
                "-r", fps, "-threads", "1", dest
            ])
            if int(probe(dest,'-select_streams','v:0','-count_frames','-show_entries','stream=nb_read_frames','-of','csv=p=0'))!=keep:
                raise SystemExit('裁剪后实际帧数不符：'+name)
            with open(cache_meta,'w',encoding='utf-8') as meta:json.dump(signature,meta,indent=2)
        w, h = probe(dest, "-select_streams", "v:0",
                     "-show_entries", "stream=width,height",
                     "-of", "csv=p=0").split(",")
        if (int(w),int(h))!=(tw,th):raise SystemExit('裁剪输出尺寸不符：'+name)
        trimmed.append(dest)
        print(f"{'resume' if cached else 'trim'} {name} -> {keep} frames {w}x{h}", flush=True)

    scaled = trimmed

    concat = os.path.join(work, "concat.txt")
    with open(concat, "w", encoding="utf-8") as f:
        for p, (_, keep), rate in zip(scaled, rows, rates):
            f.write("file '" + p.replace("\\", "/") + "'\n")
            f.write(f"duration {float(keep/rate):.12f}\n")
    silent = os.path.join(work, "silent.mp4")
    encode([
        FFMPEG, "-y", "-threads", "1", "-f", "concat", "-safe", "0", "-auto_convert", "0", "-i", concat,
        # Each trimmed input already meets H.264/CRF10. Preserve its encoded
        # quality while joining; final validation fully decodes every boundary.
        "-c:v", "copy", "-an", silent
    ])

    mux_audio(silent)


def mux_audio(silent):
    dur = float(probe(silent, "-show_entries", "format=duration", "-of", "csv=p=0"))
    out = os.path.join(OUTDIR, OUTPUT_NAME)
    encode([
        FFMPEG, "-y", "-i", silent, "-i", AUDIO,
        "-map", "0:v:0", "-map", "1:a:0",
        "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
        "-t", f"{dur:.6f}",
        out
    ])
    print(f"wrote {out} ({dur:.3f}s)", flush=True)


if __name__ == "__main__":
    main()
