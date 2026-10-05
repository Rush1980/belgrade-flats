# Belgrade Estate

Fresh flats for sale in Belgrade from 4zida.rs and halooglasi.com. Python 3.10+, no dependencies.

```
python scripts/belgrade_flats.py --today                # everything published today
python scripts/belgrade_flats.py --yesterday
python scripts/belgrade_flats.py --since 2026-10-01 --until 2026-10-03
python scripts/belgrade_flats.py --today --max-price 250000 --max-price-m2 4000 --sort price \
    --district "stari grad" --district "savski venac" --district "banovo brdo"
python scripts/belgrade_flats.py --watch 15             # poll every 15 min, print only new ones
```

Options: `--min-price/--max-price` (EUR), `--max-price-m2`, `--min-m2/--max-m2`, `--rooms`
(0.5 = garsonjera), `--district` (substring, diacritics optional, repeatable), `--since/--until
YYYY-MM-DD`, `--today`, `--yesterday`, `--source 4zida|halooglasi`, `--pages` (4zida only),
`--sort date|price`, `--tier-share PCT`, `--no-details`, `--limit`, `--json`.

Every run writes `out/belgrade_flats.html` (Russian UI): cards with photos, the same flat on both
sites folded into one card, green/red thirds by EUR/m², basement / ground floor / top floor drawn
on the photo, lift and duplex icons, NEW for listings not seen on a previous run.

## Published page

https://rush1980.github.io/belgrade-flats/ is built at home, because halooglasi answers 403 to cloud
servers: `scripts/publish.py` (run hourly by Windows Task Scheduler) builds today's page and
force-pushes it as the `site` branch, which GitHub Pages serves. Log: `out/publish.log`.
