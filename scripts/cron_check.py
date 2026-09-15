#!/usr/bin/env python3
"""Uji ketiga endpoint cron (alerts, koreksi, launchpad) terhadap aplikasi live.

Kenapa script ini ada
---------------------
`vercel env pull` TIDAK bisa membaca variabel yang ditandai "Sensitive" di Vercel:
nilainya tertulis sebagai teks literal `[SENSITIVE]` di .env.local. Akibatnya
permintaan cron dari komputer lokal selalu 403 dengan badan respons
{"detail":"Forbidden"} — dan itu MEMBINGUNGKAN, karena tampak seperti secret yang
salah padahal secret-nya memang belum pernah ikut terunduh.

Nilai aslinya cuma bisa dilihat di dashboard Vercel (Project → Settings →
Environment Variables). Salin satu kali ke .env.local, lalu script ini akan
menguji ketiga endpoint sungguhan — termasuk memastikan endpoint menolak permintaan
tanpa secret dan dengan secret yang salah.

Cara pakai
----------
  # secret sudah ada di .env.local
  .venv/bin/python scripts/cron_check.py

  # atau diberikan langsung (tidak ditulis ke disk)
  .venv/bin/python scripts/cron_check.py --secret 'NILAI_CRON_SECRET'

  # pindai Launch Pad dengan cakupan lebih luas (memakai kuota Yahoo/IDX Edge)
  .venv/bin/python scripts/cron_check.py --launchpad-limit 45 --universe liquid

Catatan: endpoint yang berhasil akan benar-benar MENJALANKAN pemindaian dan — bila
ada kandidat — mengirim notifikasi Telegram ke chat yang terpasang.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request

PLACEHOLDER = "[SENSITIVE]"
DEFAULT_BASE = "https://sahambuku.vercel.app"


def read_env_file(path: str) -> dict:
    out: dict = {}
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$", line.rstrip("\r\n"))
                if m:
                    out[m.group(1)] = m.group(2).strip().strip('"').strip("'")
    except FileNotFoundError:
        pass
    return out


def call(base: str, endpoint: str, secret: str, query: str = "", timeout: int = 300):
    url = f"{base}{endpoint}{query}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {secret}"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            return res.status, json.loads(res.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        raw = e.read().decode()[:200]
        return e.code, {"detail": raw}
    except Exception as e:  # jaringan/timeout
        return 0, {"error": f"{type(e).__name__}: {e}"}


def main() -> int:
    ap = argparse.ArgumentParser(description="Uji endpoint cron aplikasi saham")
    ap.add_argument("--base", default=os.environ.get("BASE_URL", DEFAULT_BASE))
    ap.add_argument("--secret", default="", help="CRON_SECRET (default: dari .env.local)")
    ap.add_argument("--universe", default="liquid", choices=["liquid", "all"])
    ap.add_argument("--launchpad-limit", type=int, default=45)
    args = ap.parse_args()

    secret = args.secret or os.environ.get("CRON_SECRET", "")
    source = "argumen --secret" if args.secret else ".env.local/env"
    if secret == PLACEHOLDER:
        env = read_env_file(os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), ".env.local"))
        secret = env.get("CRON_SECRET", "")
        source = ".env.local"
    if not secret or secret == PLACEHOLDER:
        print("GAGAL: CRON_SECRET belum diisi (masih kosong atau placeholder "
              f"'{PLACEHOLDER}').\n")
        print("Nilai aslinya ada di dashboard Vercel: Project → Settings → Environment")
        print("Variables → CRON_SECRET (klik ikon mata untuk menampilkan). Salin ke")
        print(".env.local sebagai  CRON_SECRET=nilai_asli  lalu jalankan ulang script ini.")
        return 2
    print(f"Secret dipakai dari: {source} (panjang {len(secret)})\n")

    checks = [
        ("/api/cron/alerts", ""),
        ("/api/cron/koreksi", ""),
        ("/api/cron/launchpad", f"?universe={args.universe}&limit={args.launchpad_limit}"),
    ]
    rc = 0
    for endpoint, query in checks:
        noauth, _ = call(args.base, endpoint, "", query, timeout=60)
        wrong, _ = call(args.base, endpoint, "salah", query, timeout=60)
        status, body = call(args.base, endpoint, secret, query)
        brief = {k: v for k, v in body.items() if not isinstance(v, (list, dict))}
        ok = status == 200 and noauth == 403 and wrong == 403
        rc = rc or (0 if ok else 1)
        print(f"{endpoint:<24} tanpa-secret={noauth} salah={wrong} benar={status} "
              f"{'OK' if ok else '<-- PERIKSA'}")
        print(f"    {json.dumps(brief)[:220]}")
        if status == 200 and body.get("telegram_sent"):
            print("    -> notifikasi Telegram TERKIRIM untuk endpoint ini")
    print("\nSelesai." if rc == 0 else "\nAda yang tidak sesuai harapan (lihat tanda di atas).")
    return rc


if __name__ == "__main__":
    sys.exit(main())
