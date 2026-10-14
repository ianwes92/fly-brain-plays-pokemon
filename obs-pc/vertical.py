#!/usr/bin/env python3
"""Compose a 1080x1920 Story from the 1920x1080 stream layout.

The source is a wide three-panel dashboard, so a 9:16 crop of it slices across
panels (which is exactly what YouTube's auto-crop does). Instead we cut the two
panels that matter out of the frame and stack them, then add a caption bar.

Usage:  python vertical.py <input.mp4|png> <output.mp4|png>
"""
import subprocess, sys, pathlib

FFMPEG = pathlib.Path(__file__).resolve().parent / 'tools' / 'ffmpeg' / 'ffmpeg.exe'
FONT = 'C\\:/Windows/Fonts/arialbd.ttf'

# Source regions, measured off a real 1920x1080 program frame.
GAME = (1155, 775, 0, 0)       # w, h, x, y - Pokemon view + its overlay line
BRAIN = (760, 775, 1160, 0)    # the fly brain visualisation

# Vertical layout: game on top, brain beneath, caption bar at the foot.
GAME_H = 725                   # 1080-wide game panel keeps its aspect
BRAIN_H = 795
CAP_Y = GAME_H + BRAIN_H       # 1520 -> 400px of caption

TITLE = 'a fly brain is playing Pokemon'
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from config import CFG
SUB = 'chat controls it  -  twitch.tv/' + CFG['twitch_channel']


def build(src, dst):
    gw, gh, gx, gy = GAME
    bw, bh, bx, by = BRAIN
    brain_w = round(bw * (BRAIN_H / bh) / 2) * 2     # keep aspect, even width
    bx_off = (1080 - brain_w) // 2

    fc = (
        f"color=c=0x0d0d12:s=1080x1920[bg];"
        f"[0:v]crop={gw}:{gh}:{gx}:{gy},scale=1080:{GAME_H}[game];"
        f"[0:v]crop={bw}:{bh}:{bx}:{by},scale={brain_w}:{BRAIN_H}[brain];"
        f"[bg][game]overlay=0:0[a];"
        f"[a][brain]overlay={bx_off}:{GAME_H}[b];"
        f"[b]drawtext=fontfile='{FONT}':text='{TITLE}':"
        f"fontcolor=white:fontsize=58:x=(w-tw)/2:y={CAP_Y + 110}[c];"
        f"[c]drawtext=fontfile='{FONT}':text='{SUB}':"
        f"fontcolor=0x9146FF:fontsize=46:x=(w-tw)/2:y={CAP_Y + 200}[out]"
    )
    cmd = [str(FFMPEG), '-y', '-i', str(src), '-filter_complex', fc, '-map', '[out]']
    if str(dst).endswith('.mp4'):
        # Story-safe: H.264 high, yuv420p, keep audio if the source has any.
        cmd += ['-map', '0:a?', '-c:v', 'libx264', '-preset', 'medium', '-crf', '20',
                '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-b:a', '128k', '-movflags', '+faststart']
    else:
        # image2 refuses a single fixed filename without these
        cmd += ['-frames:v', '1', '-update', '1']
    cmd += [str(dst)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode:
        print(r.stderr[-1500:])
        raise SystemExit('ffmpeg failed')
    print('wrote', dst, '%.1f KB' % (pathlib.Path(dst).stat().st_size / 1024))


if __name__ == '__main__':
    build(sys.argv[1], sys.argv[2])
