"""
Regenerate the unit-test evidence screenshot from a real local test run.

    python tools/render_evidence.py
"""

import os
import subprocess
import sys
import textwrap
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
EVIDENCE = ROOT / "evidence"
WRAP = 118
FONT_CANDIDATES = ["C:/Windows/Fonts/consola.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
                   "/System/Library/Fonts/Menlo.ttc"]
COLORS = {"bg": (24, 26, 33), "bar": (44, 47, 58), "text": (220, 223, 228), "title": (150, 156, 170),
          "ok": (126, 211, 132), "err": (240, 110, 110), "accent": (110, 180, 245)}


def font(size):
    for candidate in FONT_CANDIDATES:
        if os.path.exists(candidate):
            return ImageFont.truetype(candidate, size)
    return ImageFont.load_default()


def line_color(line):
    stripped = line.strip()
    if "FAIL" in line or "ERROR" in line:
        return COLORS["err"]
    if stripped.endswith(" ok") or stripped == "OK":
        return COLORS["ok"]
    if stripped.startswith("$"):
        return COLORS["accent"]
    return COLORS["text"]


def render(name, title, text):
    lines = []
    for raw in text.rstrip().splitlines():
        lines.extend(textwrap.wrap(raw, WRAP, subsequent_indent="    ") or [""])
    body_font, title_font = font(15), font(14)
    line_height, pad, bar, width = 21, 18, 34, 1240
    image = Image.new("RGB", (width, bar + pad * 2 + line_height * len(lines)), COLORS["bg"])
    draw = ImageDraw.Draw(image)
    draw.rectangle([0, 0, width, bar], fill=COLORS["bar"])
    for i, color in enumerate([(237, 106, 94), (245, 191, 79), (98, 197, 84)]):
        draw.ellipse([14 + i * 20, 11, 26 + i * 20, 23], fill=color)
    draw.text((86, 9), title, font=title_font, fill=COLORS["title"])
    for i, line in enumerate(lines):
        draw.text((pad, bar + pad + i * line_height), line, font=body_font, fill=line_color(line))
    image.save(EVIDENCE / name)
    print(f"wrote evidence/{name}")


def main():
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    run = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"],
                         cwd=ROOT, capture_output=True, text=True, env=env)
    render("02-unit-tests-passing.png", "local run - question generator unit tests",
           "$ python -m unittest discover -s tests -v\n" + run.stdout + run.stderr)


if __name__ == "__main__":
    main()
