"""
Pobiera oferty wycieczek ze strony SeePlaces (https://seeplaces.com/pl/)
i zapisuje je do oferty.json. Uruchom przed startem chatu lub okresowo.
"""
import json
import re
import sys
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://seeplaces.com/pl/"
OFERTY_PATH = "oferty.json"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"


def _parse_cena(text):
    """Wyciąga cenę z tekstu, np. 'od 339,95 zł' -> 339.95."""
    if not text:
        return None
    m = re.search(r"od\s+([\d\s]+,[\d]+)\s*zł", text, re.IGNORECASE)
    if not m:
        m = re.search(r"([\d\s]+,[\d]+)\s*zł", text)
    if m:
        s = m.group(1).replace("\xa0", "").replace(" ", "").strip().replace(",", ".")
        try:
            return round(float(s), 2)
        except ValueError:
            pass
    return None


def _parse_czas_trwania(text):
    """'Czas trwania: 9h' -> ('9h', 1), '2d' -> ('2 dni', 2), 'Cały dzień (24h)' -> ('cały dzień', 1)."""
    if not text:
        return "", 1
    text = text.strip().lower()
    # Np. "Czas trwania: 9h" lub "Czas trwania: 2d"
    m = re.search(r"(\d+)\s*h", text)
    if m:
        return f"{m.group(1)}h", 1
    m = re.search(r"(\d+)\s*d", text)
    if m:
        d = int(m.group(1))
        return f"{d} dni", d
    if "cały dzień" in text or "24h" in text:
        return "cały dzień", 1
    return "", 1


def _build_tagi(nazwa, destynacja, czas_trwania):
    """Buduje listę tagów do wyszukiwania."""
    tagi = set()
    for s in (nazwa or "").lower().split():
        if len(s) > 2:
            tagi.add(s)
    for s in (destynacja or "").lower().replace(",", " ").split():
        if len(s) > 2:
            tagi.add(s)
    if czas_trwania:
        tagi.add(czas_trwania.replace(" ", "").lower())
    tagi.update(["wycieczka", "seeplaces", "atrakcje"])
    return list(tagi)


def scrape_strona(url):
    """Pobiera jedną stronę i zwraca listę ofert."""
    oferty = []
    try:
        r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=20)
        r.raise_for_status()
        r.encoding = r.apparent_encoding or "utf-8"
    except Exception as e:
        print(f"Błąd pobierania {url}: {e}", file=sys.stderr)
        return oferty

    soup = BeautifulSoup(r.text, "html.parser")

    # Wycieczki są w linkach <a href="/pl/wycieczki/..."> zawierających kafelek z __title
    def has_tile_title(tag):
        if tag.name != "a" or not tag.get("href"):
            return False
        if "wycieczki" not in tag.get("href", ""):
            return False
        return tag.find(class_=lambda c: c and "excursion-tile" in str(c) and "title" in str(c)) is not None

    for a in soup.find_all(has_tile_title):
        href = (a.get("href") or "").strip()
        full_url = urljoin(BASE_URL, href)
        tile = a

        loc_el = tile.find(class_=lambda c: c and "excursion-tile" in str(c) and "location" in str(c))
        destynacja = loc_el.get_text(strip=True) if loc_el else ""

        title_el = tile.find(class_=lambda c: c and "excursion-tile" in str(c) and "title" in str(c))
        if not title_el:
            title_el = tile.find("h3") or tile.find("h2")
        nazwa = title_el.get_text(strip=True) if title_el else ""

        dur_el = tile.find(class_=lambda c: c and "duration" in str(c))
        dur_text = dur_el.get_text(strip=True) if dur_el else ""
        czas_trwania, dni = _parse_czas_trwania(dur_text)
        if not czas_trwania and dur_text:
            czas_trwania = re.sub(r"^Czas trwania:\s*", "", dur_text, flags=re.I).strip() or ""
        if czas_trwania == "24h":
            czas_trwania = "cały dzień"

        txt = tile.get_text(" ", strip=True)
        cena = _parse_cena(txt)
        if cena is None:
            for node in tile.find_all(string=re.compile(r"zł")):
                cena = _parse_cena(str(node))
                if cena is not None:
                    break

        if not nazwa:
            continue

        tagi = _build_tagi(nazwa, destynacja, czas_trwania)
        opis = f"{nazwa}. {destynacja}. Czas trwania: {czas_trwania or dur_text or '-'}."
        oferty.append({
            "id": len(oferty) + 1,
            "nazwa": nazwa,
            "destynacja": destynacja,
            "opis": opis,
            "cena": cena if cena is not None else 0,
            "dni": dni,
            "czas_trwania": czas_trwania or "",
            "tagi": tagi,
            "url": full_url,
        })

    return oferty


def scrape_wszystko():
    """Pobiera oferty z głównej strony i ewentualnie z listingu."""
    wszystkie = []
    seen_urls = set()

    # Główna – sekcja "Najpopularniejsze wycieczki"
    for o in scrape_strona(BASE_URL):
        if o["url"] not in seen_urls:
            seen_urls.add(o["url"])
            o["id"] = len(wszystkie) + 1
            wszystkie.append(o)

    # Strona z listą wycieczek – więcej ofert
    listing_url = urljoin(BASE_URL, "/pl/wycieczki/")
    for o in scrape_strona(listing_url):
        if o["url"] not in seen_urls:
            seen_urls.add(o["url"])
            o["id"] = len(wszystkie) + 1
            wszystkie.append(o)

    return wszystkie


def main():
    print("Pobieram oferty z SeePlaces...")
    oferty = scrape_wszystko()
    print(f"Pobrano {len(oferty)} ofert.")
    with open(OFERTY_PATH, "w", encoding="utf-8") as f:
        json.dump(oferty, f, ensure_ascii=False, indent=2)
    print(f"Zapisano do {OFERTY_PATH}")


if __name__ == "__main__":
    main()
