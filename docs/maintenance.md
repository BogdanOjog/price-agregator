# Maintenance: when a site changes

A collector reads HTML or JSON that the stores change without notice. The program is built so a
change shows up as a clear failure, never as wrong prices. This is how to repair it, with or
without an AI assistant.

## 1. See what failed

```
uv run pisaprices doctor
```

Each source shows its last run. Typical messages:

| Message | Meaning | What to do |
| --- | --- | --- |
| `Conad session is not on store …` | the Conad session expired | `pisaprices conad-session import curl.txt` (README) |
| `pickup point … not found on /stores` | Coop renamed or closed the Cascina pickup point | choose another one with the user, update `config/stores.yaml` |
| `cannot parse snapshot …: no data-grid-data tiles` | the page layout changed | step 2 |
| `lidl.it home page links no weekly offer page` | Lidl changed the URLs of the weekly pages | step 2, in `lidl_offers.py` (`WEEKLY_PAGE`) |
| `only N products, previous run had M` (partial) | far fewer products than last time | open the snapshots: a category may have moved |
| `Conad category … is not linked …` | a category code changed | `pisaprices categories check`, then fix `config/categories.yaml` |

## 2. Look at the page that failed

Every response is saved before it is parsed:

```
data/raw/<source>/<run id>/0007_cat_pasta_p2.html      # the page
data/raw/<source>/<run id>/0007_cat_pasta_p2.meta.json # URL, status, time, context
```

Open the page and compare it with what the parser expects. Where each value comes from is written,
with examples, in `recon/NOTES.md`.

## 3. Fix the parser test-first

1. Copy the failing page to `tests/fixtures/<source>/` (local only, never committed).
2. Update the synthetic page in `tests/synthetic/<source>/` to the new structure, with invented
   products — this is what CI and other people test against.
3. Change the parser in `src/pisaprices/ingest/sources/<source>.py` until its tests
   (`tests/test_coop.py`, `test_conad.py` or `test_promo_sources.py`) and `tests/test_real_pages.py` pass.
4. Run the whole suite: `uv run pytest`.

Rules that must keep holding (they are tested): no request to a path or parameter forbidden by
robots.txt, no request outside the source's hosts, no generic Conad prices, every price with the
date it was seen, unknown promotion wording stored as `UNKNOWN` (never guessed).

## 4. Rebuild the data without new requests

```
uv run pisaprices reparse --source <source> --run <run id>
```

This re-reads the saved pages of that run and replaces only the rows derived from it.

## 5. Record what you learned

Update `recon/NOTES.md` (what the site looks like now, and the rule if it changed). Future repairs
start from there.

## Recon for a new store or town

`recon/` contains the scripts used to study the sites (`politehttp.py`: polite client that saves
every response). Rules for recon: visitor only, no login, no cart, no CAPTCHA solving, robots.txt
respected, and stop before anything that looks like a gray area.
