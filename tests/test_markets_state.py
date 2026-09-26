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

import pandas as pd
from fastapi import HTTPException

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "api"))                  # penting: `index`
sys.path.insert(0, os.path.join(ROOT, "research", "markets"))   # export/summary/universe

import export as EXP        # noqa: E402
import index as IDX         # noqa: E402
import state_rules as SR    # noqa: E402
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
