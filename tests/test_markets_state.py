#!/usr/bin/env python3
"""Uji GERBANG aturan keadaan pasar AS/ETF — tanpa jaringan dan tanpa panel harga.

Yang diuji adalah keputusan "aturan mana yang boleh tampil", karena di situlah risiko
sebenarnya: menyajikan aturan yang belum lolos bar, atau meminjam angka saham biasa
untuk ETF. Semua masukan di sini data PALSU, jadi ujinya bisa jalan di mana saja.

Jalankan:
    .venv/bin/python -m unittest discover -s tests -t . -v
"""

from __future__ import annotations

import json
import os
import sys
import unittest
from unittest import mock

import numpy as np
import pandas as pd
from fastapi import HTTPException

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "api"))                  # penting: `index`
sys.path.insert(0, os.path.join(ROOT, "research", "markets"))   # export/summary/universe

import export as EXP        # noqa: E402
import index as IDX         # noqa: E402
import state_rules as SR    # noqa: E402
import study as ST          # noqa: E402
import summary as SUM       # noqa: E402
import universe as UNI      # noqa: E402

# Kunci kolom keadaan = SEMUA kandidat di registry bersama (bukan 3 yang sudah
# lolos di AS). Kandidat yang belum lolos bar tetap punya kolom di CSV, tetapi
# TIDAK disajikan endpoint sampai lolos bar di pasar itu.
STATE_COLS = tuple(SR.STATE_KEYS)


def state_frame(rows: list) -> pd.DataFrame:
    """Bingkai keadaan turunan minimal: kolom yang dibaca `state_screen`."""
    base = {"date": "2026-09-25", "close": 100.0, "sma20": 99.0, "sma50": 98.0,
            "sma200": 95.0, "high52": 110.0, "dist_high52_pct": -9.1,
            "ret5_pct": 1.0, "v20_usd": 1_000_000.0}
    out = []
    for r in rows:
        row = dict(base)
        row.update({c: 0 for c in STATE_COLS})
        row.update(r)
        out.append(row)
    return pd.DataFrame(out)


def study_payload(market: str, lolos: list, rules: list = None) -> dict:
    return {"markets": {market: {"lolos_bar": lolos, "rules": rules or []}}}


class TestStateGate(unittest.TestCase):
    """`_state_allowed` = satu-satunya tempat aturan keadaan disaring."""

    def test_only_bar_passing_rules_are_allowed(self):
        df = state_frame([{"code": "AAA", "above_sma200": 1}])
        allowed = IDX._state_allowed(study_payload("etf", ["di atas SMA200"]), "etf", df)
        self.assertEqual(list(allowed), ["above_sma200"])

    def test_rule_without_column_is_excluded(self):
        # Lolos bar, tetapi tidak punya kolom di keadaan turunan -> tidak boleh tampil.
        df = state_frame([{"code": "AAA", "above_sma200": 1}]).drop(columns=["near_high52"])
        allowed = IDX._state_allowed(
            study_payload("us", ["dekat puncak 52m", "di atas SMA200"]), "us", df)
        self.assertEqual(list(allowed), ["above_sma200"])

    def test_unmeasured_market_allows_nothing(self):
        df = state_frame([{"code": "AAA", "near_high52": 1}])
        self.assertEqual(IDX._state_allowed({"markets": {}}, "etf", df), {})
        self.assertEqual(IDX._state_allowed(None, "etf", df), {})

    def test_etf_does_not_borrow_us_measurement(self):
        # US lolos bar, ETF belum diukur -> ETF tetap kosong.
        study = {"markets": {"us": {"lolos_bar": ["di atas SMA200"], "rules": []}}}
        df = state_frame([{"code": "SPY", "above_sma200": 1}])
        self.assertEqual(IDX._state_allowed(study, "etf", df), {})
        self.assertEqual(list(IDX._state_allowed(study, "us", df)), ["above_sma200"])


class TestStateScreen(unittest.TestCase):

    def _patched(self, df, study):
        return (mock.patch.object(IDX, "load_state", return_value=df),
                mock.patch.object(IDX, "_read_json_cached", return_value=study))

    def test_503_when_market_has_no_passing_rule(self):
        df = state_frame([{"code": "SPY", "above_sma200": 1}])
        p1, p2 = self._patched(df, {"markets": {}})
        with p1, p2:
            with self.assertRaises(HTTPException) as cm:
                IDX.state_screen("etf", "all", 10)
        self.assertEqual(cm.exception.status_code, 503)

    def test_503_when_snapshot_missing(self):
        p1 = mock.patch.object(IDX, "load_state", return_value=None)
        with p1:
            with self.assertRaises(HTTPException) as cm:
                IDX.state_screen("etf", "all", 10)
        self.assertEqual(cm.exception.status_code, 503)

    def test_filter_mode_when_market_measured_but_nothing_passes(self):
        # ETF sudah diukur tetapi tak ada aturan lolos bar -> tetap bisa dipakai
        # sebagai PENYARING, dengan peringatan dan tanpa aturan kontrol.
        df = state_frame([
            {"code": "SPY", "above_sma200": 1, "v20_usd": 9_000_000.0},
            {"code": "AAA", "near_high52": 1, "v20_usd": 1_000_000.0},
            {"code": "ZZZ", "new_low52_control": 1, "v20_usd": 1_000_000.0},
        ])
        study = {"markets": {"etf": {"lolos_bar": [], "rules": [{"aturan": "di atas SMA200", "n": 5}]}}}
        p1, p2 = self._patched(df, study)
        with p1, p2:
            res = IDX.state_screen("etf", "all", 10)
        self.assertFalse(res["validated"])
        self.assertIn("BELUM lolos bar", res["warning"])
        self.assertEqual({r["ticker"] for r in res["results"]}, {"SPY", "AAA"})

    def test_filter_mode_still_422_for_unknown_criteria(self):
        df = state_frame([{"code": "SPY", "above_sma200": 1}])
        study = {"markets": {"etf": {"lolos_bar": [], "rules": []}}}
        p1, p2 = self._patched(df, study)
        with p1, p2:
            with self.assertRaises(HTTPException) as cm:
                IDX.state_screen("etf", "tidak_ada", 10)
        self.assertEqual(cm.exception.status_code, 422)

    def test_422_for_unknown_criteria_lists_choices(self):
        df = state_frame([{"code": "SPY", "above_sma200": 1}])
        p1, p2 = self._patched(df, study_payload("etf", ["di atas SMA200"]))
        with p1, p2:
            with self.assertRaises(HTTPException) as cm:
                IDX.state_screen("etf", "pullback_uptrend", 10)
        self.assertEqual(cm.exception.status_code, 422)
        self.assertIn("above_sma200", str(cm.exception.detail))

    def test_filters_labels_and_sorts_by_liquidity(self):
        df = state_frame([
            {"code": "AAA", "above_sma200": 1, "v20_usd": 2_000_000.0},
            {"code": "BBB", "above_sma200": 1, "v20_usd": 9_000_000.0},
            {"code": "CCC", "above_sma200": 0, "v20_usd": 5_000_000.0},
        ])
        rule_row = {"aturan": "di atas SMA200", "a20": 0.47, "m20": 0.54, "t20": 30.8,
                    "net20": 0.28, "halves": [0.32, 0.62], "putusan": "SEPAKAT",
                    "lolos_bar": True, "n": 10}
        study = study_payload("us", ["di atas SMA200"], [rule_row])
        study["markets"]["us"]["tickers"] = 5794
        study["markets"]["us"]["rows"] = 6325200
        p1, p2 = self._patched(df, study)
        with p1, p2:
            res = IDX.state_screen("us", "all", 10)
        self.assertEqual([r["ticker"] for r in res["results"]], ["BBB", "AAA"])
        for r in res["results"]:
            self.assertEqual(r["criteria_met"], ["close di atas SMA200"])
        self.assertEqual(res["scanned"], 3)
        self.assertEqual(res["total_matched"], 2)
        self.assertIn("above_sma200", res["measurement"])
        self.assertEqual(res["measurement"]["above_sma200"]["net20"], 0.28)


class TestScreenerRouting(unittest.TestCase):

    def test_etf_routed_to_state_screen(self):
        with mock.patch.object(IDX, "state_screen", return_value={"ok": True}) as m:
            out = IDX.markets_screener("etf", "all", 7)
        self.assertTrue(out["ok"])
        m.assert_called_once_with("etf", "all", 7)

    def test_us_still_routed_to_state_screen(self):
        with mock.patch.object(IDX, "state_screen", return_value={"ok": True}) as m:
            IDX.markets_screener("us", "near_high52", 3)
        m.assert_called_once_with("us", "near_high52", 3)

    def test_unknown_market_is_422(self):
        with self.assertRaises(HTTPException) as cm:
            IDX.markets_screener("jp", "all", 3)
        self.assertEqual(cm.exception.status_code, 422)


class TestMarketAnalyze(unittest.TestCase):
    """Analisis pasar luar IDX: us/etf dari keadaan, crypto dari snapshot OHLCV."""

    def test_state_analysis_lists_active_rules(self):
        df = state_frame([{"code": "NVDA", "pullback_uptrend": 1, "above_sma200": 1,
                           "near_high52": 1, "v20_usd": 5_000_000.0}])
        study = study_payload("us", ["pullback di uptrend", "di atas SMA200",
                                     "dekat puncak 52m"])
        with mock.patch.object(IDX, "load_state", return_value=df), \
             mock.patch.object(IDX, "_read_json_cached", return_value=study):
            out = IDX._analyze_state_market("us", "NVDA")
        self.assertEqual(out["ticker"], "NVDA")
        self.assertEqual(sorted(x["key"] for x in out["active_rules"]),
                         ["above_sma200", "near_high52", "pullback_uptrend"])
        self.assertTrue(out["analysis"])
        self.assertIn("dist_high52_pct", out["indicators"])
        # Titik acuan untuk gauge posisi (tanpa riwayat harga di jalur AS/ETF).
        self.assertEqual(sorted(out["levels"]),
                         ["high52", "price", "sma20", "sma200", "sma50"])
        self.assertEqual(out["levels"]["sma200"], out["indicators"]["sma200"])

    def test_state_analysis_only_counts_served_rules(self):
        # Aturan menyala tetapi BELUM lolos bar -> tidak masuk active_rules.
        df = state_frame([{"code": "AAA", "mom5d_ge_10": 1, "above_sma200": 1}])
        with mock.patch.object(IDX, "load_state", return_value=df), \
             mock.patch.object(IDX, "_read_json_cached",
                               return_value=study_payload("us", ["di atas SMA200"])):
            out = IDX._analyze_state_market("us", "AAA")
        self.assertEqual([x["key"] for x in out["active_rules"]], ["above_sma200"])

    def test_state_analysis_unknown_ticker_is_404(self):
        df = state_frame([{"code": "AAA"}])
        with mock.patch.object(IDX, "load_state", return_value=df), \
             mock.patch.object(IDX, "_read_json_cached", return_value={}):
            with self.assertRaises(HTTPException) as cm:
                IDX._analyze_state_market("us", "ZZZZ")
        self.assertEqual(cm.exception.status_code, 404)

    def test_crypto_analysis_has_full_indicators(self):
        n = 260
        close = pd.Series(np.linspace(100.0, 200.0, n))
        g = pd.DataFrame({"code": "BTC", "date": pd.date_range("2025-01-01", periods=n),
                          "open": close, "high": close * 1.01, "low": close * 0.99,
                          "close": close, "volume": 1_000_000.0})
        with mock.patch.object(IDX, "load_market_panel", return_value=g), \
             mock.patch.object(IDX, "_read_json_cached", return_value={}):
            out = IDX._analyze_crypto("BTC")
        self.assertEqual(out["market"], "crypto")
        for k in ("rsi14", "macd", "macd_signal", "atr_pct", "sma200"):
            self.assertIsNotNone(out["indicators"][k], k)
        # Riwayat untuk grafik: panjang seri harus konsisten dengan daftar tanggal.
        ser = out["series"]
        self.assertEqual(len(ser["dates"]), len(ser["close"]))
        self.assertEqual(set(ser), {"dates", "close", "sma20", "sma50", "sma200"})
        self.assertEqual(len(ser["sma200"]), len(ser["close"]))

    def test_crypto_analysis_unknown_ticker_is_404(self):
        g = pd.DataFrame({"code": "ETH", "date": pd.date_range("2025-01-01", periods=40),
                          "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0,
                          "volume": 1.0})
        with mock.patch.object(IDX, "load_market_panel", return_value=g):
            with self.assertRaises(HTTPException) as cm:
                IDX._analyze_crypto("BTC")
        self.assertEqual(cm.exception.status_code, 404)

    def test_routing_and_unknown_market(self):
        with mock.patch.object(IDX, "_analyze_crypto", return_value={"ok": 1}) as mc:
            self.assertEqual(IDX.markets_analyze("crypto", "btc")["ok"], 1)
            mc.assert_called_once_with("BTC")
        with mock.patch.object(IDX, "_analyze_state_market", return_value={"ok": 2}) as ms:
            IDX.markets_analyze("etf", "spy")
            ms.assert_called_once_with("etf", "SPY")
        with self.assertRaises(HTTPException) as cm:
            IDX.markets_analyze("jp", "AAA")
        self.assertEqual(cm.exception.status_code, 422)


class TestStateOptions(unittest.TestCase):
    """`_state_options` = apa yang AKAN diterima endpoint screener (server-driven)."""

    def test_filter_mode_excludes_control_rules(self):
        study = {"markets": {"etf": {"lolos_bar": [], "rules": []}},
                 "state_rules": SR.as_registry()}
        validated, opts = IDX._state_options(study, "etf")
        self.assertFalse(validated)
        self.assertIn("above_sma200", opts)
        self.assertNotIn("new_low52_control", opts)

    def test_validated_mode_only_lists_bar_passing_rules(self):
        study = {"markets": {"us": {"lolos_bar": ["di atas SMA200"], "rules": []}},
                 "state_rules": SR.as_registry()}
        validated, opts = IDX._state_options(study, "us")
        self.assertTrue(validated)
        self.assertEqual(list(opts), ["above_sma200"])

    def test_unmeasured_market_offers_nothing(self):
        self.assertEqual(IDX._state_options({"markets": {}}, "etf"), (True, {}))

    def test_study_endpoint_exposes_screener_options(self):
        data = {"markets": {"etf": {"lolos_bar": [], "rules": []}},
                "state_rules": SR.as_registry()}
        with mock.patch.object(IDX, "_read_json_cached", return_value=data):
            out = IDX.markets_study()
        self.assertFalse(out["state_screener"]["etf"]["validated"])
        self.assertIn("above_sma200", out["state_screener"]["etf"]["criteria"])


class TestSummaryEtfInstall(unittest.TestCase):

    def test_installs_only_registry_bar_passing_rules(self):
        # Aturan yang lolos bar tetapi BUKAN bagian registry tidak boleh dipasang.
        got = SUM.installed_for_state_market(["di atas SMA200", "aturan karangan"])
        self.assertEqual([c["key"] for c in got], ["above_sma200"])
        self.assertEqual(got[0]["rule"], "di atas SMA200")

    def test_registry_candidate_installs_when_it_passes(self):
        # Kandidat registry (mis. momentum) dipasang bila lolos bar di pasar itu.
        got = SUM.installed_for_state_market(["mom5d>=10%"])
        self.assertEqual([c["key"] for c in got], ["mom5d_ge_10"])

    def test_no_passing_rule_installs_nothing(self):
        self.assertEqual(SUM.installed_for_state_market([]), [])
        self.assertEqual(SUM.installed_for_state_market(None), [])

    def test_us_three_install_in_registry_order(self):
        got = SUM.installed_for_state_market(
            ["pullback di uptrend", "di atas SMA200", "dekat puncak 52m"])
        self.assertEqual([c["key"] for c in got],
                         ["pullback_uptrend", "above_sma200", "near_high52"])


class TestStudyMinValueHonoured(unittest.TestCase):
    """Bug yang pernah terjadi: study.py MENGABAIKAN `--min-value`, sehingga laporan
    diukur dengan ambang default ($1 jt) sementara backtest_screener.py memakai ambang
    yang diminta ($20 jt). Panel sama, mesin ukur sama, tetapi angka tampak "BEDA" —
    yang tampak seperti kerusakan pasar padahal kerusakan WIRING. Uji ini mengunci
    perbaikan itu supaya tidak kembali.
    """

    def test_report_meneruskan_min_value_ke_measure(self):
        S = pd.DataFrame({"code": ["AAA"], "date": [pd.Timestamp("2026-01-02")],
                          "v20": [5_000_000.0]})
        seen: dict = {}

        def fake_measure(sframe, market, signals, min_value_usd=None):
            seen["min_value_usd"] = min_value_usd
            seen["market"] = market
            return pd.DataFrame([{"aturan": "x", "n": 1, "a5": 1.0, "t5": 1.0,
                                  "a20": 1.0, "m20": 1.0, "t20": 3.0,
                                  "net20": 1.0, "p20": "+1.00/+1.00"}])

        with mock.patch.object(ST, "measure", side_effect=fake_measure):
            ST.report(S, "etf", ["x"], benchmark=None, min_value_usd=20_000_000)
        self.assertEqual(seen["min_value_usd"], 20_000_000)

    def test_parse_report_membaca_min_value_dari_kepala(self):
        import tempfile
        head = ("=== ETF · 3600 ticker · 2,890,729 baris · 2021-09-27 -> 2026-09-25 · "
                "biaya 0.3% · return dipotong di +/-100% · min nilai 20,000,000 USD/hari · "
                "pembanding SPY (pasar)\n")
        row = ("mom5d>=10%                          3,498    +0.19   +1.2    "
               "+0.02  +0.24   +0.0    +1.82     -0.77/+0.81  EKOR\n")
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False,
                                         encoding="utf-8") as fh:
            fh.write(head + row)
            path = fh.name
        try:
            rep = SUM.parse_report(path)
        finally:
            os.unlink(path)
        self.assertIsNotNone(rep)
        self.assertEqual(rep["min_value_usd"], 20_000_000.0)
        self.assertEqual(rep["winsor_pct"], 100.0)   # tidak rusak oleh tambahan segmen
        self.assertEqual(rep["rows"], 2890729)

    def test_parse_report_tanpa_saringan_dibaca_sebagai_nol(self):
        import tempfile
        head = ("=== US · 10 ticker · 1,000 baris · 2021-01-01 -> 2026-01-01 · "
                "biaya 0.3% · return dipotong di +/-100% · tanpa saringan likuiditas "
                "· pembanding rata-rata lintas-aset pada tanggal yang sama\n")
        row = ("di atas SMA20                         1,000    +0.10   +2.0    "
               "+0.20  +0.30  +10.0    +0.10     +0.10/+0.30  SEPAKAT\n")
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False,
                                         encoding="utf-8") as fh:
            fh.write(head + row)
            path = fh.name
        try:
            rep = SUM.parse_report(path)
        finally:
            os.unlink(path)
        self.assertIsNotNone(rep)
        self.assertEqual(rep["min_value_usd"], 0.0)


class TestSchemaConsistency(unittest.TestCase):
    """Kunci kolom di export.py dan kandidat di summary.py tidak boleh menyimpang."""

    def test_rule_keys_match_state_columns(self):
        export_keys = [k for k, _ in EXP.US_STATE_RULES]
        self.assertEqual(export_keys, list(STATE_COLS))
        self.assertEqual([c["key"] for c in SUM.STATE_RULE_CANDIDATES], list(STATE_COLS))
        self.assertEqual([c for c in EXP.US_STATE_COLS if c in STATE_COLS],
                         list(STATE_COLS))

    def test_export_rule_names_match_production_rule_names(self):
        # Produksi membaca registry dari api/market_study.json (ditulis summary.py
        # dari state_rules.py). Uji bahwa nama aturan di export == yang dibaca
        # produksi, dan bahwa SEMUA kandidat registry bisa dibaca produksi.
        reg = IDX._state_registry({"state_rules": SR.as_registry()})
        self.assertEqual(sorted(reg), sorted(SR.STATE_KEYS))
        for key, rule in EXP.US_STATE_RULES:
            self.assertEqual(reg[key]["rule"], rule)

    def test_production_falls_back_to_legacy_three_rules(self):
        # Snapshot lama tanpa kunci `state_rules` tetap menyajikan 3 aturan AS.
        self.assertEqual(IDX._state_registry(None),
                         {k: {"rule": v, "desc": IDX.STATE_CRITERIA_FALLBACK[k]}
                          for k, v in IDX.STATE_RULES_FALLBACK.items()})


class TestCommittedArtifacts(unittest.TestCase):
    """Uji pada BERKAS NYATA yang di-commit (bukan payload buatan).

    Registry bisa benar di payload palsu tetapi salah di JSON yang sebenarnya
    dipakai produksi, jadi berkas nyata ikut diperiksa. Bila berkas belum ada
    (mis. checkout bersih sebelum pipeline pernah jalan), ujinya DILEWATI, bukan
    gagal — supaya uji ini tidak bergantung pada artefak yang dihasilkan CI.
    """

    STUDY = os.path.join(ROOT, "api", "market_study.json")

    def test_committed_study_exposes_registry_produksi_baca(self):
        if not os.path.exists(self.STUDY):
            self.skipTest("api/market_study.json belum ada (dibuat pipeline CI)")
        with open(self.STUDY, "r", encoding="utf-8") as fh:
            study = json.load(fh)
        reg = IDX._state_registry(study)
        self.assertEqual(sorted(reg), sorted(SR.STATE_KEYS))
        for key, rule in EXP.US_STATE_RULES:
            self.assertEqual(reg[key]["rule"], rule)
        # `lolos_bar` tiap pasar harus berupa nama aturan yang ada di registry.
        known = set(SR.RULE_NAMES.values())
        for m in ("us", "etf"):
            lolos = ((study.get("markets") or {}).get(m) or {}).get("lolos_bar") or []
            self.assertTrue(set(lolos) <= known,
                            f"aturan lolos_bar {m} di luar registry: {set(lolos) - known}")

    def test_committed_state_csv_punya_semua_kolom_aturan(self):
        for m in ("us", "etf"):
            path = os.path.join(ROOT, "api", f"market_{m}_state.csv")
            if not os.path.exists(path):
                self.skipTest(f"{path} belum ada (dibuat pipeline CI)")
            cols = pd.read_csv(path, nrows=1).columns
            missing = [k for k in SR.STATE_KEYS if k not in cols]
            self.assertEqual(missing, [], f"kolom aturan hilang di keadaan {m}: {missing}")


class TestEtfUniverse(unittest.TestCase):

    def test_keeps_only_etf_flagged_rows(self):
        rows = [{"symbol": "SPY", "etf": True}, {"symbol": "AAPL", "etf": False},
                {"symbol": "QQQ", "etf": True}]
        with mock.patch.object(UNI, "us_universe", return_value=rows):
            got = UNI.etf_universe()
        self.assertEqual([r["symbol"] for r in got], ["SPY", "QQQ"])

    def test_benchmark_for_etf_is_set(self):
        self.assertIn("etf", UNI.BENCHMARK)

    def test_equity_only_buang_etf_non_ekuitas(self):
        rows = [
            {"symbol": "SPY", "name": "SPDR S&P 500 ETF Trust", "etf": True},
            {"symbol": "QQQ", "name": "Invesco QQQ Trust", "etf": True},
            {"symbol": "TQQQ", "name": "ProShares UltraPro QQQ 3x", "etf": True},
            {"symbol": "SHV", "name": "iShares Short Treasury Bond ETF", "etf": True},
            {"symbol": "GLD", "name": "SPDR Gold Shares", "etf": True},
            {"symbol": "UUP", "name": "Invesco DB US Dollar Index Bullish Fund", "etf": True},
            {"symbol": "AAPL", "name": "Apple Inc.", "etf": False},
        ]
        with mock.patch.object(UNI, "us_universe", return_value=rows):
            eq = {r["symbol"] for r in UNI.etf_universe()}
            full = {r["symbol"] for r in UNI.etf_universe(equity_only=False)}
        self.assertEqual(eq, {"SPY", "QQQ"})
        self.assertEqual(full, {"SPY", "QQQ", "TQQQ", "SHV", "GLD", "UUP"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
