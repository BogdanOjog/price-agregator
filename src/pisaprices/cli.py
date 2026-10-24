"""Command-line entry point. Thin adapter: it only calls into pisaprices.services."""

import argparse
import sys
from pathlib import Path

from datetime import date

from pisaprices.services import catalog, configuration, conad_session, fixtures, health, lists, plan, review
from pisaprices.services.app import open_runtime, project_root, with_only_categories
from pisaprices.services.configuration import ConfigError
from pisaprices.services.runs import ORDER, collect_sources, reparse_run

SOURCES = [s.value for s in ORDER]


def _eur(cents: int | None) -> str:
    return "-" if cents is None else f"{cents / 100:.2f} €".replace(".", ",")


def _print_runs(report) -> int:
    for run in report.runs:
        line = f"{run.source:16} run {run.run_id:>4}  {run.status.value:8} {run.items:>5} items {run.requests:>4} requests"
        print(line + (f"  — {run.error}" if run.error else "") + ("".join(f"  ! {w}" for w in run.warnings)))
    r = report.resolve
    print(f"promotion items: {r.linked} linked, {r.proposed} proposed for review, {r.unmatched} unmatched")
    return 0 if report.ok else 1


def cmd_init(args) -> int:
    runtime, sync = open_runtime(args.root)
    print(f"database ready: {runtime.root / runtime.config.settings.paths.db}")
    print(f"config synced: {len(sync.created)} created, {len(sync.updated)} updated, orphans {sync.orphans or 'none'}")
    selected = [c.slug for c in runtime.config.selected_categories()]
    print(f"selected categories: {', '.join(selected) or 'none (pisaprices categories select <slug>)'}")
    return 0


def cmd_categories(args) -> int:
    root = args.root or project_root()
    if args.action == "check":
        runtime, _ = open_runtime(root)
        problems = 0
        for check in health.check_categories(runtime):
            if check.error:
                print(f"{check.source:16} could not check: {check.error}")
                problems += 1
                continue
            status = "ok" if not check.missing else f"MISSING on the site: {', '.join(check.missing)}"
            print(f"{check.source:16} {status}" + (f"  (unmapped site categories: {', '.join(check.unmapped)})" if check.unmapped else ""))
            problems += bool(check.missing)
        return 1 if problems else 0
    if args.action == "list":
        for c in configuration.categories(root):
            if args.selected and not c["selected"]:
                continue
            mark = "[x]" if c["selected"] else ("[?]" if c["review"] else "[ ]")
            indent = "  " if c["parent"] else ""
            print(f"{mark} {indent}{c['slug']:40} {c['name']}")
        print("[x] selected  [ ] reviewed mapping  [?] mapping to review before selecting")
        return 0
    for slug in args.slugs:
        configuration.select_category(root, slug, args.action == "select")
        print(f"{slug}: {'selected' if args.action == 'select' else 'unselected'}")
    return 0


def cmd_collect(args) -> int:
    runtime, _ = open_runtime(args.root)
    if args.category:
        runtime = with_only_categories(runtime, args.category)
    return _print_runs(collect_sources(runtime, None if args.all else [args.source]))


def cmd_reparse(args) -> int:
    runtime, _ = open_runtime(args.root)
    return _print_runs(reparse_run(runtime, args.source, args.run))


def cmd_review(args) -> int:
    runtime, _ = open_runtime(args.root)
    if args.action == "accept":
        review.accept(runtime, args.id)
        print(f"review {args.id} accepted")
        return 0
    if args.action == "reject":
        review.reject(runtime, args.id)
        print(f"review {args.id} rejected")
        return 0
    items = review.open_reviews(runtime)
    for item in items:
        payload = {k: v for k, v in item["payload"].items() if k != "key"}
        print(f"#{item['id']:<5} {item['kind']:20} {payload}")
    print(f"{len(items)} open review items")
    return 0


def cmd_doctor(args) -> int:
    runtime, _ = open_runtime(args.root)
    report = health.doctor(runtime, check_conad=not args.offline)
    for source in report.sources:
        run = source.last_run
        when = f"run {run['id']} {run['status']} {source.age_days} days ago, {run['items']} items" if run else "-"
        print(f"{source.source:16} {when}")
        for problem in source.problems:
            print(f"{'':16} ! {problem}")
    reviews = ", ".join(f"{kind}: {n}" for kind, n in report.open_reviews.items()) or "none"
    print(f"open review items: {reviews}")
    if report.conad_session is not None:
        ok, message = report.conad_session
        print(f"conad session: {'ok' if ok else 'NOT OK'} — {message}")
    return 0 if report.ok else 1


def cmd_conad_session(args) -> int:
    root = args.root or project_root()
    if args.action == "import":
        path = conad_session.import_session(root, Path(args.file).read_text(encoding="utf-8"))
        print(f"session saved to {path}")
    runtime, _ = open_runtime(root)
    ok, message = conad_session.check_session(runtime)
    print(("ok: " if ok else "NOT OK: ") + message)
    return 0 if ok else 1


def cmd_fixtures(args) -> int:
    runtime, _ = open_runtime(args.root)
    for source, count in fixtures.capture(runtime, args.source).items():
        print(f"{source:16} {count} pages" + ("" if count else "  (no successful run yet: run `pisaprices collect` first)"))
    print("saved under tests/fixtures/ (not tracked by git)")
    return 0


UNIT_LABEL = {"g": "kg", "ml": "l", "pz": "pz"}


def _per_unit(value, unit) -> str:
    return f"{value:.2f} €/{UNIT_LABEL.get(unit or '', '?')}" if value is not None else "-"


def _promo_line(p) -> str:
    price = p.params.get("promo_price_cents")
    what = f"{_eur(price)}" if price is not None else p.mechanic
    card = f" with {p.program}" + ("" if p.user_has_card else " (you have no card)") if p.program else ""
    until = f" until {p.valid_until}" if p.valid_until else ""
    flags = (" ⚠ ends soon" if p.expiring else "") + (f" [{p.channel}]" if p.channel != "store" else "")
    return f"{what}{card}{until}{flags}"


def cmd_catalog(args) -> int:
    runtime, _ = open_runtime(args.root)
    day = date.fromisoformat(args.date) if getattr(args, "date", None) else None
    if args.action == "browse":
        views = catalog.browse(runtime, args.target, chain=args.chain, promo_only=args.promo, brand=args.brand,
                               tier=args.tier, flags=args.flag, sort=args.sort, day=day)
        for v in views:
            prices = ", ".join(f"{_eur(x.price_cents)} @{x.store}{' (old)' if x.stale else ''}" for x in v.prices) or "promo only"
            promo = f"  | {'; '.join(_promo_line(p) for p in v.promotions)}" if v.promotions else ""
            print(f"#{v.id:<6} {v.chain:8} {_per_unit(v.best_unit_price, v.unit):>12}  {v.name[:60]}  [{prices}]{promo}")
        print(f"{len(views)} products")
        return 0
    if args.action == "product":
        page = catalog.product_page(runtime, int(args.target), day)
        v = page.product
        print(f"{v.name}  ({v.brand or '-'}, {v.chain})  qty {v.net_qty or '-'} {v.unit or ''}  EAN {v.ean or '-'}  tier {v.tier or '-'} {v.flags or ''}")
        for x in v.prices:
            print(f"  {_eur(x.price_cents):>9}  {_per_unit(x.unit_price, v.unit):>12}  {x.label}, seen {x.observed_at[:10]}{' (old)' if x.stale else ''}")
        for promo in v.promotions:
            print(f"  promo: {_promo_line(promo)}")
        if page.min_30_days_cents:
            print(f"  lowest regular price in the last 30 days: {_eur(page.min_30_days_cents)}")
        print(f"  history: {len(page.history)} observations")
        for e in page.equivalents:
            print(f"  equivalent #{e.id} {e.chain:8} {_per_unit(e.best_unit_price, e.unit):>12}  {e.name}")
        return 0
    rows = catalog.promotions(runtime, day, category=args.category, chain=args.chain)
    for promo, items in rows:
        print(f"{promo.chain:8} {_promo_line(promo):45} {', '.join(i.name for i in items)[:80]}")
    print(f"{len(rows)} active promotions")
    unlinked = catalog.unlinked_promotions(runtime)
    if unlinked:
        print(f"{len(unlinked)} promotions not linked to a product yet (pisaprices review)")
    return 0


def cmd_list(args) -> int:
    runtime, _ = open_runtime(args.root)
    if args.action == "import":
        list_id = lists.import_yaml(runtime, Path(args.target).read_text(encoding="utf-8"))
        print(f"list {list_id} imported")
    elif args.action == "export":
        print(lists.export_yaml(runtime, int(args.target)), end="")
    elif args.action == "show":
        found = lists.get_list(runtime, int(args.target))
        print(f"{found['name']} (list {found['id']})")
        for i in found["items"]:
            print(f"  #{i['id']:<5} {i['label']:20} {i['qty_needed']:g} {i['qty_unit']}  match {i['match_mode']}"
                  + (f", min tier {i['min_tier']}" if i["min_tier"] else "") + (f", overbuy {i['overbuy_max_units']}" if i["overbuy_max_units"] else "")
                  + (f", name contains {', '.join(i['name_contains'])}" if i["name_contains"] else ""))
    else:
        for found in lists.all_lists(runtime):
            print(f"{found['id']:>4}  {found['name']}  ({found['created_at'][:10]})")
    return 0


def cmd_plan(args) -> int:
    runtime, _ = open_runtime(args.root)
    list_id = args.list_id
    if args.list:
        list_id = lists.import_yaml(runtime, Path(args.list).read_text(encoding="utf-8"))
    if list_id is None:
        raise ValueError("give --list lista.yaml or --list-id N")
    start = date.fromisoformat(args.start) if args.start else None
    result = plan.plan_list(runtime, list_id, start, args.window, args.max_stores)
    print(f"{result.list_name}: {len(result.needs)} items, {result.days[0]} .. {result.days[-1]}")
    if result.uncovered:
        print(f"  no store has: {', '.join(result.uncovered)}")
    for report in result.reports:
        p = report.plan
        stores = ", ".join(f"{s} on {p.days[s]:%a %d/%m}" for s in p.stores)
        cover = f"covers {len(p.assignment)} of {len(result.needs)}"
        extra = ""
        if report.marginal_saving_cents is not None and report.marginal_saving_cents <= 0:
            extra = "; no saving over one store less"
        elif report.marginal_saving_cents is not None:
            extra = f"; +1 store saves {_eur(report.marginal_saving_cents)} for {report.extra_minutes:g} more minutes"
            if report.break_even_eur_per_hour:
                extra += f" (worth it if your time is worth less than {report.break_even_eur_per_hour:.2f} €/h)"
        print(f"\nK={report.k}: {_eur(p.total_cents)} ({cover}, {p.minutes:g} min, tickets {_eur(p.transport_cents)}) — {stores}{extra}")
        for store, store_lines in report.lines.items():
            for line in store_lines:
                c = line.choice
                promo = f" promo {_promo_line_domain(c.promo)}" if c.promo else ""
                print(f"    {store:26} {line.need.label:16} {c.units} x {c.product.name[:50]:50} {_eur(c.cost_cents):>9}{promo}"
                      + f"  [{c.price_source}{', seen ' + c.observed_at[:10] if c.observed_at else ''}]"
                      + "".join(f"  ! {w}" for w in line.warnings))
        for warning in report.cap_warnings:
            print(f"    ! {warning} -> real total {_eur(report.corrected_total_cents)}")
        for suggestion in report.suggestions:
            print(f"    tip: {suggestion}")
    for program, cents in result.card_savings.items():
        print(f"with a {program} card you would save {_eur(cents)} on this list")
    return 0


def _promo_line_domain(p) -> str:
    price = p.params.get("promo_price_cents")
    return (_eur(price) if price is not None else p.mechanic.value) + (f" until {p.valid_until}" if p.valid_until else "")


def cmd_search(args) -> int:
    runtime, _ = open_runtime(args.root)
    rows = catalog.search(runtime, " ".join(args.words))
    for r in rows:
        unit = {"g": "kg", "ml": "l", "pz": "pz"}.get(r["unit"] or "", "?")
        per_unit = f"{r['unit_price']:.2f} €/{unit}" if r["unit_price"] is not None else "-"
        print(f"{r['chain']:8} {_eur(r['price_cents']):>9}  {per_unit:>12}  {r['name']}  [{r['store_slug'] or '-'}, {(r['observed_at'] or '')[:10]}]")
    print(f"{len(rows)} products")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pisaprices", description="Pisa supermarket prices and basket optimizer")
    parser.add_argument("--root", type=Path, default=None, help="project folder (default: found from the current folder)")
    commands = parser.add_subparsers(dest="command")

    commands.add_parser("init", help="create/migrate the database and sync the configuration").set_defaults(func=cmd_init)

    p = commands.add_parser("categories", help="list, (un)select or check categories against the sites")
    p.add_argument("action", choices=["list", "select", "unselect", "check"])
    p.add_argument("slugs", nargs="*")
    p.add_argument("--selected", action="store_true", help="list: only selected categories")
    p.set_defaults(func=cmd_categories)

    p = commands.add_parser("collect", help="collect prices and promotions")
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument("--source", choices=SOURCES)
    group.add_argument("--all", action="store_true")
    p.add_argument("--category", action="append", help="limit to these selected categories (repeatable)")
    p.set_defaults(func=cmd_collect)

    p = commands.add_parser("reparse", help="rebuild a run from its saved snapshots (no network)")
    p.add_argument("--source", choices=SOURCES, required=True)
    p.add_argument("--run", type=int, required=True)
    p.set_defaults(func=cmd_reparse)

    p = commands.add_parser("review", help="the review queue")
    p.add_argument("action", nargs="?", choices=["list", "accept", "reject"], default="list")
    p.add_argument("id", nargs="?", type=int)
    p.set_defaults(func=cmd_review)

    p = commands.add_parser("doctor", help="health of every source, review queue, Conad session")
    p.add_argument("--offline", action="store_true", help="skip the Conad session request")
    p.set_defaults(func=cmd_doctor)

    p = commands.add_parser("conad-session", help="import or check the Conad session (store selected in a browser)")
    p.add_argument("action", choices=["import", "check"])
    p.add_argument("file", nargs="?", help="import: text file with the 'Copy as cURL (bash)' of a category page")
    p.set_defaults(func=cmd_conad_session)

    p = commands.add_parser("fixtures", help="copy the last good run of each source into tests/fixtures (local only)")
    p.add_argument("action", choices=["capture"])
    p.add_argument("--source", choices=SOURCES, action="append")
    p.set_defaults(func=cmd_fixtures)

    p = commands.add_parser("catalog", help="browse a category, see a product, or the active promotions")
    p.add_argument("action", choices=["browse", "product", "promos"])
    p.add_argument("target", nargs="?", help="browse: category slug; product: product id")
    p.add_argument("--chain")
    p.add_argument("--promo", action="store_true", help="browse: only products on promotion")
    p.add_argument("--brand")
    p.add_argument("--tier", type=int)
    p.add_argument("--flag", action="append", help="browse: required flag (bio, gluten_free, ...)")
    p.add_argument("--category", help="promos: limit to a category")
    p.add_argument("--sort", choices=["unit_price", "price", "discount", "name"], default="unit_price")
    p.add_argument("--date", help="YYYY-MM-DD (default: today)")
    p.set_defaults(func=cmd_catalog)

    p = commands.add_parser("list", help="shopping lists")
    p.add_argument("action", choices=["all", "import", "export", "show"])
    p.add_argument("target", nargs="?", help="import: lista.yaml; export/show: list id")
    p.set_defaults(func=cmd_list)

    p = commands.add_parser("plan", help="cheapest way to buy a shopping list (K = 1..3 stores)")
    p.add_argument("--list", help="lista.yaml (imported as a new list)")
    p.add_argument("--list-id", type=int)
    p.add_argument("--from", dest="start", help="first possible shopping day, YYYY-MM-DD (default: today)")
    p.add_argument("--window", type=int, help="days in the plan window (default: settings)")
    p.add_argument("--max-stores", type=int, help="largest K (default: settings)")
    p.set_defaults(func=cmd_plan)

    p = commands.add_parser("search", help="full-text search, cheapest €/kg first")
    p.add_argument("words", nargs="+")
    p.set_defaults(func=cmd_search)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 0
    if args.command == "review" and args.action in ("accept", "reject") and args.id is None:
        parser.error("review accept/reject needs an id")
    if args.command in ("catalog", "list") and args.action in ("browse", "product", "import", "export", "show") and not args.target:
        parser.error(f"{args.command} {args.action} needs a target")
    if args.command == "conad-session" and args.action == "import" and not args.file:
        parser.error("conad-session import needs the file with the copied cURL")
    try:
        return args.func(args)
    except (ConfigError, ValueError, FileNotFoundError, lists.ListError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
