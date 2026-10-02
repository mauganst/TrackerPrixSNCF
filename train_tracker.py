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

# À ADAPTER : fais une recherche Paris->Barcelone sur sncf-connect.com, copie l'URL
# des résultats, et remplace origine/destination/date par les placeholders.
URL_TEMPLATE = "https://www.sncf-connect.com/CHANGE-ME?origin={origin}&destination={dest}&date={date}"
# -----------------------------------------------------------------------

TIME_RE = re.compile(r"\b([01]?\d|2[0-3])[:h]([0-5]\d)\b")
PRICE_RE = re.compile(r"(\d{2,3}(?:[.,]\d{1,2})?)\s?€")


def fetch_prices(origin, dest, date):
    """Retourne {"HH:MM": prix_float} pour un trajet. Best-effort : à ajuster
    si SNCF Connect change son affichage."""
    from playwright.sync_api import sync_playwright

    url = URL_TEMPLATE.format(origin=origin, dest=dest, date=date)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(locale="fr-FR")
        page.goto(url, wait_until="networkidle", timeout=60000)
        page.wait_for_timeout(4000)
        # Chaque résultat = un bloc de texte contenant une heure de départ et un prix
        blocks = page.locator("li, article, [role='listitem']").all_inner_texts()
        browser.close()

    out = {}
    for txt in blocks:
        t, pr = TIME_RE.search(txt), PRICE_RE.findall(txt)
        if t and pr:
            hhmm = f"{int(t.group(1)):02d}:{t.group(2)}"
            price = min(float(x.replace(",", ".")) for x in pr)
            out.setdefault(hhmm, price)
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
