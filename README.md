# central-bank-rates

Small, dependency-free command-line tools for fetching official reference
exchange rates published by central banks.

Two scripts are included:

- **`ecb-rate.py`** — daily and monthly reference rates from the
  [ECB Data Portal](https://data.ecb.europa.eu/), quoted as CCY per EUR.
  Supports single dates and arithmetic averages over daily or monthly periods.
- **`mnb-rate.py`** — daily rates from the
  [Magyar Nemzeti Bank (MNB)](https://www.mnb.hu/arfolyam-lekerdezes),
  quoted as HUF per unit of the requested currency.

Both tools use only the Python standard library (Python 3.11+), make a single
HTTPS request per invocation, and print results with 4 decimal precision.
They deliberately fail loudly on future dates, unsupported currencies,
missing observations, and network/API errors.

## Usage

### ECB reference rates

    # Single date (falls back to the latest previous observation, max 31 days back)
    ./ecb-rate.py 2026-09-23 --from-currency USD

    # Average over a daily period
    ./ecb-rate.py --from 2026-01-01 --to 2026-06-30 --from-currency USD

    # Average over whole months (monthly data is published after month end)
    ./ecb-rate.py --from 2026-01 --to 2026-06 --from-currency USD

The mode (daily vs. monthly) is inferred from the format of `--from`/`--to`:
`YYYY-MM-DD` means daily, `YYYY-MM` means monthly.

### MNB exchange rates

    # Rate for a specific date
    ./mnb-rate.py 2026-09-23 --from-currency EUR

    # Currencies quoted per 100 units are reported as such
    ./mnb-rate.py 2026-09-23 --from-currency JPY

MNB quotes most currencies as HUF per 1 unit, but some (e.g. JPY) per 100
units; the published quotation is reported as-is.

## Behavior notes

- `--from-currency` (3-letter ISO code) is required in both scripts.
- If no observation exists for the exact requested date, the latest previous
  observation within the last 31 days is used and explicitly reported
  (`Status: unavailable` + `Using: <date>`).
- Future dates are rejected; monthly ECB averages additionally reject the
  current or future months.
- Everything is computed with `Decimal` — no floating-point rounding of
  published rates.

## Requirements

- Python 3.11+ (standard library only)
- Internet access to `data-api.ecb.europa.eu` and `www.mnb.hu`
