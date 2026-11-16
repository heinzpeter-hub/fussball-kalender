#!/usr/bin/env python3
"""
Holt den Spielplan einer Mannschaft von fussball.de und schreibt ihn
als Kalenderdatei nach docs/spiele.ics.

  python3 build_ics.py           normal
  python3 build_ics.py --debug   speichert zusätzlich die Rohseiten in debug/
"""
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urljoin, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup
from icalendar import Calendar, Event

# ======================= EINSTELLUNGEN =======================
TEAM_ID = "011MIC3CRC000000VTVG0001VTR8C1K7"
CLUB_ID = "00ES8GNBC800009NVV0AG08LVUPGND5I"
SEASON_START = "2026-07-01"
SEASON_END = "2027-06-30"
CAL_NAME = "Fußball – SG Münchweiler/Langmeil II"
OUT_DIR = Path("docs")
OUT_FILE = OUT_DIR / "spiele.ics"
GAME_LENGTH = timedelta(hours=2)
# =============================================================

BASE = "https://www.fussball.de"
TZ = ZoneInfo("Europe/Berlin")
UTC = timezone.utc
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; privater-Kalender-Helfer)"}
DEBUG = "--debug" in sys.argv

# Das Skript probiert alle Quellen und führt die Ergebnisse zusammen.
SOURCES = [
    f"{BASE}/ajax.team.matchplan/-/datum-bis/{SEASON_END}/datum-von/{SEASON_START}"
    f"/match-type/-1/max/999/mode/PAGE/team-id/{TEAM_ID}",
    f"{BASE}/ajax.team.matchplan/-/max/999/mode/PAGE/team-id/{TEAM_ID}",
    f"{BASE}/ajax.team.matchplan/-/mode/PAGE/team-id/{TEAM_ID}",
    f"{BASE}/vereinsspielplan.druck/-/datum-bis/{SEASON_END}/datum-von/{SEASON_START}"
    f"/id/{CLUB_ID}/match-type/-1/max/999/mode/PRINT/show-venues/false/team-id/{TEAM_ID}",
]

RE_HEADER = re.compile(r"(\d{2})\.(\d{2})\.(\d{4})\s*-\s*(\d{1,2}):(\d{2})\s*Uhr")
RE_SHORT = re.compile(r"(\d{2})\.(\d{2})\.(\d{2})\s*\|\s*(\d{1,2}):(\d{2})")
RE_DT = re.compile(r"(\d{2})\.(\d{2})\.(\d{4})\s+(\d{1,2}):(\d{2})")
RE_VERLEGT = re.compile(r"verlegt vom:?\s*(\d{2})\.(\d{2})\.(\d{4})\s+(\d{1,2}):(\d{2})")
RE_ID = re.compile(r"/spiel/([0-9A-Z]{20,})")
RE_SCORE = re.compile(r"\d+\s*:\s*\d+")
CANCEL = re.compile(
    r"abgesetzt|absetzung|ausfall|ausgefallen|abbruch|abgebrochen|annullier|nichtantritt",
    re.I,
)


def clean(s):
    return re.sub(r"\s+", " ", (s or "").replace("\u200b", "")).strip()


def mk(d, m, y, hh, mi):
    y = int(y)
    if y < 100:
        y += 2000
    return datetime(y, int(m), int(d), int(hh), int(mi), tzinfo=TZ)


def get(url, tries=3):
    for i in range(tries):
        try:
            r = requests.get(url, headers=HEADERS, timeout=30)
            if r.status_code == 200 and r.text.strip():
                return r.text
            print(f"   HTTP {r.status_code}")
        except requests.RequestException as e:
            print(f"   Fehler: {e}")
        time.sleep(3 * (i + 1))
    return None


def parse_games(html):
    soup = BeautifulSoup(html, "html.parser")
    games, current_dt, current_comp, last = [], None, "", None
    for tr in soup.find_all("tr"):
        text = clean(tr.get_text(" "))
        if not text:
            continue

        # Kopfzeile eines Spiels: "Sonntag, 29.11.2026 - 12:45 Uhr | B-Klasse"
        if re.search(r"\bUhr\s*\|", text):
            m = RE_HEADER.search(text)
            current_dt = mk(*m.groups()) if m else None
            current_comp = clean(text.split("|")[-1])
            last = None
            continue

        # Zeile mit Kurzdatum: "So, 29.11.26 | 12:45"
        m = RE_SHORT.search(text)
        if m and current_dt is None:
            current_dt = mk(*m.groups())

        # Spielzeile: Teamlinks + Link zur Spielseite
        spiel_a = tr.find("a", href=RE_ID)
        teams = [a for a in tr.find_all("a", href=True) if "/mannschaft/" in a["href"]]
        if spiel_a and len(teams) >= 2:
            if not any(TEAM_ID in a["href"] for a in teams[:2]):
                continue  # Spiel einer anderen Mannschaft
            label = clean(spiel_a.get_text())
            dt = current_dt
            md = RE_DT.search(label)
            if md:  # Ergebnisfeld zeigt ein Datum = Spiel wurde auf dieses Datum verlegt
                dt = mk(*md.groups())
            attrs = " ".join(
                str(v) for el in tr.find_all(True) for v in (el.get("title"), el.get("alt")) if v
            )
            last = {
                "id": RE_ID.search(spiel_a["href"]).group(1),
                "url": urljoin(BASE, spiel_a["href"]),
                "home": clean(teams[0].get_text()),
                "away": clean(teams[1].get_text()),
                "comp": current_comp,
                "dt": dt,
                "date_only": None,
                "venue": None,
                "moved_from": None,
                "cancelled": bool(CANCEL.search(text + " " + attrs)),
                "result": label if RE_SCORE.fullmatch(label) else None,
            }
            games.append(last)
            continue

        # Zeile direkt unter einem Spiel: "verlegt vom:22.11.2026 13:00"
        if last is not None:
            mv = RE_VERLEGT.search(text)
            if mv:
                last["moved_from"] = mk(*mv.groups())
            if CANCEL.search(text):
                last["cancelled"] = True
    return games


def merge(games):
    by_id = {}
    for g in games:
        cur = by_id.get(g["id"])
        if cur is None:
            by_id[g["id"]] = dict(g)
            continue
        for k in ("dt", "moved_from", "result", "comp"):
            if not cur.get(k) and g.get(k):
                cur[k] = g[k]
        cur["cancelled"] = cur["cancelled"] or g["cancelled"]
    return by_id


def fetch_details(g):
    """Holt von der Spielseite den Spielort (und ggf. das Datum)."""
    html = get(g["url"])
    if not html:
        return
    soup = BeautifulSoup(html, "html.parser")
    a = soup.select_one('a[href*="google.de/maps"]')
    if a:
        q = parse_qs(urlparse(a["href"]).query).get("q")
        g["venue"] = clean(q[0]) if q else clean(a.get_text())
    if g["dt"] is None:
        title = clean(soup.title.get_text()) if soup.title else ""
        m = re.search(r"(\d{2})\.(\d{2})\.(\d{4})$", title)
        if m:
            g["date_only"] = datetime(int(m[3]), int(m[2]), int(m[1])).date()


def build_event(g):
    ev = Event()
    ev.add("uid", f"{g['id']}@fussball-kalender")
    title = f"{g['home']} – {g['away']}"
    if g["cancelled"]:
        title = "ABGESAGT: " + title
    ev.add("summary", title)
    if g["dt"]:
        start = g["dt"].astimezone(UTC)
        ev.add("dtstart", start)
        ev.add("dtend", start + GAME_LENGTH)
    else:  # Uhrzeit unbekannt -> ganztägiger Termin
        ev.add("dtstart", g["date_only"])
        ev.add("dtend", g["date_only"] + timedelta(days=1))
    lines = [g["comp"]]
    if g["dt"] is None:
        lines.append("Uhrzeit noch offen")
    if g["moved_from"]:
        lines.append("Verlegt vom " + g["moved_from"].strftime("%d.%m.%Y %H:%M"))
    if g["result"]:
        lines.append("Ergebnis: " + g["result"])
    lines.append(g["url"])
    ev.add("description", "\n".join(l for l in lines if l))
    if g["venue"]:
        ev.add("location", g["venue"])
    ev.add("status", "CANCELLED" if g["cancelled"] else "CONFIRMED")
    ev.add("dtstamp", datetime.now(UTC))
    return ev


def is_past(comp, now):
    start = comp.decoded("dtstart")
    if isinstance(start, datetime):
        return start < now
    return start < now.date()


def main():
    now = datetime.now(TZ)
    all_games = []
    for i, url in enumerate(SOURCES, 1):
        print(f"Quelle {i}: {url[:95]}...")
        html = get(url)
        if not html:
            print("   -> nicht abrufbar, übersprungen")
            continue
        if DEBUG:
            Path("debug").mkdir(exist_ok=True)
            Path(f"debug/quelle_{i}.html").write_text(html, encoding="utf-8")
        games = parse_games(html)
        mit = sum(1 for g in games if g["dt"])
        print(f"   -> {len(games)} Spiele gefunden, davon {mit} mit Datum und Uhrzeit")
        all_games += games
        time.sleep(1)

    by_id = merge(all_games)
    if not any(g["dt"] for g in by_id.values()):
        sys.exit("FEHLER: Kein einziges Spiel mit Datum gefunden. Datei wird nicht verändert.")

    # Spielort (und fehlende Daten) von den Spielseiten holen, nur für kommende Spiele
    for g in by_id.values():
        if g["dt"] is None or g["dt"] >= now - timedelta(days=1):
            fetch_details(g)
            time.sleep(1)

    ready = []
    for g in by_id.values():
        if g["dt"] is None and g["date_only"] is None:
            print(f"   Warnung: kein Datum für {g['home']} - {g['away']} ({g['id']})")
        else:
            ready.append(g)

    # Bisherige Datei einlesen: Vergangenes behalten, Verschwundenes markieren
    old = {}
    if OUT_FILE.exists():
        try:
            old = {str(c.get("uid")): c for c in Calendar.from_ical(OUT_FILE.read_bytes()).walk("VEVENT")}
        except Exception as e:
            print(f"   Alte Datei nicht lesbar ({e}), starte neu")
    new_uids = {f"{g['id']}@fussball-kalender" for g in ready}
    old_future = [u for u, c in old.items() if not is_past(c, now)]
    if len(old_future) >= 4 and len([u for u in old_future if u in new_uids]) < len(old_future) * 0.5:
        sys.exit("FEHLER: Deutlich weniger Spiele als beim letzten Lauf. Datei wird nicht verändert.")

    cal = Calendar()
    cal.add("prodid", "-//Fussball Kalender//DE")
    cal.add("version", "2.0")
    cal.add("calscale", "GREGORIAN")
    cal.add("x-wr-calname", CAL_NAME)
    cal.add("x-published-ttl", "PT6H")

    for g in sorted(ready, key=lambda x: x["dt"] or datetime.combine(x["date_only"], datetime.min.time(), TZ)):
        cal.add_component(build_event(g))

    for uid, comp in old.items():
        if uid in new_uids:
            continue
        if not is_past(comp, now):
            summary = str(comp.get("summary", ""))
            comp.pop("summary", None)
            comp.add("summary", "❓ " + summary)
            comp.pop("description", None)
            comp.add("description", "Dieses Spiel steht nicht mehr im fussball.de-Spielplan (abgesetzt?). Bitte dort prüfen.")
        cal.add_component(comp)

    OUT_DIR.mkdir(exist_ok=True)
    OUT_FILE.write_bytes(cal.to_ical())
    (OUT_DIR / "last_run.txt").write_text(now.strftime("%Y-%m-%d %H:%M") + "\n")

    print(f"\nFertig: {len(ready)} Spiele aktuell, {len(old) - len(set(old) & new_uids)} ältere übernommen.")
    for g in sorted(ready, key=lambda x: x["dt"] or datetime.combine(x["date_only"], datetime.min.time(), TZ)):
        wann = g["dt"].strftime("%d.%m.%Y %H:%M") if g["dt"] else f"{g['date_only']:%d.%m.%Y} (Uhrzeit offen)"
        print(f"  {wann}  {g['home']} - {g['away']}  [{g['venue'] or 'Ort unbekannt'}]")


if __name__ == "__main__":
    main()