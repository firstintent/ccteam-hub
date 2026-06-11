#!/usr/bin/env python3
"""ccteam-hub ingestion — track-upstream model.

Rebuilds index.json as UPSTREAM POINTERS: each plugin stores `upstream` (a
raw-fetchable URL @sha) + `content_sha` read from the source — the body is
NOT copied into the hub. Multi-file skills carry a `manifest` of every file
(relpath + content_sha). First-party hub-local content (source="ccteam")
stays vendored in the hub and points `upstream` at the hub's own raw URL
(the hub IS the upstream for content it ships itself).

Inputs:
  - sources.json — external sources (repo @pinned-sha + glob map). Cloned,
    globbed, turned into pointers; bodies are NOT copied into the hub.
  - the hub's own agents/ skills/ workflows/ trees — first-party content.

Design constraints (see README):
  - stdlib only (subprocess/json/hashlib/pathlib/re/glob).
  - IDEMPOTENT: re-running at the same pinned sha is byte-identical (no
    wall-clock; deterministic ordering everywhere).
  - content_sha is computed over the SOURCE bytes; only `id` is sanitized to
    [a-z0-9_-] (collisions across divisions get a `<division>-` prefix).
  - Attribution preserved: per-source LICENSE captured under LICENSES/.

Usage:  python3 scripts/sync.py
"""

from __future__ import annotations

import glob as globmod
import hashlib
import json
import re
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path

# Repo root = parent of this script's dir (scripts/).
ROOT = Path(__file__).resolve().parent.parent
SOURCES_FILE = ROOT / "sources.json"
INDEX_FILE = ROOT / "index.json"
LICENSES_DIR = ROOT / "LICENSES"

# type -> hub subdirectory (for the first-party scan).
TYPE_DIR = {"agent": "agents", "skill": "skills", "workflow": "workflows"}

# Cap stored description length (full text stays in the upstream body).
DESC_MAX = 400

LICENSE_CANDIDATES = ("LICENSE", "LICENSE.md", "LICENSE.txt", "LICENSE.MIT", "COPYING")

# First-party content points `upstream` at the hub's own raw tree on `main`
# (the hub IS the upstream for content it vendors itself). Keep in sync with
# `ccteam_core::HUB_RAW_BASE`.
HUB_RAW_BASE = "https://raw.githubusercontent.com/firstintent/ccteam-hub/main"
FIRST_PARTY_SOURCE = "ccteam"
FIRST_PARTY_LICENSE = "MIT"


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def run(args: list[str], cwd: Path | None = None) -> str:
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


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Parse top-level `key: value` lines in a leading `---`..`---` block."""
    if not text.startswith("---"):
        return {}, text
    lines = text.splitlines()
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
    return fm, "\n".join(lines[end + 1 :])


def derive_meta(text: str, fallback_name: str) -> tuple[str, str, list[str]]:
    """Derive (name, description, frontmatter-tags). Description truncated."""
    fm, body = parse_frontmatter(text)
    name = fm.get("name", "").strip() or fallback_name
    desc = fm.get("description", "").strip()
    if not desc:
        for line in body.splitlines():
            if line.strip():
                desc = line.strip()
                break
    desc = re.sub(r"\s+", " ", desc).strip()
    if len(desc) > DESC_MAX:
        desc = desc[: DESC_MAX - 1].rstrip() + "…"
    tags_raw = fm.get("tags", "").strip()
    tags = [sanitize(t) for t in re.split(r"[,\s]+", tags_raw) if t.strip()] if tags_raw else []
    return name, desc, tags


def raw_url(repo: str, sha: str, relpath: str) -> str:
    """A github repo URL + sha + relpath -> a raw.githubusercontent.com URL."""
    owner_repo = re.sub(r"^https?://github\.com/", "", repo.rstrip("/"))
    owner_repo = re.sub(r"\.git$", "", owner_repo)
    return f"https://raw.githubusercontent.com/{owner_repo}/{sha}/{relpath}"


def find_license(repo_dir: Path) -> Path | None:
    for cand in LICENSE_CANDIDATES:
        p = repo_dir / cand
        if p.is_file():
            return p
    return None


def clone_at_ref(repo: str, ref: str, dest: Path) -> str:
    """Full clone + checkout an arbitrary sha. Returns the source commit date
    (strict ISO-8601, %cI) for deterministic provenance stamping."""
    run(["git", "clone", "--quiet", repo, str(dest)])
    run(["git", "-C", str(dest), "checkout", "--quiet", ref])
    return run(["git", "-C", str(dest), "show", "-s", "--format=%cI", ref]).strip()


def skill_manifest(skill_dir: Path) -> list[dict[str, str]]:
    """Every file under a skill dir (sorted posix relpaths) →
    [{relpath, content_sha}]. SKILL.md is included; len >= 1."""
    files = sorted(p for p in skill_dir.rglob("*") if p.is_file())
    return [
        {"relpath": p.relative_to(skill_dir).as_posix(), "content_sha": sha256_file(p)}
        for p in files
    ]


def id_and_division(etype: str, rel: str) -> tuple[str, str]:
    """(id-candidate, division). Skills key off the DIR name — fixes the
    `*/SKILL.md` stem='SKILL' dup-crash; agents key off the file stem.
    division (for the collision prefix + tag) = the first segment under the
    type root (e.g. plugins/<div>/… or skills/<cat>/…)."""
    parts = rel.split("/")
    p = Path(rel)
    if etype == "skill":
        cand = p.parent.name
    else:
        cand = p.stem
    division = parts[1] if len(parts) > 1 else parts[0]
    return cand, division


def make_entry(
    etype: str,
    iid: str,
    src_path: Path,
    upstream: str,
    source: str,
    license_id: str,
    tags: list[str],
) -> dict:
    """Build one pointer entry. For a multi-file skill, attach a `manifest`
    (every file under the skill dir); single-file gets no manifest."""
    text = src_path.read_text(encoding="utf-8")
    name, desc, fm_tags = derive_meta(text, iid)
    entry = {
        "id": iid,
        "type": etype,
        "name": name,
        "description": desc,
        "upstream": upstream,
        "content_sha": sha256_file(src_path),
        "source": source,
        "license": license_id,
        "tags": tags or fm_tags,
    }
    if etype == "skill":
        manifest = skill_manifest(src_path.parent)
        if len(manifest) > 1:
            # relpaths are relative to the skill dir; `upstream` points at
            # SKILL.md and the engine derives each file URL from its dirname.
            entry["manifest"] = manifest
    return entry


# --------------------------------------------------------------------------- #
# external sources
# --------------------------------------------------------------------------- #
def collect_external(source: dict, repo_dir: Path) -> list[dict]:
    """Provisional pointer entries (id = bare candidate; `_div` carried for the
    GLOBAL collision pass in main(), which is what makes ids unique across
    sources + types, not just within one source)."""
    sname = source["name"]
    license_id = source.get("license", "")
    sha = source["ref"]
    repo = source["repo"]

    rows: list[tuple[str, str, str, str]] = []
    for m in source["map"]:
        etype = m["type"]
        if etype not in TYPE_DIR:
            raise SystemExit(f"unknown map type {etype!r} in source {sname}")
        for abspath in globmod.glob(str(repo_dir / m["glob"]), recursive=True):
            rel = Path(abspath).relative_to(repo_dir).as_posix()
            cand, division = id_and_division(etype, rel)
            rows.append((etype, sanitize(division), sanitize(cand), rel))
    rows.sort(key=lambda r: r[3])

    entries: list[dict] = []
    for etype, san_div, cand, rel in rows:
        e = make_entry(
            etype,
            cand,
            repo_dir / rel,
            raw_url(repo, sha, rel),
            sname,
            license_id,
            [san_div] if san_div else [],
        )
        e["_div"] = san_div
        entries.append(e)
    return entries


# --------------------------------------------------------------------------- #
# first-party (hub-local)
# --------------------------------------------------------------------------- #
def collect_firstparty() -> list[dict]:
    """Scan the hub's own trees — content ccteam ships itself. `upstream`
    points at the hub's raw URL on `main`; tags come from frontmatter."""
    entries: list[dict] = []
    for abspath in sorted(globmod.glob(str(ROOT / "agents" / "*.md"))):
        p = Path(abspath)
        rel = p.relative_to(ROOT).as_posix()
        entries.append(
            make_entry(
                "agent",
                sanitize(p.stem),
                p,
                f"{HUB_RAW_BASE}/{rel}",
                FIRST_PARTY_SOURCE,
                FIRST_PARTY_LICENSE,
                [],
            )
        )
    for abspath in sorted(globmod.glob(str(ROOT / "skills" / "*" / "SKILL.md"))):
        p = Path(abspath)
        rel = p.relative_to(ROOT).as_posix()
        entries.append(
            make_entry(
                "skill",
                sanitize(p.parent.name),
                p,
                f"{HUB_RAW_BASE}/{rel}",
                FIRST_PARTY_SOURCE,
                FIRST_PARTY_LICENSE,
                [],
            )
        )
    # First-party has no division → the global collision pass falls back to the
    # source name as the prefix (first-party ids rarely collide).
    for e in entries:
        e["_div"] = ""
    entries.sort(key=lambda e: e["id"])
    return entries


def write_license(repo_dir: Path, source: dict, commit_date: str) -> None:
    LICENSES_DIR.mkdir(parents=True, exist_ok=True)
    lic = find_license(repo_dir)
    out = LICENSES_DIR / f"{source['name']}.LICENSE"
    header = (
        f"Tracked by ccteam-hub from {source['repo']}\n"
        f"ref: {source['ref']}\n"
        f"commit date: {commit_date}\n"
        f"declared license: {source.get('license', '')}\n"
        f"{'-' * 72}\n\n"
    )
    body = lic.read_text(encoding="utf-8") if lic else "(no LICENSE file found in source repo)\n"
    out.write_text(header + body, encoding="utf-8")


def drop_vendored_external_bodies(old_index: dict) -> int:
    """Track-upstream: the hub stores NO external bodies. Delete any body the
    previous (vendor-copy) index recorded a hub-local `path` for, unless it's
    first-party. Idempotent (only deletes files still on disk)."""
    removed = 0
    for e in old_index.get("plugins", []):
        if e.get("source") == FIRST_PARTY_SOURCE:
            continue
        rel = e.get("path")
        if rel:
            p = ROOT / rel
            if p.is_file():
                p.unlink()
                removed += 1
    return removed


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #
def main() -> int:
    if not SOURCES_FILE.is_file():
        raise SystemExit(f"missing {SOURCES_FILE}")
    sources_doc = json.loads(SOURCES_FILE.read_text(encoding="utf-8"))

    old_index = json.loads(INDEX_FILE.read_text(encoding="utf-8")) if INDEX_FILE.is_file() else {}
    removed = drop_vendored_external_bodies(old_index)

    all_entries: list[dict] = []
    latest_commit_date: str | None = None

    for source in sources_doc["sources"]:
        with tempfile.TemporaryDirectory(prefix="ccteam-hub-sync-") as tmp:
            repo_dir = Path(tmp) / "repo"
            commit_date = clone_at_ref(source["repo"], source["ref"], repo_dir)
            if latest_commit_date is None or commit_date > latest_commit_date:
                latest_commit_date = commit_date
            entries = collect_external(source, repo_dir)
            write_license(repo_dir, source, commit_date)
            all_entries.extend(entries)
            print(f"[{source['name']}] {len(entries)} pointer entries (ref {source['ref'][:12]})")

    firstparty = collect_firstparty()
    all_entries.extend(firstparty)
    print(f"[{FIRST_PARTY_SOURCE} first-party] {len(firstparty)} entries")

    # GLOBAL collision resolution: a bare id that appears more than once
    # anywhere (across sources AND types) gets every instance prefixed with its
    # division (or, for first-party, its source). This is what guarantees
    # `HubIndex::find(id)` is unambiguous.
    counts = Counter(e["id"] for e in all_entries)
    total_collisions = 0
    for e in all_entries:
        if counts[e["id"]] > 1:
            prefix = sanitize(e["_div"] or e["source"])
            e["id"] = f"{prefix}-{e['id']}" if prefix else e["id"]
            total_collisions += 1
    for e in all_entries:
        e.pop("_div", None)

    # Stable global ordering + a hard dup-id guard (catches a residual clash
    # that prefixing couldn't resolve).
    all_entries.sort(key=lambda e: (e["id"], e["source"]))
    seen: set[str] = set()
    for e in all_entries:
        if e["id"] in seen:
            raise SystemExit(f"unresolved duplicate id after prefixing: {e['id']!r}")
        seen.add(e["id"])

    out_index = {
        "version": old_index.get("version", 1),
        "name": old_index.get("name", "ccteam-hub"),
    }
    if "description" in old_index:
        out_index["description"] = old_index["description"]
    if latest_commit_date:
        out_index["generated_at"] = latest_commit_date
    out_index["plugins"] = all_entries

    INDEX_FILE.write_text(
        json.dumps(out_index, indent=2, ensure_ascii=False, sort_keys=False) + "\n",
        encoding="utf-8",
    )
    print(
        f"wrote {INDEX_FILE.name}: {len(all_entries)} plugins "
        f"({total_collisions} collision-prefixed, {removed} vendored bodies removed)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
