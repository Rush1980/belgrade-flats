"""Fresh flats-for-sale listings in Belgrade, newest first, from 4zida.rs and halooglasi.com.

    python scripts/belgrade_flats.py                       # newest listings, both sites
    python scripts/belgrade_flats.py --max-price 150000 --min-m2 45 --rooms 2 2.5
    python scripts/belgrade_flats.py --max-price-m2 4000   # at most 4000 EUR per m2
    python scripts/belgrade_flats.py --district vracar --district dorcol
    python scripts/belgrade_flats.py --watch 15            # poll every 15 min, print only new ones
    python scripts/belgrade_flats.py --today --pages 15    # everything published today

Listings not seen on a previous run are marked NEW; the seen set lives in
out/belgrade_flats_seen.json. Every run also writes out/belgrade_flats.html (cards with photos).

Standard library only. nekretnine.rs answers 403 to non-browser clients, so it is not a source.
"""
import argparse
import html
import json
import math
import re
import sys
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / 'out'
SEEN_FILE = OUT / 'belgrade_flats_seen.json'
DETAILS_FILE = OUT / 'belgrade_flats_details.json'   # per-listing facts only on the ad page (lift)
HTML_FILE = OUT / 'belgrade_flats.html'
UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128 Safari/537.36'

# 4zida: placeIds[]=2 is Beograd; sort=createdAtDesc is newest first. Price/area filters are server-side.
ZIDA_API = 'https://api.4zida.rs/v6/search/apartments'
# halooglasi: the list page embeds `QuidditaEnvironment.serverListData = {...}`; default order is newest first.
HALO_URL = 'https://www.halooglasi.com/nekretnine/prodaja-stanova/beograd'
HALO_THREADS = 8
ZIDA_MAX_PAGES = 200


@dataclass
class Flat:
    source: str
    id: str
    url: str
    title: str
    place: str
    price: float | None
    m2: float | None
    rooms: str
    floor: str
    published: str          # ISO datetime (4zida) or ISO date (halooglasi only gives the day)
    image: str
    new: bool = False
    duplex: bool = False    # the ad says duplex (field or text)
    lift: bool = False      # the listing page says there is a lift (absent = no lift or not stated)
    tier: str = ''
    dupes: list = field(default_factory=list)   # the same flat listed again (other site or re-post)          # 'cheap' / 'dear': cheapest / dearest --tier-share by EUR/m2 in this result set

    @property
    def key(self):
        return f'{self.source}:{self.id}'

    @property
    def keys(self):
        return [self.key] + [d.key for d in self.dupes]


def get(url):
    req = urllib.request.Request(url, headers={'User-Agent': UA, 'Accept-Language': 'sr,en;q=0.8'})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode('utf-8')


# ---------------------------------------------------------------- 4zida.rs

def fetch_4zida(pages, a):
    params = [('for', 'sale'), ('placeIds[]', '2'), ('sort', 'createdAtDesc')]
    if a.min_price: params.append(('priceFrom', int(a.min_price)))
    if a.max_price: params.append(('priceTo', int(a.max_price)))
    if a.min_m2: params.append(('m2From', int(a.min_m2)))
    if a.max_m2: params.append(('m2To', int(a.max_m2)))
    flats = []
    # Strictly newest first, so with a date filter keep paging until the listings get older than it.
    for page in range(1, (ZIDA_MAX_PAGES if a.since else pages) + 1):
        data = json.loads(get(f'{ZIDA_API}?{urllib.parse.urlencode(params + [("page", page)])}'))
        for ad in data.get('ads', []):
            floor = ''
            if ad.get('redactedFloor') is not None:
                floor = f"{ad['redactedFloor']}/{ad.get('redactedTotalFloors') or '?'}"
            flats.append(Flat(
                source='4zida', id=ad['id'],
                url='https://www.4zida.rs' + ad['urlPath'],
                title=ad.get('detailedTitle') or ad.get('title') or '',
                place=', '.join(n for n in ad.get('placeNames') or [] if n != 'Beograd'),
                price=ad.get('price'), m2=ad.get('m2'),
                rooms=_rooms_4zida(ad), floor=floor,
                published=ad.get('createdAt') or '',
                image=(ad.get('image') or {}).get('search', {}).get('380x0_fill_0_jpeg', ''),
            ))
        if not data.get('ads') or (a.since and flats and flats[-1].published[:10] < a.since):
            break
    return flats


def _rooms_4zida(ad):
    abbr = ad.get('structureAbbreviation') or ''      # '2.5 stan', 'gars.'
    if abbr.startswith('gars'):
        return '0.5'
    m = re.match(r'(\d+(?:\.\d)?)', abbr)
    return m.group(1).removesuffix('.0') if m else ''


# ---------------------------------------------------------------- halooglasi.com

def fetch_halo(pages, a):
    # unit 4 = EUR, unit 1 = m2
    params = []
    if a.min_price: params += [('cena_d_from', int(a.min_price)), ('cena_d_unit', 4)]
    if a.max_price: params += [('cena_d_to', int(a.max_price)), ('cena_d_unit', 4)]
    if a.min_m2: params += [('kvadratura_d_from', int(a.min_m2)), ('kvadratura_d_unit', 1)]
    if a.max_m2: params += [('kvadratura_d_to', int(a.max_m2)), ('kvadratura_d_unit', 1)]
    params = list(dict.fromkeys(params))

    def page(n):
        query = params + ([('page', n)] if n > 1 else [])
        url = HALO_URL + ('?' + urllib.parse.urlencode(query) if query else '')
        for attempt in range(3):
            try:
                text = get(url)
                break
            except OSError:
                if attempt == 2:
                    raise
                time.sleep(2)
        start = text.find('serverListData=')
        if start < 0:
            raise RuntimeError('halooglasi: serverListData not found - page layout changed?')
        data, _ = json.JSONDecoder().raw_decode(text, start + len('serverListData='))
        return data.get('TotalPages') or 1, [f for f in map(_parse_halo, data.get('Ads') or []) if f]

    # The list is several blocks (ad tiers), each sorted newest first on its own: on 2026-10-05 fresh
    # ads restarted at pages 44 and 64. Only the full list is complete for any day, so fetch all of
    # it in parallel (~300 pages in ~20 s) and let the caller sort. `pages` does not apply here.
    total, flats = page(1)
    with ThreadPoolExecutor(HALO_THREADS) as ex:
        for _, more in ex.map(page, range(2, total + 1)):
            flats += more
    return flats


def _parse_halo(ad):
    h = html.unescape(ad.get('ListHTML') or '')
    if not h:
        return None
    def one(pattern):
        m = re.search(pattern, h, re.S)
        return html.unescape(m.group(1)).replace('\xa0', ' ').strip() if m else ''
    def feature(legend):
        return one(r"<div class='value-wrapper'>([^<]*?)(?:&nbsp;|\s)*(?:m<sup>2</sup>)?\s*<span class='legend'>" + legend)
    price = one(r'central-feature"><span data-value="([\d.,]+)"')
    m2 = feature('Kvadratura')
    date = one(r'class="publish-date">(\d\d\.\d\d\.\d{4})')
    places = [html.unescape(p).replace('\xa0', ' ').strip()
              for p in re.findall(r'<li>([^<]*)</li>', one(r'<ul class="subtitle-places">(.*?)</ul>'))]
    if places and places[0] == 'Beograd':
        places = places[1:]
    return Flat(
        source='halooglasi', id=ad['Id'],
        url='https://www.halooglasi.com' + ad['RelativeUrl'].split('?')[0],
        title=ad.get('Title') or one(r'class="product-title"><a [^>]*>([^<]*)'),
        place=', '.join(places),
        price=_num(price), m2=_num(m2.replace(',', '.'), thousands='.' not in m2),
        rooms=feature('Broj soba').removesuffix('.0'), floor=feature('Spratnost'),
        published=datetime.strptime(date, '%d.%m.%Y').date().isoformat() if date else '',
        image=one(r"<img src='([^']+)'"),
    )


def _num(s, thousands=True):
    """'204.900' -> 204900 (Serbian thousands separator); '59.5' m2 -> 59.5."""
    if not s:
        return None
    try:
        return float(s.replace('.', '')) if thousands and re.fullmatch(r'\d{1,3}(\.\d{3})+', s) else float(s)
    except ValueError:
        return None


# ---------------------------------------------------------------- per-listing details

DUPLEX_RE = re.compile(r'dupleks|duplex|dvoeta[zž]|dva nivoa|dve eta[zž]e', re.I)
DETAIL_FIELDS = ('lift', 'duplex')


def _details(f):
    """Facts only on the ad page. Neither site tells "no lift" / "not a duplex" from "not stated",
    so both are True only on a positive."""
    if f.source == '4zida':                       # `elevator` is 1 or null, `category` 'duplex'
        ad = json.loads(get(f'https://api.4zida.rs/v6/eds/{f.id}'))
        text = f"{ad.get('title') or ''} {ad.get('desc') or ''}"
        return {'lift': bool(ad.get('elevator')),
                'duplex': ad.get('category') == 'duplex' or bool(DUPLEX_RE.search(text))}
    page = get(f.url)                             # halooglasi: amenity lists in OtherFields, text in TextHtml
    i = page.find('"OtherFields":')
    if i < 0:
        raise RuntimeError('halooglasi: OtherFields not found')
    fields, _ = json.JSONDecoder().raw_decode(page, i + len('"OtherFields":'))
    j = page.find('"TextHtml":')                  # the ad's own text; the page template mentions "Duplex" too
    text = json.JSONDecoder().raw_decode(page, j + len('"TextHtml":'))[0] or '' if j >= 0 else ''
    return {'lift': 'Lift' in (fields.get('ostalo_ss') or []),
            'duplex': 'Duplex' in (fields.get('dodatno_ss') or []) or bool(DUPLEX_RE.search(f'{f.title} {text}'))}


def add_details(flats):
    """Fill DETAIL_FIELDS for the shown listings: one request per listing, cached in DETAILS_FILE."""
    try:
        cache = json.loads(DETAILS_FILE.read_text(encoding='utf-8'))
    except (FileNotFoundError, ValueError):
        cache = {}
    todo = [f for f in flats if not all(k in (cache.get(f.key) or {}) for k in DETAIL_FIELDS)]

    def one(f):
        try:
            return f.key, _details(f)
        except Exception as e:                    # a missing detail must not break the run
            print(f'! details {f.key}: {e}', file=sys.stderr)
            return f.key, None

    with ThreadPoolExecutor(HALO_THREADS) as ex:
        cache.update({k: v for k, v in ex.map(one, todo) if v is not None})
    for f in flats:
        d = cache.get(f.key) or {}
        f.lift, f.duplex = d.get('lift', False), d.get('duplex', False)
    if todo:
        OUT.mkdir(exist_ok=True)
        with open(DETAILS_FILE, 'w', encoding='utf-8') as fh:
            json.dump(cache, fh, ensure_ascii=False)


# ---------------------------------------------------------------- filtering, state, output

def norm(s):
    """Lower-case and drop Serbian diacritics so 'vracar' matches 'Vračar'."""
    return s.lower().translate(str.maketrans('čćšžđ', 'ccszd'))


def keep(f, a):
    if a.min_price and (f.price is None or f.price < a.min_price): return False
    if a.max_price and (f.price is None or f.price > a.max_price): return False
    if a.min_m2 and (f.m2 is None or f.m2 < a.min_m2): return False
    if a.max_m2 and (f.m2 is None or f.m2 > a.max_m2): return False
    if a.max_price_m2 and (not f.price or not f.m2 or f.price / f.m2 > a.max_price_m2): return False
    if a.rooms and f.rooms not in a.rooms: return False
    if a.since and f.published[:10] < a.since: return False
    if a.until and f.published[:10] > a.until: return False
    if a.district and not any(norm(d) in norm(f.place + ' ' + f.title) for d in a.district): return False
    return True


def load_seen():
    try:
        return set(json.loads(SEEN_FILE.read_text(encoding='utf-8')))
    except (FileNotFoundError, ValueError):
        return set()


def save_seen(seen):
    OUT.mkdir(exist_ok=True)
    with open(SEEN_FILE, 'w', encoding='utf-8') as fh:
        json.dump(sorted(seen), fh, ensure_ascii=False)


def collect(a):
    flats, errors = [], []
    for name, fetch in (('4zida', fetch_4zida), ('halooglasi', fetch_halo)):
        if a.source and name not in a.source:
            continue
        try:
            flats += fetch(a.pages, a)
        except Exception as e:             # one site being down should not hide the other
            errors.append(f'{name}: {e}')
    uniq = {f.key: f for f in flats if keep(f, a)}
    # 4zida carries a time, halooglasi only a day: compare on the ISO string, date-only sorts as 00:00.
    return sorted(uniq.values(), key=lambda f: f.published, reverse=True), errors


ROMAN = {'I': 1, 'V': 5, 'X': 10, 'L': 50}
FLOOR_WORDS = {'PR': 'партер', 'VPR': 'выс. партер', 'NPR': 'низк. партер', 'SUT': 'подвал',
               'PSUT': 'полуподвал', 'POT': 'мансарда', 'PK': 'мансарда'}


def _floor_part(s):
    s = s.strip().upper()
    if s in FLOOR_WORDS:
        return FLOOR_WORDS[s]
    if s and all(c in ROMAN for c in s):             # halooglasi writes floors in Roman numerals
        v = [ROMAN[c] for c in s]
        return str(sum(-x if i + 1 < len(v) and x < v[i + 1] else x for i, x in enumerate(v)))
    return s.replace('-', '−')


def floor_ru(floor):
    """'IV/6' -> 'этаж 4 из 6', 'PR/17' -> 'партер из 17', '' -> 'этаж ?'."""
    if not floor:
        return 'этаж ?'
    here, _, total = floor.partition('/')
    here = _floor_part(here)
    if here[:1].isdigit() or here.startswith('−'):
        here = f'этаж {here}'
    return f'{here} из {total}' if total and total != '?' else here


def floor_kind(floor):
    """'basement' / 'ground' / 'top' / '' for the picture overlay, and its caption."""
    here, _, total = (floor or '').partition('/')
    here, total = here.strip().upper(), total.strip()
    if here in ('SUT', 'PSUT') or here.startswith('-'):
        return 'basement', 'Полуподвал' if here == 'PSUT' else 'Подвал'
    if here in ('PR', 'VPR', 'NPR', '0'):
        return 'ground', 'Приземье'
    if here in ('POT', 'PK'):
        return 'top', 'Мансарда'
    if total.isdigit() and _floor_part(here) == total:
        return 'top', 'Последний этаж'
    return '', ''


# A small building: roof, two floors, ground line, basement; the part that matches the flat is lit.
_HOUSE = '''<svg viewBox="0 0 44 54" width="30" height="37" aria-hidden="true">
<polygon points="3,19 22,4 41,19" fill="{roof}"/>
<rect x="7" y="20" width="30" height="9" fill="{mid}"/><rect x="7" y="30" width="30" height="9" fill="{ground}"/>
<line x1="0" y1="41" x2="44" y2="41" stroke="#fff" stroke-width="1.5"/>
<rect x="7" y="43" width="30" height="9" fill="{basement}" stroke="#fff" stroke-dasharray="2 2" stroke-width=".8"/></svg>'''


def floor_overlay(floor):
    kind, caption = floor_kind(floor)
    if not kind:
        return ''
    lit, dim = '#facc15', '#ffffff55'
    svg = _HOUSE.format(**{part: lit if part == {'top': 'roof'}.get(kind, kind) else dim
                           for part in ('roof', 'mid', 'ground', 'basement')})
    return f'<span class="fl-ov fl-{kind}">{svg}<b>{caption}</b></span>'


# Duplex: a flat on two levels joined by a staircase, top-left of the photo.
DUPLEX_ICON = '''<span class="duplex" title="Дуплекс — квартира на двух уровнях"><svg viewBox="0 0 28 28" width="24" height="24" aria-hidden="true">
<rect x="2" y="2" width="24" height="24" rx="2" fill="none" stroke="#fff" stroke-width="1.8"/>
<line x1="2" y1="14" x2="26" y2="14" stroke="#fff" stroke-width="1.8"/>
<path d="M12 24h3.5v-3.3h3.5v-3.3h3.5v-3.4" fill="none" stroke="#facc15" stroke-width="2" stroke-linejoin="round"/>
<text x="5" y="11" font-size="8" font-weight="700" fill="#fff" font-family="system-ui,sans-serif">2</text>
<text x="5" y="23" font-size="8" font-weight="700" fill="#fff" font-family="system-ui,sans-serif">1</text>
</svg><b>дуплекс</b></span>'''


# Lift: a cabin with up/down arrows, bottom-right of the photo.
LIFT_ICON = '''<span class="lift" title="Есть лифт"><svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true">
<rect x="4" y="2.5" width="16" height="19" rx="2" fill="none" stroke="#fff" stroke-width="1.8"/>
<line x1="12" y1="2.5" x2="12" y2="21.5" stroke="#fff" stroke-width="1.2"/>
<polygon points="8,6.5 10.5,10 5.5,10" fill="#fff"/><polygon points="16,10 18.5,6.5 13.5,6.5" fill="#fff"/>
</svg><b>лифт</b></span>'''


def floor_label(floor):
    """Card line: 'Этаж 3 из 4', 'Партер из 6', 'Этаж не указан'."""
    if not floor:
        return 'Этаж не указан'
    s = floor_ru(floor)
    return s[0].upper() + s[1:]


def rooms_ru(rooms):
    return 'студия' if rooms == '0.5' else f'{rooms}-комн.' if rooms else '? комн.'


def place_ru(place):
    """Area names stay as the sites write them (Latin), minus the 'opština' noise."""
    return re.sub(r'\s+', ' ', re.sub(r'(?i)\bopština\b', '', place)).replace(' ,', ',').strip(' ,')


def fmt_price(p):
    return f"{p:,.0f} €".replace(',', ' ') if p else '—'


def print_table(flats, limit):
    for f in flats[:limit]:
        per = f' ({f.price / f.m2:,.0f} €/м²)'.replace(',', ' ') if f.price and f.m2 else ''
        when = f.published[:16].replace('T', ' ')
        print(f"{'НОВ ' if f.new else '    '}{TIER_MARK.get(f.tier, '  ')}{when:16}  {fmt_price(f.price):>11}{per:16} "
              f"{(f'{f.m2:g} м²' if f.m2 else '—'):>8}  {rooms_ru(f.rooms):>9}  {floor_ru(f.floor):18} "
              f"{place_ru(f.place)[:40]}")
        print(f"    {f.title[:100]}\n    {f.url}")
        for d in f.dupes:
            print(f"    = {d.url}")


TIER_MARK = {'cheap': '↓ ', 'dear': '↑ '}


TIER_BADGE = {'cheap': '<span class="badge tier cheap-b">дёшево за м²</span>',
              'dear': '<span class="badge tier dear-b">дорого за м²</span>'}


def tier_note(flats, a):
    def per(tier, pick):
        vals = [f.price / f.m2 for f in flats if f.tier == tier]
        return len(vals), f'{pick(vals):,.0f}'.replace(',', ' ') if vals else ''
    (nc, top), (nd, low) = per('cheap', max), per('dear', min)
    if not nc and not nd:
        return ''
    return (f' · по цене за м²: <b style="color:var(--ok)">зелёные</b> — {a.tier_share:g}% самых дешёвых '
            f'({nc} шт., до {top} €/м²), <b style="color:var(--bad)">красные</b> — {a.tier_share:g}% самых дорогих '
            f'({nd} шт., от {low} €/м²)')


def dupe_links(f):
    if not f.dupes:
        return ''
    # The card itself is a link, so these are spans that open the other listing via JS (no nested <a>).
    links = ' '.join(f'''<span class="dupe" role="link" tabindex="0" onclick="event.preventDefault();event.stopPropagation();window.open('{html.escape(d.url)}','_blank','noopener')">{html.escape(d.source)} ↗</span>'''
                     for d in f.dupes)
    return f'<div class="also">то же объявление ещё на: {links}</div>'


def write_html(flats, a, errors=()):
    cards = []
    for f in flats:
        per = f'{f.price / f.m2:,.0f} €/м²'.replace(',', ' ') if f.price and f.m2 else ''
        cards.append(f'''<a class="card{' new' if f.new else ''}{' ' + f.tier if f.tier else ''}" href="{html.escape(f.url)}" target="_blank" rel="noopener">
  <div class="img" style="background-image:url('{html.escape(f.image)}')"><div class="tl">{'<span class="badge">НОВОЕ</span>' if f.new else ''}{DUPLEX_ICON if f.duplex else ''}</div>{TIER_BADGE.get(f.tier, '')}{floor_overlay(f.floor)}{LIFT_ICON if f.lift else ''}</div>
  <div class="body">
    <div class="price">{fmt_price(f.price)} <small>{per}</small></div>
    <div class="meta">{f'{f.m2:g} м²' if f.m2 else '—'} · {rooms_ru(f.rooms)}</div>
    <div class="floor">🏢 {html.escape(floor_label(f.floor))}</div>
    <div class="place">{html.escape(place_ru(f.place))}</div>
    <div class="title" lang="sr" title="заголовок объявления (оригинал)">{html.escape(f.title)}</div>
    <div class="foot">{html.escape(f.source)} · опубликовано {html.escape(f.published[:16].replace('T', ' '))}{dupe_links(f)}</div>
  </div></a>''')
    page = f'''<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Квартиры в Белграде</title>
<style>
:root{{--bg:#f6f6f4;--card:#fff;--fg:#1d1d1b;--mut:#6b6b66;--acc:#c2410c;--ok:#15803d;--okbg:#f0fdf4;--bad:#b91c1c;--badbg:#fef2f2}}
@media (prefers-color-scheme:dark){{:root{{--bg:#161615;--card:#22221f;--fg:#ecebe6;--mut:#9a9a92;--acc:#fb923c;--ok:#4ade80;--okbg:#14241a;--bad:#f87171;--badbg:#2a1616}}}}
body{{margin:0;padding:16px;background:var(--bg);color:var(--fg);font:14px/1.4 system-ui,sans-serif}}
h1{{font-size:20px;margin:0 0 4px}} .sub{{color:var(--mut);margin-bottom:16px}}
.err{{background:var(--badbg);border:1px solid var(--bad);border-radius:8px;padding:8px 12px;margin-bottom:14px}}
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:14px}}
.card{{background:var(--card);border-radius:10px;overflow:hidden;color:inherit;text-decoration:none;box-shadow:0 1px 3px #0002}}
.card.new{{outline:2px solid var(--acc)}}
.card.cheap{{outline:3px solid var(--ok);background:var(--okbg)}} .card.cheap .price small{{color:var(--ok);font-weight:700}}
.card.dear{{outline:3px solid var(--bad);background:var(--badbg)}} .card.dear .price small{{color:var(--bad);font-weight:700}}
.badge.tier{{left:auto;right:8px}} .badge.cheap-b{{background:var(--ok)}} .badge.dear-b{{background:var(--bad)}}
.img{{height:170px;background:#8883 center/cover;position:relative}}
.tl{{position:absolute;top:8px;left:8px;display:flex;flex-direction:column;align-items:flex-start;gap:5px}}
.tl .badge{{position:static}}
.duplex{{display:flex;align-items:center;gap:5px;padding:4px 9px 4px 5px;border-radius:8px;background:#7c3aede6;color:#fff;font-size:12px}}
.duplex svg{{display:block}}
.badge{{position:absolute;top:8px;left:8px;background:var(--acc);color:#fff;font-weight:700;font-size:11px;padding:2px 7px;border-radius:4px}}
.body{{padding:10px 12px}} .price{{font-size:18px;font-weight:700}} .price small{{font-weight:400;color:var(--mut);font-size:12px}}
.meta,.foot{{color:var(--mut);font-size:12px}} .floor{{font-size:13px;font-weight:600;margin-top:2px}}
.also{{margin-top:4px;color:var(--fg)}} .dupe{{text-decoration:underline;cursor:pointer;font-weight:600;margin-right:6px}}
.lift{{position:absolute;right:8px;bottom:8px;display:flex;align-items:center;gap:4px;padding:4px 8px 4px 5px;
  border-radius:8px;background:#1d4ed8e6;color:#fff;font-size:12px}} .lift svg{{display:block}}
.fl-ov{{position:absolute;left:8px;bottom:8px;display:flex;align-items:center;gap:6px;padding:4px 9px 4px 5px;
  border-radius:8px;background:#000b;color:#fff;font-size:12px}} .fl-ov svg{{display:block}} .place{{font-weight:600;margin:4px 0}}
.title{{font-size:13px;margin-bottom:6px;font-style:italic;color:var(--mut)}}
</style></head><body>
{''.join(f'<div class="err">⚠️ Источник не ответил, его объявлений здесь нет: {html.escape(e)}</div>' for e in errors)}
<h1>Квартиры на продажу в Белграде — {'от дешёвых к дорогим' if a.sort == 'price' else 'сначала новые'}</h1>
<div class="sub">{len(flats)} объявл., новых: {sum(f.new for f in flats)}{tier_note(flats, a)} · загружено {datetime.now():%Y-%m-%d %H:%M}
{html.escape(' · фильтры: ' + ' '.join(sys.argv[1:])) if len(sys.argv) > 1 else ''}</div>
<div class="grid">{''.join(cards)}</div></body></html>'''
    OUT.mkdir(exist_ok=True)
    HTML_FILE.write_text(page, encoding='utf-8')


def mark_tiers(flats, share):
    """Tag the cheapest and the dearest `share` (percent) of the listings by EUR/m2 as 'cheap' / 'dear';
    ties at a cut-off go with the tier. The rest stay untagged."""
    per = sorted(f.price / f.m2 for f in flats if f.price and f.m2)
    k = round(len(per) * share / 100)
    if not k:
        return
    low, high = per[k - 1], per[-k]
    for f in flats:
        if f.price and f.m2:
            v = f.price / f.m2
            f.tier = 'cheap' if v <= low else 'dear' if v >= high else ''


def _floor_num(floor):
    here = _floor_part((floor or '').partition('/')[0])
    return '0' if 'партер' in here else here


def _total_floors(floor):
    total = (floor or '').partition('/')[2].strip()
    return total if total.isdigit() else ''


def _same_flat(f, g):
    """Same price, area (to the m²) and rooms, and the floors do not contradict each other.
    On one site the floor must match exactly, so two identical flats in one new building stay apart.
    Across sites only a clash of two known upper floors or of the building height splits them: the
    sites disagree below ground level (4zida '-1' vs halooglasi 'PR' for one flat)."""
    if not (f.price and f.m2 and f.price == g.price and round(f.m2) == round(g.m2) and f.rooms == g.rooms):
        return False
    a, b = _floor_num(f.floor), _floor_num(g.floor)
    ta, tb = _total_floors(f.floor), _total_floors(g.floor)
    if ta and tb and ta != tb:
        return False
    if f.source == g.source:
        return a == b
    return not (a.isdigit() and b.isdigit() and int(a) > 0 and int(b) > 0 and a != b)


def merge_dupes(flats):
    """Fold repeat listings of one flat into one card: the 4zida one (exact time) wins, then the newest."""
    order = sorted(sorted(flats, key=lambda f: f.published, reverse=True), key=lambda f: f.source != '4zida')
    kept = []
    for f in order:
        main = next((k for k in kept if all(_same_flat(x, f) for x in [k] + k.dupes)), None)
        if main:
            main.dupes.append(f)
            main.lift = main.lift or f.lift
            main.duplex = main.duplex or f.duplex
            main.image = main.image or f.image
        else:
            kept.append(f)
    return sorted(kept, key=lambda f: f.published, reverse=True)


def run_once(a, seen, first_run):
    flats, errors = collect(a)
    for e in errors:
        print(f'! {e}', file=sys.stderr)
    if not a.no_details:
        add_details(flats)
    flats = merge_dupes(flats)
    mark_tiers(flats, a.tier_share)
    if a.sort == 'price':
        flats.sort(key=lambda f: f.price or math.inf)
    for f in flats:
        f.new = not first_run and not any(k in seen for k in f.keys)
    seen |= {k for f in flats for k in f.keys}
    save_seen(seen)
    if a.json:
        print(json.dumps([asdict(f) for f in flats], ensure_ascii=False, indent=1))
    else:
        write_html(flats, a, errors)
    return flats


def main():
    p = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    p.add_argument('--min-price', type=float); p.add_argument('--max-price', type=float, help='EUR')
    p.add_argument('--min-m2', type=float); p.add_argument('--max-m2', type=float)
    p.add_argument('--max-price-m2', type=float, help='EUR per m2, filtered client-side')
    p.add_argument('--rooms', nargs='+', help="room counts as the sites write them: 0.5 (garsonjera) 1 1.5 2 2.5 ...")
    p.add_argument('--district', action='append', help='substring of the area, diacritics optional (repeatable)')
    p.add_argument('--since', metavar='YYYY-MM-DD', help='only listings published on or after this day')
    p.add_argument('--until', metavar='YYYY-MM-DD', help='only listings published on or before this day')
    p.add_argument('--today', action='store_true', help='same as --since <today>')
    p.add_argument('--yesterday', action='store_true', help='only listings published yesterday (raise --pages)')
    p.add_argument('--source', action='append', choices=['4zida', 'halooglasi'])
    p.add_argument('--pages', type=int, default=3,
                   help='4zida pages, 20 listings each (default 3); ignored with a date filter. '
                        'halooglasi is always read in full')
    p.add_argument('--limit', type=int, default=30, help='rows printed (default 30)')
    p.add_argument('--watch', type=float, metavar='MIN', help='poll every MIN minutes and print only new listings')
    p.add_argument('--json', action='store_true', help='print JSON instead of the table')
    p.add_argument('--tier-share', type=float, default=33.3, metavar='PCT',
                   help='mark the cheapest PCT%% by EUR/m2 green and the dearest PCT%% red (default 33.3, 0 = off)')
    p.add_argument('--no-details', action='store_true',
                   help='skip the per-listing page requests (no lift icons)')
    p.add_argument('--sort', choices=['date', 'price'], default='date',
                   help='date: newest first (default); price: total price, cheapest first')
    a = p.parse_args()
    if a.today:
        a.since = datetime.now().date().isoformat()
    if a.yesterday:
        a.since = a.until = (datetime.now().date() - timedelta(days=1)).isoformat()
    if sys.stdout.encoding.lower() != 'utf-8':
        sys.stdout.reconfigure(encoding='utf-8')

    seen = load_seen()
    flats = run_once(a, seen, first_run=not seen)
    if not a.json:
        print(f'{len(flats)} объявл. (новых с прошлого запуска: {sum(f.new for f in flats)}) -> {HTML_FILE}\n')
        print_table(flats, a.limit)
    while a.watch:
        time.sleep(a.watch * 60)
        new = [f for f in run_once(a, seen, first_run=False) if f.new]
        print(f'\n--- {datetime.now():%H:%M} · новых: {len(new)}')
        print_table(new, len(new))


if __name__ == '__main__':
    main()
