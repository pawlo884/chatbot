"""
Pobiera oferty wycieczek ze strony SeePlaces (https://seeplaces.com/pl/)
i zapisuje je do oferty.json. Źródło URL-i: sitemap.xml (wszystko pod /pl/wycieczki/)
lub fallback: główna strona + listing. Uruchom przed startem chatu lub okresowo.
"""
import json
import os
import re
import sys
import xml.etree.ElementTree as ET
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://seeplaces.com/pl/"
OFERTY_PATH = "oferty.json"
SITEMAP_PATH = "sitemap.xml"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
WYCIECZKI_PREFIX = "https://seeplaces.com/pl/wycieczki/"


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


def _wczytaj_locs_z_sitemap(path):
    """Zwraca listę URL-i z sitemap: <loc> pod wycieczki (bez ?) oraz z ? – dodajemy bazę (bez query) jako listing 1–2 seg."""
    urls = []
    try:
        tree = ET.parse(path)
        root = tree.getroot()
        ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
        for loc in root.findall(".//sm:loc", ns):
            if loc.text is None:
                continue
            u = loc.text.strip()
            if not u.startswith(WYCIECZKI_PREFIX):
                continue
            if "?" in u:
                base = u.split("?")[0].strip().rstrip("/")
                if not base or base == WYCIECZKI_PREFIX.rstrip("/"):
                    continue
                suf = base[len(WYCIECZKI_PREFIX) :].lstrip("/")
                segmenty = [s for s in suf.split("/") if s]
                if len(segmenty) in (1, 2):
                    urls.append(base)
                continue
            suf = u[len(WYCIECZKI_PREFIX) :].rstrip("/")
            if not suf:
                continue
            urls.append(u)
    except Exception:
        try:
            with open(path, encoding="utf-8") as f:
                content = f.read()
            for m in re.finditer(r"<loc>(https://seeplaces\.com/pl/wycieczki/[^<]+)</loc>", content):
                u = m.group(1).strip()
                if not u.startswith(WYCIECZKI_PREFIX):
                    continue
                if "?" in u:
                    base = u.split("?")[0].strip().rstrip("/")
                    if base and base != WYCIECZKI_PREFIX.rstrip("/"):
                        suf = base[len(WYCIECZKI_PREFIX) :].lstrip("/")
                        segmenty = [s for s in suf.split("/") if s]
                        if len(segmenty) in (1, 2):
                            urls.append(base)
                    continue
                suf = u[len(WYCIECZKI_PREFIX) :].rstrip("/")
                if suf:
                    urls.append(u)
        except Exception:
            pass
    return urls


def wczytaj_url_listingow_z_sitemap(path):
    """URL-e stron z kafelkami: 1 segment (np. wycieczki/tunezja/) lub 2 (np. wycieczki/madera/madera/)."""
    all_urls = _wczytaj_locs_z_sitemap(path)
    listing = []
    seen = set()
    for u in all_urls:
        suf = u[len(WYCIECZKI_PREFIX) :].rstrip("/")
        segmenty = [s for s in suf.split("/") if s]
        if len(segmenty) not in (1, 2):
            continue
        if u not in seen:
            seen.add(u)
            listing.append(u)
    return listing


def wczytaj_url_ofert_z_sitemap(path):
    """Z sitemap.xml wyciąga URL-e stron pojedynczych ofert (3+ segmenty: kraj/miasto/slug)."""
    all_urls = _wczytaj_locs_z_sitemap(path)
    return [u for u in all_urls if len([s for s in u[len(WYCIECZKI_PREFIX) :].rstrip("/").split("/") if s]) >= 3]


def scrape_pojedyncza_strona(url):
    """Pobiera stronę szczegółów jednej wycieczki i zwraca słownik oferty lub None."""
    try:
        r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=25)
        r.raise_for_status()
        r.encoding = r.apparent_encoding or "utf-8"
    except Exception as e:
        print(f"Błąd {url}: {e}", file=sys.stderr)
        return None

    soup = BeautifulSoup(r.text, "html.parser")

    nazwa = ""
    h1 = soup.find("h1")
    if h1:
        nazwa = h1.get_text(strip=True)
    if not nazwa:
        meta_og = soup.find("meta", property="og:title")
        if meta_og and meta_og.get("content"):
            nazwa = meta_og["content"].split("|")[0].strip()

    destynacja = ""
    for a in soup.find_all("a", href=re.compile(r"/pl/wycieczki/[^/]+/?$")):
        t = a.get_text(strip=True)
        if t and len(t) < 80:
            destynacja = t
            break
    if not destynacja:
        for el in soup.find_all(class_=lambda c: c and "breadcrumb" in str(c).lower()):
            parts = [x.get_text(strip=True) for x in el.find_all(["a", "span"]) if x.get_text(strip=True)]
            if len(parts) >= 2:
                destynacja = ", ".join(parts[:2])
                break

    txt = soup.get_text(" ", strip=True)
    cena = _parse_cena(txt)
    dur_text = ""
    m = re.search(r"Czas trwania\s*[:\s]*(\d+\s*h|\d+\s*d|cały dzień[^.]*?)", txt, re.I | re.DOTALL)
    if m:
        dur_text = m.group(1).strip().split()[0] if m.group(1) else ""
    czas_trwania, dni = _parse_czas_trwania("Czas trwania: " + dur_text) if dur_text else ("", 1)
    if czas_trwania == "24h":
        czas_trwania = "cały dzień"

    opis = nazwa
    if destynacja:
        opis += f". {destynacja}."
    if czas_trwania:
        opis += f" Czas trwania: {czas_trwania}."
    desc_meta = soup.find("meta", attrs={"name": "description"})
    if desc_meta and desc_meta.get("content"):
        opis = (desc_meta["content"] or "")[:400] or opis
    else:
        for h in soup.find_all(["h2", "h3"]):
            if "opis" in (h.get_text() or "").lower() or "program" in (h.get_text() or "").lower():
                par = h.find_next_sibling()
                if par:
                    opis = (par.get_text(" ", strip=True) or "")[:400] or opis
                break

    if not nazwa:
        return None

    tagi = _build_tagi(nazwa, destynacja, czas_trwania)
    return {
        "id": 0,
        "nazwa": nazwa,
        "destynacja": destynacja,
        "opis": opis,
        "cena": cena if cena is not None else 0,
        "dni": dni,
        "czas_trwania": czas_trwania or "",
        "tagi": tagi,
        "url": url,
    }


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


def scrape_szczegoly_ofert(oferty_z_kafelkow, delay=0.35):
    """Dla każdej oferty (z kafelka) otwiera stronę szczegółów i pobiera pełne dane do JSON."""
    import time
    wynik = []
    total = len(oferty_z_kafelkow)
    for i, o in enumerate(oferty_z_kafelkow, 1):
        url = o.get("url")
        if not url:
            o["id"] = len(wynik) + 1
            wynik.append(o)
            continue
        detail = scrape_pojedyncza_strona(url)
        if detail:
            detail["id"] = len(wynik) + 1
            detail["url"] = url
            wynik.append(detail)
        else:
            o["id"] = len(wynik) + 1
            wynik.append(o)
        if (i % 25 == 0) or i == total:
            print(f"  Szczegóły: {i}/{total} ofert")
        time.sleep(delay)
    return wynik


def scrape_z_sitemap(sitemap_path, otwieraj_kafelki=True):
    """Pobiera oferty ze stron listingowych; opcjonalnie otwiera każdy kafelek i scrapuje pełne dane."""
    import time
    listing_urls = wczytaj_url_listingow_z_sitemap(sitemap_path)
    if not listing_urls:
        print("Brak URL-i listingów w sitemap. Próba pojedynczych ofert...", file=sys.stderr)
        urls = wczytaj_url_ofert_z_sitemap(sitemap_path)
        if not urls:
            return scrape_wszystko()
        seen = set()
        oferty = []
        for i, url in enumerate(urls, 1):
            if url in seen:
                continue
            seen.add(url)
            o = scrape_pojedyncza_strona(url)
            if o:
                o["id"] = len(oferty) + 1
                oferty.append(o)
            if i % 50 == 0:
                print(f"  {i}/{len(urls)} ...")
            time.sleep(0.25)
        return oferty
    print(f"Sitemap: {len(listing_urls)} stron listingowych (kafelki). Pobieram...")
    seen_urls = set()
    oferty = []
    for i, url in enumerate(listing_urls, 1):
        for o in scrape_strona(url):
            if o["url"] in seen_urls:
                continue
            seen_urls.add(o["url"])
            o["id"] = len(oferty) + 1
            oferty.append(o)
        if (i % 10 == 0) or i == len(listing_urls):
            print(f"  {i}/{len(listing_urls)} listingów → łącznie {len(oferty)} ofert")
        time.sleep(0.3)
    if otwieraj_kafelki and oferty:
        print(f"Otwieram każdy kafelek ({len(oferty)} stron) i pobieram pełne dane...")
        oferty = scrape_szczegoly_ofert(oferty)
    return oferty


def main():
    print("Pobieram oferty z SeePlaces...")
    if os.path.isfile(SITEMAP_PATH):
        oferty = scrape_z_sitemap(SITEMAP_PATH)
    else:
        oferty = scrape_wszystko()
    print(f"Pobrano {len(oferty)} ofert.")
    with open(OFERTY_PATH, "w", encoding="utf-8") as f:
        json.dump(oferty, f, ensure_ascii=False, indent=2)
    print(f"Zapisano do {OFERTY_PATH}")


if __name__ == "__main__":
    main()
