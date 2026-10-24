"""The pages of the Streamlit UI. Thin adapter: it only calls pisaprices.services."""

from datetime import date, timedelta

import pandas as pd
import streamlit as st

from pisaprices.services import catalog, category_setup, configuration, conad_session, health, lists, plan, quick, review
from pisaprices.services.app import open_runtime, project_root, with_only_categories
from pisaprices.services.configuration import ConfigError
from pisaprices.services.runs import collect_sources

UNIT = {"g": "kg", "ml": "l", "pz": "pz"}
DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


@st.cache_resource
def runtime():
    rt, _ = open_runtime(project_root())
    return rt


def reload() -> None:
    runtime.clear()
    st.rerun()


def eur(cents) -> str:
    return "-" if cents is None else f"{cents / 100:.2f} €".replace(".", ",")


def per_unit(value, unit) -> str:
    return "-" if value is None else f"{value:.2f} €/{UNIT.get(unit or '', '?')}"


def promo_text(p) -> str:
    price = p.params.get("promo_price_cents")
    text = eur(price) if price is not None else p.mechanic
    if p.program:
        text += f" cu {CARDS.get(p.program, p.program)}" + ("" if p.user_has_card else " (nu ai cardul)")
    if p.valid_until:
        until = p.valid_until if isinstance(p.valid_until, date) else date.fromisoformat(str(p.valid_until))
        text += f", până pe {until:%d.%m}"
    if p.expiring:
        text += " ⚠ expiră curând"
    return text


def add_to_list_form(product) -> None:
    rt = runtime()
    with st.form(f"add-{product.id}"):
        all_lists = lists.all_lists(rt)
        choice = st.selectbox("Lista", [f"{x['id']} · {x['name']}" for x in all_lists] + ["+ listă nouă"])
        default_qty = f"{product.net_qty:g} {product.unit}" if product.net_qty and product.unit else "1 pz"
        qty = st.text_input("Cantitate", default_qty)
        mode = st.radio("Accept", ["orice produs din categorie", "aceeași marcă", "doar acest produs (EAN)"], horizontal=True)
        if st.form_submit_button("Adaugă în listă"):
            list_id = lists.create_list(rt, "Lista mea") if choice.startswith("+") else int(choice.split(" · ")[0])
            item = {"label": product.name[:40], "quantity": qty, "category": product.category}
            if mode.startswith("aceeași"):
                item |= {"match": "brand", "brand": product.brand}
            elif mode.startswith("doar"):
                item |= {"match": "exact_gtin", "gtin": product.ean}
            try:
                lists.add_item(rt, list_id, item)
                st.success("Adăugat")
            except ValueError as exc:
                st.error(str(exc))


CHAINS = {"coop": "Coop", "conad": "Conad", "lidl": "Lidl", "eurospin": "Eurospin"}
SORTS = {"unit_price": "€/kg cel mai mic", "price": "preț cel mai mic", "discount": "reducere cea mai mare", "name": "nume"}
QUALITY_RO = {None: "oricare", 1: "economic", 2: "standard", 3: "premium"}
FLAGS_RO = {"bio": "bio", "gluten_free": "fără gluten", "lactose_free": "fără lactoză", "vegan": "vegan"}


def category_choices(root) -> dict[str, str]:
    """slug -> label, groups first in each block: 'Pasta, riso e farine (tot)', '   Pasta secca'."""
    all_categories = configuration.categories(root)
    choices = {}
    for group in [c for c in all_categories if not c["parent"]]:
        children = [c for c in all_categories if c["parent"] == group["slug"] and c["selected"]]
        if not children and group["selected"]:
            choices[group["slug"]] = group["name"]
        elif len(children) == 1:
            choices[children[0]["slug"]] = group["name"]
        elif children:
            choices[group["slug"]] = f"{group['name']} (tot)"
            choices |= {c["slug"]: f"{group['name']} › {c['name']}" for c in children}
    return choices


def chain_filter(key: str) -> str | None:
    picked = st.pills("Magazin", list(CHAINS), format_func=CHAINS.get, key=key)
    return picked


def page_catalog() -> None:
    rt = runtime()
    st.title("Catalog")
    words = st.text_input("Caută un produs", placeholder="ex. mozzarella, Barilla, caffè")
    if words:
        rows = catalog.search(rt, words)
        st.caption(f"{len(rows)} produse găsite")
        st.dataframe(pd.DataFrame([{"magazin": CHAINS.get(r["chain"], r["chain"]), "produs": r["name"],
                                    "marcă": quick.display_brand(r["brand"]), "preț": eur(r["price_cents"]),
                                    "€/kg sau €/l": per_unit(r["unit_price"], r["unit"])} for r in rows]),
                     hide_index=True, width="stretch")
        return
    choices = category_choices(rt.root)
    if not choices:
        st.info("Nicio categorie colectată: alege-le în «Categorii».")
        return
    category = st.selectbox("Categorie", list(choices), format_func=choices.get)
    left, right = st.columns([3, 1], vertical_alignment="bottom")
    with left:
        chain = chain_filter("catalog-chain")
    promo_only = right.toggle("Doar în ofertă")
    with st.expander("Mai multe filtre"):
        cols = st.columns(4)
        tier = cols[0].selectbox("Calitate minimă", list(QUALITY_RO), format_func=QUALITY_RO.get)
        flags = cols[1].multiselect("Cerințe", list(FLAGS_RO), format_func=FLAGS_RO.get)
        sort = cols[2].selectbox("Ordonează după", list(SORTS), format_func=SORTS.get)
        day = cols[3].date_input("Prețuri valabile pe", date.today())
    views = catalog.browse(rt, category, chain=chain, promo_only=promo_only, tier=tier, flags=flags, sort=sort, day=day)
    table = pd.DataFrame([{
        "id": v.id, "magazin": CHAINS.get(v.chain, v.chain), "produs": v.name, "marcă": quick.display_brand(v.brand),
        "preț": eur(min((p.price_cents for p in v.prices), default=None)) if v.prices else "doar în ofertă",
        "€/kg sau €/l": per_unit(v.best_unit_price, v.unit),
        "ofertă": "; ".join(promo_text(p) for p in v.promotions),
    } for v in views])
    st.caption(f"{len(views)} produse · alege un rând ca să vezi detaliile")
    event = st.dataframe(table, hide_index=True, width="stretch", on_select="rerun", selection_mode="single-row",
                         column_config={"id": None})
    rows = event.selection.rows if event else []
    if rows:
        product_page(int(table.iloc[rows[0]]["id"]), day)


def product_page(product_id: int, day: date) -> None:
    page = catalog.product_page(runtime(), product_id, day)
    v = page.product
    with st.container(border=True):
        st.subheader(v.name)
        facts = [quick.display_brand(v.brand), CHAINS.get(v.chain, v.chain),
                 f"{v.net_qty:g} {v.unit}" if v.net_qty and v.unit else "", f"calitate {QUALITY_RO.get(v.tier, '-')}",
                 *(FLAGS_RO.get(f, f) for f in v.flags)]
        st.caption(" · ".join(f for f in facts if f))
        for p in v.prices:
            st.write(f"**{eur(p.price_cents)}** · {per_unit(p.unit_price, v.unit)} · {p.label} · văzut pe {p.observed_at[:10]}"
                     f"{' · ⚠ preț vechi' if p.stale else ''}")
        for promo in v.promotions:
            st.write(f"🏷️ {promo_text(promo)}")
        if page.min_30_days_cents:
            st.caption(f"Cel mai mic preț din ultimele 30 de zile: {eur(page.min_30_days_cents)}")
        if len(page.history) > 1:
            history = pd.DataFrame([{"data": h["observed_at"][:10], "magazin": h["store_slug"], "preț €": h["price_cents"] / 100}
                                    for h in page.history])
            st.line_chart(history.pivot_table(index="data", columns="magazin", values="preț €"))
        if page.equivalents:
            st.write("La celelalte magazine:")
            st.dataframe(pd.DataFrame([{"magazin": CHAINS.get(e.chain, e.chain), "produs": e.name,
                                        "€/kg sau €/l": per_unit(e.best_unit_price, e.unit)} for e in page.equivalents]),
                         hide_index=True)
        with st.expander("Adaugă într-o listă salvată"):
            add_to_list_form(v)


def page_promotions() -> None:
    rt = runtime()
    st.title("Oferte active")
    choices = category_choices(rt.root)
    left, right = st.columns([2, 3], vertical_alignment="bottom")
    category = left.selectbox("Categorie", [None, *choices], format_func=lambda s: "toate" if s is None else choices[s])
    with right:
        chain = chain_filter("promo-chain")
    rows = catalog.promotions(rt, date.today(), category=category, chain=chain)
    st.caption(f"{len(rows)} oferte valabile azi")
    st.dataframe(pd.DataFrame([{
        "magazin": CHAINS.get(p.chain, p.chain), "produs": ", ".join(i.name for i in items), "ofertă": promo_text(p),
        "doar online": "da" if p.channel == "online" else "",
    } for p, items in rows]), hide_index=True, width="stretch")
    unlinked = catalog.unlinked_promotions(rt)
    if unlinked:
        st.caption(f"{len(unlinked)} oferte nu sunt încă legate de un produs (vezi «Revizuire»).")


def page_lists() -> None:
    rt = runtime()
    st.title("Liste de cumpărături")
    all_lists = lists.all_lists(rt)
    with st.expander("Listă nouă / importă lista.yaml"):
        name = st.text_input("Nume")
        if st.button("Creează") and name:
            lists.create_list(rt, name)
            st.rerun()
        uploaded = st.file_uploader("lista.yaml", type=["yaml", "yml"])
        if uploaded and st.button("Importă"):
            try:
                lists.import_yaml(rt, uploaded.read().decode("utf-8"))
                st.rerun()
            except ValueError as exc:
                st.error(str(exc))
    if not all_lists:
        st.info("Nicio listă: creează una sau adaugă produse din catalog.")
        return
    chosen = st.selectbox("Lista", all_lists, format_func=lambda x: f"{x['id']} · {x['name']}")
    found = lists.get_list(rt, chosen["id"])
    for item in found["items"]:
        cols = st.columns([4, 2, 2, 1])
        cols[0].write(item["label"])
        cols[1].write(f"{item['qty_needed']:g} {item['qty_unit']}")
        cols[2].write(item["match_mode"] + (f", calitate ≥ {item['min_tier']}" if item["min_tier"] else "")
                      + (f", conține {' '.join(item['name_contains'])}" if item["name_contains"] else ""))
        if cols[3].button("✕", key=f"del-{item['id']}"):
            lists.remove_item(rt, item["id"])
            st.rerun()
    with st.form("new-item"):
        selected = [c["slug"] for c in configuration.categories(rt.root) if c["selected"]]
        label = st.text_input("Articol")
        category = st.selectbox("Categorie", selected)
        qty = st.text_input("Cantitate", "1 kg")
        contains = st.text_input("Numele conține (opțional, ex. extravergine)")
        min_tier = st.selectbox("Calitate minimă", [None, 1, 2, 3], format_func=lambda t: {None: "oricare", 1: "economic", 2: "standard", 3: "premium"}[t])
        overbuy = st.number_input("Bucăți în plus acceptate dacă e mai ieftin", 0, 5, 0)
        if st.form_submit_button("Adaugă") and label:
            try:
                lists.add_item(rt, chosen["id"], {"label": label, "category": category, "quantity": qty,
                                                  "min_tier": min_tier, "overbuy": overbuy, "contains": contains.split() or None})
                st.rerun()
            except ValueError as exc:
                st.error(str(exc))
    st.download_button("Exportă lista.yaml", lists.export_yaml(rt, chosen["id"]), file_name="lista.yaml")
    if found["items"]:
        st.header("Unde cumpăr lista asta")
        start = st.date_input("Din ziua", date.today())
        window = st.slider("Pot merge în următoarele (zile)", 1, 14, rt.config.settings.optimizer.planning_window_days)
        if st.button("Arată unde cumpăr", type="primary"):
            st.session_state.list_result = plan.plan_list(rt, chosen["id"], start, window)
        result = st.session_state.get("list_result")
        if result is not None and result.list_name == found["name"]:
            show_plan(result, "list")


WEEKDAYS_RO = ["luni", "marți", "miercuri", "joi", "vineri", "sâmbătă", "duminică"]
CARDS = {"lidl_plus": "Lidl Plus", "coop_socio": "Carta Socio Coop", "conad_carta_insieme": "Carta Insieme Conad",
         "eurospin_family": "Eurospin Family"}
QUALITY = {"oricare": None, "economic": 1, "standard": 2, "premium": 3}
EXAMPLE = "pasta 1 kg\n2 l latte\nuova 10\nmozzarella\nolio extravergine\nmele 2 kg"


def day_ro(day: date) -> str:
    return f"{WEEKDAYS_RO[day.weekday()]} {day:%d.%m}"


def stores_ro(k: int) -> str:
    return f"{k} {'magazin' if k == 1 else 'magazine'}"


def offer_ro(choice) -> str:
    promo = choice.promo
    if promo is None:
        return ""
    parts = []
    price = promo.params.get("promo_price_cents")
    if price is not None and promo.prior_price_cents:
        parts.append(f"{eur(price)} în loc de {eur(promo.prior_price_cents)}")
    elif price is not None:
        parts.append(f"ofertă {eur(price)}")
    if promo.valid_until:
        parts.append(f"până pe {promo.valid_until:%d.%m}")
    if promo.program:
        parts.append(f"cu {CARDS.get(promo.program, promo.program)}")
    if promo.is_sottocosto:
        parts.append("stoc limitat")
    if promo.requires_activation:
        parts.append("activează cuponul în aplicație")
    return ", ".join(parts)


def product_ro(product) -> str:
    brand = quick.display_brand(product.brand)
    return f"{product.name} · {brand}" if brand and brand.lower() not in product.name.lower() else product.name


def plan_summary(result, key: str):
    """The recommendation in one box, the other options as a small choice. Returns the report shown."""
    options = result.options
    if not options:
        st.error("Niciun magazin nu are toate produsele în zilele alese. Încearcă mai multe zile sau scoate un produs.")
        return None
    cheapest = options[-1]   # each option is cheaper than the ones with fewer stores
    k = cheapest.k
    if len(options) > 1:
        labels = {r.k: f"{stores_ro(r.k)} · {eur(r.plan.total_cents)}" for r in options}
        k = st.segmented_control("Câte magazine?", list(labels), default=cheapest.k, format_func=labels.get,
                                 key=f"{key}-k") or cheapest.k
    report = next(r for r in options if r.k == k)
    with st.container(border=True):
        st.metric(f"Total cu {stores_ro(k)}", eur(report.plan.total_cents),
                  delta=f"-{eur(report.saving_vs_best_single_cents)} față de un singur magazin"
                  if report.saving_vs_best_single_cents else None, delta_color="inverse")
        visits = " → ".join(f"**{result.store_names.get(s, s)}** ({day_ro(report.plan.days[s])})" for s in report.plan.stores)
        st.markdown(visits)
        travel = f"{report.plan.minutes:g} min de drum"
        if report.plan.transport_cents:
            travel += f", bilete {eur(report.plan.transport_cents)} (incluse în total)"
        st.caption(travel)
    return report


def shopping_route(result, report) -> None:
    for store in report.plan.stores:
        lines = report.lines.get(store, [])
        subtotal = sum(line.choice.cost_cents for line in lines)
        name = result.store_names.get(store, store)
        st.subheader(f"🛒 {name} · {day_ro(report.plan.days[store])} · {eur(subtotal)}")
        rows = [{"pentru": line.need.label, "ce cumperi": product_ro(line.choice.product), "buc.": line.choice.units,
                 "preț": eur(line.choice.cost_cents), "ofertă": offer_ro(line.choice)} for line in lines]
        table = pd.DataFrame(rows)
        if not table["ofertă"].any():
            table = table.drop(columns="ofertă")
        st.dataframe(table, hide_index=True, width="stretch")
        sources = {line.choice.price_source for line in lines if line.choice.price_source != name}
        if sources:
            seen = sorted({line.choice.observed_at[:10] for line in lines if line.choice.observed_at})
            st.caption(f"Prețuri: {', '.join(sources)}, văzute pe {', '.join(seen)}.")
    for _ in report.cap_warnings:
        st.warning(f"Limită de bucăți depășită: totalul real e {eur(report.corrected_total_cents)}.")
    for tip in report.suggestions:
        st.info(tip)
    for program, cents in result.card_savings.items():
        st.info(f"Cu {CARDS.get(program, program)} ai plăti cu {eur(cents)} mai puțin pentru această listă.")


def _cell(choice) -> str:
    brand = quick.display_brand(choice.product.brand)
    return f"{eur(choice.cost_cents)} · {brand}" if brand else eur(choice.cost_cents)


def price_report(result) -> None:
    stores = [s for s in result.store_names if any(s in by_store for by_store in result.matrix.values())]
    rows, values = [], []
    for need_id, need in result.needs.items():
        by_store = result.matrix.get(need_id, {})
        rows.append({"produs": need.label, **{result.store_names[s]: _cell(by_store[s]) if s in by_store else "—" for s in stores}})
        values.append([by_store[s].cost_cents if s in by_store else None for s in stores])
    table = pd.DataFrame(rows)

    def highlight(row):
        costs = [v for v in values[row.name] if v is not None]
        best = min(costs) if costs else None
        return [""] + ["font-weight: bold; color: #2e9e55" if best is not None and values[row.name][i] == best else ""
                       for i in range(len(stores))]

    st.dataframe(table.style.apply(highlight, axis=1), hide_index=True, width="stretch")
    st.caption("Cel mai bun preț din zilele alese pentru cantitatea cerută, cu marca produsului. Cu verde: cel mai mic.")


def show_plan(result, key: str = "plan") -> None:
    if result.uncovered:
        st.warning(f"Niciun magazin nu are: {', '.join(result.uncovered)}")
    report = plan_summary(result, key)
    if report is None:
        return
    shopping_route(result, report)
    with st.expander("Compară prețurile la toate magazinele"):
        price_report(result)


def _drafts_table(drafts, categories: dict[str, str]) -> pd.DataFrame:
    return pd.DataFrame([{
        "produs": d.label, "categorie": categories.get(d.category, "—"), "cantitate": d.quantity,
        "numele conține": " ".join(d.words), "marcă": d.brand or "", "calitate": next(k for k, v in QUALITY.items() if v == d.min_tier),
        "bio": "bio" in d.flags, "mărci găsite": ", ".join(d.brands),
    } for d in drafts])


def _drafts_from(table: pd.DataFrame, by_name: dict[str, str], previous) -> list:
    brands = {d.label: d.brands for d in previous}
    return [quick.Draft(line=row["produs"], label=str(row["produs"])[:40], words=str(row["numele conține"] or "").split(),
                        quantity=str(row["cantitate"] or "1 pz"), category=by_name.get(row["categorie"]),
                        min_tier=QUALITY.get(row["calitate"]), flags=["bio"] if row["bio"] else [],
                        brand=str(row.get("marcă") or "").strip() or None, brands=brands.get(row["produs"], []))
            for row in table.to_dict("records") if row.get("produs")]


def _plan(rt, drafts) -> None:
    try:
        st.session_state.shopping_result = quick.plan_drafts(rt, drafts, st.session_state.shop_start, st.session_state.shop_window)
    except ValueError:
        st.session_state.shopping_result = None


def page_shopping() -> None:
    rt = runtime()
    st.session_state.setdefault("shop_start", date.today())
    st.session_state.setdefault("shop_window", rt.config.settings.optimizer.planning_window_days)
    st.title("Ce cumperi?")
    st.caption("Câte un produs pe rând, cum îți vine. Cantitatea și marca sunt opționale.")
    text = st.text_area("Lista de cumpărături", st.session_state.get("shopping_text", ""), height=170,
                        placeholder=EXAMPLE, label_visibility="collapsed")
    left, right = st.columns([1, 4], vertical_alignment="center")
    search = left.button("Caută", type="primary", width="stretch")
    days = st.session_state.shop_window
    with right.popover(f"📅 {'de azi' if st.session_state.shop_start == date.today() else 'din ' + day_ro(st.session_state.shop_start)}, "
                       f"în următoarele {days} zile"):
        st.date_input("Din ziua", key="shop_start")
        st.slider("Pot merge în următoarele (zile)", 1, 14, key="shop_window")

    if search and text.strip():
        st.session_state.shopping_text = text
        st.session_state.drafts = quick.interpret(rt, text)
        st.session_state.drafts_version = st.session_state.get("drafts_version", 0) + 1
        _plan(rt, st.session_state.drafts)
    drafts = st.session_state.get("drafts")
    if not drafts:
        return

    missing = [d for d in drafts if not d.category]
    if missing:
        st.warning("Nu găsesc: " + "; ".join(f"**{d.label}** ({d.note})" for d in missing)
                   + ". Le poți corecta mai jos, la «Cum am înțeles lista».")
    result = st.session_state.get("shopping_result")
    if result is not None:
        show_plan(result, "shop")

    categories = {c["slug"]: c["name"] for c in configuration.categories(rt.root) if c["selected"]}
    with st.expander("Cum am înțeles lista (poți corecta)", expanded=result is None):
        st.caption("Schimbă categoria, cantitatea sau marca, apoi apasă «Recalculează». "
                   "«Numele conține» = cuvinte obligatorii în numele produsului.")
        edited = st.data_editor(_drafts_table(drafts, categories), hide_index=True, width="stretch", num_rows="dynamic",
                                disabled=["mărci găsite"], key=f"drafts-{st.session_state.get('drafts_version', 0)}",
                                column_config={
                                    "categorie": st.column_config.SelectboxColumn(options=["—", *sorted(categories.values())]),
                                    "marcă": st.column_config.TextColumn(help="Gol = orice marcă. Ex.: Barilla, Conad, Coop"),
                                    "calitate": st.column_config.SelectboxColumn(options=list(QUALITY)),
                                })
        if st.button("Recalculează"):
            st.session_state.drafts = _drafts_from(edited, {v: k for k, v in categories.items()}, drafts)
            st.session_state.drafts_version += 1
            _plan(rt, st.session_state.drafts)
            st.rerun()
    if result is not None:
        with st.popover("💾 Salvează lista"):
            name = st.text_input("Numele listei", "Cumpărăturile mele")
            if st.button("Salvează"):
                st.success(f"Lista {quick.save_as_list(rt, name, drafts)} e salvată în «Liste salvate».")


def page_review() -> None:
    rt = runtime()
    st.title("Revizuire")
    items = review.open_reviews(rt)
    st.caption(f"{len(items)} elemente deschise")
    for item in items:
        payload = {k: v for k, v in item["payload"].items() if k != "key"}
        cols = st.columns([2, 6, 1, 1])
        cols[0].write(item["kind"])
        cols[1].write(payload)
        if cols[2].button("✓", key=f"ok-{item['id']}"):
            review.accept(rt, item["id"])
            st.rerun()
        if cols[3].button("✕", key=f"no-{item['id']}"):
            review.reject(rt, item["id"])
            st.rerun()


def page_settings() -> None:
    rt = runtime()
    root = rt.root
    st.title("Setări")
    try:
        st.subheader("Categorii colectate")
        for c in configuration.categories(root):
            if c["review"] and not c["selected"]:
                continue
            value = st.checkbox(c["name"], c["selected"], key=f"cat-{c['slug']}")
            if value != c["selected"]:
                configuration.select_category(root, c["slug"], value)
                reload()
        st.caption("Aici apar doar categoriile cu mapare verificată. Pentru una nouă: pagina «Categorii».")

        st.subheader("Carduri")
        for program, cfg in rt.config.user.programs.items():
            value = st.checkbox(program.value, cfg.has_card, key=f"card-{program.value}")
            if value != cfg.has_card:
                configuration.set_program(root, program.value, value, cfg.n_cards)
                reload()

        st.subheader("Magazine")
        for slug, travel in rt.config.user.stores.items():
            with st.form(f"store-{slug}"):
                st.write(f"**{slug}**")
                minutes = st.number_input("Minute (doar dus)", 0.0, 180.0, float(travel.travel_minutes_one_way))
                transport = st.selectbox("Transport", ["walk", "bike", "bus", "car"], ["walk", "bike", "bus", "car"].index(travel.transport.value))
                ticket = st.number_input("Bilet dus-întors (€)", 0.0, 50.0, float(travel.transport_cost_eur or 0))
                allowed = st.multiselect("Zile posibile (gol = oricând)", DAYS, [d.value for d in travel.allowed_days or []])
                on_route = st.multiselect("Zile în care e în drum", DAYS, [d.value for d in travel.on_route_days])
                if st.form_submit_button("Salvează"):
                    configuration.update_user_store(root, slug, travel_minutes_one_way=minutes, transport=transport,
                                                    transport_cost_eur=ticket if transport == "bus" else None,
                                                    allowed_days=allowed or None, on_route_days=on_route)
                    reload()
    except ConfigError as exc:
        st.error(str(exc))


def page_health() -> None:
    rt = runtime()
    st.title("Stare")
    report = health.doctor(rt, check_conad=st.checkbox("Verifică sesiunea Conad (1 request)", True))
    st.dataframe(pd.DataFrame([{
        "sursă": s.source, "ultima rulare": s.last_run["status"] if s.last_run else "-",
        "acum (zile)": s.age_days, "produse": s.last_run["items"] if s.last_run else None, "probleme": "; ".join(s.problems),
    } for s in report.sources]), hide_index=True, width="stretch")
    if report.conad_session:
        ok, message = report.conad_session
        (st.success if ok else st.error)(message)
    with st.expander("Actualizează sesiunea Conad"):
        st.markdown("Pe spesaonline.conad.it alege *Ordina e Ritira* și magazinul, deschide o categorie, "
                    "F12 → Network → **Doc** → click dreapta pe pagină → *Copy as cURL (bash)* și lipește aici.")
        text = st.text_area("cURL copiat")
        if st.button("Salvează sesiunea") and text:
            try:
                conad_session.import_session(rt.root, text)
                ok, message = conad_session.check_session(rt)
                (st.success if ok else st.error)(message)
            except ValueError as exc:
                st.error(str(exc))
    if st.button("Colectează acum (toate sursele)"):
        with st.spinner("Colectare în curs: câteva minute, cu pauze între request-uri"):
            result = collect_sources(rt)
        for run in result.runs:
            st.write(f"{run.source}: {run.status.value}, {run.items} produse" + (f" — {run.error}" if run.error else ""))


def _words(text: str) -> list[str]:
    return [w.strip() for w in text.split(",") if w.strip()]


def page_categories() -> None:
    rt = runtime()
    st.title("Categorii")
    st.caption("Adaugă o categorie la colectare: alegi corespondentul ei pe fiecare site, verifici regulile "
               "pentru Lidl și Eurospin pe ofertele deja descărcate, apoi salvezi.")
    if st.button("Actualizează listele de categorii ale site-urilor (3 request-uri)"):
        with st.spinner("Citesc categoriile Coop, Conad și Eurospin"):
            for source, outcome in category_setup.refresh_site_categories(rt).items():
                (st.success if isinstance(outcome, int) else st.warning)(f"{source}: {outcome}")

    every = configuration.categories(rt.root)
    status = {c["slug"]: ("✓ colectată" if c["selected"] else "verificată" if not c["review"] else "de configurat") for c in every}
    slug = st.selectbox("Categoria", [c["slug"] for c in every],
                        format_func=lambda s: f"{next(c['name'] for c in every if c['slug'] == s)} — {status[s]}")
    current = category_setup.draft(rt, slug)
    st.write(f"**Conad**: cod {', '.join(current.sources.get('conad_catalog', [])) or '-'} (taxonomia proprie e arborele Conad)")

    coop_options = {c["key"]: c["name"] for c in category_setup.site_categories(rt, "coop_catalog")}
    for key in current.sources.get("coop_catalog", []):
        coop_options.setdefault(key, key)
    if not coop_options:
        st.info("Lista Coop e goală: apasă «Actualizează listele de categorii».")
    coop = st.multiselect("Coop: categoriile de pe prenotalaspesa", sorted(coop_options),
                          default=current.sources.get("coop_catalog", []), format_func=lambda k: f"{coop_options[k]}  ({k})")

    lidl_seen = dict(category_setup.lidl_coarse_categories(rt))
    lidl_options = sorted(set(lidl_seen) | set(current.sources.get("lidl_offers", [])))
    lidl = st.multiselect("Lidl: categorii grosiere (din ofertele ultimei rulări)", lidl_options,
                          default=current.sources.get("lidl_offers", []),
                          format_func=lambda k: f"{k} ({lidl_seen.get(k, 0)} produse)")
    euro_options = {c["key"]: c["name"] for c in category_setup.site_categories(rt, "eurospin_offers")}
    for key in current.sources.get("eurospin_offers", []):
        euro_options.setdefault(key, key)
    eurospin = st.multiselect("Eurospin: categorii grosiere", sorted(euro_options),
                              default=current.sources.get("eurospin_offers", []), format_func=lambda k: f"{euro_options[k]} ({k})")

    include = _words(st.text_input("Titlul conține unul dintre (separate prin virgulă; gol = orice)", ", ".join(current.include)))
    exclude = _words(st.text_input("Titlul NU conține niciunul dintre", ", ".join(current.exclude)))

    fetch = st.checkbox("Pentru categoriile Eurospin nedescărcate încă, încarcă exemple (1 request fiecare)")
    for source, keys in (("lidl_offers", lidl), ("eurospin_offers", eurospin)):
        if not keys:
            continue
        lines, missing = category_setup.preview(rt, source, keys, include, exclude, allow_fetch=fetch)
        taken = [l for l in lines if l.included]
        st.write(f"**{source}**: {len(taken)} din {len(lines)} produse ar intra în categorie")
        if lines:
            st.dataframe(pd.DataFrame([{"intră": "✓" if l.included else "", "produs": l.title, "categorie site": l.coarse} for l in lines]),
                         hide_index=True, width="stretch")
        if missing:
            st.info(f"Fără exemple pentru {', '.join(missing)}: bifează încărcarea exemplelor de mai sus.")

    collect_now = st.checkbox("Colectează imediat această categorie după salvare")
    if st.button("Salvează și selectează categoria", type="primary"):
        try:
            category_setup.save(rt, slug, coop=coop, lidl=lidl, eurospin=eurospin, include=include, exclude=exclude)
        except (ConfigError, ValueError) as exc:
            st.error(str(exc))
            return
        if collect_now:
            fresh = open_runtime(rt.root)[0]
            with st.spinner("Colectare în curs (câteva minute, cu pauze între request-uri)"):
                result = collect_sources(with_only_categories(fresh, [slug]))
            for run in result.runs:
                st.write(f"{run.source}: {run.status.value}, {run.items} produse" + (f" — {run.error}" if run.error else ""))
        st.success("Salvat. Categoria intră în colectarea zilnică.")
        runtime.clear()
