"""Seed the gallery with baked ascii-magic pieces (kind="piece").

Reads static/anim/manifest.json, renders each piece's first frame to a thumbnail
GIF and an OG image, copies the baked .json.gz into data/gallery/ (each work owns
its own file so deleting a work cannot destroy the shared bake), and inserts one
works row per piece.

Idempotent: pieces already in the database are skipped unless --force is given.

Usage: python scripts/seed_pieces.py [--force] [--limit N]
"""
import argparse
import datetime
import json
import os
import shutil
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from termify import gallery as G          # noqa: E402
from termify import pieces as P           # noqa: E402
from termify.output import video as V     # noqa: E402

MANIFEST = os.path.join(P.ANIM_DIR, "manifest.json")
DATA_DIR = os.path.join(ROOT, "data")
DB_PATH = os.path.join(DATA_DIR, "termify.db")
AUTHOR = "ASCII Magic"
# 634 个作品里 ocean-sunset / starry-ridge 是 Unsplash 照片衍生，不在 MIT 内，
# 已在烘焙阶段剔除，manifest 里本就只有 632 条。
BASE_TAGS = ["动画", "ASCII art"]
# 排在既有真实作品之前的时间基线上：最新一条真实作品是 2026-09-27，
# piece 从 2026-01-01 起逐条 +1 秒，latest 排序时全部落在真实作品之后。
EPOCH = datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc)
DESC_MAX = 500


def work_time(index: int) -> str:
    ts = EPOCH + datetime.timedelta(seconds=index)
    return ts.strftime("%Y-%m-%dT%H:%M:%SZ")


def first_frame_png(piece: dict, dst_png: str) -> None:
    """Rasterise frame 0 of a piece to PNG via the ANSI video renderer.

    output/video.frame_to_image is the only renderer that draws ANSI truecolor
    and braille cells correctly (the braille glyph is vector-drawn because no
    bundled font has it).
    """
    lines = P.ansi_lines(piece["frames"][0], piece.get("palette") or {})
    font = V.pick_font(14)
    cw, ch = V._measure_cell(font)
    img = V.frame_to_image(lines, font, cw, ch,
                           int(piece["cols"]) * cw, int(piece["rows"]) * ch)
    img.save(dst_png)


def build_work(item: dict, manifest: dict, index: int, base: str, db: G.GalleryDB) -> dict | None:
    slug = item["slug"]
    piece = P.read_piece(slug)
    if not piece:
        print("  ! missing bake for %s" % slug)
        return None
    work_id = _unique_id(db)
    src = os.path.join(base, work_id + ".json.gz")
    shutil.copyfile(P.piece_path(slug), src)
    png = os.path.join(base, work_id + "_src.png")
    thumb = os.path.join(base, work_id + "_thumb.gif")
    og = os.path.join(base, work_id + "_og.png")
    try:
        first_frame_png(piece, png)
        G.make_thumbnail(png, thumb, fit=True)
        G.make_og_image(thumb, og, item.get("name") or slug, AUTHOR)
    finally:
        if os.path.isfile(png):
            os.remove(png)
    params = {
        "kind": "piece",
        "piece": slug,
        "charset": "blocks",
        "width": int(piece["cols"]),
        "height": P.natural_height(piece),
        "interval": float(item.get("interval") or P.DEFAULT_INTERVAL),
        "color": "source",
    }
    tags = list(BASE_TAGS) + G.sanitize_custom_tags(item.get("tags") or [])
    return {
        "id": work_id,
        "title": G.sanitize(item.get("name") or slug, 60),
        "description": G.sanitize(item.get("note") or "", DESC_MAX),
        "tags": json.dumps(tags, ensure_ascii=False),
        "author": AUTHOR,
        "source_path": src,
        "thumbnail_path": thumb,
        "og_path": og,
        "params_json": json.dumps(params, ensure_ascii=False),
        "is_private": 0,
        "admin_token": G.make_admin_token(),
        "created_at": work_time(index),
        "ip": "seed",
    }


def _unique_id(db: G.GalleryDB) -> str:
    for _ in range(64):
        sid = G.make_short_id()
        if not db.id_collides(sid):
            return sid
    raise RuntimeError("could not allocate a unique work id")


def existing_pieces(db: G.GalleryDB) -> dict:
    """slug -> work_id for every kind=piece row already in the database."""
    out = {}
    with db._connect() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT id, params_json FROM works").fetchall()
    for row in rows:
        try:
            params = json.loads(row["params_json"] or "{}")
        except ValueError:
            continue
        if params.get("kind") == "piece" and params.get("piece"):
            out[params["piece"]] = row["id"]
    return out


def drop_work(db: G.GalleryDB, work_id: str) -> None:
    deleted = db.delete_work(work_id)
    if not deleted:
        return
    for key in ("source_path", "thumbnail_path", "og_path"):
        p = deleted.get(key)
        if p and os.path.isfile(p):
            os.remove(p)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true",
                    help="rebuild pieces already present in the database")
    ap.add_argument("--limit", type=int, default=0, help="seed at most N pieces")
    args = ap.parse_args()

    with open(MANIFEST, encoding="utf-8") as f:
        manifest = json.load(f)
    items = manifest["items"]
    if args.limit:
        items = items[:args.limit]

    db = G.GalleryDB(DB_PATH)
    db.init_db()
    have = existing_pieces(db)
    base = G.gallery_base(DATA_DIR)

    done = skipped = failed = 0
    for i, item in enumerate(items):
        slug = item["slug"]
        if slug in have:
            if not args.force:
                skipped += 1
                continue
            drop_work(db, have[slug])
        work = build_work(item, manifest, i, base, db)
        if not work:
            failed += 1
            continue
        db.insert_work(work)
        done += 1
        if done % 25 == 0:
            print("  ... %d/%d" % (done, len(items)), flush=True)

    print("seed 完成: 新增 %d, 跳过 %d, 失败 %d" % (done, skipped, failed))
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
