#!/usr/bin/env python3
"""Extrait les horaires de la ligne 230 (Fluo Grand Est 67) a l'arret Furdenheim Viridis.

Sortie : bus.json, avec pour chaque jour des N prochains jours
  - "aller"  : departs de Viridis vers Strasbourg  -> [heure_depart_viridis, heure_arrivee_terminus, terminus]
  - "retour" : cars de Strasbourg qui desservent Viridis
               -> [heure_depart_strasbourg, heure_arrivee_viridis, arret_de_depart]

Usage : python build_bus.py [chemin_ou_url_gtfs] [sortie.json]
Bibliotheque standard uniquement.
"""
import csv
import io
import json
import sys
import unicodedata
import urllib.request
import zipfile
from datetime import date, datetime, timedelta, timezone

GTFS_URL = "https://transport.data.gouv.fr/resources/80417/download"
ROUTE = "230"
STOP_KEYWORD = "viridis"
TOWN_KEYWORD = "furdenheim"
CITY_KEYWORD = "strasbourg"  # terminus cote ville
DAYS = 21


def norm(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    return s.lower()


def read_zip(src):
    if src.startswith("http"):
        req = urllib.request.Request(src, headers={"User-Agent": "page-du-matin/1.0"})
        with urllib.request.urlopen(req, timeout=60) as r:
            data = r.read()
    else:
        with open(src, "rb") as f:
            data = f.read()
    return zipfile.ZipFile(io.BytesIO(data))


def table(z, name):
    names = {n.split("/")[-1]: n for n in z.namelist()}
    if name not in names:
        return []
    raw = z.read(names[name]).decode("utf-8-sig")
    return list(csv.DictReader(io.StringIO(raw)))


def to_min(t):
    h, m, *_ = t.strip().split(":")
    return int(h) * 60 + int(m)


def hhmm(m):
    return f"{(m // 60) % 24:02d}:{m % 60:02d}"


def main():
    src = sys.argv[1] if len(sys.argv) > 1 else GTFS_URL
    out = sys.argv[2] if len(sys.argv) > 2 else "bus.json"
    z = read_zip(src)

    stops = {s["stop_id"]: s for s in table(z, "stops.txt")}
    routes = [r for r in table(z, "routes.txt") if r.get("route_short_name", "").strip() == ROUTE]
    if not routes:
        sys.exit(f"Ligne {ROUTE} introuvable dans le GTFS")
    route_ids = {r["route_id"] for r in routes}
    trips = {t["trip_id"]: t for t in table(z, "trips.txt") if t["route_id"] in route_ids}

    def stop_name(sid):
        s = stops.get(sid, {})
        name = s.get("stop_name", sid)
        parent = stops.get(s.get("parent_station") or "", {})
        return name, parent.get("stop_name", "")

    # Horaires par course
    seq = {}
    for st in table(z, "stop_times.txt"):
        if st["trip_id"] in trips:
            t = st.get("departure_time") or st.get("arrival_time")
            a = st.get("arrival_time") or t
            if not t:
                continue
            seq.setdefault(st["trip_id"], []).append(
                (int(st["stop_sequence"]), st["stop_id"], to_min(a), to_min(t)))

    def is_viridis(sid):
        n, p = stop_name(sid)
        full = norm(n + " " + p)
        return STOP_KEYWORD in full

    def in_city(sid):
        n, p = stop_name(sid)
        full = norm(n + " " + p)
        return CITY_KEYWORD in full or "halles" in full

    services = {}  # service_id -> {"aller": [...], "retour": [...]}
    for tid, rows in seq.items():
        rows.sort()
        idx = [i for i, r in enumerate(rows) if is_viridis(r[1])]
        if not idx:
            continue
        i = idx[0]
        _, sid, arr, dep = rows[i]
        first, last = rows[0], rows[-1]
        last_name = stop_name(last[1])[0]
        first_name = stop_name(first[1])[0]
        sv = services.setdefault(trips[tid]["service_id"], {"aller": [], "retour": []})
        if i < len(rows) - 1 and in_city(last[1]):
            sv["aller"].append([dep, last[2], last_name])
        elif i > 0 and in_city(first[1]):
            sv["retour"].append([first[3], arr, first_name])

    cal = {c["service_id"]: c for c in table(z, "calendar.txt")}
    exc = {}
    for c in table(z, "calendar_dates.txt"):
        exc.setdefault(c["date"], {})[c["service_id"]] = c["exception_type"].strip()

    wd = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
    today = datetime.now(timezone(timedelta(hours=1))).date()
    days = {}
    for k in range(DAYS):
        d = today + timedelta(days=k)
        ds = d.strftime("%Y%m%d")
        active = set()
        for sid, c in cal.items():
            if c["start_date"] <= ds <= c["end_date"] and c.get(wd[d.weekday()], "0").strip() == "1":
                active.add(sid)
        for sid, typ in exc.get(ds, {}).items():
            if typ == "1":
                active.add(sid)
            elif typ == "2":
                active.discard(sid)
        aller, retour = [], []
        for sid in active:
            if sid in services:
                aller += services[sid]["aller"]
                retour += services[sid]["retour"]
        aller = sorted({tuple(x) for x in aller})
        retour = sorted({tuple(x) for x in retour})
        days[d.isoformat()] = {
            "aller": [[hhmm(a), hhmm(b), c] for a, b, c in aller],
            "retour": [[hhmm(a), hhmm(b), c] for a, b, c in retour],
        }

    viridis = sorted({stop_name(s)[0] for rows in seq.values() for _, s, _, _ in rows if is_viridis(s)})
    payload = {
        "ligne": ROUTE,
        "arret": viridis[0] if viridis else "Furdenheim Viridis",
        "source": "GTFS Fluo Grand Est 67 (transport.data.gouv.fr), horaires théoriques",
        "genere": datetime.now(timezone.utc).isoformat(timespec="minutes"),
        "jours": days,
    }
    with open(out, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
    n = sum(len(v["aller"]) + len(v["retour"]) for v in days.values())
    print(f"{out} : {len(days)} jours, {n} passages, arrêt = {payload['arret']}")
    if n == 0:
        sys.exit("Aucun passage trouvé : vérifier le nom d'arrêt ou la ligne")


if __name__ == "__main__":
    main()
