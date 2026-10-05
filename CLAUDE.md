# Belgrade Estate — Project Context

## Purpose

Agent that shows the freshest flats-for-sale listings in Belgrade, newest first.
Created 2026-10-05 (moved out of `testagent`). Standard-library Python only — no
`requirements.txt`, no install step.

## Layout

```
scripts/
  belgrade_flats.py   # fetch 4zida.rs + halooglasi.com, filter, mark NEW, print table
                      #   -> out/belgrade_flats.html (cards with photos); exit 2 if no source answered
  publish.py          # build the default search and force-push it as branch `site` (GitHub Pages)
out/                  # generated, gitignored
  belgrade_flats.html
  belgrade_flats_seen.json   # listing keys seen on earlier runs; drives the NEW marks
  belgrade_flats_details.json  # per-listing facts from the ad page (lift), so each ad is fetched once
  publish.log         # one line per publish.py run
```

## Sources

| Site | How it is read | Freshness field |
| --- | --- | --- |
| 4zida.rs | JSON API `api.4zida.rs/v6/search/apartments?for=sale&placeIds[]=2&sort=createdAtDesc` (placeId 2 = Beograd). Server-side `priceFrom/priceTo/m2From/m2To`. | `createdAt`, exact time (UTC) |
| halooglasi.com | List page `/nekretnine/prodaja-stanova/beograd`; JSON in `QuidditaEnvironment.serverListData`, each ad's fields parsed from its `ListHTML`. Server-side `cena_d_from/to` (unit 4 = EUR), `kvadratura_d_from/to` (unit 1 = m²). Read in full every run (see below). | `publish-date`, day only |
| nekretnine.rs | Not used — returns 403 to scripted clients. | — |

## What was concluded while building it

- **halooglasi has only a date**, so on a given day its ads sort after all of 4zida's
  timestamped ones. Its "today" also includes renewed (re-published) old ads.
- **Room counts differ in format**: 4zida `"2.0 stan"` / `"gars."`, halooglasi `"2.0"`. Both are
  normalised to `"2"`, `"2.5"`, `"0.5"` (garsonjera) so `--rooms` matches across sites.
- **halooglasi's list is not one newest-first sequence.** It is several blocks (ad tiers), each
  sorted newest first on its own; on 2026-10-05 (price ≤ 250k) fresh ads restarted at pages 44 and
  64 of 290. Reading only the first N pages silently misses most of a day — the early "today"
  results built that way were incomplete (5 instead of 21 for the default search below). Sorting
  is done by an AJAX call, not a URL parameter, so the script fetches all pages in parallel
  (8 threads, ~20 s with the price filter) and sorts itself. `--pages` only applies to 4zida.
- **Lift is only on the ad page**, not in either list: 4zida `api.4zida.rs/v6/eds/{id}` → `elevator`
  (1 or null), halooglasi ad HTML → `"OtherFields"` JSON → `ostalo_ss` contains `"Lift"`. Neither
  distinguishes "no lift" from "not stated", so only a positive is shown. **Duplex** comes from the
  same request: 4zida `category == "duplex"`, halooglasi `dodatno_ss` contains `"Duplex"`, or
  "dupleks/duplex/dvoetažni/dva nivoa" in the ad's own title/text (halooglasi `TextHtml` — do not
  search the whole page, its template and "similar ads" links contain "Duplex"). Fetched for the shown
  listings only (~20 s for 20 new ads); `--no-details` skips it.
- **4zida is strictly newest first**; with a date filter the script pages until it passes the date.
- **District, rooms and €/m² are filtered client-side**, price and area server-side.
- **The same flat is often listed on both sites** (and sometimes twice on halooglasi); `merge_dupes`
  folds them into one card with links to the other listings. Match = same price, m² (rounded),
  rooms, same building height when both known; on one site also the same floor, across sites only
  two known upper floors may not differ (sites disagree on basement vs. ground floor for one flat).
  2026-10-05, all Belgrade: 79 of ~810 listings folded, ~2/3 of them 4zida+halooglasi pairs.
  Remaining false-positive risk: identical flats in one new building on different sites.
- **halooglasi (behind Cloudflare) answers 403 to cloud IPs**: GitHub Actions (Azure) and gitlab.com
  shared runners (Google Cloud, US) both got 403 on 2026-10-05, while 4zida answered 200 from both.
  It works from a home connection. Do not try to get around the block (spoofing, third-party proxies).

## Published page

https://rush1980.github.io/belgrade-flats/ — repo `Rush1980/belgrade-flats` (public; commits use the
noreply address set in the local git config). Pages serves branch `site` as is (index.html +
.nojekyll); `scripts/publish.py` rebuilds it and replaces the branch with a fresh one-commit history.
Windows Task Scheduler task **"Belgrade flats publish"** runs it hourly via pythonw while the PC is on.
The search it publishes is the `SEARCH` list in publish.py — change it there when the user's default
filters change. No GitHub Actions: they were removed because runners only get 4zida.
- Neither site has a public API contract; a layout change breaks that source and is reported
  as an error line while the other source still works.

## Default search (user preference)

Unless the user asks otherwise, limit listings to three districts: **Stari grad, Savski venac,
Banovo brdo**.

```
python scripts/belgrade_flats.py --today --limit 400 \
  --district "stari grad" --district "savski venac" --district "banovo brdo"
```

Other days: `--yesterday`, or `--since D --until D`. The user has also been narrowing with
`--max-price 250000 --max-price-m2 4000 --sort price` (cards coloured by €/m² thirds: green cheapest,
red dearest — on by default, `--tier-share`); carry every filter from the previous request into the
next one unless the user drops it.

## Conventions

- `json.dump` always gets `encoding='utf-8'` (Serbian diacritics).
- On Windows the script reconfigures stdout to UTF-8 itself; ad-hoc analysis scripts need
  `PYTHONIOENCODING=utf-8`.
