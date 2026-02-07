#!/usr/bin/env python3
"""
Neon Usage & Cost Calculator for Launch Plan (v2 API)

Fetches usage metrics from the Neon v2 Consumption History API and calculates
costs based on Launch plan pricing.  Includes monthly forecasting.

Usage:
    export NEON_API_KEY="your-api-key"
    export ORG_ID="org-your-org-id"       # optional, or use --org-id
    python neon_usage.py [--org-id ORG] [--detail] [--granularity daily] [--json]

Credentials can also be placed in a .env file in the script's directory.
Priority: CLI argument > environment variable > .env file.
"""

import os
import sys
import json
import re
import argparse
from datetime import datetime, timezone
from calendar import monthrange
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode

# Launch Plan Pricing (as of 2026)
PRICING = {
    "compute_per_cu_hour": 0.106,           # $ per CU-hour
    "storage_per_gb_month": 0.35,           # $ per GB-month (root + child)
    "data_transfer_included_gb": 100,       # GB included free
    "data_transfer_per_gb": 0.10,           # $ per GB after included
    "instant_restore_per_gb_month": 0.20,   # $ per GB-month
    "extra_branch_per_month": 1.50,         # $ per branch-month
    "minimum_monthly": 5.00,                # $ minimum spend
}

# All v2 metric names
V2_METRICS = [
    "compute_unit_seconds",
    "root_branch_bytes_month",
    "child_branch_bytes_month",
    "instant_restore_bytes_month",
    "public_network_transfer_bytes",
    "private_network_transfer_bytes",
    "extra_branches_month",
]

# Cumulative metrics (sum across timeframes); everything else is point-in-time
CUMULATIVE_METRICS = {
    "compute_unit_seconds",
    "public_network_transfer_bytes",
    "private_network_transfer_bytes",
}


# ---------------------------------------------------------------------------
# .env file loader
# ---------------------------------------------------------------------------

def _load_dotenv():
    """Load key=value pairs from a .env file next to this script (if present).

    Only simple KEY=VALUE lines are supported.  Quotes are stripped.
    Lines starting with # and blank lines are ignored.
    Existing environment variables are NOT overwritten.
    """
    env_path = Path(__file__).resolve().parent / ".env"
    if not env_path.is_file():
        return
    kv_re = re.compile(
        r'^(?:export\s+)?'
        r'([A-Za-z_][A-Za-z0-9_]*)'
        r'\s*=\s*'
        r'(.*)$'
    )
    try:
        for raw_line in env_path.read_text().splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            m = kv_re.match(line)
            if not m:
                continue
            key, val = m.group(1), m.group(2).strip()
            if len(val) >= 2 and val[0] == val[-1] and val[0] in ('"', "'"):
                val = val[1:-1]
            if key not in os.environ:
                os.environ[key] = val
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

_NEON_ID_PATTERN = re.compile(r'^[a-zA-Z0-9][a-zA-Z0-9._-]{0,255}$')
_NEON_ORG_ID_PATTERN = re.compile(r'^org-[a-zA-Z0-9-]{1,64}$')


def _validate_org_id(value):
    """Validate org_id format (org-...)."""
    if not _NEON_ORG_ID_PATTERN.match(value):
        print(f"Error: Invalid org_id format: {value!r} (expected 'org-...')")
        sys.exit(1)
    return value


# ---------------------------------------------------------------------------
# API helpers
# ---------------------------------------------------------------------------

def get_api_key():
    """Get API key from environment (or .env)."""
    api_key = os.environ.get("NEON_API_KEY")
    if not api_key:
        print("Error: NEON_API_KEY not set.")
        print("Set it via environment variable, .env file, or see:")
        print("  https://console.neon.tech/app/settings/api-keys")
        sys.exit(1)
    return api_key


def get_org_id(cli_value=None):
    """Resolve org_id: CLI arg > $ORG_ID env var > .env."""
    org_id = cli_value or os.environ.get("ORG_ID")
    if org_id:
        _validate_org_id(org_id)
    return org_id


def api_request(endpoint, api_key, params=None):
    """Make a GET request to the Neon API v2."""
    base_url = "https://console.neon.tech/api/v2"
    url = f"{base_url}/{endpoint}"

    if params:
        safe_params = {k: v for k, v in params.items() if v is not None}
        url = f"{url}?{urlencode(safe_params)}"

    request = Request(url, headers={
        "Authorization": f"Bearer {api_key}",
        "Accept": "application/json",
    })

    try:
        with urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode())
    except HTTPError as e:
        code = e.code
        if code == 401:
            print("API Error (401): Authentication failed. Check your NEON_API_KEY.")
        elif code == 403:
            print("API Error (403): Access denied. Check your permissions.")
        elif code == 404:
            print(f"API Error (404): Resource not found at endpoint '{endpoint}'.")
        elif code == 429:
            print("API Error (429): Rate limited. Please wait and try again.")
        else:
            print(f"API Error ({code}): Request failed.")
        sys.exit(1)
    except URLError:
        print("Network Error: Unable to reach Neon API.")
        sys.exit(1)


# ---------------------------------------------------------------------------
# Data fetching
# ---------------------------------------------------------------------------

def get_project_names(api_key, org_id=None):
    """Fetch project id -> name mapping via GET /projects."""
    params = {}
    if org_id:
        params["org_id"] = org_id
    result = api_request("projects", api_key, params)
    return {p["id"]: p.get("name", p["id"]) for p in result.get("projects", [])}


def get_v2_consumption(api_key, org_id, from_ts, to_ts, granularity="daily"):
    """Fetch consumption data from the v2 endpoint, handling pagination."""
    all_projects = []
    cursor = None

    while True:
        params = {
            "from": from_ts,
            "to": to_ts,
            "granularity": granularity,
            "metrics": ",".join(V2_METRICS),
        }
        if org_id:
            params["org_id"] = org_id
        if cursor:
            params["cursor"] = cursor

        result = api_request("consumption_history/v2/projects", api_key, params)
        all_projects.extend(result.get("projects", []))

        pagination = result.get("pagination", {})
        new_cursor = pagination.get("cursor")
        if not new_cursor or new_cursor == cursor:
            break
        cursor = new_cursor

    return all_projects


# ---------------------------------------------------------------------------
# Metric aggregation
# ---------------------------------------------------------------------------

def bytes_to_gb(b):
    """Convert bytes to GB (base-1024)."""
    return b / (1024 ** 3)


def aggregate_project_metrics(project_data):
    """Aggregate v2 consumption data for a single project.

    Returns a flat dict of metric totals:
      - Cumulative metrics: summed across all timeframes
      - Point-in-time metrics: latest timeframe value
    """
    metrics = {m: 0 for m in V2_METRICS}

    periods = project_data.get("periods", [])
    if not periods:
        return metrics

    # Use the first (current) period
    consumption = periods[0].get("consumption", [])
    if not consumption:
        return metrics

    for timeframe in consumption:
        tf_metrics = {m["metric_name"]: m["value"]
                      for m in timeframe.get("metrics", [])}
        for name in V2_METRICS:
            val = tf_metrics.get(name)
            if val is None:
                continue
            if name in CUMULATIVE_METRICS:
                metrics[name] += val
            else:
                # Point-in-time: keep latest value
                metrics[name] = val

    return metrics


def aggregate_all_projects(projects_data):
    """Aggregate metrics across all projects.

    Returns (per_project_list, totals_dict).
    """
    per_project = []
    totals = {m: 0 for m in V2_METRICS}

    for proj in projects_data:
        proj_id = proj.get("project_id", "unknown")
        proj_metrics = aggregate_project_metrics(proj)
        per_project.append({"project_id": proj_id, "metrics": proj_metrics})

        for name in V2_METRICS:
            totals[name] += proj_metrics[name]

    return per_project, totals


# ---------------------------------------------------------------------------
# Cost calculation
# ---------------------------------------------------------------------------

def calculate_costs(metrics, days_elapsed):
    """Calculate costs from aggregated v2 metrics."""
    costs = {}

    # Compute: CU-seconds -> CU-hours
    cu_seconds = metrics.get("compute_unit_seconds", 0)
    cu_hours = cu_seconds / 3600
    costs["compute"] = {
        "cu_hours": cu_hours,
        "cost": cu_hours * PRICING["compute_per_cu_hour"],
    }

    # Storage: root + child branches (point-in-time, GB-month billing)
    root_bytes = metrics.get("root_branch_bytes_month", 0)
    child_bytes = metrics.get("child_branch_bytes_month", 0)
    storage_bytes = root_bytes + child_bytes
    storage_gb = bytes_to_gb(storage_bytes)
    costs["storage"] = {
        "gb": storage_gb,
        "root_gb": bytes_to_gb(root_bytes),
        "child_gb": bytes_to_gb(child_bytes),
        "cost": storage_gb * PRICING["storage_per_gb_month"],
    }

    # Instant Restore (point-in-time, GB-month billing)
    ir_bytes = metrics.get("instant_restore_bytes_month", 0)
    ir_gb = bytes_to_gb(ir_bytes)
    costs["instant_restore"] = {
        "gb": ir_gb,
        "cost": ir_gb * PRICING["instant_restore_per_gb_month"],
    }

    # Data Transfer: public + private, with 100 GB free tier
    pub_bytes = metrics.get("public_network_transfer_bytes", 0)
    priv_bytes = metrics.get("private_network_transfer_bytes", 0)
    transfer_bytes = pub_bytes + priv_bytes
    transfer_gb = bytes_to_gb(transfer_bytes)
    billable_transfer = max(0, transfer_gb - PRICING["data_transfer_included_gb"])
    costs["data_transfer"] = {
        "gb": transfer_gb,
        "public_gb": bytes_to_gb(pub_bytes),
        "private_gb": bytes_to_gb(priv_bytes),
        "included_gb": PRICING["data_transfer_included_gb"],
        "billable_gb": billable_transfer,
        "cost": billable_transfer * PRICING["data_transfer_per_gb"],
    }

    # Extra branches: branch-hours -> average branch count -> monthly cost
    total_branch_hours = metrics.get("extra_branches_month", 0)
    if days_elapsed > 0:
        avg_extra_branches = total_branch_hours / (24 * days_elapsed)
    else:
        avg_extra_branches = 0
    costs["extra_branches"] = {
        "branch_hours": total_branch_hours,
        "avg_count": avg_extra_branches,
        "cost": avg_extra_branches * PRICING["extra_branch_per_month"],
    }

    # Total
    subtotal = sum(c["cost"] for c in costs.values())
    costs["total"] = {
        "subtotal": subtotal,
        "minimum": PRICING["minimum_monthly"],
        "final": max(subtotal, PRICING["minimum_monthly"]),
    }

    return costs


def calculate_forecast(costs, days_elapsed, days_in_month):
    """Forecast end-of-month costs based on current run rate."""
    if days_elapsed == 0:
        return None

    ratio = days_elapsed / days_in_month
    forecast = {}

    # Compute scales with time
    forecast["compute"] = {
        "cu_hours": costs["compute"]["cu_hours"] / ratio,
        "cost": costs["compute"]["cost"] / ratio,
    }

    # Storage is point-in-time
    forecast["storage"] = {
        "gb": costs["storage"]["gb"],
        "cost": costs["storage"]["cost"],
    }

    # Instant restore is point-in-time
    forecast["instant_restore"] = {
        "gb": costs["instant_restore"]["gb"],
        "cost": costs["instant_restore"]["cost"],
    }

    # Transfer scales with time
    proj_transfer = costs["data_transfer"]["gb"] / ratio
    proj_billable = max(0, proj_transfer - PRICING["data_transfer_included_gb"])
    forecast["data_transfer"] = {
        "gb": proj_transfer,
        "billable_gb": proj_billable,
        "cost": proj_billable * PRICING["data_transfer_per_gb"],
    }

    # Extra branches stay constant
    forecast["extra_branches"] = {
        "avg_count": costs["extra_branches"]["avg_count"],
        "cost": costs["extra_branches"]["cost"],
    }

    subtotal = sum(forecast[k]["cost"] for k in
                   ["compute", "storage", "instant_restore",
                    "data_transfer", "extra_branches"])
    forecast["total"] = {
        "subtotal": subtotal,
        "minimum": PRICING["minimum_monthly"],
        "final": max(subtotal, PRICING["minimum_monthly"]),
    }

    return forecast


# ---------------------------------------------------------------------------
# Display helpers
# ---------------------------------------------------------------------------

def fmt_currency(amount):
    return f"${amount:,.2f}"


def fmt_num(num, decimals=2):
    return f"{num:,.{decimals}f}"


def separator(char="-", length=64):
    print(char * length)


def print_project_summary(per_project, name_map, detail):
    """Print per-project usage table."""
    separator()
    print("\nPER-PROJECT USAGE")
    separator()

    for proj in per_project:
        pid = proj["project_id"]
        m = proj["metrics"]
        name = name_map.get(pid, pid)

        cu_hours = m["compute_unit_seconds"] / 3600
        storage_gb = bytes_to_gb(
            m["root_branch_bytes_month"] + m["child_branch_bytes_month"])
        ir_gb = bytes_to_gb(m["instant_restore_bytes_month"])
        transfer_gb = bytes_to_gb(
            m["public_network_transfer_bytes"]
            + m["private_network_transfer_bytes"])

        # Skip projects with zero activity
        if cu_hours == 0 and storage_gb == 0 and transfer_gb == 0 and ir_gb == 0:
            continue

        print(f"\n{name} ({pid[:12]}...)")
        print(f"  Compute:         {fmt_num(cu_hours)} CU-hours")

        if detail:
            print(f"  Root Storage:    {fmt_num(bytes_to_gb(m['root_branch_bytes_month']))} GB")
            print(f"  Child Storage:   {fmt_num(bytes_to_gb(m['child_branch_bytes_month']))} GB")
        else:
            print(f"  Storage:         {fmt_num(storage_gb)} GB")

        print(f"  Instant Restore: {fmt_num(ir_gb)} GB")

        if detail:
            print(f"  Public Transfer: {fmt_num(bytes_to_gb(m['public_network_transfer_bytes']))} GB")
            print(f"  Private Transfer:{fmt_num(bytes_to_gb(m['private_network_transfer_bytes']))} GB")
        else:
            print(f"  Transfer:        {fmt_num(transfer_gb)} GB")


def print_current_usage(costs, detail):
    """Print aggregated month-to-date costs."""
    separator()
    print("\nCURRENT USAGE (Month-to-Date)")
    separator()

    print(f"\nCompute:")
    print(f"  Usage:  {fmt_num(costs['compute']['cu_hours'])} CU-hours")
    print(f"  Cost:   {fmt_currency(costs['compute']['cost'])}")

    print(f"\nStorage:")
    if detail:
        print(f"  Root:     {fmt_num(costs['storage']['root_gb'])} GB")
        print(f"  Child:    {fmt_num(costs['storage']['child_gb'])} GB")
    print(f"  Total:    {fmt_num(costs['storage']['gb'])} GB")
    print(f"  Cost:     {fmt_currency(costs['storage']['cost'])}")

    print(f"\nInstant Restore:")
    print(f"  Usage:  {fmt_num(costs['instant_restore']['gb'])} GB")
    print(f"  Cost:   {fmt_currency(costs['instant_restore']['cost'])}")

    print(f"\nData Transfer:")
    if detail:
        print(f"  Public:    {fmt_num(costs['data_transfer']['public_gb'])} GB")
        print(f"  Private:   {fmt_num(costs['data_transfer']['private_gb'])} GB")
    print(f"  Total:     {fmt_num(costs['data_transfer']['gb'])} GB")
    print(f"  Included:  {fmt_num(costs['data_transfer']['included_gb'])} GB")
    print(f"  Billable:  {fmt_num(costs['data_transfer']['billable_gb'])} GB")
    print(f"  Cost:      {fmt_currency(costs['data_transfer']['cost'])}")

    print(f"\nExtra Branches:")
    print(f"  Avg count: {fmt_num(costs['extra_branches']['avg_count'], 1)}")
    print(f"  Cost:      {fmt_currency(costs['extra_branches']['cost'])}")

    separator()
    print(f"\nCURRENT TOTAL (Month-to-Date)")
    print(f"  Subtotal:  {fmt_currency(costs['total']['subtotal'])}")
    print(f"  Minimum:   {fmt_currency(costs['total']['minimum'])}")
    print(f"  TOTAL:     {fmt_currency(costs['total']['final'])}")


def print_forecast(forecast):
    """Print end-of-month forecast."""
    if not forecast:
        return

    separator()
    print("\nFORECAST (End of Month)")
    separator()

    print(f"\n  Compute:         {fmt_currency(forecast['compute']['cost']):>10}"
          f"  ({fmt_num(forecast['compute']['cu_hours'])} CU-hours)")
    print(f"  Storage:         {fmt_currency(forecast['storage']['cost']):>10}"
          f"  ({fmt_num(forecast['storage']['gb'])} GB)")
    print(f"  Instant Restore: {fmt_currency(forecast['instant_restore']['cost']):>10}"
          f"  ({fmt_num(forecast['instant_restore']['gb'])} GB)")
    print(f"  Data Transfer:   {fmt_currency(forecast['data_transfer']['cost']):>10}"
          f"  ({fmt_num(forecast['data_transfer']['gb'])} GB)")
    print(f"  Extra Branches:  {fmt_currency(forecast['extra_branches']['cost']):>10}"
          f"  ({fmt_num(forecast['extra_branches']['avg_count'], 1)} branches)")

    separator()
    print(f"\nFORECAST TOTAL")
    print(f"  Subtotal:  {fmt_currency(forecast['total']['subtotal'])}")
    print(f"  Minimum:   {fmt_currency(forecast['total']['minimum'])}")
    print(f"  TOTAL:     {fmt_currency(forecast['total']['final'])}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    # Load .env before anything else so env vars are available
    _load_dotenv()

    parser = argparse.ArgumentParser(
        description="Neon Usage & Cost Calculator (v2 Consumption History API)"
    )
    parser.add_argument(
        "--org-id",
        help="Organization ID (default: $ORG_ID env var or .env)",
        default=None,
    )
    parser.add_argument(
        "--detail",
        action="store_true",
        help="Show detailed breakdown (root/child storage, public/private transfer)",
    )
    parser.add_argument(
        "--granularity",
        choices=["hourly", "daily", "monthly"],
        default="daily",
        help="Metric granularity (default: daily)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output results as JSON",
    )
    args = parser.parse_args()

    api_key = get_api_key()
    org_id = get_org_id(args.org_id)

    # Current date info
    now = datetime.now(timezone.utc)
    _, days_in_month = monthrange(now.year, now.month)
    days_elapsed = now.day

    # Month-to-date time range
    from_ts = now.replace(day=1, hour=0, minute=0, second=0,
                          microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")
    to_ts = now.strftime("%Y-%m-%dT%H:%M:%SZ")

    if not args.json:
        print(f"\nNeon Usage Report - {now.strftime('%B %Y')}")
        print(f"Generated: {now.strftime('%Y-%m-%d %H:%M UTC')}")
        print(f"Day {days_elapsed} of {days_in_month} "
              f"({days_elapsed / days_in_month * 100:.1f}% of month)")
        separator("=")

    # Fetch project names (v2 only returns project_id)
    if not args.json:
        print("\nFetching projects...")
    name_map = get_project_names(api_key, org_id)
    if not args.json:
        print(f"Found {len(name_map)} projects")

    # Fetch v2 consumption data
    if not args.json:
        print(f"Fetching consumption metrics ({args.granularity} granularity)...")
    projects_data = get_v2_consumption(
        api_key, org_id, from_ts, to_ts, args.granularity)

    if not projects_data:
        print("No consumption data found for this period.")
        sys.exit(0)

    # Aggregate
    per_project, totals = aggregate_all_projects(projects_data)
    costs = calculate_costs(totals, days_elapsed)
    forecast = calculate_forecast(costs, days_elapsed, days_in_month)

    # JSON output
    if args.json:
        output = {
            "report_date": now.isoformat(),
            "billing_period": {
                "month": now.strftime("%B %Y"),
                "day": days_elapsed,
                "days_in_month": days_in_month,
                "progress_percent": round(
                    days_elapsed / days_in_month * 100, 1),
            },
            "projects": [
                {
                    "project_id": p["project_id"],
                    "name": name_map.get(p["project_id"],
                                         p["project_id"]),
                    "metrics": p["metrics"],
                }
                for p in per_project
            ],
            "totals": totals,
            "costs": costs,
            "forecast": forecast,
            "pricing": PRICING,
        }
        print(json.dumps(output, indent=2))
        return

    # Text output
    print(f"Processing {len(per_project)} projects...")

    print_project_summary(per_project, name_map, args.detail)
    print_current_usage(costs, args.detail)
    print_forecast(forecast)

    separator("=")
    print(f"\nPricing based on Neon Launch plan (2026)")
    print(f"  Compute:         {fmt_currency(PRICING['compute_per_cu_hour'])}/CU-hour")
    print(f"  Storage:         {fmt_currency(PRICING['storage_per_gb_month'])}/GB-month")
    print(f"  Instant Restore: {fmt_currency(PRICING['instant_restore_per_gb_month'])}/GB-month")
    print(f"  Transfer:        {PRICING['data_transfer_included_gb']} GB free, "
          f"then {fmt_currency(PRICING['data_transfer_per_gb'])}/GB")
    print(f"  Extra Branches:  {fmt_currency(PRICING['extra_branch_per_month'])}/branch-month")
    print()


if __name__ == "__main__":
    main()
