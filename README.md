# price-agregator

Prices and promotions of the supermarkets in Pisa (Coop, Conad, Lidl, Eurospin), a searchable
catalog, and a shopping planner that tells you **where, on which day and what exactly** to buy so
a shopping list costs the least, with 1, 2 or 3 stores.

Everything runs on your computer: a SQLite database, no server, no account, no AI.

## What it does

- **Collects** regular prices and promotions every day, politely (pauses between requests,
  `robots.txt` respected, no anti-bot evasion).
- **Catalog**: browse a category, search, see a product's price at each store with the date it was
  seen, its active promotions and their end dates, its price history and the equivalent products
  of the other chains.
- **Planner**: for a shopping list, the cheapest plan with 1, 2 and 3 stores, the visit day for
  each store, the promotions and cards to use, and how much each extra store saves for how many
  extra minutes.

The configuration is for the stores of the Comune di Pisa. Another town needs new store ids and
some recon (see `recon/NOTES.md` and `docs/maintenance.md`).

## Install

Requirements: [uv](https://docs.astral.sh/uv/) (it installs Python 3.12 by itself) and Git.

```
git clone <this repository> price-agregator
cd price-agregator
uv sync
copy config\user.example.yaml config\user.yaml      # Linux/macOS: cp config/user.example.yaml config/user.yaml
```

Edit `config/user.yaml`: which loyalty cards you have, how many minutes each store is from you,
how you get there (walk, bike, bus + ticket price, car), the days you can go, the days a store is
on your way anyway. These values are yours: the code never assumes any of them.

```
uv run pisaprices init          # creates data/pisa_prices.db and checks the configuration
```

### Conad: one manual step

Conad protects its store selection against bots, and this project never tries to get around that.
You select the store once in your own browser and give the session to the program:

1. On https://spesaonline.conad.it choose **Ordina e Ritira** and your store; do not log in.
2. Open any category page, press **F12 → Network → Doc**, reload, right-click the first request →
   **Copy → Copy as cURL (bash)**, and save the text to a file, e.g. `curl.txt`.
3. `uv run pisaprices conad-session import curl.txt`

When the session expires, `pisaprices doctor` says so; repeat the three steps (about a minute).

## Use

```
uv run pisaprices categories list               # what is collected ([x]); categories select/unselect <slug>
uv run pisaprices collect --all                 # about 40 minutes, with pauses between requests
uv run pisaprices doctor                        # state of every source, review queue, Conad session
uv run pisaprices catalog browse pasta_secca --sort unit_price
uv run pisaprices catalog promos
uv run pisaprices plan --list lista.yaml --from 2026-10-26
uv run streamlit run ui/app.py                  # the same in a browser UI
```

The browser UI opens on **Ce cumpăr și unde** (what to buy, and where): type one product per line
(`pasta 1 kg`, `2 l latte`, `uova 10`, `mozzarella`) and press **Caută**. You get the recommended
stores and days with the total, then what to buy in each store. Fewer or more stores are one click
away; correcting what the app understood, comparing every price and saving the list are in folded
sections below. The YAML lists below are the same thing for the command line.

A shopping list (`lista.yaml`):

```yaml
name: Spesa della settimana
items:
  - {label: Pasta, category: pasta_secca, quantity: 1 kg, overbuy: 1}
  - {label: Uova, category: uova, quantity: 10 pz}
  - {label: Olio EVO, category: olio_extravergine, quantity: 1 l, min_tier: 3}
  - {label: Latte, match: brand, category: latte, brand: Milbona, quantity: 2 l}
```

`overbuy: 1` allows one extra pack when it makes the item cheaper (3x2 offers). `min_tier`
(1 economy, 2 standard, 3 premium) and `flags` (`bio`, `gluten_free`, ...) restrict the accepted
products; tiers and flags come from `config/tier_rules.yaml`.

### Categories

`config/categories.yaml` is this project's own tree: food and soft drinks only, 12 groups and 66
categories, organised the way one shops rather than the way a store shelves. Each store's
categories point into it; a store category that mixes things (Conad "Olio, aceto e altri
condimenti") is split by words in the product title, and the weekly offers of Lidl and Eurospin
take the category of the catalog products named alike. The rule is described at the top of the
file.

To add a category, in the UI, page **Categorii**: pick a category, choose its Coop counterpart from the
site's own list (refreshed with one button, three requests), pick the coarse Lidl/Eurospin
categories and write the title words (`include` / `exclude`). A preview shows which products of
this week's Lidl and Eurospin offers the rules would take, from the pages already downloaded.
Saving validates the whole configuration, marks the mapping reviewed and selects the category; it
is collected from the next run (or immediately, if you tick the box).

### Every day, automatically

Windows: `powershell -ExecutionPolicy Bypass -File scripts\schedule_windows.ps1 -Time 07:30`
registers a daily task that runs `pisaprices collect --all` and logs to `data/logs/`.
Linux/macOS (cron): `30 7 * * * cd /path/to/price-agregator && uv run pisaprices collect --all >> data/logs/collect.log 2>&1`

## When something breaks

Sites change. A run never stores doubtful data: it stops with the reason (`pisaprices doctor`),
and every downloaded page stays in `data/raw/` so the data can be rebuilt without new requests once
the parser is fixed. `pisaprices categories check` tells you when a site renamed a category you
collect. The repair procedure is in [docs/maintenance.md](docs/maintenance.md).

## Tests

```
uv run pytest
```

Tests run on hand-written pages in `tests/synthetic/` (same structure as the sites, invented
products). Real pages are never committed — the sites' content may not be redistributed — but you
can test against your own: `uv run pisaprices fixtures capture` copies the pages of your last
successful run to `tests/fixtures/` (ignored by git) and the `real_fixtures` tests use them.

## Project layout

`src/pisaprices/domain` (pure model and optimiser), `ingest` (one collector per source),
`storage` (SQLite via SQLAlchemy, migrations in `migrations/`), `services` (use cases), `cli.py`
and `ui/` (thin adapters); `tests/test_architecture.py` keeps imports flowing downwards. What each
site looks like and how each source is read: `recon/NOTES.md`; how categories are decided: the top
of `config/categories.yaml`.
