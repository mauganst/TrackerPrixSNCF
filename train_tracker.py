#!/usr/bin/env python3
"""
Suivi des prix de trains Paris <-> Barcelone, avec notifications push (ntfy.sh).

Installation (une fois) :
    pip install playwright requests
    playwright install chromium

Lancement :
    python train_tracker.py          # tourne en boucle, 1 passage par heure
    python train_tracker.py --once   # un seul passage (pour cron)
    python train_tracker.py --test   # envoie une notif de test

Notifications : installe l'app "ntfy" sur ton tel, abonne-toi au topic NTFY_TOPIC.
"""
import argparse, json, os, re, sys, time
from datetime import datetime
from pathlib import Path

import requests

# ----------------------------- CONFIG ---------------------------------
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "maugan-trains-bcn-2026")  # secret GitHub en prod
INTERVAL_MIN = 60                       # fréquence de vérification
STATE_FILE = Path("prices_state.json")
NOTIFY_HOURLY_SUMMARY = True            # récap toutes les heures
NOTIFY_ON_CHANGE = True                 # alerte dès qu'un prix change

TRIPS = [
    ("PARIS", "BARCELONE", d) for d in ("2026-12-29", "2026-12-30", "2026-12-31")
] + [
    ("BARCELONE", "PARIS", d) for d in ("2027-01-01", "2027-01-02", "2027-01-03")
]

HOME_URL = "https://www.sncf-connect.com/"
# -----------------------------------------------------------------------

API_MARK = "/bff/api/v1/itineraries/"


def search_on_site(page, origin, dest, date):
    """Remplit le formulaire de SNCF Connect. SÉLECTEURS À AJUSTER d'après debug.png / le journal."""
    page.goto(HOME_URL, wait_until="domcontentloaded", timeout=60000)
    for label in ("Tout accepter", "Accepter", "Continuer sans accepter"):
        btn = page.get_by_role("button", name=label)
        if btn.count():
            btn.first.click()
            break
    page.get_by_label("Départ", exact=False).first.fill(origin.title())
    page.get_by_role("option").first.click()
    page.get_by_label("Arrivée", exact=False).first.fill(dest.title())
    page.get_by_role("option").first.click()
    page.get_by_label("Date", exact=False).first.fill(datetime.strptime(date, "%Y-%m-%d").strftime("%d/%m/%Y"))
    page.get_by_role("button", name=re.compile("Rechercher", re.I)).first.click()


def parse_proposals(payload, date):
    """Extrait {"HH:MM": prix} des trains du jour demandé depuis la réponse JSON de SNCF Connect."""
    out = {}
    props = payload["output"]["longDistance"]["proposals"]["proposals"]
    for pr in props:
        day, hhmm = pr["travelId"][:10], pr["travelId"][11:16]   # ex. "2026-12-30T06:56_9711"
        if day == date and pr.get("status", {}).get("isBookable", True):
            out[hhmm] = float(pr["bestPrice"]["value"])
    return out


def fetch_prices(origin, dest, date):
    from playwright.sync_api import sync_playwright

    captured = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(locale="fr-FR")
        page.on("response", lambda r: captured.append(r) if API_MARK in r.url else None)
        try:
            search_on_site(page, origin, dest, date)
            page.wait_for_timeout(8000)
        finally:
            if not captured:
                page.screenshot(path=f"debug-{origin}-{date}.png")
        payloads = []
        for r in captured:
            try:
                payloads.append(r.json())
            except Exception:
                pass
        browser.close()

    out = {}
    for pl in payloads:
        try:
            out.update(parse_proposals(pl, date))
        except (KeyError, TypeError):
            continue
    return out


def notify(title, message, priority="default"):
    try:
        requests.post(
            f"https://ntfy.sh/{NTFY_TOPIC}",
            data=message.encode("utf-8"),
            headers={"Title": title.encode("utf-8"), "Priority": priority},
            timeout=15,
        )
    except requests.RequestException as e:
        print(f"[notif] échec : {e}", file=sys.stderr)


def load_state():
    return json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {}


def run_once():
    state, changes, lines = load_state(), [], []
    for origin, dest, date in TRIPS:
        key = f"{origin}-{dest}-{date}"
        try:
            prices = fetch_prices(origin, dest, date)
        except Exception as e:
            lines.append(f"{date} {origin[:3]}→{dest[:3]} : erreur ({e.__class__.__name__})")
            continue
        old = state.get(key, {})
        for hhmm, price in prices.items():
            if hhmm in old and old[hhmm] != price:
                changes.append(f"{date} {hhmm} {origin.title()}→{dest.title()} : {old[hhmm]:.0f}€ → {price:.0f}€")
        state[key] = prices
        lines.append(f"{date} {origin[:3]}→{dest[:3]} : " +
                     (" | ".join(f"{h} {p:.0f}€" for h, p in sorted(prices.items())) or "aucun résultat"))
    STATE_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False))
    stamp = datetime.now().strftime("%d/%m %H:%M")
    print(f"--- {stamp} ---\n" + "\n".join(lines))
    if NOTIFY_ON_CHANGE and changes:
        notify("Prix train modifié", "\n".join(changes), priority="high")
    if NOTIFY_HOURLY_SUMMARY:
        notify(f"Trains BCN – {stamp}", "\n".join(lines))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--test", action="store_true")
    a = ap.parse_args()
    if a.test:
        notify("Test", "Les notifications fonctionnent ✅")
    elif a.once:
        run_once()
    else:
        while True:
            run_once()
            time.sleep(INTERVAL_MIN * 60)
