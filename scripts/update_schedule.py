"""Rebuild the games list in index.html from the family Google Calendar.

Reads the calendar's private iCal address (from the CALENDAR_ICS_URL
environment variable), picks out the kids' games, and rewrites the block
between the EVENTS:START / EVENTS:END markers in index.html.

Run locally:  CALENDAR_ICS_URL="https://..." python scripts/update_schedule.py
Or pass a downloaded file:  python scripts/update_schedule.py calendar.ics
"""

import datetime as dt
import json
import os
import re
import sys
import urllib.request
from pathlib import Path
from zoneinfo import ZoneInfo

import icalendar
import recurring_ical_events

# ---------------------------------------------------------------------------
# Settings — change these when the season changes.
# ---------------------------------------------------------------------------
SEASON_START = dt.date(2026, 11, 1)
SEASON_END = dt.date(2027, 2, 28)   # inclusive
SPORT = "basketball"
KIDS = ["Jack", "Wes"]

# A calendar event counts as a game when its title starts with a kid's name
# followed by a colon, and contains one of these words (case-insensitive).
# Examples that match:  "Jack: Hoops", "Wes: Basketball game"
# Examples that don't:  "Jack: Atkinson Basketball" (practice), "Kellen: Zers game"
GAME_WORDS = ["hoops", "game"]
# Titles containing any of these are skipped even if they match above.
SKIP_WORDS = ["practice", "atkinson"]

TIMEZONE = ZoneInfo("America/Los_Angeles")
# ---------------------------------------------------------------------------

ROOT = Path(__file__).resolve().parent.parent
PAGE = ROOT / "index.html"
BLOCK = re.compile(r"(// EVENTS:START[^\n]*\n).*?(\n// EVENTS:END)", re.S)


def load_calendar() -> icalendar.Calendar:
    if len(sys.argv) > 1:
        data = Path(sys.argv[1]).read_bytes()
    else:
        url = os.environ.get("CALENDAR_ICS_URL", "").strip()
        if not url:
            sys.exit("CALENDAR_ICS_URL is not set. Add it as a repository secret.")
        url = url.replace("webcal://", "https://", 1)
        with urllib.request.urlopen(url, timeout=60) as resp:
            data = resp.read()
    return icalendar.Calendar.from_ical(data)


def match_kid(title: str):
    for kid in KIDS:
        if re.match(rf"^\s*{re.escape(kid)}\b\s*:", title, re.I):
            return kid
    return None


def is_game(title: str) -> bool:
    t = title.lower()
    return any(w in t for w in GAME_WORDS) and not any(w in t for w in SKIP_WORDS)


def to_local(value):
    if isinstance(value, dt.datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=TIMEZONE)
        return value.astimezone(TIMEZONE)
    return value  # all-day event: a plain date


def collect_games(cal):
    start = dt.datetime.combine(SEASON_START, dt.time.min, TIMEZONE)
    end = dt.datetime.combine(SEASON_END + dt.timedelta(days=1), dt.time.min, TIMEZONE)
    games = {}
    for ev in recurring_ical_events.of(cal).between(start, end):
        if str(ev.get("STATUS", "")).upper() == "CANCELLED":
            continue
        title = str(ev.get("SUMMARY", "")).strip()
        kid = match_kid(title)
        if not kid or not is_game(title):
            continue
        s = to_local(ev.decoded("DTSTART"))
        e = to_local(ev.decoded("DTEND")) if ev.get("DTEND") else s
        if not isinstance(s, dt.datetime):
            continue  # skip all-day entries
        item = {
            "d": s.strftime("%Y-%m-%d"),
            "t": s.strftime("%H:%M"),
            "end": e.strftime("%H:%M"),
            "sport": SPORT,
            "who": kid,
            "kind": "Game",
            "title": SPORT.capitalize(),
            "loc": str(ev.get("LOCATION", "")).strip(),
        }
        games[(item["d"], item["t"], kid)] = item  # de-duplicate
    # Kids playing at the same place and time show as one card ("Jack + Wes").
    merged = {}
    for item in games.values():
        key = (item["d"], item["t"], item["loc"])
        if key in merged:
            merged[key]["who"] += " + " + item["who"]
        else:
            merged[key] = dict(item)
    return sorted(merged.values(), key=lambda g: (g["d"], g["t"], g["who"]))


def render(games) -> str:
    keys = ["d", "t", "end", "sport", "who", "kind", "title", "loc"]
    rows = []
    for g in games:
        fields = ",".join(f"{k}:{json.dumps(g[k], ensure_ascii=False)}" for k in keys)
        rows.append("  {" + fields + "}")
    return "const events = [\n" + ",\n".join(rows) + "\n];"


def main():
    games = collect_games(load_calendar())
    html = PAGE.read_text(encoding="utf-8")
    if not BLOCK.search(html):
        sys.exit("Couldn't find the EVENTS:START / EVENTS:END markers in index.html.")
    new_html = BLOCK.sub(lambda m: m.group(1) + render(games) + m.group(2), html, count=1)
    if new_html == html:
        print(f"No changes — {len(games)} games already on the page.")
    else:
        PAGE.write_text(new_html, encoding="utf-8")
        print(f"Updated index.html with {len(games)} games:")
    for g in games:
        print(f"  {g['d']} {g['t']}  {g['who']:<12} {g['loc']}")


if __name__ == "__main__":
    main()
