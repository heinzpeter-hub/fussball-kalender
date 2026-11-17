#!/usr/bin/env python3
"""
Spielplan des 1. FC Kaiserslautern (Profis) als Kalenderdatei docs/fck.ics.

Quellen:
  Liga und DFB-Pokal  -> bundesliga.com (offizielle DFL-Seiten)
  Freundschaftsspiele -> Datei freundschaftsspiele.csv (von dir gepflegt)

  python3 build_fck.py           normal
  python3 build_fck.py --debug   speichert zusätzlich die Rohseiten in debug/
"""
import csv
import hashlib
import re
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup
from icalendar import Calendar, Event

# ======================= EINSTELLUNGEN =======================
SEASON = "2026-2027"            # jede Saison anpassen
LEAGUE = "2bundesliga"          # bei Aufstieg: "bundesliga"
CLUB_SLUG = "1-fc-kaiserslautern"
CAL_NAME = "1. FC Kaiserslautern"
OUT_FILE = Path("docs/fck.ics")
FRIENDLIES_FILE = Path("freundschaftsspiele.csv")
GAME_LENGTH = timedelta(hours=2)
HOME_VENUE = "Fritz-Walter-Stadion, Fritz-Walter-Straße 1, 67663 Kaiserslautern"
POKAL_ROUNDS = ["runde-1", "runde-2", "achtelfinale", "viertelfinale", "halbfinale", "finale"]
# =============================================================

BASE = "https://www.bundesliga.com"
SEASON_START_YEAR = int(SEASON[:4])
TZ = ZoneInfo("Europe/Berlin")
UTC = timezone.utc
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; privater-Kalender-Helfer)"}
DEBUG = "--debug" in sys.argv

SOURCES = [("Liga", f"{BASE}/de/{LEAGUE}/spieltag/{SEASON}/{CLUB_SLUG}")]
SOURCES += [("DFB-Pokal", f"{BASE}/de/dfb-pokal/spieltag/{SEASON}/{r}") for r in POKAL_ROUNDS]
SOURCES += [("DFB-Pokal (aktuelle Runde)", f"{BASE}/de/dfb-pokal/spieltag")]

MONTHS = {"Januar": 1, "Februar": 2, "März": 3, "April": 4, "Mai": 5, "Juni": 6, "Juli": 7,
          "August": 8, "September": 9, "Oktober": 10, "November": 11, "Dezember": 12}
MON = "|".join(MONTHS)
RE_RANGE = re.compile(rf"(\d{{1,2}})\.\s*({MON})\s*-\s*(\d{{1,2}})\.\s*({MON})")
RE_DAY = re.compile(rf"(\d{{1,2}})\.\s*({MON})")
RE_TIME = re.compile(r"\b([01]?\d|2[0-3]):([0-5]\d)\b")
RE_MATCH = re.compile(
    r"/de/(bundesliga|2bundesliga|dfb-pokal)/spieltag/(\d{4}-\d{4})/"
    r"(?:([a-z0-9-]+)/)?([a-z0-9-]+)-vs-([a-z0-9-]+)/liveticker"
)
ROUND_NAMES = {"runde-1": "1. Runde", "runde-2": "2. Runde", "achtelfinale": "Achtelfinale",
               "viertelfinale": "Viertelfinale", "halbfinale": "Halbfinale", "finale": "Finale"}
PRESERVE = ("bundesliga-", "2bundesliga-", "dfb-pokal-")

NAMES = {
    "1-fc-kaiserslautern": "1. FC Kaiserslautern", "vfl-wolfsburg": "VfL Wolfsburg",
    "karlsruher-sc": "Karlsruher SC", "fc-st-pauli": "FC St. Pauli",
    "sv-darmstadt-98": "SV Darmstadt 98", "1-fc-magdeburg": "1. FC Magdeburg",
    "eintracht-braunschweig": "Eintracht Braunschweig", "1-fc-heidenheim-1846": "1. FC Heidenheim 1846",
    "vfl-bochum-1848": "VfL Bochum 1848", "1-fc-nuernberg": "1. FC Nürnberg",
    "hertha-bsc": "Hertha BSC", "vfl-osnabrueck": "VfL Osnabrück",
    "sg-dynamo-dresden": "SG Dynamo Dresden", "holstein-kiel": "Holstein Kiel",
    "spvgg-greuther-fuerth": "SpVgg Greuther Fürth", "dsc-arminia-bielefeld": "DSC Arminia Bielefeld",
    "hannover-96": "Hannover 96", "fc-energie-cottbus": "FC Energie Cottbus",
    "sport-club-freiburg": "SC Freiburg",
}
AWAY_CITY = {
    "vfl-wolfsburg": "Wolfsburg", "karlsruher-sc": "Karlsruhe", "fc-st-pauli": "Hamburg",
    "sv-darmstadt-98": "Darmstadt", "1-fc-magdeburg": "Magdeburg", "eintracht-braunschweig": "Braunschweig",
    "1-fc-heidenheim-1846": "Heidenheim an der Brenz", "vfl-bochum-1848": "Bochum",
    "1-fc-nuernberg": "Nürnberg", "hertha-bsc": "Berlin", "vfl-osnabrueck": "Osnabrück",
    "sg-dynamo-dresden": "Dresden", "holstein-kiel": "Kiel", "spvgg-greuther-fuerth": "Fürth",
    "dsc-arminia-bielefeld": "Bielefeld", "hannover-96": "Hannover", "fc-energie-cottbus": "Cottbus",
}
SPECIAL = {"fc": "FC", "sv": "SV", "vfl": "VfL", "vfb": "VfB", "sc": "SC", "sg": "SG", "tsg": "TSG",
           "tsv": "TSV", "bsc": "BSC", "fsv": "FSV", "rb": "RB", "tus": "TuS", "msv": "MSV",
           "ssv": "SSV", "spvgg": "SpVgg", "kfc": "KFC", "dsc": "DSC", "bvb": "BVB"}


def clean(s):
    return re.sub(r"\s+", " ", (s or "").replace("\u200b", "")).strip()


def slug_to_name(slug):
    if slug in NAMES:
        return NAMES[slug]
    words, out = slug.split("-"), []
    for i, w in enumerate(words):
        if i == 0 and w.isdigit() and len(words) > 1 and words[1] == "fc":
            out.append(w + ".")
        else:
            out.append(SPECIAL.get(w, w.capitalize()))
    return " ".join(out)


def year_of(month):
    return SEASON_START_YEAR if month >= 7 else SEASON_START_YEAR + 1


def get(url, tries=3):
    for i in range(tries):
        try:
            r = requests.get(url, headers=HEADERS, timeout=30)
            if r.status_code == 200 and r.text.strip():
                return r.text
            if r.status_code == 404:
                return None
            print(f"   HTTP {r.status_code}")
        except requests.RequestException as e:
            print(f"   Fehler: {e}")
        time.sleep(3 * (i + 1))
    return None


def parse_heading(text):
    r = RE_RANGE.search(text)
    if r:
        m1, m2 = MONTHS[r[2]], MONTHS[r[4]]
        return ("range", date(year_of(m1), m1, int(r[1])), date(year_of(m2), m2, int(r[3])))
    d = RE_DAY.search(text)
    if d:
        m = MONTHS[d[2]]
        return ("day", date(year_of(m), m, int(d[1])), None)
    return None


def parse_page(html):
    soup = BeautifulSoup(html, "html.parser")
    found, seen, cur = [], set(), None
    for el in soup.find_all(["h2", "h3", "h4", "a"]):
        if el.name != "a":                       # Datums-Überschrift
            cur = parse_heading(clean(el.get_text(" "))) or cur
            continue
        m = RE_MATCH.search(el.get("href", ""))  # Link zu einem Spiel
        if not m or cur is None:
            continue
        comp_key, season, rnd, home_slug, away_slug = m.groups()
        if CLUB_SLUG not in (home_slug, away_slug) or (comp_key, rnd) in seen:
            continue
        seen.add((comp_key, rnd))                # jedes Spiel hat 2 Links, nur 1x zählen
        home_game = home_slug == CLUB_SLUG
        opp = away_slug if home_game else home_slug
        round_name = ROUND_NAMES.get(rnd) or (f"{rnd}. Spieltag" if rnd and rnd.isdigit() else rnd or "")
        g = {
            "uid": f"{comp_key}-{season}-{rnd}@fck-kalender",
            "comp_key": comp_key,
            "comp": "DFB-Pokal" if comp_key == "dfb-pokal" else "Liga",
            "round": round_name,
            "home": slug_to_name(home_slug),
            "away": slug_to_name(away_slug),
            "venue": HOME_VENUE if home_game else AWAY_CITY.get(opp),
            "url": urljoin(BASE, el["href"]),
            "tag": f"DFB-Pokal, {round_name}" if comp_key == "dfb-pokal" else "",
            "note": "",
            "date": cur[1],
        }
        if cur[0] == "range":
            g.update(kind="range", end_date=cur[2])
        else:
            box = el.find_parent("li") or el.parent
            hrefs = {a["href"] for a in box.find_all("a", href=RE_MATCH)}
            tm = RE_TIME.search(clean(box.get_text(" "))) if len(hrefs) == 1 else None
            if tm:
                d = cur[1]
                g.update(kind="timed", start=datetime(d.year, d.month, d.day, int(tm[1]), int(tm[2]), tzinfo=TZ))
            else:
                g.update(kind="day")
        found.append(g)
    return found


def load_friendlies():
    if not FRIENDLIES_FILE.exists():
        return []
    text = FRIENDLIES_FILE.read_text(encoding="utf-8-sig")
    lines = [l for l in text.splitlines() if l.strip() and not l.lstrip().startswith("#")]
    out = []
    for row in csv.DictReader(lines, delimiter=";"):
        try:
            d = datetime.strptime(row["datum"].strip(), "%Y-%m-%d").date()
            home, away = row["heim"].strip(), row["gast"].strip()
            zeit = (row.get("uhrzeit") or "").strip()
            g = {
                "uid": "test-" + hashlib.sha1(f"{home}|{away}".encode()).hexdigest()[:12] + "@fck-kalender",
                "comp_key": "test", "comp": "Freundschaftsspiel", "round": "",
                "home": home, "away": away,
                "venue": (row.get("ort") or "").strip() or None,
                "url": "", "tag": "Freundschaftsspiel",
                "note": (row.get("hinweis") or "").strip(), "date": d,
            }
            if zeit:
                hh, mm = zeit.split(":")
                g.update(kind="timed", start=datetime(d.year, d.month, d.day, int(hh), int(mm), tzinfo=TZ))
            else:
                g["kind"] = "day"
            out.append(g)
        except Exception as e:
            print(f"   Zeile in {FRIENDLIES_FILE} übersprungen ({e}): {row}")
    return out


def build_event(g, today):
    ev = Event()
    ev.add("uid", g["uid"])
    title = f"{g['home']} – {g['away']}"
    if g["tag"]:
        title += f" ({g['tag']})"
    ev.add("summary", title)
    notes = []
    if g["kind"] == "timed":
        start = g["start"].astimezone(UTC)
        ev.add("dtstart", start)
        ev.add("dtend", start + GAME_LENGTH)
    elif g["kind"] == "range":
        ev.add("dtstart", g["date"])
        ev.add("dtend", g["end_date"] + timedelta(days=1))
        notes.append(f"Noch nicht zeitgenau angesetzt (Spieltag-Zeitraum {g['date']:%d.%m.}–{g['end_date']:%d.%m.}).")
    else:
        ev.add("dtstart", g["date"])
        ev.add("dtend", g["date"] + timedelta(days=1))
        notes.append("Anstoßzeit nicht mehr abrufbar." if g["date"] < today else "Uhrzeit noch offen.")
    desc = [f"{g['comp']} {g['round']}".strip()] + notes
    if g["note"]:
        desc.append(g["note"])
    if g["url"]:
        desc.append(g["url"])
    ev.add("description", "\n".join(desc))
    if g["venue"]:
        ev.add("location", g["venue"])
    ev.add("dtstamp", datetime.now(UTC))
    return ev


def is_past(comp, now):
    s = comp.decoded("dtstart")
    return s < now if isinstance(s, datetime) else s < now.date()


def main():
    now = datetime.now(TZ)
    today = now.date()

    found = []
    for label, url in SOURCES:
        print(f"{label}: {url}")
        html = get(url)
        if html is None:
            print("   -> nicht abrufbar oder Seite existiert nicht, übersprungen")
            continue
        if DEBUG:
            Path("debug").mkdir(exist_ok=True)
            safe = re.sub(r"[^a-z0-9]+", "_", url.lower())[-60:]
            Path(f"debug/fck_{safe}.html").write_text(html, encoding="utf-8")
        games = parse_page(html)
        print(f"   -> {len(games)} FCK-Spiel(e) gefunden")
        found += games
        time.sleep(1)

    by_uid = {}
    for g in found:
        cur = by_uid.get(g["uid"])
        if cur is None or (cur["kind"] != "timed" and g["kind"] == "timed"):
            by_uid[g["uid"]] = g

    n_league = sum(1 for g in by_uid.values() if g["comp_key"] == LEAGUE)
    if n_league < 10:
        sys.exit(f"FEHLER: Nur {n_league} Ligaspiele gefunden (erwartet ca. 34). Datei wird nicht verändert.")

    for g in load_friendlies():
        by_uid[g["uid"]] = g

    old = {}
    if OUT_FILE.exists():
        try:
            old = {str(c.get("uid")): c for c in Calendar.from_ical(OUT_FILE.read_bytes()).walk("VEVENT")}
        except Exception as e:
            print(f"Alte Datei nicht lesbar ({e}), starte neu")

    cal = Calendar()
    cal.add("prodid", "-//FCK Kalender//DE")
    cal.add("version", "2.0")
    cal.add("calscale", "GREGORIAN")
    cal.add("x-wr-calname", CAL_NAME)
    cal.add("x-published-ttl", "PT6H")

    games = sorted(by_uid.values(), key=lambda x: x["date"])
    for g in games:
        prev = old.get(g["uid"])
        # Vergangenes Spiel ohne Uhrzeit auf der Seite: alte Version mit Uhrzeit behalten
        if prev is not None and g["kind"] == "day" and g["date"] < today:
            s = prev.decoded("dtstart")
            if isinstance(s, datetime) and s.astimezone(TZ).date() == g["date"]:
                cal.add_component(prev)
                continue
        cal.add_component(build_event(g, today))

    for uid, comp in old.items():              # Spiele, die von der Seite verschwunden sind
        if uid in by_uid or not uid.startswith(PRESERVE):
            continue
        if not is_past(comp, now):
            summary = str(comp.get("summary", ""))
            comp.pop("summary", None)
            comp.add("summary", "❓ " + summary)
            comp.pop("description", None)
            comp.add("description", "Dieses Spiel steht nicht mehr im Spielplan auf bundesliga.com. Bitte prüfen.")
        cal.add_component(comp)

    OUT_FILE.parent.mkdir(exist_ok=True)
    OUT_FILE.write_bytes(cal.to_ical())

    print(f"\nFertig: {len(games)} Spiele.")
    for g in games:
        if g["kind"] == "timed":
            wann = g["start"].strftime("%d.%m.%Y %H:%M")
        elif g["kind"] == "range":
            wann = f"{g['date']:%d.%m.%Y}–{g['end_date']:%d.%m.%Y} (Zeitraum)"
        else:
            wann = f"{g['date']:%d.%m.%Y} (ganztägig)"
        print(f"  {wann:<32} {g['home']} - {g['away']}  [{g['comp']}{' ' + g['round'] if g['round'] else ''}]")


if __name__ == "__main__":
    main()