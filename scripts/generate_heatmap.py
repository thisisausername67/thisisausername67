#!/usr/bin/env python3
"""
ASCII Contribution Heatmap Generator
======================================
Fetches a GitHub user's contribution calendar via the GraphQL API and renders it as:
  1. A block-density ASCII grid (for raw markdown/code-fence embedding)
  2. An SVG image (for crisp embedding via <img> tag)

Also computes streak stats and injects everything into a target README between
marker comments, so the whole thing can run unattended on a daily cron.

Env vars (all can also be set in config.yml, but env vars take priority):
  GH_TOKEN          - GitHub token with `read:user` scope (required)
  GH_USERNAME        - GitHub username to track (required)
  README_PATH        - path to the README to inject into (default: README.md)
  THEME               - color theme name: green | ocean | sunset | mono (default: green)
  OUTPUT_DIR          - where to write generated assets (default: assets)
"""

import os
import sys
import json
import datetime
import urllib.request
import urllib.error

try:
    import yaml
except ImportError:
    yaml = None

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

BLOCKS = [" ", "░", "▒", "▓", "█"]  # intensity 0 -> 4

THEMES = {
    "green":  ["#161b22", "#0e4429", "#006d32", "#26a641", "#39d353"],
    "ocean":  ["#161b22", "#062f4a", "#0b5788", "#1c8fd4", "#5fc4ff"],
    "sunset": ["#161b22", "#5a1f1f", "#a3311b", "#e0632b", "#ffb454"],
    "mono":   ["#161b22", "#3a3a3a", "#6e6e6e", "#a6a6a6", "#e6e6e6"],
}

GITHUB_GRAPHQL_URL = "https://api.github.com/graphql"

QUERY = """
query($userName: String!) {
  user(login: $userName) {
    contributionsCollection {
      contributionCalendar {
        totalContributions
        weeks {
          contributionDays {
            date
            contributionCount
          }
        }
      }
    }
  }
}
"""


def load_config():
    """Merge config.yml (if present) with environment variables. Env wins."""
    cfg = {
        "username": None,
        "readme_path": "README.md",
        "theme": "green",
        "output_dir": "assets",
    }
    config_path = os.path.join(os.path.dirname(__file__), "..", "config.yml")
    if yaml and os.path.exists(config_path):
        with open(config_path) as f:
            file_cfg = yaml.safe_load(f) or {}
        cfg.update({k: v for k, v in file_cfg.items() if v is not None})

    cfg["username"] = os.environ.get("GH_USERNAME", cfg["username"])
    cfg["readme_path"] = os.environ.get("README_PATH", cfg["readme_path"])
    cfg["theme"] = os.environ.get("THEME", cfg["theme"])
    cfg["output_dir"] = os.environ.get("OUTPUT_DIR", cfg["output_dir"])

    if not cfg["username"]:
        sys.exit("ERROR: GH_USERNAME is not set (env var or config.yml).")

    token = os.environ.get("GH_TOKEN")
    if not token:
        sys.exit("ERROR: GH_TOKEN is not set. It needs at least `read:user` scope.")
    cfg["token"] = token

    if cfg["theme"] not in THEMES:
        print(f"WARNING: unknown theme '{cfg['theme']}', falling back to 'green'.")
        cfg["theme"] = "green"

    return cfg


# ---------------------------------------------------------------------------
# Data fetch
# ---------------------------------------------------------------------------

def fetch_contributions(username, token):
    body = json.dumps({"query": QUERY, "variables": {"userName": username}}).encode()
    req = urllib.request.Request(
        GITHUB_GRAPHQL_URL,
        data=body,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "ascii-contribution-heatmap",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        sys.exit(f"ERROR: GitHub API request failed ({e.code}): {e.read().decode()}")

    if "errors" in data:
        sys.exit(f"ERROR: GitHub API returned errors: {data['errors']}")

    user = data.get("data", {}).get("user")
    if not user:
        sys.exit(f"ERROR: user '{username}' not found or token lacks access.")

    calendar = user["contributionsCollection"]["contributionCalendar"]
    weeks = calendar["weeks"]
    total = calendar["totalContributions"]

    days = []
    for week in weeks:
        for day in week["contributionDays"]:
            days.append({
                "date": day["date"],
                "count": day["contributionCount"],
            })
    return days, total


# ---------------------------------------------------------------------------
# Intensity mapping
# ---------------------------------------------------------------------------

def compute_levels(days):
    """Map raw contribution counts to 0-4 intensity buckets using quartiles
    of the non-zero days, so the visual scales to each user's own activity."""
    counts = sorted(d["count"] for d in days if d["count"] > 0)
    if not counts:
        thresholds = [1, 2, 3, 4]
    else:
        n = len(counts)
        thresholds = [
            counts[int(n * 0.25)] if n > 4 else 1,
            counts[int(n * 0.50)] if n > 4 else 2,
            counts[int(n * 0.75)] if n > 4 else 4,
            counts[-1],
        ]
        # ensure strictly increasing thresholds
        for i in range(1, len(thresholds)):
            if thresholds[i] <= thresholds[i - 1]:
                thresholds[i] = thresholds[i - 1] + 1

    for d in days:
        c = d["count"]
        if c == 0:
            d["level"] = 0
        elif c <= thresholds[0]:
            d["level"] = 1
        elif c <= thresholds[1]:
            d["level"] = 2
        elif c <= thresholds[2]:
            d["level"] = 3
        else:
            d["level"] = 4
    return days


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------

def compute_stats(days):
    by_date = {d["date"]: d["count"] for d in days}
    dates_sorted = sorted(by_date.keys())

    total = sum(by_date.values())
    active_days = sum(1 for c in by_date.values() if c > 0)

    # best week: sum contributions in each 7-day block aligned to the grid weeks
    weeks = build_week_grid(days)
    best_week = 0
    for week in weeks:
        week_total = sum(d["count"] for d in week if d)
        best_week = max(best_week, week_total)

    # current streak: walk backward from most recent day, track date range
    current_streak = 0
    current_streak_start = None
    current_streak_end = dates_sorted[-1]
    idx = len(dates_sorted) - 1
    while idx >= 0 and by_date[dates_sorted[idx]] > 0:
        current_streak += 1
        current_streak_start = dates_sorted[idx]
        idx -= 1

    # longest streak overall, track date range
    longest_streak = 0
    longest_streak_start = None
    longest_streak_end = None
    running = 0
    running_start = None
    for date in dates_sorted:
        if by_date[date] > 0:
            if running == 0:
                running_start = date
            running += 1
            if running > longest_streak:
                longest_streak = running
                longest_streak_start = running_start
                longest_streak_end = date
        else:
            running = 0

    busiest_date = max(by_date, key=by_date.get)
    busiest_count = by_date[busiest_date]

    # the "year" label — most common year in the dataset (handles the
    # rolling 12-month window spanning two calendar years)
    year_counts = {}
    for date in dates_sorted:
        y = date[:4]
        year_counts[y] = year_counts.get(y, 0) + 1
    year_label = max(year_counts, key=year_counts.get)

    return {
        "year": year_label,
        "total": total,
        "active_days": active_days,
        "best_week": best_week,
        "current_streak": current_streak,
        "current_streak_start": current_streak_start,
        "current_streak_end": current_streak_end,
        "longest_streak": longest_streak,
        "longest_streak_start": longest_streak_start,
        "longest_streak_end": longest_streak_end,
        "busiest_date": busiest_date,
        "busiest_count": busiest_count,
    }


def format_date_short(iso_date):
    """'2026-07-21' -> 'Jul 21'"""
    d = datetime.date.fromisoformat(iso_date)
    return f"{MONTH_ABBR[d.month - 1]} {d.day}"


# ---------------------------------------------------------------------------
# ASCII rendering
# ---------------------------------------------------------------------------

MONTH_ABBR = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
              "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
DAY_LABELS = ["Mon", "", "Wed", "", "Fri", "", ""]


def build_week_grid(days):
    """Arrange days into a list of weeks (columns), each a list of 7 day dicts
    (Sun-Sat), matching GitHub's own layout. Pads the first/last week."""
    if not days:
        return []

    first_date = datetime.date.fromisoformat(days[0]["date"])
    # Sunday = 0 ... Saturday = 6 (Python weekday(): Mon=0..Sun=6)
    lead_pad = (first_date.weekday() + 1) % 7  # days to pad before first_date to reach Sunday

    padded = [None] * lead_pad + days
    while len(padded) % 7 != 0:
        padded.append(None)

    weeks = [padded[i:i + 7] for i in range(0, len(padded), 7)]
    return weeks


def render_ascii(weeks):
    lines = [""] * 7  # one line per weekday row
    month_markers = []  # (col_index, label)
    last_month = None

    for col, week in enumerate(weeks):
        first_real = next((d for d in week if d), None)
        if first_real:
            month = datetime.date.fromisoformat(first_real["date"]).month
            if month != last_month:
                month_markers.append((col, MONTH_ABBR[month - 1]))
                last_month = month

        for row in range(7):
            day = week[row]
            if day is None:
                lines[row] += "  "
            else:
                lines[row] += BLOCKS[day["level"]] + " "

    # build month header line aligned to columns (2 chars per column),
    # skipping a label if it would overlap the previous one
    header = [" "] * (len(weeks) * 2)
    next_free_pos = 0
    for col, label in month_markers:
        pos = col * 2
        if pos < next_free_pos:
            continue  # would overlap previous label, skip this one
        for i, ch in enumerate(label):
            if pos + i < len(header):
                header[pos + i] = ch
        next_free_pos = pos + len(label) + 1  # +1 for a spacer gap
    header_line = "".join(header)

    out = ["    " + header_line]
    for row in range(7):
        label = DAY_LABELS[row].ljust(3)
        out.append(f"{label} {lines[row]}")

    legend = "Less " + " ".join(BLOCKS) + " More"
    out.append("")
    out.append(legend)
    return "\n".join(out)


# ---------------------------------------------------------------------------
# SVG rendering
# ---------------------------------------------------------------------------

def render_svg(weeks, palette, username, stats):
    cell = 11
    gap = 3
    pad_left = 30
    pad_top = 18
    pad_bottom = 52  # room for legend row + two stats lines

    n_weeks = len(weeks)
    width = pad_left + n_weeks * (cell + gap) + 20
    height = pad_top + 7 * (cell + gap) + pad_bottom

    parts = []
    parts.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" font-family="Consolas, Monaco, monospace">'
    )
    parts.append(f'<rect width="100%" height="100%" fill="{palette[0]}" rx="8"/>')

    # month labels
    last_month = None
    for col, week in enumerate(weeks):
        first_real = next((d for d in week if d), None)
        if first_real:
            month = datetime.date.fromisoformat(first_real["date"]).month
            if month != last_month:
                x = pad_left + col * (cell + gap)
                parts.append(
                    f'<text x="{x}" y="{pad_top - 6}" fill="#8b949e" font-size="9">{MONTH_ABBR[month-1]}</text>'
                )
                last_month = month

    # cells
    for col, week in enumerate(weeks):
        for row in range(7):
            day = week[row]
            x = pad_left + col * (cell + gap)
            y = pad_top + row * (cell + gap)
            if day is None:
                continue
            color = palette[day["level"]]
            date = day["date"]
            count = day["count"]
            parts.append(
                f'<rect x="{x}" y="{y}" width="{cell}" height="{cell}" rx="2" fill="{color}">'
                f'<title>{date}: {count} contribution{"s" if count != 1 else ""}</title>'
                f'</rect>'
            )

    # legend row
    grid_bottom = pad_top + 7 * (cell + gap)
    legend_y = grid_bottom + 14
    parts.append(f'<text x="{pad_left}" y="{legend_y}" fill="#8b949e" font-size="9">Less</text>')
    lx = pad_left + 32
    for level, color in enumerate(palette):
        parts.append(f'<rect x="{lx}" y="{legend_y - 9}" width="{cell}" height="{cell}" rx="2" fill="{color}"/>')
        lx += cell + gap
    parts.append(f'<text x="{lx + 4}" y="{legend_y}" fill="#8b949e" font-size="9">More</text>')

    # stats line 1: year summary
    stats_line1 = (
        f'{stats["year"]}  ·  {stats["total"]} contributions  ·  '
        f'{stats["active_days"]} active days  ·  best week {stats["best_week"]}'
    )
    stats_y1 = legend_y + 16
    parts.append(
        f'<text x="{pad_left}" y="{stats_y1}" fill="#c9d1d9" font-size="9">{stats_line1}</text>'
    )

    # stats line 2: streaks with date ranges
    cur_start = format_date_short(stats["current_streak_start"]) if stats["current_streak_start"] else "—"
    cur_end = format_date_short(stats["current_streak_end"]) if stats["current_streak_start"] else "—"
    long_start = format_date_short(stats["longest_streak_start"]) if stats["longest_streak_start"] else "—"
    long_end = format_date_short(stats["longest_streak_end"]) if stats["longest_streak_start"] else "—"
    stats_line2 = (
        f'current streak {stats["current_streak"]}d ({cur_start} – {cur_end})  ·  '
        f'longest {stats["longest_streak"]}d ({long_start} – {long_end})'
    )
    stats_y2 = stats_y1 + 14
    parts.append(
        f'<text x="{pad_left}" y="{stats_y2}" fill="#c9d1d9" font-size="9">{stats_line2}</text>'
    )

    parts.append("</svg>")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# README injection
# ---------------------------------------------------------------------------

START_MARKER = "<!-- HEATMAP:START -->"
END_MARKER = "<!-- HEATMAP:END -->"


def build_block(ascii_art, svg_path, username, stats):
    lines = []
    lines.append(START_MARKER)
    lines.append("")
    lines.append(f"### {username}'s Contribution Heatmap")
    lines.append("")
    lines.append(f'<img src="{svg_path}" alt="{username} contribution heatmap" />')
    lines.append("")
    lines.append("<details>")
    lines.append("<summary>ASCII version (click to expand)</summary>")
    lines.append("")
    lines.append("```")
    lines.append(ascii_art)
    lines.append("```")
    lines.append("")
    lines.append("</details>")
    lines.append("")
    lines.append(
        f"**{stats['year']}** &nbsp;·&nbsp; "
        f"{stats['total']} contributions &nbsp;·&nbsp; "
        f"{stats['active_days']} active days &nbsp;·&nbsp; "
        f"best week {stats['best_week']}"
    )
    lines.append("")
    cur_start = format_date_short(stats["current_streak_start"]) if stats["current_streak_start"] else "—"
    cur_end = format_date_short(stats["current_streak_end"]) if stats["current_streak_start"] else "—"
    long_start = format_date_short(stats["longest_streak_start"]) if stats["longest_streak_start"] else "—"
    long_end = format_date_short(stats["longest_streak_end"]) if stats["longest_streak_start"] else "—"
    lines.append(
        f"current streak **{stats['current_streak']}d** ({cur_start} – {cur_end}) &nbsp;·&nbsp; "
        f"longest **{stats['longest_streak']}d** ({long_start} – {long_end})"
    )
    lines.append("")
    lines.append(
        f"Busiest day: {stats['busiest_date']} ({stats['busiest_count']} contributions)"
    )
    lines.append("")
    lines.append(f"_Last updated: {datetime.datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}_")
    lines.append("")
    lines.append(END_MARKER)
    return "\n".join(lines)


def inject_into_readme(readme_path, block):
    if not os.path.exists(readme_path):
        print(f"NOTE: {readme_path} does not exist yet, creating it.")
        with open(readme_path, "w") as f:
            f.write(block + "\n")
        return

    with open(readme_path) as f:
        content = f.read()

    if START_MARKER in content and END_MARKER in content:
        pre = content.split(START_MARKER)[0]
        post = content.split(END_MARKER)[1]
        new_content = pre + block + post
    else:
        sep = "\n\n" if content and not content.endswith("\n\n") else ""
        new_content = content + sep + block + "\n"

    with open(readme_path, "w") as f:
        f.write(new_content)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    cfg = load_config()

    print(f"Fetching contributions for {cfg['username']}...")
    days, total = fetch_contributions(cfg["username"], cfg["token"])
    days = compute_levels(days)
    stats = compute_stats(days)

    weeks = build_week_grid(days)
    ascii_art = render_ascii(weeks)

    palette = THEMES[cfg["theme"]]
    svg = render_svg(weeks, palette, cfg["username"], stats)

    os.makedirs(cfg["output_dir"], exist_ok=True)
    svg_out_path = os.path.join(cfg["output_dir"], "heatmap.svg")
    ascii_out_path = os.path.join(cfg["output_dir"], "heatmap.txt")

    with open(svg_out_path, "w") as f:
        f.write(svg)
    with open(ascii_out_path, "w") as f:
        f.write(ascii_art)

    print(f"Wrote {svg_out_path} and {ascii_out_path}")

    # relative path for embedding (assumes assets/ sits next to README)
    svg_rel_path = os.path.join(cfg["output_dir"], "heatmap.svg").replace(os.sep, "/")
    block = build_block(ascii_art, svg_rel_path, cfg["username"], stats)
    inject_into_readme(cfg["readme_path"], block)

    print(f"Updated {cfg['readme_path']}")
    print("\n--- ASCII preview ---\n")
    print(ascii_art)


if __name__ == "__main__":
    main()
