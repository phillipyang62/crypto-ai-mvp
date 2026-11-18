"""Read-only valuation of supported account positions using fresh exact quotes."""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, localcontext

from .providers import QUOTE_CLOCK_SKEW_SECONDS


_ASSET = re.compile(r"[A-Z0-9]{1,20}")
_PAIR = re.compile(r"([A-Z0-9]{1,20})[/_-]([A-Z0-9]{1,20})")
_MAX_LOOKUPS = 10
_MAX_ROWS = 50
_MAX_NUMBER = Decimal("1e30")
_MIN_NUMBER = Decimal("1e-30")


def _number(value: object, *, positive: bool = False) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        raise ValueError("invalid decimal")
    raw = str(value)
    if len(raw) > 80:
        raise ValueError("decimal exceeds the length limit")
    try:
        number = Decimal(raw)
    except InvalidOperation as error:
        raise ValueError("invalid decimal") from error
    if (
        not number.is_finite()
        or abs(number) > _MAX_NUMBER
        or (number != 0 and abs(number) < _MIN_NUMBER)
        or (positive and number <= 0)
    ):
        raise ValueError("decimal exceeds the supported range")
    return number


def _string(number: Decimal) -> str:
    if number == 0:
        return "0"
    text = format(number, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def _freshness(value: object, now: datetime, max_age: int) -> str:
    if not isinstance(value, str) or len(value) > 40:
        return "invalid"
    try:
        timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if timestamp.tzinfo is None:
            return "invalid"
        age = (now - timestamp).total_seconds()
    except (ValueError, OverflowError):
        return "invalid"
    if age < -QUOTE_CLOCK_SKEW_SECONDS:
        return "future"
    return "stale" if age > max_age else "fresh"


def _market_quote(result: object, symbol: str, max_age: int) -> tuple[dict | None, str | None]:
    if not isinstance(result, dict) or result.get("status") == "unavailable":
        return None, "quote_unavailable"
    if result.get("status") == "ambiguous":
        return None, "ambiguous_quote"
    matches = result.get("matches")
    if result.get("status") != "exact" or not isinstance(matches, list) or len(matches) != 1:
        return None, "missing_exact_quote"
    record = matches[0]
    if not isinstance(record, dict) or record.get("symbol") != symbol:
        return None, "quote_symbol_mismatch"
    freshness = _freshness(record.get("as_of"), datetime.now(timezone.utc), max_age)
    evidence = {
        "symbol": symbol,
        "as_of": record.get("as_of"),
        "source": record.get("source"),
        "freshness": freshness,
    }
    if freshness != "fresh":
        return evidence, f"quote_{freshness}"
    try:
        price = _number(record.get("price"), positive=True)
    except ValueError:
        return None, "invalid_quote_price"
    return {**evidence, "price": _string(price)}, None


def portfolio_risk(
    account: dict,
    quote_currency: str,
    lookup_market: Callable[[str], dict],
    *,
    max_age: int,
) -> dict:
    """Value at most 50 positions with at most 10 distinct noncash quote lookups.

    Whole-account metrics require a fresh, complete snapshot and every position
    valued. Observed totals remain available when the result is partial.
    """
    if not isinstance(quote_currency, str) or not _ASSET.fullmatch(quote_currency.upper()):
        raise ValueError("quote_currency must be an asset code of 1 to 20 ASCII letters or digits")
    if type(max_age) is not int or max_age < 1:
        raise ValueError("max_age must be a positive integer")
    currency = quote_currency.upper()
    response = {
        "state": "unavailable",
        "quote_currency": currency,
        "scope": "positions",
        "account_as_of": None,
        "account_freshness": "unknown",
        "reasons": [],
        "positions": [],
        "summary": None,
    }
    if not isinstance(account, dict) or account.get("state") != "ok":
        state = account.get("state") if isinstance(account, dict) else None
        response["state"] = "not_configured" if state == "not_configured" else "unavailable"
        response["reasons"] = ["account_not_configured" if state == "not_configured" else "account_unavailable"]
        return response
    rows = account.get("positions")
    orders = account.get("open_orders")
    if not isinstance(rows, list) or not isinstance(orders, list):
        response["reasons"] = ["invalid_account_snapshot"]
        return response

    now = datetime.now(timezone.utc)
    freshness = _freshness(account.get("as_of"), now, max_age)
    response["account_as_of"] = account.get("as_of")
    response["account_freshness"] = freshness
    response["evaluated_at"] = now.isoformat()
    reasons = response["reasons"]
    if freshness != "fresh":
        reasons.append(f"account_{freshness}")
    if account.get("truncated", False) or max(len(rows), len(orders)) > _MAX_ROWS:
        reasons.append("account_truncated")

    quotes = {}
    values = []
    asset_exposures = {}
    with localcontext() as context:
        context.prec = 200
        for row in rows[:_MAX_ROWS]:
            item = {"symbol": row.get("symbol") if isinstance(row, dict) else None, "state": "unvalued"}
            response["positions"].append(item)
            try:
                raw_symbol = row["symbol"].upper()
                quantity = _number(row["quantity"])
                pair = _PAIR.fullmatch(raw_symbol)
                if pair:
                    base, counter = pair.groups()
                    if counter != currency:
                        raise ValueError("unsupported_quote_currency")
                elif _ASSET.fullmatch(raw_symbol):
                    base = raw_symbol
                else:
                    raise ValueError("unsupported_symbol")
            except (AttributeError, KeyError, TypeError, ValueError) as error:
                item["reason"] = str(error) if str(error) in {"unsupported_quote_currency", "unsupported_symbol"} else "invalid_position"
                continue

            symbol = f"{base}/{currency}"
            item.update({"base": base, "quantity": _string(quantity), "valuation_symbol": symbol})
            if base == currency or quantity == 0:
                value = quantity
                item["valuation_basis"] = "unit_currency" if base == currency else "zero_quantity"
            else:
                if symbol not in quotes:
                    if len(quotes) >= _MAX_LOOKUPS:
                        item["reason"] = "quote_lookup_limit"
                        continue
                    try:
                        quotes[symbol] = _market_quote(lookup_market(symbol), symbol, max_age)
                    except (LookupError, OSError, ValueError):
                        quotes[symbol] = (None, "quote_unavailable")
                evidence, reason = quotes[symbol]
                if evidence is not None:
                    item["quote"] = evidence.copy()
                if reason:
                    item["reason"] = reason
                    continue
                value = quantity * Decimal(evidence["price"])
                item["valuation_basis"] = "market_quote"
            item.update({"state": "valued", "market_value": _string(value), "gross_exposure": _string(abs(value))})
            values.append(value)
            asset_exposures[base] = asset_exposures.get(base, Decimal(0)) + abs(value)

        for item in response["positions"]:
            if item["state"] != "valued" and item["reason"] not in reasons:
                reasons.append(item["reason"])
        complete = not reasons and len(values) == len(rows)
        net = sum(values, Decimal(0))
        gross = sum((abs(value) for value in values), Decimal(0))
        long = sum((value for value in values if value > 0), Decimal(0))
        short = sum((-value for value in values if value < 0), Decimal(0))
        largest = None
        if complete and gross:
            base = max(sorted(asset_exposures), key=asset_exposures.get)
            largest = {
                "symbol": base,
                "gross_exposure": _string(asset_exposures[base]),
                "gross_concentration_pct": _string((asset_exposures[base] / gross * 100).quantize(Decimal("0.01"))),
            }
        response["state"] = "ok" if complete else "partial"
        response["summary"] = {
            "complete": complete,
            "total_positions": len(rows),
            "valued_positions": len(values),
            "open_order_count": len(orders),
            "quote_lookups": len(quotes),
            "observed_net_value": _string(net),
            "observed_gross_exposure": _string(gross),
            "observed_long_exposure": _string(long),
            "observed_short_exposure": _string(short),
            "net_value": _string(net) if complete else None,
            "gross_exposure": _string(gross) if complete else None,
            "largest_asset": largest,
        }
    return response
