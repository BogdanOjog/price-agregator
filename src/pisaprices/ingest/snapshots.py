"""Raw responses on disk: data/raw/<source>/<run_id>/<seq>_<label>.<ext> plus a .meta.json.

A snapshot is written before it is parsed and never changes; `reparse` reads it back."""

import json
import re
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class RawSnapshot:
    source: str
    run_id: int
    seq: int
    label: str
    url: str
    status: int
    content_type: str | None
    fetched_at: str
    path: Path
    context: dict = field(default_factory=dict)   # collector data needed to parse, e.g. the category key
    phase: str = "data"                           # "context" (session/store checks) or "data" (parsed)

    def text(self) -> str:
        return self.path.read_text(encoding="utf-8")

    def json(self):
        return json.loads(self.text())


META_FIELDS = ("source", "run_id", "seq", "label", "url", "status", "content_type", "fetched_at", "context", "phase")


def run_dir(raw_root: Path, source: str, run_id: int) -> Path:
    return raw_root / source / str(run_id)


def save_snapshot(raw_root: Path, source: str, run_id: int, seq: int, label: str, url: str, status: int,
                  content_type: str | None, body: bytes, fetched_at: str, context: dict | None = None,
                  phase: str = "data") -> RawSnapshot:
    directory = run_dir(raw_root, source, run_id)
    directory.mkdir(parents=True, exist_ok=True)
    safe_label = re.sub(r"[^A-Za-z0-9_-]+", "_", label)[:60]
    extension = "json" if content_type and "json" in content_type else "html"
    path = directory / f"{seq:04d}_{safe_label}.{extension}"
    path.write_bytes(body)
    snapshot = RawSnapshot(source, run_id, seq, label, url, status, content_type, fetched_at, path, context or {}, phase)
    meta = {k: getattr(snapshot, k) for k in META_FIELDS} | {"file": path.name}
    path.with_name(path.stem + ".meta.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    return snapshot


def load_snapshots(raw_root: Path, source: str, run_id: int, phase: str | None = "data") -> list[RawSnapshot]:
    directory = run_dir(raw_root, source, run_id)
    if not directory.exists():
        raise FileNotFoundError(f"no snapshots for {source} run {run_id} in {directory}")
    snapshots = []
    for meta_path in sorted(directory.glob("*.meta.json")):
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        body = directory / meta.pop("file")
        snapshot = RawSnapshot(path=body, **meta)
        if phase is None or snapshot.phase == phase:
            snapshots.append(snapshot)
    return snapshots
