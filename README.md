# Neon Usage

A single-file Python script that pulls your [Neon](https://neon.tech) project metrics from the **v2 Consumption History API** and turns them into a readable usage & cost report — including a month-to-date summary and an end-of-month forecast.

> **Heads-up:** This assumes the standard Neon **Launch** plan pricing (2026). Pricing constants are defined at the top of the script and can be adjusted if your plan differs. The forecast is a linear extrapolation — treat it as a rough guesstimate, not a guarantee.

## Features

- **Month-to-date costs** — compute, storage, instant restore, data transfer, extra branches
- **End-of-month forecast** — linear projection based on current usage rate
- **Per-project breakdown** — see which project is eating your budget
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

# Combine flags
python3 neon_usage.py --detail --granularity monthly --json
```

### Options

```
--org-id ORG_ID       Organization ID (default: $ORG_ID or .env)
--detail              Show root/child storage & public/private transfer split
--granularity         hourly | daily (default) | monthly
--json                Output as JSON instead of a text report
```

### Example output

```
Neon Usage Report - February 2026
Generated: 2026-02-07 14:45 UTC
Day 7 of 28 (25.0% of month)
================================================================

Fetching projects...
Found 6 projects
Fetching consumption metrics (daily granularity)...
Processing 10 projects...
----------------------------------------------------------------

CURRENT USAGE (Month-to-Date)
----------------------------------------------------------------

Compute:
  Usage:  109.53 CU-hours
  Cost:   $11.61

Storage:
  Total:    26.10 GB
  Cost:     $9.13

...

CURRENT TOTAL (Month-to-Date)
  Subtotal:  $21.45
  Minimum:   $5.00
  TOTAL:     $21.45
```

## How it works

1. Fetches project names via `GET /projects`
2. Pulls all consumption data in a single call to the **v2 Consumption History API** (`GET /consumption_history/v2/projects`)
3. Aggregates metrics per-project and across the account
4. Calculates costs using Launch plan pricing
5. Projects a linear end-of-month forecast

## License

Apache-2.0 — see [LICENSE](LICENSE) for details.
