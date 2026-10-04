#!/usr/bin/env python3
"""Download the current Dutch supermarket offer brochures (reclamefolders).

Stores: Albert Heijn, Jumbo, Vomar, LIDL, ALDI.
Each store publishes on a different platform, so every store has its own
resolver. All of them are plain HTTP -- no browser and no login required.

Usage:
    python3 fetch_supermarket_folders.py              # current week's folder per store
    python3 fetch_supermarket_folders.py --only AH LIDL
    python3 fetch_supermarket_folders.py --outdir /tmp/folders
    python3 fetch_supermarket_folders.py --selfcheck  # offline parser checks

Albert Heijn publishes no PDF; its folder is served as page images, which are
composed into a PDF locally with ImageMagick (`magick`).
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
TIMEOUT = 60

# Albert Heijn blocks direct requests (Akamai 403), so its folder is read from
# the Folder24/Prisero aggregator, which mirrors the same weekly pages.
AH_LISTING = "https://folder24.nl/fb/23854449356420276/albert-heijn"
AH_MONTHS = {
    "januari": 1, "februari": 2, "maart": 3, "april": 4, "mei": 5, "juni": 6,
    "juli": 7, "augustus": 8, "september": 9, "oktober": 10, "november": 11,
    "december": 12,
}
JUMBO_PAGE = "https://www.jumbo.com/folder"
JUMBO_PUBLITAS = "https://view.publitas.com/jumbo-supermarkten/"
VOMAR_PAGE = "https://www.vomar.nl/folder"
LIDL_FOLDERS = "https://www.lidl.nl/c/service-contact-folders/s10008124"
LIDL_API = "https://endpoints.leaflets.schwarz/v4/flyer?flyer_identifier={}&region_id=0"
ALDI_FOLDERS = "https://www.aldi.nl/folders"


def fetch(url: str, *, as_json: bool = False, accept: str | None = None):
    """GET a URL and return text (or parsed JSON). Raises on HTTP errors."""
    headers = {"User-Agent": UA, "Accept-Language": "nl-NL,nl;q=0.9"}
    if accept:
        headers["Accept"] = accept
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        raw = resp.read()
    text = raw.decode("utf-8", errors="replace")
    return json.loads(text) if as_json else text


def download(url: str, dest: Path, *, referer: str | None = None) -> int:
    """Download to dest with curl (retry + resume). Returns bytes on disk."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["curl", "-sSL", "--fail", "--retry", "3", "--retry-delay", "2",
           "-C", "-", "-A", UA, "-o", str(dest)]
    if referer:
        cmd += ["-e", referer]
    cmd.append(url)
    subprocess.run(cmd, check=True)
    size = dest.stat().st_size
    if size == 0:
        raise RuntimeError(f"empty download: {url}")
    return size


# --------------------------------------------------------------------------
# Pure parsers (kept separate so --selfcheck can exercise them offline)
# --------------------------------------------------------------------------

def ah_folder_ids(html: str) -> list[str]:
    """Weekly folder page ids from the Folder24 AH listing (`/folder/<n>`)."""
    return sorted(set(re.findall(r"/albert-heijn/folder/(\d+)", html)), key=int)


def ah_folder_start_date(title_html: str) -> dt.date | None:
    """Start date of an AH folder from its page title, e.g. 'Van maandag 28 september 2026'."""
    m = re.search(r"Van\s+\w+\s+(\d{1,2})\s+([a-z]+)\s+(\d{4})", title_html, re.I)
    if not m:
        return None
    day, month, year = m.group(1), m.group(2).lower(), m.group(3)
    if month not in AH_MONTHS:
        return None
    return dt.date(int(year), AH_MONTHS[month], int(day))


def ah_pick_current(candidates: list[tuple[dt.date, str]], today: dt.date) -> str | None:
    """Newest AH folder whose week has already started.

    The listing date is the *publication* date, not the week the folder is
    valid for, so pick by the start date printed on the folder itself. This
    also skips teaser leaflets (e.g. a Disneyland promo) that are published
    the same day as the real weekly bonus folder.
    """
    started = [(d, fid) for d, fid in candidates if d <= today]
    if not started:
        return None
    return max(started, key=lambda pair: pair[0])[1]


def ah_page_hash(html: str) -> str | None:
    """Prisero image-bundle hash that serves the AH folder pages."""
    m = re.search(r"priserocdn\.com/m2/nl/([a-f0-9]{64})/\d+\.jpg", html)
    return m.group(1) if m else None


def publitas_pdf_url(html: str) -> str | None:
    """Attachment PDF link embedded in a Publitas publication page."""
    m = re.search(r'https://view\.publitas\.com/\d+/\d+/pdfs/[^"\'\s]+?\.pdf[^"\'\s]*', html)
    return m.group(0) if m else None


def publitas_title(html: str) -> str:
    m = re.search(r"<title>(.*?)</title>", html, re.S | re.I)
    if not m:
        return ""
    return re.sub(r"\s*-\s*Pagina\s+\d+\s*$", "", m.group(1).strip(), flags=re.I)


def jumbo_folder_name(html: str) -> str | None:
    """Publitas publication name used by Jumbo's active folder widget."""
    m = re.search(r'id="(jumbo-actiefolder-[a-z0-9-]+)"', html)
    return m.group(1) if m else None


def vomar_publitas_slug(html: str) -> str | None:
    m = re.search(r"view\.publitas\.com/(folder-deze-week[a-z0-9-]*)", html)
    return m.group(1) if m else None


def lidl_flyer_slugs(html: str) -> list[str]:
    """Flyer identifiers linked from the LIDL folder overview page."""
    return sorted(set(re.findall(r"/l/folders/([a-z0-9-]+)", html)))


def lidl_is_main_weekly(slug: str) -> bool:
    return bool(re.fullmatch(r"hah-wk\d+-\d{4}", slug))


def lidl_pick_current(flyers: list[dict], today: dt.date) -> dict | None:
    """Main weekly flyer whose *offer* window contains today.

    Weekend/non-food/special flyers are skipped. The offer dates are used
    rather than the publication dates, because LIDL publishes next week's
    folder a few days early: on 2 Oct the week-41 flyer is already live but
    its offers only start on 5 Oct.
    """
    def window(f: dict) -> tuple[dt.date, dt.date] | None:
        start, end = f.get("offerStartDate"), f.get("offerEndDate")
        if not start or not end:
            return None
        return dt.date.fromisoformat(start), dt.date.fromisoformat(end)

    live = []
    for f in flyers:
        if not lidl_is_main_weekly(f.get("_slug", "")):
            continue
        w = window(f)
        if w and w[0] <= today <= w[1]:
            live.append((w[0], f))
    return min(live, key=lambda pair: pair[0])[1] if live else None


def aldi_current_folder_path(html: str) -> str | None:
    """CMS reference for ALDI's 'Folder van deze week' tile."""
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    if not m:
        return None
    data = json.loads(m.group(1))
    tiles = (data.get("props", {}).get("pageProps", {})
                 .get("page", {}).get("leaflets", {}).get("0", {}).get("tiles", {}))
    for key, tile in tiles.items():
        if key.startswith("@") or not isinstance(tile, dict):
            continue
        if "deze week" in (tile.get("title") or "").lower():
            return (tile.get("reference") or {}).get("path")
    return None


def aldi_pdf_url(html: str) -> str | None:
    """ipaper PDF endpoint behind an ALDI folder page."""
    m = re.search(r"(https://folder\.aldi\.nl/[^\"'\\ ]+/)(?:GetPDF\.ashx)?", html)
    return m.group(1) + "GetPDF.ashx" if m else None


def aldi_title(html: str) -> str:
    """Human-readable folder name.

    `flipbookTitle` is an internal code (e.g. '2026_CW40_Mo_Promotion_Weekfolder_Magnolia'),
    so prefer the folder slug that ALDI's CMS points at.
    """
    m = re.search(r"https://folder\.aldi\.nl/(?:[a-z-]+/)*\d{4}/([^/\"']+)/", html)
    if m:
        return m.group(1)
    m = re.search(r'"flipbookTitle":"([^"]+)"', html)
    return m.group(1) if m else "folder"


# --------------------------------------------------------------------------
# Resolvers -- each returns (label, downloader) or raises
# --------------------------------------------------------------------------

def resolve_ah(outdir: Path, today: dt.date) -> Path:
    ids = ah_folder_ids(fetch(AH_LISTING))
    if not ids:
        raise RuntimeError("AH: no folder pages found in listing")

    candidates: list[tuple[dt.date, str]] = []
    pages: dict[str, str] = {}
    for fid in ids:
        page = fetch(f"{AH_LISTING}/folder/{fid}")
        start = ah_folder_start_date(page)
        if start:
            candidates.append((start, fid))
            pages[fid] = page

    fid = ah_pick_current(candidates, today)
    if not fid:
        raise RuntimeError("AH: no folder whose week has started")
    page = pages[fid]
    start = next(d for d, i in candidates if i == fid)

    bundle = ah_page_hash(page)
    if not bundle:
        raise RuntimeError("AH: no page images found")

    tmp = outdir / ".ah-pages"
    tmp.mkdir(parents=True, exist_ok=True)
    images, n = [], 0
    while True:  # pages are numbered 0..N-1; the first missing page returns 403
        dest = tmp / f"{n:02d}.jpg"
        try:
            download(f"https://priserocdn.com/m2/nl/{bundle}/{n}.jpg", dest, referer=AH_LISTING)
        except subprocess.CalledProcessError:
            break
        images.append(dest)
        n += 1
        if n > 200:
            raise RuntimeError("AH: page probe did not terminate")
    if not images:
        raise RuntimeError("AH: no folder pages downloaded")

    pdf = outdir / f"AH-bonusfolder-{start.isoformat()}.pdf"
    magick = shutil.which("magick") or shutil.which("convert")
    if not magick:
        raise RuntimeError("AH: ImageMagick (magick) required to compose the PDF")
    subprocess.run([magick, *map(str, images), "-quality", "88", str(pdf)], check=True)
    shutil.rmtree(tmp, ignore_errors=True)
    print(f"    week of {start.isoformat()} -- {len(images)} pages -> {pdf.name}")
    return pdf


def resolve_jumbo(outdir: Path) -> Path:
    name = jumbo_folder_name(fetch(JUMBO_PAGE))
    if not name:
        raise RuntimeError("Jumbo: no active folder on the folder page")
    page = fetch(JUMBO_PUBLITAS + name)
    url = publitas_pdf_url(page)
    if not url:
        raise RuntimeError("Jumbo: no PDF link in publication")
    pdf = outdir / f"Jumbo-{name}.pdf"
    download(url, pdf, referer=JUMBO_PUBLITAS + name)
    print(f"    {publitas_title(page)}")
    return pdf


def resolve_vomar(outdir: Path) -> Path:
    slug = vomar_publitas_slug(fetch(VOMAR_PAGE))
    if not slug:
        raise RuntimeError("Vomar: no folder link on the folder page")
    url_page = f"https://view.publitas.com/{slug}"
    page = fetch(url_page)
    url = publitas_pdf_url(page)
    if not url:
        raise RuntimeError("Vomar: no PDF link in publication")
    pdf = outdir / f"Vomar-{slug}.pdf"
    download(url, pdf, referer=url_page)
    print(f"    {publitas_title(page)}")
    return pdf


def resolve_lidl(outdir: Path, today: dt.date) -> list[Path]:
    slugs = lidl_flyer_slugs(fetch(LIDL_FOLDERS))
    if not slugs:
        raise RuntimeError("LIDL: no flyers linked from the overview page")
    flyers = []
    for slug in slugs:
        try:
            f = fetch(LIDL_API.format(slug), as_json=True).get("flyer") or {}
        except urllib.error.HTTPError:
            continue
        if f.get("pdfUrl"):
            f["_slug"] = slug
            flyers.append(f)
    pick = lidl_pick_current(flyers, today)
    if not pick:
        raise RuntimeError("LIDL: no flyer valid today")
    name = re.sub(r"[^A-Za-z0-9]+", "-", f"{pick['name']}-{pick['title']}").strip("-")
    pdf = outdir / f"LIDL-{name}-{pick['offerStartDate']}.pdf"
    download(pick["pdfUrl"], pdf)
    print(f"    {pick['name']} | {pick['title']} | "
          f"offers {pick['offerStartDate']} ~ {pick['offerEndDate']}")
    return [pdf]


def resolve_aldi(outdir: Path) -> Path:
    path = aldi_current_folder_path(fetch(ALDI_FOLDERS))
    if not path:
        raise RuntimeError("ALDI: no 'deze week' folder tile found")
    page_url = f"https://www.aldi.nl{path}"
    page = fetch(page_url)
    url = aldi_pdf_url(page)
    if not url:
        raise RuntimeError("ALDI: no PDF endpoint on the folder page")
    title = aldi_title(page)
    label = re.sub(r"[^A-Za-z0-9]+", "-", title).strip("-") or "folder"
    pdf = outdir / f"ALDI-{label}.pdf"
    download(url, pdf, referer=page_url)
    print(f"    {title}")
    return pdf


RESOLVERS = {
    "AH": lambda out, today: [resolve_ah(out, today)],
    "JUMBO": lambda out, today: [resolve_jumbo(out)],
    "VOMAR": lambda out, today: [resolve_vomar(out)],
    "LIDL": lambda out, today: resolve_lidl(out, today),
    "ALDI": lambda out, today: [resolve_aldi(out)],
}


# --------------------------------------------------------------------------
# Offline self-check of the parsers
# --------------------------------------------------------------------------

def selfcheck() -> None:
    listing = """
      <a href="/fb/23854449356420276/albert-heijn/folder/1">
      <a href="/fb/23854449356420276/albert-heijn/folder/2">
      <a href="/fb/23854449356420276/albert-heijn/folder/3">
    """
    assert ah_folder_ids(listing) == ["1", "2", "3"]
    assert ah_folder_ids("<html></html>") == []

    assert ah_folder_start_date("<title>ALBERT HEIJN folder - Van maandag 28 september 2026</title>") == \
        dt.date(2026, 9, 28)
    assert ah_folder_start_date("<title>ALBERT HEIJN folders en aanbiedingen</title>") is None
    assert ah_folder_start_date("<title>Van donderdag 1 oktober 2026</title>") == dt.date(2026, 10, 1)

    # The listing date is a publication date, so selection uses the start date
    # printed on the folder. A teaser published the same day as the weekly
    # folder must not win, and a folder starting later this week must not either.
    today = dt.date(2026, 10, 2)
    candidates = [
        (dt.date(2026, 9, 21), "1"),
        (dt.date(2026, 9, 28), "2"),   # week 40: the current one
        (dt.date(2026, 10, 5), "3"),   # week 41: starts next Monday
    ]
    assert ah_pick_current(candidates, today) == "2"
    assert ah_pick_current(candidates, dt.date(2026, 10, 6)) == "3"
    assert ah_pick_current(candidates, dt.date(2026, 9, 1)) is None

    assert ah_page_hash("x priserocdn.com/m2/nl/" + "a" * 64 + "/0.jpg y") == "a" * 64

    assert jumbo_folder_name('<div id="jumbo-actiefolder-rtek-40" class="fep-publitas">') == \
        "jumbo-actiefolder-rtek-40"
    assert vomar_publitas_slug('<a href="https://view.publitas.com/folder-deze-week?publitas_embed=maximized">') \
        == "folder-deze-week"

    pub = '<a href="https://view.publitas.com/171/3381846/pdfs/521560e9-287f-4c50-8cd5-f1975d60d341.pdf?x=1">'
    assert publitas_pdf_url(pub).startswith("https://view.publitas.com/171/3381846/pdfs/")
    assert publitas_title("<title>Jumbo actiefolder - week 40 - Pagina 1</title>") == \
        "Jumbo actiefolder - week 40"

    assert lidl_is_main_weekly("hah-wk40-2026")
    assert not lidl_is_main_weekly("hah-wk40-nf-2026")
    assert not lidl_is_main_weekly("special-deluxe-herfst-2026")
    assert lidl_flyer_slugs('<a href="/l/folders/hah-wk40-2026/ar/0">') == ["hah-wk40-2026"]

    today = dt.date(2026, 10, 2)
    flyers = [
        {"_slug": "hah-wk40-2026", "offerStartDate": "2026-09-28", "offerEndDate": "2026-10-04"},
        {"_slug": "hah-wk41-2026", "offerStartDate": "2026-10-05", "offerEndDate": "2026-10-11"},
        {"_slug": "hah-wk40-nf-2026", "offerStartDate": "2026-09-30", "offerEndDate": "2026-10-06"},
    ]
    # week 41 is already published on 2 Oct but its offers start on 5 Oct
    assert lidl_pick_current(flyers, today)["_slug"] == "hah-wk40-2026"
    assert lidl_pick_current(flyers, dt.date(2026, 10, 8))["_slug"] == "hah-wk41-2026"
    assert lidl_pick_current(flyers, dt.date(2027, 1, 1)) is None

    next_data = ('<script id="__NEXT_DATA__" type="application/json">' + json.dumps({
        "props": {"pageProps": {"page": {"leaflets": {"0": {"tiles": {
            "tiles0": {"title": "Folder van vorige week",
                       "reference": {"path": "/folders/folder-van-vorige-week"}},
            "tiles1": {"title": "Folder van deze week",
                       "reference": {"path": "/folders/folder-van-deze-week"}},
        }}}}}}
    }) + "</script>")
    assert aldi_current_folder_path(next_data) == "/folders/folder-van-deze-week"
    assert aldi_current_folder_path("<html></html>") is None
    assert aldi_pdf_url('<a href="https://folder.aldi.nl/fixed/2026/Folder-week-40-magnolia/">') == \
        "https://folder.aldi.nl/fixed/2026/Folder-week-40-magnolia/GetPDF.ashx"
    assert aldi_title('<a href="https://folder.aldi.nl/fixed/2026/Folder-week-40-magnolia/">') == \
        "Folder-week-40-magnolia"

    print("selfcheck OK")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--only", nargs="+", metavar="STORE",
                    help=f"subset of {', '.join(RESOLVERS)}")
    ap.add_argument("--outdir", type=Path, default=None,
                    help="target directory (default: folders/<today>)")
    ap.add_argument("--selfcheck", action="store_true", help="run offline parser checks")
    args = ap.parse_args()

    if args.selfcheck:
        selfcheck()
        return 0

    today = dt.date.today()
    outdir = args.outdir or Path(__file__).resolve().parent / "folders" / today.isoformat()
    stores = [s.upper() for s in (args.only or RESOLVERS)]
    unknown = [s for s in stores if s not in RESOLVERS]
    if unknown:
        print(f"unknown store(s): {', '.join(unknown)}", file=sys.stderr)
        return 2

    outdir.mkdir(parents=True, exist_ok=True)
    print(f"Downloading current folders into {outdir}\n")
    failures = []
    for store in stores:
        print(f"[{store}]")
        try:
            files = RESOLVERS[store](outdir, today)
            for f in files:
                print(f"    OK  {f.name}  ({f.stat().st_size / 1e6:.1f} MB)")
        except Exception as exc:  # one store failing must not hide the others
            failures.append(store)
            print(f"    FAIL {type(exc).__name__}: {exc}", file=sys.stderr)

    print()
    if failures:
        print(f"failed: {', '.join(failures)}", file=sys.stderr)
        return 1
    print(f"all {len(stores)} stores downloaded to {outdir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
