"""PDF -> PNG page rendering (PyMuPDF) and chart-region cropping (Pillow)."""
from __future__ import annotations

from pathlib import Path

import fitz  # PyMuPDF
from PIL import Image


def render_pdf_to_pngs(pdf_path: Path, out_dir: Path, dpi: int, max_pages: int) -> list[dict]:
    """Render each page to a PNG. Returns list of {page_number, filename, width, height}."""
    out_dir.mkdir(parents=True, exist_ok=True)
    pages: list[dict] = []
    doc = fitz.open(pdf_path)
    try:
        total = min(len(doc), max_pages)
        for i in range(total):
            page = doc[i]
            pix = page.get_pixmap(dpi=dpi)  # 1.19.2+ dpi shortcut
            filename = f"page_{i + 1:03d}.png"
            pix.save(str(out_dir / filename))
            pages.append(
                {
                    "page_number": i + 1,
                    "filename": filename,
                    "width": pix.width,
                    "height": pix.height,
                }
            )
    finally:
        doc.close()
    return pages


def page_count(pdf_path: Path) -> int:
    doc = fitz.open(pdf_path)
    try:
        return len(doc)
    finally:
        doc.close()


def crop_chart(page_png: Path, bbox: list[float], out_path: Path) -> dict:
    """Crop a normalized bbox [x0,y0,x1,y1] (0..1) from the page image.

    Returns {filename, width, height}. Falls back to the whole page on bad bbox.
    """
    img = Image.open(page_png).convert("RGB")
    w, h = img.size
    try:
        x0, y0, x1, y1 = bbox
        # clamp + sanity
        x0, x1 = sorted((max(0.0, min(1.0, x0)), max(0.0, min(1.0, x1))))
        y0, y1 = sorted((max(0.0, min(1.0, y0)), max(0.0, min(1.0, y1))))
        # small padding so axis labels aren't clipped
        pad = 0.01
        x0 = max(0.0, x0 - pad)
        y0 = max(0.0, y0 - pad)
        x1 = min(1.0, x1 + pad)
        y1 = min(1.0, y1 + pad)
        box = (int(x0 * w), int(y0 * h), int(x1 * w), int(y1 * h))
        if box[2] - box[0] < 8 or box[3] - box[1] < 8:
            box = (0, 0, w, h)
        crop = img.crop(box)
    except Exception:
        crop = img
    out_path.parent.mkdir(parents=True, exist_ok=True)
    crop.save(str(out_path))
    return {"filename": out_path.name, "width": crop.width, "height": crop.height}
