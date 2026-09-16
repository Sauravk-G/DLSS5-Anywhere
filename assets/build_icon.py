"""Build dlss5-anywhere.ico - the app icon, one file, every size Windows asks for.

Two sources, not one. An .ico stores a separate image per size, and that is there to
be used: below 32px the four satellites around the core fall to about two pixels and
antialias into smudges, so 16, 20 and 24 are drawn from dlss5-anywhere-small.svg -
the same five blocks closed up into one solid cross - and everything from 32 up from
dlss5-anywhere.svg. Each size is rendered from vector at exactly that size rather
than downscaled from the 256, because a resample turns a two-pixel block into grey.

    python assets\\build_icon.py

Qt does the rasterising: it is already a dependency of the app, and requiring
ImageMagick or PIL to draw an icon would be a strange thing to add.
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, Qt
from PySide6.QtGui import QGuiApplication, QImage, QPainter
from PySide6.QtSvg import QSvgRenderer

HERE = Path(__file__).resolve().parent

# size -> which drawing to use for it.
SMALL = HERE / "dlss5-anywhere-small.svg"
FULL = HERE / "dlss5-anywhere.svg"
# 32 is the taskbar at 100% scaling, which is where this icon is seen most often, and
# there the satellites are still only three pixels and sit dim against the plate. It gets
# the solid cross too; the constellation starts at 48, where the four blocks have enough
# room to read as separate things rather than as noise around the core.
PLAN = {16: SMALL, 20: SMALL, 24: SMALL, 32: SMALL,
        48: FULL, 64: FULL, 128: FULL, 256: FULL}


def render(svg: Path, size: int) -> bytes:
    renderer = QSvgRenderer(str(svg))
    if not renderer.isValid():
        raise SystemExit(f"could not parse {svg}")

    image = QImage(size, size, QImage.Format_ARGB32)
    image.fill(Qt.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.Antialiasing, True)
    renderer.render(painter)
    painter.end()

    # QBuffer keeps a pointer to this and does not own it, so it must outlive the
    # call - QBuffer(QByteArray()) hands it a temporary and segfaults the process.
    payload = QByteArray()
    buffer = QBuffer(payload)
    buffer.open(QIODevice.WriteOnly)
    image.save(buffer, "PNG")
    buffer.close()
    return bytes(payload)


def pack_ico(pngs: dict[int, bytes], dest: Path) -> None:
    """A six-byte header, a sixteen-byte directory entry per image, then the images.

    Every entry is stored as PNG rather than an uncompressed BMP - Windows has read
    that since Vista, and it keeps the 256 from costing a quarter of a megabyte. A
    width or height byte of 0 means 256.
    """
    entries, blobs, offset = [], [], 6 + 16 * len(pngs)
    for size in sorted(pngs):
        data = pngs[size]
        entries.append(struct.pack(
            "<BBBBHHII",
            size if size < 256 else 0, size if size < 256 else 0,
            0, 0, 1, 32, len(data), offset,
        ))
        blobs.append(data)
        offset += len(data)

    dest.write_bytes(
        struct.pack("<HHH", 0, 1, len(pngs)) + b"".join(entries) + b"".join(blobs)
    )


def main() -> int:
    QGuiApplication.instance() or QGuiApplication(sys.argv)

    pngs = {}
    preview = HERE / "dlss5-anywhere-png"
    preview.mkdir(exist_ok=True)
    for size, svg in PLAN.items():
        pngs[size] = render(svg, size)
        (preview / f"{size}.png").write_bytes(pngs[size])

    ico = HERE / "dlss5-anywhere.ico"
    pack_ico(pngs, ico)

    small = ", ".join(str(s) for s in sorted(PLAN) if PLAN[s] is SMALL)
    full = ", ".join(str(s) for s in sorted(PLAN) if PLAN[s] is FULL)
    print(f"{ico.name}: {ico.stat().st_size:,} bytes, {len(pngs)} sizes")
    print(f"  {small:14} from {SMALL.name}")
    print(f"  {full:14} from {FULL.name}")
    print(f"  PNGs also written to {preview.name}\\ for inspection")
    return 0


if __name__ == "__main__":
    sys.exit(main())
