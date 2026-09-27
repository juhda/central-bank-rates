#!/usr/bin/env python3
"""
Fetch MNB (Magyar Nemzeti Bank) daily exchange rates from the official
MNB website.

Examples:
  mnb-rate.py 2026-09-23 --from-currency EUR
  mnb-rate.py 2026-09-23 --from-currency JPY

The MNB data source used by this script is the official per-currency
exchange-rate query of the MNB website ("devizánkénti lekérdezés" on
https://www.mnb.hu/arfolyam-lekerdezes). A single HTTPS GET request
to the MNB website returns a small HTML table listing the quoted
currency, its quotation unit, and one observation (date and rate) per
published day. Missing observations appear in the table as "-".

MNB publishes daily exchange rates of foreign currencies against the
HUF, quoted as HUF per 1 unit of the foreign currency, except for a
few currencies (e.g., JPY) which MNB quotes per 100 units. The
published quotation is reported as-is: rates quoted over multiple
units are printed as "X HUF / N CCY".

The script requires --from-currency (3-letter ISO code) and deliberately
fails for future dates, unsupported currencies, missing observations,
or network/API errors.

For a single requested date, if no MNB observation exists, the latest
previous available observation (within the last 31 days) is used and
explicitly reported. Rates are printed with 4 decimal precision.
"""

from __future__ import annotations

import argparse
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

QUERY_URL = "https://www.mnb.hu/arfolyam-tablazat"
USER_AGENT = "mnb-rate-cli/1.0"
FALLBACK_WINDOW = timedelta(days=31)

HUNGARIAN_MONTHS = {
    "január": 1,
    "február": 2,
    "március": 3,
    "április": 4,
    "május": 5,
    "június": 6,
    "július": 7,
    "augusztus": 8,
    "szeptember": 9,
    "október": 10,
    "november": 11,
    "december": 12,
}

HEADER_PATTERN = re.compile(r'<th class="rotate">([^<]*)</th>')
ROW_PATTERN = re.compile(
    r'<td class="rotate">([^<]*)</td>\s*<td class="rotate">([^<]*)</td>'
)
DATE_PATTERN = re.compile(r"(\d{4})\. ([^\s]+) (\d{1,2})\.")


@dataclass(frozen=True)
class Observation:
    period: date
    value: Decimal  # HUF per `unit` units of the currency, as published
    unit: int


class MnbError(RuntimeError):
    pass


def parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"invalid date {value!r}; expected YYYY-MM-DD"
        ) from exc


def build_query_url(currency: str, start: date, end: date) -> str:
    # The form action ends in a bare "query" parameter; the server rejects
    # the request with HTTP 404 when it is submitted as "query=".
    params = urllib.parse.urlencode({
        "deviza": "rbCurrencySelect",
        "devizaSelected": currency,
        "datefrom": start.strftime("%Y.%m.%d."),
        "datetill": end.strftime("%Y.%m.%d."),
        "order": "1",
    })
    return f"{QUERY_URL}?query&{params}"


def fetch_page(url: str) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})

    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        raise MnbError(f"MNB returned HTTP {exc.code} for {url}") from exc
    except urllib.error.URLError as exc:
        raise MnbError(f"could not reach MNB website: {exc.reason}") from exc
    except TimeoutError as exc:
        raise MnbError("MNB request timed out") from exc


def parse_table(page: str, currency: str) -> list[Observation]:
    headers = [header.strip() for header in HEADER_PATTERN.findall(page)]
    if not headers or headers[0] != currency:
        raise MnbError(f"MNB does not publish {currency} rates")

    try:
        unit = int(headers[2])
    except (IndexError, ValueError) as exc:
        raise MnbError(
            "MNB response contains an invalid quotation unit") from exc

    result: list[Observation] = []
    for day_text, rate_text in ROW_PATTERN.findall(page):
        match = DATE_PATTERN.match(day_text.strip())
        if match is None:
            raise MnbError("MNB response contains an invalid date")
        try:
            period = date(
                int(match.group(1)),
                HUNGARIAN_MONTHS[match.group(2)],
                int(match.group(3)),
            )
        except (KeyError, ValueError) as exc:
            raise MnbError("MNB response contains an invalid date") from exc

        rate_text = rate_text.strip()
        if not rate_text or rate_text == "-":
            continue
        try:
            value = Decimal(rate_text.replace(",", "."))
        except InvalidOperation as exc:
            raise MnbError("MNB response contains an invalid rate") from exc
        result.append(Observation(period, value, unit))

    return sorted(result, key=lambda item: item.period)


def format_rate(value: Decimal) -> str:
    return f"{value:.4f}"


def print_single(currency: str, requested: date) -> None:
    # Fetch observations from up to one month before the requested date to support
    # fallback to the latest previous observation when the exact date is unavailable.
    start_date = requested - FALLBACK_WINDOW
    url = build_query_url(currency, start_date, requested)
    observations = parse_table(fetch_page(url), currency)

    exact = next(
        (obs for obs in observations if obs.period == requested),
        None,
    )
    selected = exact or (observations[-1] if observations else None)

    if selected is None:
        raise MnbError(
            f"no MNB {currency} observation exists on or before "
            f"{requested.isoformat()}"
        )

    print("MNB exchange rate")
    print("Source:       MNB (Magyar Nemzeti Bank) website")
    print(f"Query:        {url}")
    print(f"Currency:     HUF/{currency}")
    print(f"Requested:    {requested.isoformat()}")

    if exact:
        print("Status:       available")
    else:
        print("Status:       unavailable")
        print(f"Using:        {selected.period.isoformat()} (latest previous observation)")

    if selected.unit == 1:
        print(f"Rate:         {format_rate(selected.value)} HUF/{currency}")
    else:
        print(
            f"Rate:         {format_rate(selected.value)} HUF / "
            f"{selected.unit} {currency}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Fetch MNB daily exchange rates against the HUF."
    )
    parser.add_argument(
        "date",
        type=parse_date,
        help="observation date (YYYY-MM-DD)",
    )
    parser.add_argument(
        "--from-currency",
        required=True,
        metavar="CCY",
        help="currency quoted against HUF (3-letter ISO code)",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    currency = args.from_currency.upper()
    if len(currency) != 3 or not currency.isalpha():
        parser.error(f"invalid currency code {currency!r}; must be a 3-letter ISO code")

    if args.date > date.today():
        parser.error(f"date {args.date} is in the future")

    try:
        print_single(currency, args.date)
        return 0
    except MnbError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
