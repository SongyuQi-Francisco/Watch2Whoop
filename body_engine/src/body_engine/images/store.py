"""Validate, square-crop, resize and re-encode an uploaded image; keep it in table `images` (migration 655).

process() never trusts the file name or the declared type: Pillow reads the header, only params.ACCEPTED_FORMATS pass, the
pixel count is checked before decoding, the EXIF orientation is applied and then every bit of metadata (EXIF, GPS, XMP,
ICC, comments) is dropped by drawing the pixels into a new image and encoding that as WebP. Animated files keep their
first frame. Errors are ImageError(Chinese message); errors_en.py has the English.
"""
from __future__ import annotations

import hashlib
import io

import psycopg
from PIL import Image, ImageOps, UnidentifiedImageError

from . import params as P


class ImageError(ValueError):
    pass


def process(data: bytes, size: int = P.SIZE) -> dict:
    """bytes of a PNG / JPEG / WebP file -> {data, mime, width, height} of a size x size WebP without metadata."""
    if not data:
        raise ImageError("图片是空的")
    if len(data) > P.MAX_UPLOAD_BYTES:
        raise ImageError(f"图片太大（最多 {P.MAX_UPLOAD_BYTES // (1024 * 1024)} MB）")
    try:
        with Image.open(io.BytesIO(data)) as im:
            if im.format not in P.ACCEPTED_FORMATS:
                raise ImageError("只支持 PNG、JPEG、WebP 图片")
            w, h = im.size
            if w * h > P.MAX_PIXELS:
                raise ImageError("图片像素太多")
            if min(w, h) < P.MIN_SIDE:
                raise ImageError(f"图片太小（至少 {P.MIN_SIDE}×{P.MIN_SIDE}）")
            im.seek(0)
            im = ImageOps.exif_transpose(im)
            im.load()
    except ImageError:
        raise
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError, Image.DecompressionBombError) as e:
        raise ImageError("读不出这张图片（只支持 PNG、JPEG、WebP）") from e
    mode = "RGBA" if im.mode in ("RGBA", "LA", "PA") or (im.mode == "P" and "transparency" in im.info) else "RGB"
    im = ImageOps.fit(im.convert(mode), (size, size), Image.Resampling.LANCZOS, centering=(0.5, 0.5))
    clean = Image.new(mode, im.size)             # fresh image: no info dict, no EXIF, no ICC profile
    clean.paste(im)
    out = io.BytesIO()
    clean.save(out, P.OUTPUT_FORMAT, quality=P.OUTPUT_QUALITY, method=6)
    return {"data": out.getvalue(), "mime": P.OUTPUT_MIME, "width": size, "height": size}


def save(conn: psycopg.Connection, data: bytes, kind: str, user_id: int | None) -> int:
    """process() and insert; returns the image id. user_id = who uploaded it (None for the admin CLI without an account)."""
    if kind not in P.KINDS:
        raise ValueError(f"unknown image kind {kind!r}")
    img = process(data)
    return conn.execute(
        """INSERT INTO images (user_id, kind, mime, width, height, data, sha256) VALUES (%s, %s, %s, %s, %s, %s, %s)
           RETURNING id""", (user_id, kind, img["mime"], img["width"], img["height"], img["data"],
                             hashlib.sha256(img["data"]).hexdigest())).fetchone()["id"]


def get(conn: psycopg.Connection, image_id: int) -> dict | None:
    """{id, mime, data, sha256, created_at} or None. The caller has already checked that the viewer may see it."""
    return conn.execute("SELECT id, mime, data, sha256, created_at FROM images WHERE id = %s", (image_id,)).fetchone()


def delete(conn: psycopg.Connection, image_id: int) -> None:
    conn.execute("DELETE FROM images WHERE id = %s", (image_id,))
