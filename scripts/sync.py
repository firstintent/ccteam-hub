#!/usr/bin/env python3
"""ccteam-hub ingestion pipeline.

Reads sources.json, clones each open-source source at a pinned sha, globs the
declared content files, copies them VERBATIM into the hub (agents/ skills/
workflows/), and rebuilds index.json.

Design constraints (see ccteam-hub/README.md):
  - stdlib only (subprocess/json/hashlib/pathlib/re/glob).
  - IDEMPOTENT: re-running with the same pinned sha produces a BYTE-IDENTICAL
    tree. No wall-clock timestamps; deterministic ordering everywhere.
  - Content is verbatim; only the on-disk filename (`id`) is sanitized to
    [a-z0-9_-]. Collisions across divisions get a `<division>-` prefix.
  - Attribution preserved: per-source LICENSE captured under LICENSES/.

Usage:  python3 scripts/sync.py
"""

from __future__ import annotations

import glob as globmod
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# Repo root = parent of this script's dir (scripts/).
ROOT = Path(__file__).resolve().parent.parent
SOURCES_FILE = ROOT / "sources.json"
INDEX_FILE = ROOT / "index.json"
LICENSES_DIR = ROOT / "LICENSES"

# type -> hub subdirectory.
TYPE_DIR = {"agent": "agents", "skill": "skills", "workflow": "workflows"}

# Cap stored description length (full text stays verbatim in the .md body).
DESC_MAX = 400

LICENSE_CANDIDATES = ("LICENSE", "LICENSE.md", "LICENSE.txt", "LICENSE.MIT", "COPYING")


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def run(args: list[str], cwd: Path | None = None) -> str:
    """Run a command, return stdout (text), raise on non-zero."""
    res = subprocess.run(
        args,
        cwd=str(cwd) if cwd else None,
        check=True,
        capture_output=True,
        text=True,
    )
    return res.stdout


def sanitize(stem: str) -> str:
    """Lowercase, map any run of non-[a-z0-9_-] to a single '-', trim '-'."""
    s = stem.lower()
    s = re.sub(r"[^a-z0-9_-]+", "-", s)
    s = re.sub(r"-{2,}", "-", s)
    return s.strip("-")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Return (frontmatter dict, body-after-frontmatter).

    Only top-level `key: value` lines in a leading `---`..`---` block are
    parsed (sufficient for name/description). Returns ({}, full text) when no
    frontmatter is present.
    """
    if not text.startswith("---"):
        return {}, text
    lines = text.splitlines()
    # First line is the opening '---'; find the closing one.
    end = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end = i
            break
    if end is None:
        return {}, text
    fm: dict[str, str] = {}
    for line in lines[1:end]:
        m = re.match(r"^([A-Za-z0-9_-]+):\s*(.*)$", line)
        if m:
            fm[m.group(1)] = m.group(2).strip()
    body = "\n".join(lines[end + 1 :])
    return fm, body


def derive_meta(text: str, stem: str) -> tuple[str, str]:
    """Derive (name, description) with fallbacks. Description truncated."""
    fm, body = parse_frontmatter(text)
    name = fm.get("name", "").strip() or stem
    desc = fm.get("description", "").strip()
    if not desc:
        for line in body.splitlines():
            if line.strip():
                desc = line.strip()
                break
    desc = re.sub(r"\s+", " ", desc).strip()
    if len(desc) > DESC_MAX:
        desc = desc[: DESC_MAX - 1].rstrip() + "…"  # ellipsis
    return name, desc


def find_license(repo_dir: Path) -> Path | None:
    for cand in LICENSE_CANDIDATES:
        p = repo_dir / cand
        if p.is_file():
            return p
    return None


# --------------------------------------------------------------------------- #
# core
# --------------------------------------------------------------------------- #
def clone_at_ref(repo: str, ref: str, dest: Path) -> str:
    """Full clone + checkout an arbitrary sha. Returns the source commit date
    (strict ISO-8601, %cI) for deterministic provenance stamping."""
    run(["git", "clone", "--quiet", repo, str(dest)])
    run(["git", "-C", str(dest), "checkout", "--quiet", ref])
    commit_date = run(["git", "-C", str(dest), "show", "-s", "--format=%cI", ref]).strip()
    return commit_date


def collect_entries(source: dict, repo_dir: Path):
    """Glob the source, resolve globally-unique ids, return (entries, collisions).

    A sanitized stem appearing in >1 directory is a collision: ALL of its
    instances get a `<division>-` prefix (deterministic — never arbitrary which
    keeps the bare stem). Single-occurrence stems keep the bare sanitized stem.
    """
    sname = source["name"]
    license_id = source.get("license", "")
    sha = source["ref"]
    upstream_base = source["repo"].rstrip("/")

    # Pass 1: gather (entry-type, division, sanitized-stem, rel-path) sorted.
    raw: list[tuple[str, str, str, str]] = []
    for m in source["map"]:
        etype = m["type"]
        if etype not in TYPE_DIR:
            raise SystemExit(f"unknown map type {etype!r} in source {sname}")
        for abspath in globmod.glob(str(repo_dir / m["glob"])):
            rel = Path(abspath).relative_to(repo_dir).as_posix()
            parts = rel.split("/")
            # division = first path segment under the repo (e.g. plugins/<div>/...)
            division = parts[1] if len(parts) > 1 else parts[0]
            stem = Path(rel).stem
            raw.append((etype, sanitize(division), sanitize(stem), rel))
    raw.sort(key=lambda r: r[3])  # sort by rel path -> deterministic order

    # Pass 2: which sanitized stems collide (per type, to be safe).
    counts: dict[tuple[str, str], int] = {}
    for etype, _div, san_stem, _rel in raw:
        counts[(etype, san_stem)] = counts.get((etype, san_stem), 0) + 1

    entries = []
    collisions = 0
    seen_ids: set[str] = set()
    for etype, san_div, san_stem, rel in raw:
        if counts[(etype, san_stem)] > 1:
            iid = f"{san_div}-{san_stem}"
            collisions += 1
        else:
            iid = san_stem
        if iid in seen_ids:
            # Defensive: should not happen given upstream layout; make unique.
            raise SystemExit(f"unexpected duplicate id {iid!r} (from {rel})")
        seen_ids.add(iid)

        src_path = repo_dir / rel
        text = src_path.read_text(encoding="utf-8")
        name, desc = derive_meta(text, san_stem)
        subdir = TYPE_DIR[etype]
        rel_out = f"{subdir}/{iid}.md"
        entries.append(
            {
                "_src_abs": src_path,
                "_rel_out": rel_out,
                "id": iid,
                "type": etype,
                "name": name,
                "description": desc,
                "path": rel_out,
                "content_sha": sha256_file(src_path),
                "source": sname,
                "upstream": f"{upstream_base}/blob/{sha}/{rel}",
                "license": license_id,
                "tags": [san_div],
            }
        )
    entries.sort(key=lambda e: e["id"])
    return entries, collisions


def clean_source_outputs(index: dict, source_name: str) -> None:
    """Remove previously-synced files for this source so removed-upstream
    files don't linger. Idempotent: deletes only paths recorded for the source
    in the existing index that still exist on disk."""
    for entry in index.get("plugins", []):
        if entry.get("source") == source_name:
            p = ROOT / entry["path"]
            if p.is_file():
                p.unlink()


def write_license(repo_dir: Path, source: dict, commit_date: str) -> None:
    LICENSES_DIR.mkdir(parents=True, exist_ok=True)
    lic = find_license(repo_dir)
    out = LICENSES_DIR / f"{source['name']}.LICENSE"
    header = (
        f"Vendored into ccteam-hub from {source['repo']}\n"
        f"ref: {source['ref']}\n"
        f"commit date: {commit_date}\n"
        f"declared license: {source.get('license', '')}\n"
        f"{'-' * 72}\n\n"
    )
    body = lic.read_text(encoding="utf-8") if lic else "(no LICENSE file found in source repo)\n"
    out.write_text(header + body, encoding="utf-8")


def main() -> int:
    if not SOURCES_FILE.is_file():
        raise SystemExit(f"missing {SOURCES_FILE}")
    sources_doc = json.loads(SOURCES_FILE.read_text(encoding="utf-8"))

    # Existing index (for cleanup of stale outputs). Preserve top-level meta.
    if INDEX_FILE.is_file():
        index = json.loads(INDEX_FILE.read_text(encoding="utf-8"))
    else:
        index = {"version": 1, "name": "ccteam-hub", "plugins": []}

    # Keep entries from sources NOT being re-synced this run; drop the rest as
    # we rebuild them below.
    synced_names = {s["name"] for s in sources_doc["sources"]}
    retained = [e for e in index.get("plugins", []) if e.get("source") not in synced_names]

    all_new_entries: list[dict] = []
    latest_commit_date = None
    total_collisions = 0

    for source in sources_doc["sources"]:
        with tempfile.TemporaryDirectory(prefix="ccteam-hub-sync-") as tmp:
            repo_dir = Path(tmp) / "repo"
            commit_date = clone_at_ref(source["repo"], source["ref"], repo_dir)
            if latest_commit_date is None or commit_date > latest_commit_date:
                latest_commit_date = commit_date

            entries, collisions = collect_entries(source, repo_dir)
            total_collisions += collisions

            # Clean previously-synced outputs for this source, then re-copy.
            clean_source_outputs(index, source["name"])
            for e in entries:
                dest = ROOT / e["_rel_out"]
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(e["_src_abs"], dest)

            write_license(repo_dir, source, commit_date)

            for e in entries:
                e.pop("_src_abs", None)
                e.pop("_rel_out", None)
            all_new_entries.extend(entries)
            print(
                f"[{source['name']}] {len(entries)} entries, "
                f"{collisions} collision-prefixed (ref {source['ref'][:12]})"
            )

    plugins = retained + all_new_entries
    plugins.sort(key=lambda e: e["id"])

    out_index = {
        "version": index.get("version", 1),
        "name": index.get("name", "ccteam-hub"),
    }
    if "description" in index:
        out_index["description"] = index["description"]
    # Deterministic provenance stamp derived from source commit date (NOT
    # wall-clock) so re-runs at the same sha are byte-identical.
    if latest_commit_date:
        out_index["generated_at"] = latest_commit_date
    out_index["plugins"] = plugins

    INDEX_FILE.write_text(
        json.dumps(out_index, indent=2, ensure_ascii=False, sort_keys=False) + "\n",
        encoding="utf-8",
    )
    print(
        f"wrote {INDEX_FILE.name}: {len(plugins)} plugins "
        f"({total_collisions} collision-prefixed total)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
