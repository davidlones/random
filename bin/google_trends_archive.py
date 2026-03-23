#!/usr/bin/env python3
"""
Scrape archived Google Trends "Hot Searches" pages from the Wayback Machine.

The old Hot Trends pages expose daily top-20 query lists, not a true monthly
leaderboard. For month-level results, this script aggregates daily lists and
ranks queries by:
1. number of days they appear in the daily top 20
2. best rank achieved
3. average rank

This is a practical reconstruction, not an official Google monthly chart.
"""

from __future__ import annotations

import argparse
import calendar
import json
import re
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Iterable

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By


WAYBACK_URL = "https://web.archive.org/web/{stamp}/http://www.google.com/trends/hottrends"
HOT_SECTION_RE = re.compile(
    r"Hot Searches\s*\(USA\)(.*?)(?:Google Trends provides insights|©2008 Google|©\d{4} Google)",
    re.S,
)
RANK_LINE_RE = re.compile(r"^\s*(\d{1,2})\.\s*(.+?)\s*$")
PAGE_DATE_RE = re.compile(
    r"Hot Searches\s*\(USA\)\s*([A-Za-z]+\s+\d{1,2},\s+\d{4})",
    re.S,
)


@dataclass
class QueryStat:
    days: int = 0
    best_rank: int = 999
    total_rank: int = 0
    dates: list[str] = field(default_factory=list)

    @property
    def avg_rank(self) -> float:
        return self.total_rank / self.days if self.days else 999.0


def month_days(year: int, month: int) -> Iterable[date]:
    last_day = calendar.monthrange(year, month)[1]
    for day in range(1, last_day + 1):
        yield date(year, month, day)


def selected_days(year: int, month: int, day_numbers: list[int]) -> list[date]:
    last_day = calendar.monthrange(year, month)[1]
    dates: list[date] = []
    seen: set[int] = set()
    for day in day_numbers:
        if day < 1 or day > last_day or day in seen:
            continue
        seen.add(day)
        dates.append(date(year, month, day))
    return dates


def build_driver() -> webdriver.Chrome:
    opts = Options()
    opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--window-size=1440,2400")
    return webdriver.Chrome(options=opts)


def fetch_archive_text(driver: webdriver.Chrome, day: date, pause_s: float) -> str:
    stamp = day.strftime("%Y%m%d")
    driver.get(WAYBACK_URL.format(stamp=stamp))
    time.sleep(pause_s)
    return driver.find_element(By.TAG_NAME, "body").text


def parse_queries(page_text: str) -> list[tuple[int, str]]:
    match = HOT_SECTION_RE.search(page_text)
    if not match:
        return []

    section = match.group(1)
    queries: list[tuple[int, str]] = []
    for raw_line in section.splitlines():
        line = " ".join(raw_line.split())
        if not line:
            continue
        rank_match = RANK_LINE_RE.match(line)
        if not rank_match:
            continue
        rank = int(rank_match.group(1))
        query = rank_match.group(2).strip()
        if query and query.lower() not in {"change date", "site feed", "igoogle gadget", "new!"}:
            queries.append((rank, query))
    return queries


def parse_page_date(page_text: str) -> str | None:
    match = PAGE_DATE_RE.search(page_text)
    return match.group(1) if match else None


def aggregate_month(
    driver: webdriver.Chrome,
    year: int,
    month: int,
    pause_s: float,
    limit_days: int | None,
    explicit_days: list[int] | None,
) -> tuple[dict[str, QueryStat], list[dict[str, object]], int]:
    stats: dict[str, QueryStat] = defaultdict(QueryStat)
    captures: list[dict[str, object]] = []
    seen_page_dates: set[str] = set()

    days_iterable = selected_days(year, month, explicit_days) if explicit_days else list(month_days(year, month))

    for idx, day in enumerate(days_iterable, start=1):
        if limit_days is not None and idx > limit_days:
            break

        text = fetch_archive_text(driver, day, pause_s=pause_s)
        actual_page_date = parse_page_date(text)
        queries = parse_queries(text)
        record = {
            "requested_date": day.isoformat(),
            "page_date": actual_page_date,
            "query_count": len(queries),
            "queries": [q for _, q in queries],
        }
        captures.append(record)

        if actual_page_date and actual_page_date in seen_page_dates:
            continue
        if actual_page_date:
            seen_page_dates.add(actual_page_date)

        seen_today: set[str] = set()
        for rank, query in queries:
            if query in seen_today:
                continue
            seen_today.add(query)
            stat = stats[query]
            stat.days += 1
            stat.best_rank = min(stat.best_rank, rank)
            stat.total_rank += rank
            stat.dates.append(actual_page_date or day.isoformat())

    return stats, captures, len(seen_page_dates)


def sort_stats(stats: dict[str, QueryStat]) -> list[tuple[str, QueryStat]]:
    return sorted(
        stats.items(),
        key=lambda item: (-item[1].days, item[1].best_rank, item[1].avg_rank, item[0].lower()),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("month", help="Month in YYYY-MM format")
    parser.add_argument("--top", type=int, default=20, help="Number of aggregated queries to print")
    parser.add_argument("--pause", type=float, default=5.0, help="Seconds to wait after each page load")
    parser.add_argument("--limit-days", type=int, default=None, help="Only fetch the first N days of the month")
    parser.add_argument(
        "--days",
        nargs="+",
        type=int,
        default=None,
        help="Fetch only these day numbers within the month, e.g. --days 1 15 31",
    )
    parser.add_argument("--json-out", type=Path, default=None, help="Optional path for JSON output")
    args = parser.parse_args()

    try:
        year_s, month_s = args.month.split("-", 1)
        year = int(year_s)
        month = int(month_s)
    except Exception:
        print("month must be YYYY-MM", file=sys.stderr)
        return 2

    driver = build_driver()
    try:
        stats, captures, unique_page_dates = aggregate_month(
            driver=driver,
            year=year,
            month=month,
            pause_s=args.pause,
            limit_days=args.limit_days,
            explicit_days=args.days,
        )
    finally:
        driver.quit()

    ordered = sort_stats(stats)
    payload = {
        "month": args.month,
        "days_requested": calendar.monthrange(year, month)[1],
        "days_fetched": len(captures),
        "unique_page_dates": unique_page_dates,
        "days_with_query_data": sum(1 for c in captures if c["query_count"]),
        "top_queries": [
            {
                "query": query,
                "days": stat.days,
                "best_rank": stat.best_rank,
                "avg_rank": round(stat.avg_rank, 2),
                "dates": stat.dates,
            }
            for query, stat in ordered[: args.top]
        ],
        "captures": captures,
    }

    if args.json_out:
        args.json_out.write_text(json.dumps(payload, indent=2))

    print(f"Month: {args.month}")
    print(f"Days fetched: {payload['days_fetched']}")
    print(f"Days with query data: {payload['days_with_query_data']}")
    print()
    for idx, item in enumerate(payload["top_queries"], start=1):
        print(
            f"{idx}. {item['query']} | days={item['days']} | "
            f"best_rank={item['best_rank']} | avg_rank={item['avg_rank']}"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
