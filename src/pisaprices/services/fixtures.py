"""Real fixtures for local tests: copied from the last good run of each source, never committed.

The repository is public and store pages may not be redistributed, so tests/fixtures/ is ignored
by git; `pisaprices fixtures capture` rebuilds it on any machine from that machine's own runs."""

import json
import re
import shutil
from pathlib import Path

from sqlalchemy import select

from pisaprices.domain.enums import RunStatus
from pisaprices.ingest.snapshots import load_snapshots
from pisaprices.services.collect import Runtime
from pisaprices.services.runs import ORDER
from pisaprices.storage.orm import ScrapeRun

FIXTURES = Path("tests") / "fixtures"
# identifiers of the user's session that pages may echo back (cart ids, uuids)
SCRUB = [
    (re.compile(r"c-s-N-\d{2}-\d{6,}"), "c-s-N-00-00000000"),
    (re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"), "00000000-0000-0000-0000-000000000000"),
]


def _scrub(text: str, secrets: list[str]) -> str:
    for pattern, replacement in SCRUB:
        text = pattern.sub(replacement, text)
    for secret in secrets:
        text = text.replace(secret, "redacted")
    return text


def capture(runtime: Runtime, sources: list[str] | None = None) -> dict[str, int]:
    """Copy the data snapshots of each source's last ok/partial run to tests/fixtures/<source>/captured/."""
    session_file = runtime.root / "data" / "conad_session.txt"
    secrets = [part.split("=", 1)[1].strip('"') for part in session_file.read_text(encoding="utf-8").strip().split("; ")
               if "=" in part] if session_file.exists() else []
    copied = {}
    for source in sources or [s.value for s in ORDER]:
        with runtime.sessions() as session:
            run_id = session.scalar(select(ScrapeRun.id).where(
                ScrapeRun.source == source, ScrapeRun.status.in_([RunStatus.OK.value, RunStatus.PARTIAL.value]))
                .order_by(ScrapeRun.id.desc()).limit(1))
        if run_id is None:
            copied[source] = 0
            continue
        target = runtime.root / FIXTURES / source / "captured"
        shutil.rmtree(target, ignore_errors=True)
        target.mkdir(parents=True)
        snapshots = load_snapshots(runtime.raw_root, source, run_id)
        for snap in snapshots:
            (target / snap.path.name).write_text(_scrub(snap.text(), secrets), encoding="utf-8")
            meta = {"context": snap.context, "fetched_at": snap.fetched_at, "url": snap.url, "content_type": snap.content_type}
            (target / f"{snap.path.stem}.meta.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
        copied[source] = len(snapshots)
    return copied
