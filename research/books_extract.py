"""Ekstraksi teks buku di folder `buku tambahan` ke `research/.cache/books/`.

Kenapa ada berkas ini
---------------------
Buku rujukan strategi ada sebagai PDF di repo, tetapi tidak ada satu pun yang bisa
dibaca langsung oleh kode: isinya harus jadi teks dulu sebelum bisa dipelajari,
dikutip, dan diterjemahkan menjadi aturan yang bisa diukur. Tanpa skrip ini,
setiap orang (dan setiap sesi) mengulang pekerjaan yang sama dengan cara berbeda —
dan hasil pembacaannya tidak bisa ditelusuri kembali.

Dua rupa PDF diperlakukan berbeda, dan itu dideteksi, bukan diasumsikan:

1. **PDF berteks** — teksnya diambil apa adanya (`pypdf`).
2. **PDF hasil pindai** — halaman tidak punya teks sama sekali (hanya satu XObject
   gambar). Kalau teks kosong, gambar mentah halaman itu ditulis ke disk supaya
   bisa di-OCR. Ini penting karena kalau tidak, buku hasil pindai akan diam-diam
   menghasilkan berkas kosong dan terlihat "tidak ada isinya".

Keluaran (semua di `research/.cache/`, tidak masuk git):
    books/<judul>.txt              teks per halaman, ditandai "===== PAGE n ====="
    books/scans/<judul>/p%04d.<ext>  gambar mentah halaman hasil pindai

Pemakaian:
    .venv/bin/python research/books_extract.py            # hanya yang belum ada
    .venv/bin/python research/books_extract.py --force    # timpa
"""
from __future__ import annotations

import argparse
import glob
import os
import re
import sys

from pypdf import PdfReader

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_DIR = os.path.join(ROOT, "buku tambahan")
OUT_DIR = os.path.join(ROOT, "research", ".cache", "books")
SCAN_DIR = os.path.join(OUT_DIR, "scans")

# /Filter -> ekstensi berkas. Hanya filter yang lazim dipakai pemindai.
FILTER_EXT = {
    "/DCTDecode": "jpg",
    "/JPXDecode": "jp2",
    "/CCITTFaxDecode": "tif",
    "/FlateDecode": "png",
}


def slug(name: str) -> str:
    name = re.sub(r"\.pdf$", "", name, flags=re.I)
    name = re.sub(r"[^0-9A-Za-zÀ-ÿ]+", "_", name).strip("_")
    return name[:80]


def page_text(page) -> str:
    try:
        return page.extract_text() or ""
    except Exception:
        return ""


def page_image(page):
    """Kembalikan (bytes, ekstensi) gambar halaman, atau None kalau bukan pindaian."""
    try:
        res = page.get("/Resources") or {}
        xo = res.get("/XObject")
        if not xo:
            return None
        for _, ref in xo.items():
            try:
                obj = ref.get_object()
            except Exception:
                continue
            if obj.get("/Subtype") != "/Image":
                continue
            filt = obj.get("/Filter")
            if isinstance(filt, list):
                filt = filt[0] if len(filt) == 1 else None
            ext = FILTER_EXT.get(filt)
            if not ext:
                continue
            return obj.get_data(), ext
    except Exception:
        return None
    return None


def extract(path: str, force: bool = False) -> dict:
    base = slug(os.path.basename(path))
    txt_path = os.path.join(OUT_DIR, f"{base}.txt")
    if os.path.exists(txt_path) and not force:
        return {"file": os.path.basename(path), "skipped": True, "txt": txt_path}

    reader = PdfReader(path)
    n = len(reader.pages)
    chunks, text_pages, scan_pages = [], 0, 0
    img_dir = os.path.join(SCAN_DIR, base)
    img_ext = None

    for i in range(n):
        page = reader.pages[i]
        t = page_text(page)
        if t.strip():
            text_pages += 1
            chunks.append(f"===== PAGE {i+1} =====\n{t.strip()}\n")
            continue
        # Halaman tanpa teks: kalau ada gambar, simpan supaya bisa di-OCR.
        img = page_image(page)
        if img is None:
            chunks.append(f"===== PAGE {i+1} =====\n[halaman tanpa teks]\n")
            continue
        scan_pages += 1
        data, ext = img
        img_ext = ext
        os.makedirs(img_dir, exist_ok=True)
        with open(os.path.join(img_dir, f"p{i+1:04d}.{ext}"), "wb") as fh:
            fh.write(data)
        chunks.append(f"===== PAGE {i+1} =====\n[halaman hasil pindai — gambar: scans/{base}/p{i+1:04d}.{ext}]\n")

    os.makedirs(OUT_DIR, exist_ok=True)
    with open(txt_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(chunks))

    return {
        "file": os.path.basename(path),
        "pages": n,
        "pages_with_text": text_pages,
        "pages_scanned": scan_pages,
        "img_ext": img_ext,
        "chars": sum(len(c) for c in chunks),
        "txt": txt_path,
        "scans": img_dir if scan_pages else None,
        "skipped": False,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    pdfs = sorted(glob.glob(os.path.join(SRC_DIR, "*.pdf")))
    if not pdfs:
        print(f"Tidak ada PDF di {SRC_DIR}")
        return 1

    for p in pdfs:
        try:
            r = extract(p, force=args.force)
        except Exception as e:  # jangan hentikan seluruh proses karena satu berkas
            print(f"GAGAL  {os.path.basename(p)}: {type(e).__name__}: {e}")
            continue
        if r.get("skipped"):
            print(f"LEWAT  {r['file']}")
            continue
        if r["pages_scanned"] and not r["pages_with_text"]:
            kind = "PINDAIAN (butuh OCR)"
        elif r["pages_scanned"]:
            kind = "campuran"
        else:
            kind = "teks"
        print(f"OK     {r['file'][:52]:54s} {r['pages']:4d} hal  "
              f"teks={r['pages_with_text']:4d} pindai={r['pages_scanned']:4d}  "
              f"{r['chars']:8d} huruf  [{kind}]")
    print(f"\nKeluaran: {OUT_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
