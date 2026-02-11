# Neon Usage

A single-file Python script that pulls your [Neon](https://neon.tech) project metrics from the **v2 Consumption History API** and turns them into a readable usage & cost report — including a month-to-date summary and an end-of-month forecast.

> **Heads-up:** This assumes the standard Neon **Launch** plan pricing (2026). Pricing constants are defined at the top of the script and can be adjusted if your plan differs. The forecast is a linear extrapolation — treat it as a rough guesstimate, not a guarantee.

## Features

- **Month-to-date costs** — compute, storage, instant restore, data transfer, extra branches
- **Per-project cost breakdown** — see per-project dollar costs for each metric, with transfer cost split proportionally (the 100 GB free tier is org-wide)
- **Deleted project tracking** — projects deleted mid-month still show their incurred costs (tagged `[deleted]`); their cumulative costs are fixed in the forecast while point-in-time costs drop to zero
- **End-of-month forecast** — linear projection based on current usage rate, with smart handling of deleted projects
- **Detailed mode** — split storage into root/child branches, transfer into public/private
- **JSON output** — pipe into `jq`, dashboards, or monitoring scripts
- **Single file, no dependencies** — only uses the Python standard library

## Requirements

- Python 3.8+
- A [Neon](https://neon.tech) account on a **paid plan** (Launch, Scale, Business, or Enterprise)
- A Neon API key ([create one here](https://console.neon.tech/app/settings/api-keys))

## Installation

```bash
git clone https://github.com/klumz33/Neon_usage.git
cd Neon_usage
chmod +x neon_usage.py
```

That's it — no `pip install`, no virtual environment needed.

## Configuration

The script needs your **Neon API key** and (optionally) your **Organization ID**. There are three ways to provide them, listed by priority (highest first):

| Method | API Key | Org ID |
|---|---|---|
| CLI argument | — | `--org-id org-...` |
| Environment variable | `NEON_API_KEY` | `ORG_ID` |
| `.env` file (next to script) | `NEON_API_KEY=...` | `ORG_ID=...` |

### Option A: Environment variables

```bash
export NEON_API_KEY="your-api-key"
export ORG_ID="org-your-org-id"       # optional
```

### Option B: `.env` file

Create a `.env` file in the same directory as the script:

```
NEON_API_KEY=your-api-key
ORG_ID=org-your-org-id
```

The `.env` file is listed in `.gitignore` and will **never** override existing environment variables.

## Usage

```bash
# Default report (month-to-date + forecast)
python3 neon_usage.py

# Detailed breakdown (root/child storage, public/private transfer)
python3 neon_usage.py --detail

# JSON output (for scripting / dashboards)
python3 neon_usage.py --json

# Hourly granularity
python3 neon_usage.py --granularity hourly

# Override org ID from CLI
python3 neon_usage.py --org-id org-example-12345678

# Only show active projects (exclude deleted)
python3 neon_usage.py --active-only

# Combine flags
python3 neon_usage.py --detail --granularity monthly --json
```

### Options

```
--org-id ORG_ID       Organization ID (default: $ORG_ID or .env)
--detail              Show root/child storage & public/private transfer split
--active-only         Exclude deleted projects from report and forecast
--granularity         hourly | daily (default) | monthly
--json                Output as JSON instead of a text report
```

### Example output

```
Neon Usage Report - February 2026
Generated: 2026-02-11 14:23 UTC
Day 11 of 28 (39.3% of month)
================================================================

Fetching projects...
Found 6 active projects
Fetching consumption metrics (daily granularity)...
Processing 6 active + 4 deleted projects...
----------------------------------------------------------------

PER-PROJECT USAGE
----------------------------------------------------------------

infisical (plain-butter...)
  Compute:         60.84 CU-hours  cost: $6.45
  Storage:         1.11 GB         cost: $0.39
  Instant Restore: 0.14 GB         cost: $0.03
  Transfer:        24.24 GB        cost: $0.00
  infisical total cost: $6.87

assetdb (royal-fire-7...)
  Compute:         36.68 CU-hours  cost: $3.89
  Storage:         21.00 GB        cost: $7.35
  Instant Restore: 0.00 GB         cost: $0.00
  Transfer:        14.86 GB        cost: $0.00
  assetdb total cost: $11.24

...

CURRENT USAGE (Month-to-Date)
----------------------------------------------------------------

Compute:
  Usage:  157.75 CU-hours
  Cost:   $16.72

Storage:
  Total:    26.13 GB
  Cost:     $9.15

...

CURRENT TOTAL (Month-to-Date)
  Subtotal:  $26.37
  Minimum:   $5.00
  TOTAL:     $26.37

...

FORECAST TOTAL
  Subtotal:  $52.26
  Minimum:   $5.00
  TOTAL:     $52.26
```

## Deleted projects

By default the report includes projects that were deleted during the current billing month. These still incur charges for any usage before deletion.

- Deleted projects appear with a `[deleted]` tag in the per-project section
- **Current costs** include all incurred charges (active + deleted)
- **Forecast** handles deleted projects specially:
  - Cumulative costs (compute, transfer) are locked at their current value — they won't grow
  - Point-in-time costs (storage, instant restore, extra branches) drop to zero — the resources are gone
- Use `--active-only` to exclude deleted projects entirely

## How it works

1. Fetches project names via `GET /projects`
2. Pulls all consumption data in a single call to the **v2 Consumption History API** (`GET /consumption_history/v2/projects`)
3. Tags projects present in consumption but absent from `/projects` as deleted
4. Aggregates metrics per-project and across the account
5. Calculates costs using Launch plan pricing, with per-project cost attribution
6. Projects a linear end-of-month forecast (with deleted-project adjustments)

## License

Apache-2.0 — see [LICENSE](LICENSE) for details.
