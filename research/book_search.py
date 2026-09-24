"""Cari istilah di teks buku (hasil `books_extract.py` / `books_ocr.py`) **beserta nomor halaman**.

Kenapa ada
----------
Aturan dari buku hanya berguna kalau bisa ditelusuri kembali: "aturan ini dari buku
X halaman Y". `grep` biasa memberi nomor baris, bukan nomor halaman, dan halaman
hasil pindai punya baris kosong yang membuat pencarian manual melelahkan.

Satu jebakan yang sudah ditutup di sini: beberapa buku **campuran** (mayoritas
berteks, satu-dua halaman hasil pindai). Kalau berkas gabungan hasil OCR dipakai
apa adanya untuk buku seperti itu, isinya hanya halaman pindaiannya saja — jadi
buku tebal terlihat "hampir kosong" dan pencarian diam-diam tidak menemukan apa pun.
Karena itu teks di sini disusun per halaman: teks PDF dipakai bila ada, dan hanya
halaman yang kosong diisi hasil OCR.

Pemakaian:
    .venv/bin/python research/book_search.py "volatility contraction" -b minervini
    .venv/bin/python research/book_search.py "position siz" -b tharp -n 6 -C 3
    .venv/bin/python research/book_search.py --list
"""
from __future__ import annotations

import argparse
import glob
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BOOKS = os.path.join(ROOT, "research", ".cache", "books")
PAGE_RE = re.compile(r"===== PAGE (\d+) =====")


def _pages_from(path: str) -> dict[int, str]:
    """Pecah berkas teks menjadi {nomor halaman: isi}."""
    if not os.path.exists(path):
        return {}
    text = open(path, encoding="utf-8").read()
    parts = PAGE_RE.split(text)
    # parts = ['', '1', isi1, '2', isi2, ...]
    out: dict[int, str] = {}
    for i in range(1, len(parts) - 1, 2):
        out[int(parts[i])] = parts[i + 1]
    return out


def book_names() -> list[str]:
    names = {os.path.basename(p)[:-4] for p in glob.glob(os.path.join(BOOKS, "*.txt"))}
    names |= {os.path.basename(p)[:-8] for p in glob.glob(os.path.join(BOOKS, "ocr", "*.ocr.txt"))}
    return sorted(names)


def load(name: str) -> str:
    """Teks satu buku, teks PDF diutamakan dan hasil OCR menambal halaman kosong."""
    extract = _pages_from(os.path.join(BOOKS, f"{name}.txt"))
    ocr = _pages_from(os.path.join(BOOKS, "ocr", f"{name}.ocr.txt"))
    page_dirs = os.path.join(BOOKS, "ocr", name)
    if os.path.isdir(page_dirs):  # hasil OCR per halaman, kalau ada lebih rinci
        for p in glob.glob(os.path.join(page_dirs, "p*.txt")):
            n = int(re.search(r"p(\d+)\.txt$", p).group(1))
            if os.path.getsize(p) > 0:
                ocr.setdefault(n, open(p, encoding="utf-8").read())

    nums = sorted(set(extract) | set(ocr))
    chunks = []
    for n in nums:
        t = (extract.get(n) or "").strip()
        if t and not t.startswith("[halaman hasil pindai"):
            body = t
        else:
            body = (ocr.get(n) or "").strip() or "[halaman tanpa teks]"
        chunks.append(f"===== PAGE {n} =====\n{body}\n")
    return "\n".join(chunks)


def page_of(text: str, pos: int) -> int:
    last = 0
    for m in PAGE_RE.finditer(text, 0, pos):
        last = int(m.group(1))
    return last


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.lower())


def search(text: str, pattern: str, ctx: int, limit: int):
    """Cari PER HALAMAN, bukan per berkas.

    Versi pertama memetakan posisi karakter ke halaman lewat indeks kata, dan cara
    itu gagal diam-diam: buku yang panjang (475 hal) menghasilkan nol temuan untuk
    kata yang jelas ada, sehingga pembaca menyimpulkan "buku ini tidak membahasnya".
    Mencari di dalam tiap halaman menghapus seluruh kelas galat itu — nomor halaman
    sudah pasti benar dan tidak ada pemetaan yang bisa meleset.
    """
    hits = []
    for page, body in _pages(text).items():
        hay = norm(body)
        for m in re.finditer(pattern, hay, re.I):
            ws = hay[:m.start()].split()
            # Sisipan: pakai bentuk ASLI (tanpa normalisasi) untuk kutipan yang jujur.
            raw = re.findall(r"\S+", body)
            i0 = len(ws)
            i1 = i0 + max(1, len(hay[m.start():m.end()].split()))
            snippet = " ".join(raw[max(0, i0 - ctx): i1 + ctx])
            hits.append((page, snippet))
            if len(hits) >= limit:
                break
        if len(hits) >= limit:
            break
    return hits


def _pages(text: str) -> dict:
    parts = PAGE_RE.split(text)
    return {int(parts[i]): parts[i + 1] for i in range(1, len(parts) - 1, 2)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("pattern", nargs="?", help="regex istilah")
    ap.add_argument("-b", "--book", default=None, help="saring nama buku (substring)")
    ap.add_argument("-n", "--limit", type=int, default=8, help="maks hasil per buku")
    ap.add_argument("-C", "--context", type=int, default=2, help="kata konteks")
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args()

    names = book_names()
    if args.list:
        for k in names:
            print(f"{k:70s} {len(load(k)):9d} huruf")
        return 0
    if not args.pattern:
        ap.error("butuh pola pencarian")

    for k in names:
        if args.book and args.book.lower() not in k.lower():
            continue
        hits = search(load(k), args.pattern, args.context, args.limit)
        if not hits:
            continue
        print(f"\n### {k}")
        for page, snippet in hits:
            print(f"  [hal {page:4d}] …{snippet.strip()}…")
    return 0


if __name__ == "__main__":
    sys.exit(main())
