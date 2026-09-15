#!/usr/bin/env python3
"""Uji ketiga endpoint cron (alerts, koreksi, launchpad) tanpa harus punya CRON_SECRET.

Kenapa script ini ada
---------------------
`vercel env pull` TIDAK bisa membaca variabel yang ditandai "Sensitive" di Vercel:
nilainya tertulis sebagai teks literal `[SENSITIVE]` di .env.local. Akibatnya
permintaan cron dari komputer lokal selalu 403 dengan badan respons
{"detail":"Forbidden"} — dan itu MEMBINGUNGKAN, karena tampak seperti secret yang
salah padahal secret-nya memang belum pernah ikut terunduh.

Jadi jangan mengejar nilainya. Ada tiga cara menguji yang tidak butuh menyalin
secret sama sekali, dan ketiganya dipakai script ini:

  1. --local        Jalankan aplikasi di komputer ini dengan secret ACAK sekali pakai,
                    lalu uji ketiga endpoint terhadapnya. Menjawab pertanyaan "logika
                    otorisasi & endpoint-nya benar tidak?" Tanpa menyentuh produksi,
                    tanpa mengirim notifikasi Telegram (token dikosongkan).
  2. --via-actions  Picu workflow GitHub yang MEMANG sudah menyimpan CRON_SECRET yang
                    benar, lalu baca lognya. Menjawab pertanyaan "produksi benar-benar
                    membalas 200 tidak?" Tanpa perlu tahu nilainya.
  3. (default)      Bila .env.local / --secret berisi nilai ASLI, uji langsung ke
                    produksi. Kalau masih `[SENSITIVE]`, script menganjurkan dua mode
                    di atas alih-alih menebak.

Cara pakai
----------
  .venv/bin/python scripts/cron_check.py --local
  .venv/bin/python scripts/cron_check.py --via-actions
  .venv/bin/python scripts/cron_check.py --secret 'NILAI_CRON_SECRET'

Catatan: mode 2 dan 3 benar-benar MENJALANKAN pemindaian di produksi dan — bila ada
kandidat — mengirim notifikasi Telegram ke chat yang terpasang. Mode 1 tidak.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

PLACEHOLDER = "[SENSITIVE]"
DEFAULT_BASE = "https://sahambuku.vercel.app"
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Workflow GitHub yang memanggil endpoint cron -> dipakai mode --via-actions.
ACTION_WORKFLOWS = ["alerts.yml", "launchpad.yml"]


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


def check_all(base: str, secret: str, universe: str, lp_limit: int) -> int:
    """Uji ketiga endpoint: tanpa secret harus 403, secret salah 403, secret benar 200."""
    checks = [
        ("/api/cron/alerts", ""),
        ("/api/cron/koreksi", ""),
        ("/api/cron/launchpad", f"?universe={universe}&limit={lp_limit}"),
    ]
    rc = 0
    for endpoint, query in checks:
        noauth, _ = call(base, endpoint, "", query, timeout=60)
        wrong, _ = call(base, endpoint, "salah", query, timeout=60)
        status, body = call(base, endpoint, secret, query)
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


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def run_local(args) -> int:
    """Jalankan server sekali pakai dengan secret acak, uji, lalu matikan.

    Server ini memakai TELEGRAM_BOT_TOKEN kosong sehingga tidak ada notifikasi yang
    terkirim, dan SYNC_ENABLED=0 supaya tidak menyentuh penyimpanan produksi.
    """
    port = _free_port()
    secret = secrets.token_urlsafe(24)
    base = f"http://127.0.0.1:{port}"
    env = {**os.environ, "CRON_SECRET": secret, "TELEGRAM_BOT_TOKEN": "",
           "TELEGRAM_CHAT_ID": "", "SYNC_ENABLED": "0"}
    print(f"Menjalankan server lokal di {base} dengan CRON_SECRET acak "
          f"(panjang {len(secret)}).\n")
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "api.index:app", "--host", "127.0.0.1",
         "--port", str(port)],
        cwd=REPO_ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        deadline = time.time() + args.startup_timeout
        ready = False
        while time.time() < deadline:
            if proc.poll() is not None:
                print("GAGAL: server lokal berhenti sendiri. Jalankan manual untuk "
                      "melihat galatnya:\n  .venv/bin/uvicorn api.index:app --port 8000")
                return 1
            try:
                with urllib.request.urlopen(f"{base}/openapi.json", timeout=5) as r:
                    if r.status == 200:
                        ready = True
                        break
            except Exception:
                time.sleep(1.5)
        if not ready:
            print(f"GAGAL: server lokal tidak siap dalam {args.startup_timeout}s.")
            return 1
        print("Server siap. Menguji otorisasi & endpoint...\n")
        return check_all(base, secret, args.universe, args.launchpad_limit)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()


def _gh(*cmd: str) -> subprocess.CompletedProcess:
    return subprocess.run(["gh", *cmd], cwd=REPO_ROOT, capture_output=True, text=True)


def run_via_actions(args) -> int:
    """Picu workflow GitHub (yang memegang CRON_SECRET asli) lalu baca lognya.

    Ini cara membuktikan PRODUKSI sehat tanpa pernah tahu nilainya.
    """
    probe = _gh("auth", "status")
    if probe.returncode != 0:
        print("GAGAL: `gh` belum login. Jalankan `gh auth login` dulu.\n")
        print(probe.stderr.strip()[:400])
        return 1

    rc = 0
    for wf in ACTION_WORKFLOWS:
        print(f"=== {wf} ===")
        before = _gh("run", "list", "--workflow", wf, "--limit", "1",
                     "--json", "databaseId", "--jq", ".[0].databaseId")
        prev_id = before.stdout.strip() if before.returncode == 0 else ""
        run = _gh("workflow", "run", wf)
        if run.returncode != 0:
            print(f"  GAGAL memicu: {(run.stderr or run.stdout).strip()[:300]}\n")
            rc = 1
            continue
        print("  dipicu, menunggu selesai…")
        run_id = ""
        deadline = time.time() + args.actions_timeout
        while time.time() < deadline:
            time.sleep(6)
            got = _gh("run", "list", "--workflow", wf, "--limit", "1",
                      "--json", "databaseId,status,conclusion",
                      "--jq", '.[0] | "\\(.databaseId) \\(.status) \\(.conclusion)"')
            if got.returncode != 0:
                continue
            parts = got.stdout.split()
            if len(parts) >= 2 and parts[0] != prev_id:
                if parts[1] == "completed":
                    run_id = parts[0]
                    break
        if not run_id:
            print(f"  GAGAL: run tidak selesai dalam {args.actions_timeout}s.\n")
            rc = 1
            continue
        log = _gh("run", "view", run_id, "--log")
        if log.returncode != 0:
            print(f"  GAGAL membaca log run {run_id}.\n")
            rc = 1
            continue
        codes = re.findall(r"HTTP (\d{3})", log.stdout)
        payload = [ln.strip() for ln in log.stdout.splitlines() if '"time"' in ln]
        ok = bool(codes) and all(c == "200" for c in codes)
        rc = rc or (0 if ok else 1)
        print(f"  run {run_id}: kode HTTP {codes or 'tidak ditemukan'} "
              f"{'OK' if ok else '<-- PERIKSA'}")
        for p in payload:
            print(f"    {p[:220]}")
        print()
    print("Selesai." if rc == 0 else "Ada yang tidak sesuai harapan (lihat tanda di atas).")
    return rc


def main() -> int:
    ap = argparse.ArgumentParser(description="Uji endpoint cron aplikasi saham")
    ap.add_argument("--base", default=os.environ.get("BASE_URL", DEFAULT_BASE))
    ap.add_argument("--secret", default="", help="CRON_SECRET (default: dari .env.local)")
    ap.add_argument("--universe", default="liquid", choices=["liquid", "all"])
    ap.add_argument("--launchpad-limit", type=int, default=45)
    ap.add_argument("--local", action="store_true",
                    help="jalankan server lokal sekali pakai (tanpa perlu CRON_SECRET)")
    ap.add_argument("--via-actions", action="store_true",
                    help="picu workflow GitHub lalu baca lognya (tanpa perlu CRON_SECRET)")
    ap.add_argument("--startup-timeout", type=int, default=120,
                    help="detik menunggu server lokal siap")
    ap.add_argument("--actions-timeout", type=int, default=420,
                    help="detik menunggu workflow GitHub selesai")
    args = ap.parse_args()

    if args.local:
        return run_local(args)
    if args.via_actions:
        return run_via_actions(args)

    secret = args.secret or os.environ.get("CRON_SECRET", "")
    source = "argumen --secret" if args.secret else ".env.local/env"
    if secret == PLACEHOLDER:
        env = read_env_file(os.path.join(REPO_ROOT, ".env.local"))
        secret = env.get("CRON_SECRET", "")
        source = ".env.local"
    if not secret or secret == PLACEHOLDER:
        print(f"CRON_SECRET lokal = placeholder '{PLACEHOLDER}' (variabel Sensitive di "
              "Vercel tidak bisa diunduh).\n")
        print("Tidak perlu mengejar nilainya — pakai salah satu mode ini:\n")
        print("  .venv/bin/python scripts/cron_check.py --local")
        print("      Uji otorisasi & ketiga endpoint di server lokal dengan secret acak.")
        print("      Tidak menyentuh produksi, tidak mengirim notifikasi Telegram.\n")
        print("  .venv/bin/python scripts/cron_check.py --via-actions")
        print("      Picu workflow GitHub yang sudah memegang CRON_SECRET asli, lalu baca")
        print("      lognya — membuktikan PRODUKSI membalas 200 tanpa tahu nilainya.\n")
        print("Kalau tetap ingin mode langsung, salin nilai asli dari dashboard Vercel")
        print("(Project → Settings → Environment Variables → CRON_SECRET) ke .env.local.")
        return 2
    print(f"Secret dipakai dari: {source} (panjang {len(secret)})\n")
    return check_all(args.base, secret, args.universe, args.launchpad_limit)


if __name__ == "__main__":
    sys.exit(main())
