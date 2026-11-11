#!/usr/bin/env python3
"""Genere une planche de N cases en un seul appel, puis la decoupe.

Exemple : 2x2 = 4 instants successifs d'une meme scene, 3x3 = 9 cases de combat.
Le prompt doit demander des cases egales, des gouttieres noires regulieres,
aucun texte et aucun bord exterieur.

Usage :
  python grid.py --prompt-file p.txt --cols 2 --rows 2 --size 2048x1152 \
      --name bonus_vitesse --out-dir sorties\\bonus
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
import imagegen


def crop_grid(src: Path, cols: int, rows: int, out_dir: Path, name: str, inset: float = 0.012):
    im = Image.open(src).convert("RGB")
    cw, ch = im.width / cols, im.height / rows
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for r in range(rows):
        for c in range(cols):
            left = int(c * cw + cw * inset)
            top = int(r * ch + ch * inset)
            right = int((c + 1) * cw - cw * inset)
            bottom = int((r + 1) * ch - ch * inset)
            panel = im.crop((left, top, right, bottom))
            p = out_dir / f"{name}_r{r + 1}c{c + 1}.png"
            panel.save(p)
            paths.append(str(p))
    return paths, im.size


def contact_sheet(panels, out: Path, cols: int, rows: int):
    thumbs = [Image.open(p).convert("RGB") for p in panels]
    tw = 480
    th = int(tw * thumbs[0].height / thumbs[0].width)
    sheet = Image.new("RGB", (cols * tw, rows * th), "white")
    for i, im in enumerate(thumbs):
        im = im.resize((tw, th))
        sheet.paste(im, ((i % cols) * tw, (i // cols) * th))
    sheet.save(out, quality=88)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--prompt")
    ap.add_argument("--prompt-file")
    ap.add_argument("--ref", action="append", default=[])
    ap.add_argument("--cols", type=int, default=2)
    ap.add_argument("--rows", type=int, default=2)
    ap.add_argument("--size", default="2048x1152")
    ap.add_argument("--quality", default="high")
    ap.add_argument("--name", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--inset", type=float, default=0.012)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    prompt = args.prompt or Path(args.prompt_file).read_text(encoding="utf-8")
    out_dir = Path(args.out_dir)
    raw = out_dir / f"{args.name}_planche.png"
    result = imagegen.generate(prompt, args.ref, out=raw, size=args.size,
                               quality=args.quality, timeout=420,
                               dry_run=args.dry_run,
                               meta={"grid": f"{args.cols}x{args.rows}", "name": args.name})
    if args.dry_run:
        print(json.dumps(result, ensure_ascii=False, indent=1))
        return 0
    panels, size = crop_grid(raw, args.cols, args.rows, out_dir, args.name, args.inset)
    sheet = contact_sheet(panels, out_dir / f"{args.name}_contact.jpg", args.cols, args.rows)
    print(json.dumps({"planche": str(raw), "taille": size, "cases": panels,
                      "contact": str(sheet), "duree_s": result.get("duration_s"),
                      "quota": result.get("quota")}, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
