import unittest
from datetime import datetime, timedelta, timezone

from ai_mvp.risk import portfolio_risk


def timestamp(seconds_ago=0):
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds_ago)).isoformat()


def snapshot(positions, **overrides):
    return {
        "state": "ok", "as_of": timestamp(), "positions": positions,
        "open_orders": [], "truncated": False, **overrides,
    }


def market(symbol, price="100", **overrides):
    return {
        "status": "exact", "match_count": 1,
        "matches": [{"symbol": symbol, "price": price, "as_of": timestamp(), "source": "test", **overrides}],
    }


class PortfolioRiskTests(unittest.TestCase):
    def test_signed_valuations_and_concentration_use_gross_exposure(self):
        account = snapshot([
            {"symbol": "BTC", "quantity": "2"},
            {"symbol": "BTC/USDT", "quantity": "-1"},
            {"symbol": "USDT", "quantity": "100"},
        ])
        calls = []

        def lookup(symbol):
            calls.append(symbol)
            return market(symbol)

        result = portfolio_risk(account, "usdt", lookup, max_age=60)
        self.assertEqual(result["state"], "ok")
        self.assertEqual(calls, ["BTC/USDT"])
        self.assertEqual(result["summary"]["net_value"], "200")
        self.assertEqual(result["summary"]["gross_exposure"], "400")
        self.assertEqual(result["summary"]["observed_short_exposure"], "100")
        self.assertEqual(result["summary"]["largest_asset"]["gross_concentration_pct"], "75")
        self.assertEqual(result["positions"][1]["market_value"], "-100")
        self.assertEqual(result["positions"][0]["quote"]["source"], "test")

    def test_stale_missing_ambiguous_and_mismatched_quotes_do_not_become_values(self):
        results = {
            "BTC/USDT": market("BTC/USDT", as_of=timestamp(300)),
            "ETH/USDT": {"status": "not_found", "matches": []},
            "SOL/USDT": {"status": "ambiguous", "matches": [market("SOL/USDT")["matches"][0]]},
            "ADA/USDT": market("ADA/USD"),
        }
        account = snapshot([{"symbol": symbol, "quantity": "1"} for symbol in ("BTC", "ETH", "SOL", "ADA")])
        result = portfolio_risk(account, "USDT", results.__getitem__, max_age=60)
        self.assertEqual(result["state"], "partial")
        self.assertEqual(result["reasons"], ["quote_stale", "missing_exact_quote", "ambiguous_quote", "quote_symbol_mismatch"])
        self.assertIsNone(result["summary"]["gross_exposure"])
        self.assertIsNone(result["summary"]["largest_asset"])
        self.assertEqual(result["summary"]["valued_positions"], 0)
        self.assertEqual(result["positions"][0]["quote"]["freshness"], "stale")
        self.assertNotIn("price", result["positions"][0]["quote"])

    def test_snapshot_freshness_and_truncation_prevent_whole_account_totals(self):
        for overrides, reason in (
            ({"as_of": timestamp(300)}, "account_stale"),
            ({"as_of": timestamp(-60)}, "account_future"),
            ({"as_of": "2026-11-18T10:00:00"}, "account_invalid"),
            ({"truncated": True}, "account_truncated"),
        ):
            with self.subTest(reason=reason):
                result = portfolio_risk(snapshot([{"symbol": "USDT", "quantity": "10"}], **overrides), "USDT", market, max_age=60)
                self.assertEqual(result["state"], "partial")
                self.assertIn(reason, result["reasons"])
                self.assertEqual(result["summary"]["observed_net_value"], "10")
                self.assertIsNone(result["summary"]["net_value"])

    def test_mismatched_counter_asset_and_zero_quantity_do_not_trigger_lookups(self):
        calls = []

        def lookup(symbol):
            calls.append(symbol)
            return market(symbol)

        account = snapshot([
            {"symbol": "BTC/EUR", "quantity": "1"},
            {"symbol": "ETH/USDT", "quantity": "0"},
            {"symbol": "USDT", "quantity": "5.50"},
        ])
        result = portfolio_risk(account, "USDT", lookup, max_age=60)
        self.assertEqual(calls, [])
        self.assertEqual(result["reasons"], ["unsupported_quote_currency"])
        self.assertEqual(result["summary"]["observed_net_value"], "5.5")

    def test_at_most_ten_unique_quotes_are_looked_up_even_after_errors(self):
        calls = []

        def lookup(symbol):
            calls.append(symbol)
            raise OSError("unavailable")

        positions = [{"symbol": f"ASSET{index}", "quantity": "1"} for index in range(12)]
        positions += [positions[0]]
        result = portfolio_risk(snapshot(positions), "USDT", lookup, max_age=60)
        self.assertEqual(len(calls), 10)
        self.assertEqual(len(set(calls)), 10)
        self.assertIn("quote_lookup_limit", result["reasons"])
        self.assertEqual(result["summary"]["quote_lookups"], 10)

    def test_invalid_and_extreme_decimals_cannot_enter_totals(self):
        positions = [
            {"symbol": "USDT", "quantity": value}
            for value in ("NaN", "Infinity", "1e9999999999999999999999", "1e-999999", True)
        ]
        positions.append({"symbol": "BTC", "quantity": "1"})
        result = portfolio_risk(snapshot(positions), "USDT", lambda symbol: market(symbol, "Infinity"), max_age=60)
        self.assertEqual(result["state"], "partial")
        self.assertEqual(result["summary"]["observed_net_value"], "0")
        self.assertEqual(result["summary"]["valued_positions"], 0)

    def test_account_unavailability_remains_distinguishable_from_empty_account(self):
        for state in ("not_configured", "unavailable"):
            result = portfolio_risk({"state": state}, "USDT", market, max_age=60)
            self.assertEqual(result["state"], state)
            self.assertIsNone(result["summary"])
        result = portfolio_risk(snapshot([]), "USDT", market, max_age=60)
        self.assertEqual(result["state"], "ok")
        self.assertEqual(result["summary"]["net_value"], "0")
        self.assertIsNone(result["summary"]["largest_asset"])


if __name__ == "__main__":
    unittest.main()
