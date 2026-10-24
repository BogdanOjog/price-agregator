"""stores.yaml and categories.yaml -> database rows, upserted by slug.

Only identity is synced (for foreign keys); the site-category mapping stays in the YAML. Rows
missing from the YAML are not deleted, since observations may reference them; they are
reported as orphans."""

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from pisaprices.config.schemas import AppConfig
from pisaprices.storage.orm import Category, Store


@dataclass
class SyncReport:
    created: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    orphans: list[str] = field(default_factory=list)


def _assign(row, values: dict) -> bool:
    changed = False
    for name, value in values.items():
        if getattr(row, name) != value:
            setattr(row, name, value)
            changed = True
    return changed


def _upsert(session: Session, model, slug: str, values: dict, report: SyncReport, label: str):
    row = session.scalar(select(model).where(model.slug == slug))
    if row is None:
        row = model(slug=slug, **values)
        session.add(row)
        report.created.append(f"{label}:{slug}")
    elif _assign(row, values):
        report.updated.append(f"{label}:{slug}")
    return row


def sync_config(session: Session, config: AppConfig) -> SyncReport:
    report = SyncReport()

    stores = {}
    for s in config.stores.stores:
        values = dict(
            chain=s.chain.value, name=s.name, address=s.address, external_id=s.external_id, cap=s.cap,
            outside_scope_approved=s.outside_scope_approved, source_mode=s.source_mode.value, visitable=s.visitable,
        )
        stores[s.slug] = _upsert(session, Store, s.slug, values, report, "store")
    session.flush()
    for s in config.stores.stores:
        price_source_id = stores[s.price_source].id if s.price_source else None
        if _assign(stores[s.slug], {"price_source_store_id": price_source_id}) and f"store:{s.slug}" not in report.created:
            report.updated.append(f"store:{s.slug}")

    categories = {c.slug: _upsert(session, Category, c.slug, {"name": c.name}, report, "category")
                  for c in config.categories.categories}
    session.flush()
    for c in config.categories.categories:
        parent_id = categories[c.parent].id if c.parent else None
        if _assign(categories[c.slug], {"parent_id": parent_id}) and f"category:{c.slug}" not in report.created:
            report.updated.append(f"category:{c.slug}")

    yaml_stores = set(stores)
    yaml_categories = set(categories)
    report.orphans += [f"store:{slug}" for slug in session.scalars(select(Store.slug)) if slug not in yaml_stores]
    report.orphans += [f"category:{slug}" for slug in session.scalars(select(Category.slug)) if slug not in yaml_categories]
    session.flush()
    return report
