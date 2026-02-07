#!/usr/bin/env python3
"""
Neon Usage & Cost Calculator for Launch Plan

Fetches usage metrics from the Neon API and calculates costs based on Launch plan pricing.
Includes monthly forecasting based on current usage patterns.

Usage:
    export NEON_API_KEY="your-api-key"
    python neon_usage.py [--org-id YOUR_ORG_ID]
"""

import os
import sys
import json
import re
import argparse
from datetime import datetime, timezone
from calendar import monthrange
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, quote

# Launch Plan Pricing (as of 2026)
PRICING = {
    "compute_per_cu_hour": 0.106,  # $ per CU-hour
    "storage_per_gb_month": 0.35,  # $ per GB-month
    "data_transfer_included_gb": 100,  # GB included free
    "data_transfer_per_gb": 0.10,  # $ per GB after included
    "instant_restore_per_gb_month": 0.20,  # $ per GB-month
    "extra_branch_per_month": 1.50,  # $ per branch-month
    "minimum_monthly": 5.00,  # $ minimum spend
}

# Branches included in Launch plan per project
BRANCHES_INCLUDED_PER_PROJECT = 10


def get_api_key():
    """Get API key from environment variable."""
    api_key = os.environ.get("NEON_API_KEY")
    if not api_key:
        print("Error: NEON_API_KEY environment variable not set.")
        print("Get your API key from: https://console.neon.tech/app/settings/api-keys")
        sys.exit(1)
    return api_key


# Validation patterns
_NEON_ID_PATTERN = re.compile(r'^[a-zA-Z0-9][a-zA-Z0-9._-]{0,255}$')
_NEON_ORG_ID_PATTERN = re.compile(r'^org-[a-zA-Z0-9-]{1,64}$')


def _validate_identifier(value, name="identifier"):
    """Validate that a value is a safe API identifier (project ID, etc.)."""
    if not _NEON_ID_PATTERN.match(value):
        print(f"Error: Invalid {name}: {value!r}")
        sys.exit(1)
    return value


def _validate_org_id(value):
    """Validate that an org_id matches the expected Neon format."""
    if not _NEON_ORG_ID_PATTERN.match(value):
        print(f"Error: Invalid org_id format: {value!r} (expected 'org-...')")
        sys.exit(1)
    return value


def api_request(endpoint, api_key, params=None):
    """Make a request to the Neon API."""
    base_url = "https://console.neon.tech/api/v2"
    url = f"{base_url}/{endpoint}"

    if params:
        # Use proper URL encoding to prevent parameter injection
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
        # Avoid leaking potentially sensitive API error details
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
    except URLError as e:
        print(f"Network Error: Unable to reach Neon API.")
        sys.exit(1)


def get_projects(api_key, org_id=None):
    """Fetch all projects."""
    endpoint = "projects"
    params = {}
    if org_id:
        _validate_org_id(org_id)
        params["org_id"] = org_id

    result = api_request(endpoint, api_key, params)
    return result.get("projects", [])


def get_projects_with_metrics(api_key, org_id=None):
    """Fetch all projects with their consumption metrics.
    
    This method works for all plan types (Free, Launch, Scale, etc.)
    and returns current billing period metrics embedded in the project response.
    """
    projects = get_projects(api_key, org_id)
    
    # Each project returned by GET /projects includes consumption metrics
    # for the current billing period in the project response
    return projects


def get_project_details(api_key, project_id):
    """Get details for a specific project including branch count."""
    safe_id = quote(_validate_identifier(project_id, "project_id"), safe="")
    result = api_request(f"projects/{safe_id}", api_key)
    return result.get("project", {})


def get_project_branches(api_key, project_id):
    """Get branches for a specific project."""
    safe_id = quote(_validate_identifier(project_id, "project_id"), safe="")
    result = api_request(f"projects/{safe_id}/branches", api_key)
    return result.get("branches", [])


def bytes_to_gb(bytes_value):
    """Convert bytes to GB."""
    return bytes_value / (1024 ** 3)


def seconds_to_hours(seconds):
    """Convert seconds to hours."""
    return seconds / 3600


def calculate_cu_hours(compute_time_seconds):
    """
    Calculate CU-hours from compute_time_seconds.
    compute_time_seconds is already in CU-seconds (accounts for compute size).
    """
    return compute_time_seconds / 3600


def calculate_costs(metrics, branch_count, projects_count):
    """Calculate costs based on usage metrics."""
    costs = {}

    # Compute cost (CU-hours)
    compute_seconds = metrics.get("compute_time_seconds", 0)
    cu_hours = calculate_cu_hours(compute_seconds)
    costs["compute"] = {
        "cu_hours": cu_hours,
        "cost": cu_hours * PRICING["compute_per_cu_hour"],
    }

    # Storage cost
    # synthetic_storage_size_bytes represents the total billable storage
    storage_bytes = metrics.get("synthetic_storage_size_bytes", 0)
    storage_gb = bytes_to_gb(storage_bytes)
    costs["storage"] = {
        "gb": storage_gb,
        "cost": storage_gb * PRICING["storage_per_gb_month"],
    }

    # Data transfer (egress)
    transfer_bytes = metrics.get("data_transfer_bytes", 0)
    transfer_gb = bytes_to_gb(transfer_bytes)
    billable_transfer = max(0, transfer_gb - PRICING["data_transfer_included_gb"])
    costs["data_transfer"] = {
        "gb": transfer_gb,
        "included_gb": PRICING["data_transfer_included_gb"],
        "billable_gb": billable_transfer,
        "cost": billable_transfer * PRICING["data_transfer_per_gb"],
    }

    # Extra branches cost
    total_branches_included = projects_count * BRANCHES_INCLUDED_PER_PROJECT
    extra_branches = max(0, branch_count - total_branches_included)
    costs["extra_branches"] = {
        "total_branches": branch_count,
        "included": total_branches_included,
        "extra": extra_branches,
        "cost": extra_branches * PRICING["extra_branch_per_month"],
    }

    # Total
    subtotal = sum(c["cost"] for c in costs.values())
    costs["total"] = {
        "subtotal": subtotal,
        "minimum": PRICING["minimum_monthly"],
        "final": max(subtotal, PRICING["minimum_monthly"]),
    }

    return costs


def calculate_forecast(costs, current_day, days_in_month):
    """Calculate forecasted costs for the full month."""
    if current_day == 0:
        return None

    # Calculate daily run rate
    progress_ratio = current_day / days_in_month

    forecast = {}

    # Forecast compute (scales with time)
    forecast["compute"] = {
        "cu_hours": costs["compute"]["cu_hours"] / progress_ratio,
        "cost": costs["compute"]["cost"] / progress_ratio,
    }

    # Storage is point-in-time, not cumulative - use current value
    forecast["storage"] = {
        "gb": costs["storage"]["gb"],
        "cost": costs["storage"]["cost"],
    }

    # Data transfer (scales with time)
    projected_transfer = costs["data_transfer"]["gb"] / progress_ratio
    billable_transfer = max(0, projected_transfer - PRICING["data_transfer_included_gb"])
    forecast["data_transfer"] = {
        "gb": projected_transfer,
        "billable_gb": billable_transfer,
        "cost": billable_transfer * PRICING["data_transfer_per_gb"],
    }

    # Extra branches (use current count)
    forecast["extra_branches"] = costs["extra_branches"].copy()

    # Total forecast
    subtotal = sum(forecast[k]["cost"] for k in ["compute", "storage", "data_transfer", "extra_branches"])
    forecast["total"] = {
        "subtotal": subtotal,
        "minimum": PRICING["minimum_monthly"],
        "final": max(subtotal, PRICING["minimum_monthly"]),
    }

    return forecast


def format_currency(amount):
    """Format amount as currency."""
    return f"${amount:,.2f}"


def format_number(num, decimals=2):
    """Format number with commas and decimals."""
    return f"{num:,.{decimals}f}"


def print_separator(char="-", length=60):
    """Print a separator line."""
    print(char * length)


def main():
    parser = argparse.ArgumentParser(
        description="Calculate Neon usage costs for Launch plan"
    )
    parser.add_argument(
        "--org-id",
        help="Organization ID (for org accounts)",
        default=None,
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output results as JSON",
    )
    args = parser.parse_args()

    api_key = get_api_key()

    # Get current date info
    now = datetime.now(timezone.utc)
    _, days_in_month = monthrange(now.year, now.month)
    current_day = now.day

    print(f"\nNeon Usage Report - {now.strftime('%B %Y')}")
    print(f"Generated: {now.strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"Day {current_day} of {days_in_month} ({current_day/days_in_month*100:.1f}% of month)")
    print_separator("=")

    # Fetch projects
    print("\nFetching projects...")
    projects = get_projects(api_key, args.org_id)
    project_count = len(projects)
    print(f"Found {project_count} projects")

    if project_count == 0:
        print("No projects found.")
        sys.exit(0)

    # Count total branches across all projects
    print("Counting branches...")
    total_branches = 0
    for project in projects:
        branches = get_project_branches(api_key, project["id"])
        total_branches += len(branches)
    print(f"Total branches: {total_branches}")

    # Extract consumption data by fetching detailed project info
    print("Fetching detailed project metrics...")
    
    # Aggregate metrics across all projects
    aggregated_metrics = {
        "compute_time_seconds": 0,
        "active_time_seconds": 0,
        "written_data_bytes": 0,
        "synthetic_storage_size_bytes": 0,
        "data_storage_bytes_hour": 0,
        "data_transfer_bytes": 0,
    }

    project_details = []

    for project in projects:
        project_id = project.get("id", "unknown")
        project_name = project.get("name", project_id)
        
        # Fetch detailed project info which includes consumption metrics
        detailed_project = get_project_details(api_key, project_id)
        
        proj_metrics = {
            "compute_time_seconds": detailed_project.get("compute_time_seconds", 0),
            "active_time_seconds": detailed_project.get("active_time_seconds", 0),
            "written_data_bytes": detailed_project.get("written_data_bytes", 0),
            "synthetic_storage_size_bytes": detailed_project.get("synthetic_storage_size", 0),
            "data_storage_bytes_hour": detailed_project.get("data_storage_bytes_hour", 0),
            "data_transfer_bytes": detailed_project.get("data_transfer_bytes", 0),
        }

        # Aggregate
        for key in aggregated_metrics:
            aggregated_metrics[key] += proj_metrics.get(key, 0)

        project_details.append({
            "id": project_id,
            "name": project_name,
            "metrics": proj_metrics,
        })

    # Calculate costs
    costs = calculate_costs(aggregated_metrics, total_branches, project_count)
    forecast = calculate_forecast(costs, current_day, days_in_month)

    if args.json:
        output = {
            "report_date": now.isoformat(),
            "billing_period": {
                "month": now.strftime("%B %Y"),
                "day": current_day,
                "days_in_month": days_in_month,
                "progress_percent": round(current_day / days_in_month * 100, 1),
            },
            "projects": {
                "count": project_count,
                "total_branches": total_branches,
                "details": project_details,
            },
            "metrics": aggregated_metrics,
            "costs": costs,
            "forecast": forecast,
            "pricing": PRICING,
        }
        print(json.dumps(output, indent=2))
        return

    # Print project summary
    print_separator()
    print("\nPER-PROJECT USAGE")
    print_separator()

    for proj in project_details:
        m = proj["metrics"]
        cu_hours = calculate_cu_hours(m["compute_time_seconds"])
        storage_gb = bytes_to_gb(m["synthetic_storage_size_bytes"])
        transfer_gb = bytes_to_gb(m["data_transfer_bytes"])
        
        # Calculate project cost
        proj_compute_cost = cu_hours * PRICING["compute_per_cu_hour"]
        proj_storage_cost = storage_gb * PRICING["storage_per_gb_month"]
        proj_transfer_cost = max(0, transfer_gb - PRICING["data_transfer_included_gb"]) * PRICING["data_transfer_per_gb"]
        proj_total_cost = proj_compute_cost + proj_storage_cost + proj_transfer_cost
        
        print(f"\n{proj['name']} ({proj['id'][:8]}...)")
        print(f"  Compute:  {format_number(cu_hours)} CU-hours")
        print(f"  Storage:  {format_number(storage_gb)} GB")
        print(f"  Transfer: {format_number(transfer_gb)} GB")
        print(f"  Cost:     {format_currency(proj_total_cost)}")

    # Print aggregated costs
    print_separator()
    print("\nCURRENT USAGE (Month-to-Date)")
    print_separator()

    print(f"\nCompute:")
    print(f"  Usage:  {format_number(costs['compute']['cu_hours'])} CU-hours")
    print(f"  Cost:   {format_currency(costs['compute']['cost'])}")

    print(f"\nStorage:")
    print(f"  Usage:  {format_number(costs['storage']['gb'])} GB")
    print(f"  Cost:   {format_currency(costs['storage']['cost'])}")

    print(f"\nData Transfer:")
    print(f"  Usage:     {format_number(costs['data_transfer']['gb'])} GB")
    print(f"  Included:  {format_number(costs['data_transfer']['included_gb'])} GB")
    print(f"  Billable:  {format_number(costs['data_transfer']['billable_gb'])} GB")
    print(f"  Cost:      {format_currency(costs['data_transfer']['cost'])}")

    print(f"\nBranches:")
    print(f"  Total:     {costs['extra_branches']['total_branches']}")
    print(f"  Included:  {costs['extra_branches']['included']}")
    print(f"  Extra:     {costs['extra_branches']['extra']}")
    print(f"  Cost:      {format_currency(costs['extra_branches']['cost'])}")

    print_separator()
    print(f"\nCURRENT TOTAL (Month-to-Date)")
    print(f"  Subtotal:  {format_currency(costs['total']['subtotal'])}")
    print(f"  Minimum:   {format_currency(costs['total']['minimum'])}")
    print(f"  TOTAL:     {format_currency(costs['total']['final'])}")

    # Print forecast
    if forecast:
        print_separator()
        print("\nFORECAST (End of Month)")
        print_separator()

        print(f"\nCompute:       {format_currency(forecast['compute']['cost'])} ({format_number(forecast['compute']['cu_hours'])} CU-hours)")
        print(f"Storage:       {format_currency(forecast['storage']['cost'])} ({format_number(forecast['storage']['gb'])} GB)")
        print(f"Data Transfer: {format_currency(forecast['data_transfer']['cost'])} ({format_number(forecast['data_transfer']['gb'])} GB)")
        print(f"Extra Branches:{format_currency(forecast['extra_branches']['cost'])}")

        print_separator()
        print(f"\nFORECAST TOTAL")
        print(f"  Subtotal:  {format_currency(forecast['total']['subtotal'])}")
        print(f"  Minimum:   {format_currency(forecast['total']['minimum'])}")
        print(f"  TOTAL:     {format_currency(forecast['total']['final'])}")

    print_separator("=")
    print("\nPricing based on Neon Launch plan (2026)")
    print(f"  Compute: {format_currency(PRICING['compute_per_cu_hour'])}/CU-hour")
    print(f"  Storage: {format_currency(PRICING['storage_per_gb_month'])}/GB-month")
    print(f"  Transfer: {PRICING['data_transfer_included_gb']} GB free, then {format_currency(PRICING['data_transfer_per_gb'])}/GB")
    print()


if __name__ == "__main__":
    main()
