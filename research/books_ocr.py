"""OCR halaman hasil pindai dari `research/books_extract.py` menjadi teks.

Kenapa dipisah dari ekstraksi
-----------------------------
`books_extract.py` hanya mengambil apa yang sudah berupa teks di PDF. Dua buku
rujukan (`Ilmu Saham Biawak VVIP`, `Technical Analysis for Mega Profit`) seluruhnya
hasil pindai, jadi tanpa langkah ini keduanya terbaca sebagai berkas kosong —
keadaan yang menyesatkan karena tampak seperti "buku tidak ada isinya".

Sifatnya lambat dan bisa terputus, karena itu hasilnya disimpan **per halaman**
(`ocr/p%04d.txt`) dan halaman yang sudah ada dilewati. Menjalankan ulang skrip ini
meneruskan dari halaman terakhir, bukan mengulang dari awal.

Catatan mutu: mesin OCR di mesin ini hanya punya bahasa `eng`, sedangkan kedua buku
berbahasa Indonesia. Angka, nama indikator, dan istilah teknis umumnya terbaca;
ejaan Indonesia bisa melenceng (mis. "yarg" untuk "yang"). Teks ini dipakai untuk
menemukan aturan dan halaman rujukan, **bukan** untuk dikutip kata per kata.

Pemakaian:
    .venv/bin/python research/books_ocr.py                # semua buku pindai
    .venv/bin/python research/books_ocr.py --book Biawak --pages 1-40
    .venv/bin/python research/books_ocr.py --join         # gabung jadi <judul>.ocr.txt
"""
from __future__ import annotations

import argparse
import glob
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCAN_ROOT = os.path.join(ROOT, "research", ".cache", "books", "scans")
OCR_ROOT = os.path.join(ROOT, "research", ".cache", "books", "ocr")

# Nama kartu tesseract (kartu angka sering lebih akurat untuk tabel harga/volume).
PSM = "3"


def ocr_one(img: str) -> str:
    out = subprocess.run(
        ["tesseract", img, "-", "--psm", PSM],
        capture_output=True, text=True,
    )
    return out.stdout or ""


def book_dirs(only: str | None):
    dirs = sorted(d for d in glob.glob(os.path.join(SCAN_ROOT, "*")) if os.path.isdir(d))
    if only:
        key = only.lower()
        dirs = [d for d in dirs if key in os.path.basename(d).lower()]
    return dirs


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--book", default=None, help="saring nama buku (substring)")
    ap.add_argument("--pages", default=None, help="mis. 1-40")
    ap.add_argument("--join", action="store_true", help="gabungkan hasil jadi satu berkas")
    args = ap.parse_args()

    lo, hi = 1, 10 ** 9
    if args.pages:
        a, _, b = args.pages.partition("-")
        lo, hi = int(a), int(b or a)

    total_new = total_skip = 0
    for d in book_dirs(args.book):
        base = os.path.basename(d)
        out_dir = os.path.join(OCR_ROOT, base)
        os.makedirs(out_dir, exist_ok=True)
        imgs = sorted(glob.glob(os.path.join(d, "p*")))
        new = skip = 0
        for img in imgs:
            m = re.search(r"p(\d+)\.", os.path.basename(img))
            if not m:
                continue
            n = int(m.group(1))
            if not (lo <= n <= hi):
                continue
            dest = os.path.join(out_dir, f"p{n:04d}.txt")
            if os.path.exists(dest) and os.path.getsize(dest) > 0:
                skip += 1
                continue
            with open(dest, "w", encoding="utf-8") as fh:
                fh.write(ocr_one(img))
            new += 1
        total_new += new
        total_skip += skip
        print(f"{base[:56]:58s} baru={new:4d} dilewati={skip:4d}")

        if args.join:
            joined = os.path.join(OCR_ROOT, f"{base}.ocr.txt")
            with open(joined, "w", encoding="utf-8") as fh:
                for n in range(1, len(imgs) + 1):
                    p = os.path.join(out_dir, f"p{n:04d}.txt")
                    if not os.path.exists(p):
                        continue
                    fh.write(f"\n===== PAGE {n} =====\n")
                    fh.write(open(p, encoding="utf-8").read().strip() + "\n")
            print(f"   -> {joined}")

    print(f"\nTotal baru={total_new} dilewati={total_skip}\nKeluaran: {OCR_ROOT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
