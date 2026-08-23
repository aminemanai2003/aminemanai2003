#!/usr/bin/env python3
"""Generate a self-contained isometric view of public GitHub contributions."""

from __future__ import annotations

import argparse
import html
import re
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path


GITHUB_USERNAME = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?$")
COUNT_PATTERN = re.compile(r"([\d,]+)\s+contributions?\b", re.IGNORECASE)


@dataclass
class Contribution:
    level: int
    count: int | None = None


class ContributionParser(HTMLParser):
    """Parse GitHub's public contribution calendar without extra packages."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.contributions: dict[date, Contribution] = {}
        self._cell_dates: dict[str, date] = {}
        self._tooltip_for: str | None = None
        self._tooltip_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        classes = (attributes.get("class") or "").split()

        if tag == "td" and "ContributionCalendar-day" in classes:
            cell_id = attributes.get("id")
            raw_date = attributes.get("data-date")
            raw_level = attributes.get("data-level")
            if not cell_id or not raw_date or raw_level is None:
                return

            try:
                contribution_date = date.fromisoformat(raw_date)
                level = int(raw_level)
            except (TypeError, ValueError):
                return

            self._cell_dates[cell_id] = contribution_date
            self.contributions[contribution_date] = Contribution(level=level)

        if tag == "tool-tip":
            cell_id = attributes.get("for")
            if cell_id in self._cell_dates:
                self._tooltip_for = cell_id
                self._tooltip_text = []

    def handle_data(self, data: str) -> None:
        if self._tooltip_for is not None:
            self._tooltip_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag != "tool-tip" or self._tooltip_for is None:
            return

        contribution_date = self._cell_dates[self._tooltip_for]
        tooltip = "".join(self._tooltip_text).strip()
        match = COUNT_PATTERN.search(tooltip)
        self.contributions[contribution_date].count = (
            int(match.group(1).replace(",", "")) if match else 0
        )
        self._tooltip_for = None
        self._tooltip_text = []


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--username", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--end-date",
        type=date.fromisoformat,
        default=datetime.now(timezone.utc).date(),
        help="Final date in YYYY-MM-DD format; defaults to today in UTC.",
    )
    return parser.parse_args()


def fetch_year(username: str, year: int) -> dict[date, Contribution]:
    url = (
        f"https://github.com/users/{username}/contributions"
        f"?from={year}-01-01&to={year}-12-31"
    )
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "text/html",
            "User-Agent": f"{username}-profile-contribution-calendar/1.0",
        },
    )

    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            source = response.read().decode("utf-8")
    except (urllib.error.URLError, TimeoutError) as error:
        raise RuntimeError(f"Could not fetch GitHub contributions for {year}: {error}") from error

    parser = ContributionParser()
    parser.feed(source)
    if len(parser.contributions) < 300:
        raise RuntimeError(
            f"GitHub contribution markup for {year} was not recognized "
            f"({len(parser.contributions)} days parsed)."
        )

    unresolved = [
        day
        for day, contribution in parser.contributions.items()
        if contribution.level > 0 and contribution.count is None
    ]
    if unresolved:
        raise RuntimeError(f"Contribution counts were unavailable for {len(unresolved)} active days.")

    return parser.contributions


def longest_streak(counts: list[int]) -> int:
    longest = 0
    current = 0
    for count in counts:
        if count > 0:
            current += 1
            longest = max(longest, current)
        else:
            current = 0
    return longest


def polygon(points: list[tuple[float, float]], css_class: str) -> str:
    coordinates = " ".join(f"{x:.1f},{y:.1f}" for x, y in points)
    return f'<polygon class="{css_class}" points="{coordinates}" />'


def block(cx: float, cy: float, level: int) -> list[str]:
    tile_width = 19.0
    tile_height = 10.0
    height = (0.0, 7.0, 14.0, 22.0, 31.0)[level]

    left = (cx - tile_width / 2, cy - height)
    right = (cx + tile_width / 2, cy - height)
    top = (cx, cy - height - tile_height / 2)
    bottom = (cx, cy - height + tile_height / 2)
    base_left = (cx - tile_width / 2, cy)
    base_right = (cx + tile_width / 2, cy)
    base_bottom = (cx, cy + tile_height / 2)

    shapes: list[str] = []
    if height:
        shapes.append(
            polygon([left, bottom, base_bottom, base_left], f"level-{level}-left")
        )
        shapes.append(
            polygon([bottom, right, base_right, base_bottom], f"level-{level}-right")
        )
    shapes.append(polygon([top, right, bottom, left], f"level-{level}-top"))
    return shapes


def render_svg(
    username: str,
    start: date,
    end: date,
    contributions: dict[date, Contribution],
) -> str:
    days = [start + timedelta(days=offset) for offset in range((end - start).days + 1)]
    counts = [contributions.get(day, Contribution(0, 0)).count or 0 for day in days]
    total = sum(counts)
    active_days = sum(count > 0 for count in counts)
    streak = longest_streak(counts)
    best_day = max(counts, default=0)

    graph_start = start - timedelta(days=(start.weekday() + 1) % 7)
    cells: list[tuple[int, int, int]] = []
    for day in days:
        week = (day - graph_start).days // 7
        weekday = (day.weekday() + 1) % 7
        level = contributions.get(day, Contribution(0, 0)).level
        cells.append((week, weekday, level))

    chart: list[str] = []
    for week, weekday, level in sorted(cells, key=lambda cell: (cell[0] + cell[1], cell[0])):
        cx = 120 + (week - weekday) * 9.5
        cy = 90 + (week + weekday) * 5
        chart.extend(block(cx, cy, level))

    legend: list[str] = []
    for level in range(5):
        cx = 752 + level * 30
        cy = 333
        legend.extend(block(cx, cy, level))

    title = html.escape(username)
    date_range = f"{start.strftime('%d %b %Y')} — {end.strftime('%d %b %Y')}"
    return f'''<svg xmlns="http://www.w3.org/2000/svg" role="img" aria-labelledby="title description" viewBox="0 0 1000 440">
  <title id="title">{title}'s GitHub contribution activity</title>
  <desc id="description">An isometric calendar showing {total} public contributions across {active_days} active days from {date_range}.</desc>
  <style>
    text {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }}
    .eyebrow {{ fill: #57606a; font-size: 12px; font-weight: 700; letter-spacing: 1.4px; }}
    .heading {{ fill: #24292f; font-size: 25px; font-weight: 700; }}
    .value {{ fill: #24292f; font-size: 32px; font-weight: 700; }}
    .label {{ fill: #57606a; font-size: 13px; }}
    .meta {{ fill: #57606a; font-size: 12px; }}
    .rule {{ stroke: #d0d7de; }}
    .level-0-top {{ fill: #eaeef2; stroke: #d0d7de; stroke-width: .55; }}
    .level-1-top {{ fill: #2f81f7; }} .level-1-left {{ fill: #1f6feb; }} .level-1-right {{ fill: #1958b7; }}
    .level-2-top {{ fill: #29a3d8; }} .level-2-left {{ fill: #2386b3; }} .level-2-right {{ fill: #196987; }}
    .level-3-top {{ fill: #2dd4bf; }} .level-3-left {{ fill: #20ad9d; }} .level-3-right {{ fill: #187f74; }}
    .level-4-top {{ fill: #7eead9; }} .level-4-left {{ fill: #39cdb8; }} .level-4-right {{ fill: #239c8d; }}
    @media (prefers-color-scheme: dark) {{
      .eyebrow, .label, .meta {{ fill: #8b949e; }}
      .heading, .value {{ fill: #f0f6fc; }}
      .rule {{ stroke: #30363d; }}
      .level-0-top {{ fill: #161b22; stroke: #30363d; }}
    }}
  </style>
  <g aria-hidden="true">
    {''.join(chart)}
  </g>
  <g transform="translate(690 0)">
    <text class="eyebrow" x="0" y="82">CONTRIBUTION ACTIVITY</text>
    <text class="heading" x="0" y="116">Last 12 months</text>
    <line class="rule" x1="0" y1="140" x2="250" y2="140" />

    <text class="value" x="0" y="188">{total:,}</text>
    <text class="label" x="0" y="211">public contributions</text>

    <text class="value" x="0" y="267">{active_days}</text>
    <text class="label" x="0" y="290">active days</text>

    <text class="value" x="126" y="267">{streak}</text>
    <text class="label" x="126" y="290">longest streak</text>

    <text class="meta" x="0" y="338">Less</text>
    <g transform="translate(-690 0)" aria-hidden="true">{''.join(legend)}</g>
    <text class="meta" x="226" y="338">More</text>

    <line class="rule" x1="0" y1="365" x2="250" y2="365" />
    <text class="meta" x="0" y="390">Best day: {best_day} contributions</text>
    <text class="meta" x="0" y="414">{date_range}</text>
  </g>
</svg>
'''


def main() -> int:
    args = parse_args()
    if not GITHUB_USERNAME.fullmatch(args.username):
        print("Invalid GitHub username.", file=sys.stderr)
        return 2

    end = args.end_date
    start = end - timedelta(days=364)
    contributions: dict[date, Contribution] = {}
    for year in range(start.year, end.year + 1):
        contributions.update(fetch_year(args.username, year))

    relevant = {day: value for day, value in contributions.items() if start <= day <= end}
    if len(relevant) != 365:
        raise RuntimeError(f"Expected 365 calendar days, found {len(relevant)}.")

    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(f"{output.suffix}.tmp")
    temporary.write_text(render_svg(args.username, start, end, relevant), encoding="utf-8")
    temporary.replace(output)
    print(f"Generated {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
