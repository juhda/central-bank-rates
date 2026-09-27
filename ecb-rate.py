#!/usr/bin/env python3
"""
Fetch ECB reference exchange rates from the ECB Data Portal.

Examples:
  ecb-rate.py 2026-09-23 --from-currency USD
  ecb-rate.py --from 2026-01-01 --to 2026-06-30 --from-currency USD
  ecb-rate.py --from 2026-01 --to 2026-06 --from-currency USD

The ECB series used by this script is the direct bilateral reference-rate
series, quoted as TARGET_CURRENCY per SOURCE_CURRENCY when the ECB
publishes that direction. For EUR-based ECB reference rates, the source
currency is EUR and the target currency is the requested currency.

The script requires --from-currency (3-letter ISO code) and deliberately
fails for future dates, the current or future months, unsupported currencies,
missing period observations, malformed ranges, or network/API errors.

For a single requested date, if no ECB observation exists, the latest
previous available observation (within the last 31 days) is used and
explicitly reported. Rates and averages are printed with 4 decimal precision.

The mode (daily or monthly) is inferred from the format of --from and --to:
YYYY-MM-DD implies daily, YYYY-MM implies monthly.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Iterable


API_BASE = "https://data-api.ecb.europa.eu/service/data"
USER_AGENT = "ecb-rate-cli/1.0"
FALLBACK_WINDOW = timedelta(days=31)


@dataclass(frozen=True)
class Observation:
    period: str
    value: Decimal


class EcbError(RuntimeError):
    pass


def parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"invalid date {value!r}; expected YYYY-MM-DD"
        ) from exc


def parse_month(value: str) -> tuple[int, int]:
    try:
        parsed = datetime.strptime(value, "%Y-%m")
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"invalid month {value!r}; expected YYYY-MM"
        ) from exc
    return parsed.year, parsed.month


def infer_frequency(start: str, end: str) -> str:
    """Infer frequency from start/end format: YYYY-MM-DD -> D, YYYY-MM -> M."""
    try:
        # Check if it looks like a date (YYYY-MM-DD)
        date.fromisoformat(start)
        date.fromisoformat(end)
        return "D"
    except ValueError:
        pass

    try:
        # Check if it looks like a month (YYYY-MM)
        datetime.strptime(start, "%Y-%m")
        datetime.strptime(end, "%Y-%m")
        return "M"
    except ValueError:
        pass

    raise argparse.ArgumentTypeError(
        f"cannot infer frequency from {start!r} and {end!r}; "
        f"use YYYY-MM-DD for daily or YYYY-MM for monthly"
    )


def next_month(year: int, month: int) -> tuple[int, int]:
    return (year + 1, 1) if month == 12 else (year, month + 1)


def month_strings(start: tuple[int, int], end: tuple[int, int]) -> list[str]:
    result = []
    y, m = start
    while (y, m) <= end:
        result.append(f"{y:04d}-{m:02d}")
        y, m = next_month(y, m)
    return result


def build_series(currency: str, frequency: str) -> str:
    if len(currency) != 3 or not currency.isalpha():
        raise EcbError(
            f"invalid currency code {currency!r}; must be a 3-letter ISO code"
        )
    return f"EXR/{frequency}.{currency}.EUR.SP00.A"


def fetch_series(
    series: str,
    start: str | None = None,
    end: str | None = None,
) -> list[Observation]:
    params = {
        "format": "jsondata",
    }
    if start:
        params["startPeriod"] = start
    if end:
        params["endPeriod"] = end

    url = f"{API_BASE}/{series}?{urllib.parse.urlencode(params)}"

    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.sdmx.data+json",
            "User-Agent": USER_AGENT,
        },
    )

    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            payload = json.load(response)
    except urllib.error.HTTPError as exc:
        raise EcbError(f"ECB API returned HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise EcbError(f"could not reach ECB API: {exc.reason}") from exc
    except TimeoutError as exc:
        raise EcbError("ECB API request timed out") from exc
    except json.JSONDecodeError as exc:
        raise EcbError("ECB API returned invalid JSON") from exc

    try:
        series_data = payload["dataSets"][0]["series"]
        series_key = next(iter(series_data))
        observations = series_data[series_key]["observations"]
        periods = payload["structure"]["dimensions"]["observation"][0]["values"]
    except (KeyError, IndexError, TypeError, StopIteration) as exc:
        raise EcbError("ECB API response has an unexpected structure") from exc

    result: list[Observation] = []
    for index, value in observations.items():
        try:
            period = periods[int(index)]["id"]
            raw_value = value[0]
            # Skip observations where the value is missing/None
            if raw_value is None:
                continue
            decimal_value = Decimal(str(raw_value))
        except (KeyError, IndexError, TypeError, ValueError, InvalidOperation) as exc:
            raise EcbError(
                "ECB API response contains an invalid observation") from exc
        result.append(Observation(period, decimal_value))

    return sorted(result, key=lambda item: item.period)


def format_rate(value: Decimal) -> str:
    return f"{value:.4f}"


def average(values: Iterable[Decimal]) -> Decimal:
    values = list(values)
    if not values:
        raise EcbError("no observations available")
    return sum(values, Decimal(0)) / Decimal(len(values))


def print_single(currency: str, requested: date) -> None:
    series = build_series(currency, "D")
    # Fetch observations from up to one month before the requested date to support
    # fallback to the latest previous observation when the exact date is unavailable.
    start_date = requested - FALLBACK_WINDOW
    observations = fetch_series(
        series, start=start_date.isoformat(), end=requested.isoformat())

    if not observations:
        raise EcbError(
            f"no ECB observation exists on or before {requested.isoformat()}"
        )

    exact = next(
        (obs for obs in observations if obs.period == requested.isoformat()),
        None,
    )
    selected = exact or observations[-1]

    print("ECB reference exchange rate")
    print(f"Source:       ECB Data Portal")
    print(f"Series:       {series}")
    print(f"Currency:     {currency}/EUR")
    print(f"Requested:    {requested.isoformat()}")

    if exact:
        print("Status:       available")
    else:
        print("Status:       unavailable")
        print(f"Using:        {selected.period} (latest previous observation)")

    print(f"Rate:         {format_rate(selected.value)} {currency}/EUR")


def print_daily_average(
    currency: str,
    start: date,
    end: date,
) -> None:
    if start > end:
        raise EcbError("start date must not be after end date")

    series = build_series(currency, "D")
    observations = fetch_series(series, start.isoformat(), end.isoformat())

    if not observations:
        raise EcbError("no ECB observations exist in the requested period")

    print("ECB reference exchange rate")
    print(f"Source:       ECB Data Portal")
    print(f"Mode:         daily")
    print(f"Series:       {series}")
    print(f"Currency:     {currency}/EUR")
    print(f"Period:       {start.isoformat()} .. {end.isoformat()}")
    print("Method:       arithmetic mean of available daily observations")
    print(f"Observations: {len(observations)}")
    print(
        f"Average:      {format_rate(average(o.value for o in observations))} {currency}/EUR")


def print_monthly_average(
    currency: str,
    start: tuple[int, int],
    end: tuple[int, int],
) -> None:
    if start > end:
        raise EcbError("start month must not be after end month")

    series = build_series(currency, "M")
    expected = month_strings(start, end)

    observations = fetch_series(series, expected[0], expected[-1])
    by_period = {obs.period: obs for obs in observations}
    missing = [period for period in expected if period not in by_period]

    if missing:
        raise EcbError(
            "missing ECB monthly observations: " + ", ".join(missing)
        )

    selected = [by_period[period] for period in expected]

    print("ECB reference exchange rate")
    print(f"Source:       ECB Data Portal")
    print(f"Mode:         monthly")
    print(f"Series:       {series}")
    print(f"Currency:     {currency}/EUR")
    print(f"Period:       {expected[0]} .. {expected[-1]}")
    print("Method:       arithmetic mean of monthly observations")
    print(f"Observations: {len(selected)}")
    print(
        f"Average:      {format_rate(average(o.value for o in selected))} {currency}/EUR")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Fetch ECB reference exchange rates and calculate averages."
    )
    parser.add_argument(
        "date",
        nargs="?",
        type=parse_date,
        help="single observation date (YYYY-MM-DD)",
    )
    parser.add_argument(
        "--from",
        dest="start",
        help="period start: YYYY-MM-DD for daily, YYYY-MM for monthly",
    )
    parser.add_argument(
        "--to",
        dest="end",
        help="period end: YYYY-MM-DD for daily, YYYY-MM for monthly",
    )
    parser.add_argument(
        "--from-currency",
        required=True,
        metavar="CCY",
        help="ECB reference currency quoted against EUR",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    currency = args.from_currency.upper()
    today = date.today()

    if args.date and (args.start or args.end):
        parser.error("single-date mode cannot be combined with period options")

    if args.start is None or args.end is None:
        if not args.date:
            parser.error("--from and --to are required in period mode")
    else:
        if args.date:
            parser.error("--from/--to cannot be combined with a single date")

    # Infer frequency from --from/--to format
    frequency = None
    if not args.date:
        try:
            frequency = infer_frequency(args.start, args.end)
        except argparse.ArgumentTypeError as exc:
            parser.error(str(exc))

    try:
        if args.date:
            if args.date > today:
                parser.error(f"date {args.date} is in the future")
            print_single(currency, args.date)
        elif frequency == "D":
            start = parse_date(args.start)
            end = parse_date(args.end)
            if end > today:
                parser.error("period contains future dates")
            print_daily_average(currency, start, end)
        else:
            start = parse_month(args.start)
            end = parse_month(args.end)
            # Reject current or future months (monthly data is not available until month end)
            if end >= (today.year, today.month):
                parser.error("period contains current or future months")
            print_monthly_average(currency, start, end)
        return 0
    except EcbError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
