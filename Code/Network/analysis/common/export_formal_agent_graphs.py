#!/usr/bin/env python3
"""Export the exact frozen Agent graphs used by the formal 18-task suite.

This is intentionally separate from the Step 0.5 four-site graph export. It reads the
same same-state Agent snapshot referenced by the formal task suite, reconstructs the
rendered_all and rendered_aria_followable directed graphs, and hard-validates all
18 frozen task metrics (distance, shortest-path count, and first-hop count) before
writing a derived graph artifact for task-level matched-null analysis.
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict, deque
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

Edge = Tuple[str, str]

DEFAULT_SNAPSHOT_ROOT = Path("data/runs/agent_interface_aria_v1_2026-09-20")
DEFAULT_TASKS = Path("docs/agent_formal_task_suite_v1.json")
DEFAULT_OUTPUT = Path("data/derived/formal_agent_graphs_v1.json")
DEFAULT_VALIDATION = Path("results/analysis/network_null/formal_graph_validation.csv")
DEFAULT_ARIA_ONLY = Path("results/analysis/network_null/formal_aria_only_edges.csv")
FORMAL_SITES = ("mdn_js", "wikipedia", "usa_gov")


def _read_jsonl(path: Path) -> List[Dict[str, object]]:
    with path.open("r", encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def _load_tasks(path: Path) -> List[Dict[str, object]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, dict):
        raw = raw.get("tasks", raw.get("items", []))
    if not isinstance(raw, list):
        raise ValueError(f"Expected task list in {path}")
    if len(raw) != 18:
        raise ValueError(f"Expected 18 formal tasks in {path}; got {len(raw)}")
    return [dict(item) for item in raw]


def _alias_map(page_rows: Sequence[Mapping[str, object]], alias_rows: Sequence[Mapping[str, object]]) -> Dict[str, str]:
    aliases: Dict[str, str] = {}
    for page in page_rows:
        primary = page.get("normalized_final_url")
        if not isinstance(primary, str) or not primary:
            continue
        for key in ("requested_url", "final_url", "normalized_url", "normalized_final_url"):
            value = page.get(key)
            if isinstance(value, str) and value:
                aliases[value] = primary
    for row in alias_rows:
        requested = row.get("requested_url")
        primary = row.get("normalized_final_url")
        if isinstance(requested, str) and requested and isinstance(primary, str) and primary:
            aliases[requested] = primary
    return aliases


def _resolve_primary(value: object, aliases: Mapping[str, str], nodes: Set[str]) -> Optional[str]:
    if not isinstance(value, str) or not value:
        return None
    if value in aliases:
        resolved = aliases[value]
        return resolved if resolved in nodes else None
    return value if value in nodes else None


def _site_graphs(snapshot_root: Path, site: str) -> Dict[str, object]:
    site_dir = snapshot_root / site
    pages = _read_jsonl(site_dir / "pages.jsonl")
    links = _read_jsonl(site_dir / "links.jsonl")
    aliases_path = site_dir / "url_aliases.jsonl"
    alias_rows = _read_jsonl(aliases_path) if aliases_path.exists() else []

    nodes = {
        str(page["normalized_final_url"])
        for page in pages
        if isinstance(page.get("normalized_final_url"), str) and page.get("normalized_final_url")
    }
    aliases = _alias_map(pages, alias_rows)
    edges: Dict[str, Set[Edge]] = defaultdict(set)

    for row in links:
        rep = row.get("representation")
        if rep == "rendered_all_anchors" or rep == "rendered_all":
            canonical = "rendered_all"
        elif rep == "rendered_aria_followable":
            canonical = "rendered_aria_followable"
        else:
            continue
        if row.get("boundary") or not row.get("target_normalized_url"):
            continue
        source = _resolve_primary(row.get("source_normalized_final_url") or row.get("source_url"), aliases, nodes)
        target = (
            _resolve_primary(row.get("target_requested_url"), aliases, nodes)
            or _resolve_primary(row.get("target_normalized_url"), aliases, nodes)
        )
        if source and target and source != target:
            edges[canonical].add((source, target))

    all_edges = set(edges["rendered_all"])
    aria_full = set(edges["rendered_aria_followable"])
    retained = aria_full & all_edges
    aria_only = aria_full - all_edges
    return {
        "site": site,
        "nodes": sorted(nodes),
        "rendered_all_edges": [list(edge) for edge in sorted(all_edges)],
        "rendered_aria_full_edges": [list(edge) for edge in sorted(aria_full)],
        "rendered_aria_retained_edges": [list(edge) for edge in sorted(retained)],
        "aria_only_edges": [list(edge) for edge in sorted(aria_only)],
        "counts": {
            "nodes": len(nodes),
            "rendered_all_edges": len(all_edges),
            "rendered_aria_full_edges": len(aria_full),
            "rendered_aria_retained_edges": len(retained),
            "aria_only_edges": len(aria_only),
        },
    }


def _shortest_stats(nodes: Iterable[str], edges: Iterable[Sequence[str]], source: str, target: str) -> Tuple[Optional[int], Optional[int], Optional[int]]:
    node_set = set(nodes)
    if source not in node_set or target not in node_set:
        return None, None, None
    adjacency: Dict[str, Set[str]] = defaultdict(set)
    for u, v in edges:
        if u in node_set and v in node_set and u != v:
            adjacency[u].add(v)
    distance: Dict[str, int] = {source: 0}
    path_count: Dict[str, int] = {source: 1}
    first_hops: Dict[str, Set[str]] = {source: set()}
    queue: deque[str] = deque([source])
    while queue:
        current = queue.popleft()
        nd = distance[current] + 1
        for neighbor in sorted(adjacency.get(current, ())):
            candidate_hops = {neighbor} if current == source else first_hops[current]
            if neighbor not in distance:
                distance[neighbor] = nd
                path_count[neighbor] = path_count[current]
                first_hops[neighbor] = set(candidate_hops)
                queue.append(neighbor)
            elif distance[neighbor] == nd:
                path_count[neighbor] += path_count[current]
                first_hops[neighbor].update(candidate_hops)
    if target not in distance:
        return None, None, None
    return distance[target], path_count[target], len(first_hops[target])


def _write_csv(path: Path, rows: Sequence[Mapping[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields: List[str] = []
    seen = set()
    for row in rows:
        for key in row:
            if key not in seen:
                seen.add(key)
                fields.append(key)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def export_formal_graphs(
    snapshot_root: Path = DEFAULT_SNAPSHOT_ROOT,
    task_file: Path = DEFAULT_TASKS,
    output: Path = DEFAULT_OUTPUT,
    validation_output: Path = DEFAULT_VALIDATION,
    aria_only_output: Path = DEFAULT_ARIA_ONLY,
) -> Dict[str, object]:
    tasks = _load_tasks(task_file)
    task_sites = {str(task["site"]) for task in tasks}
    if task_sites != set(FORMAL_SITES):
        raise ValueError(f"Unexpected formal sites: {sorted(task_sites)}")

    sites = {site: _site_graphs(snapshot_root, site) for site in FORMAL_SITES}
    validation_rows: List[Dict[str, object]] = []
    aria_only_rows: List[Dict[str, object]] = []
    for site, graph in sites.items():
        for source, target in graph["aria_only_edges"]:
            aria_only_rows.append({"site_id": site, "source_url": source, "target_url": target})

    for task in tasks:
        graph = sites[str(task["site"])]
        all_stats = _shortest_stats(graph["nodes"], graph["rendered_all_edges"], str(task["start_url"]), str(task["target_url"]))
        aria_stats = _shortest_stats(graph["nodes"], graph["rendered_aria_full_edges"], str(task["start_url"]), str(task["target_url"]))
        retained_stats = _shortest_stats(graph["nodes"], graph["rendered_aria_retained_edges"], str(task["start_url"]), str(task["target_url"]))
        expected_all = (
            task.get("d_all"),
            task.get("shortest_path_count_rendered_all"),
            task.get("first_hop_count_rendered_all"),
        )
        expected_aria = (
            task.get("d_aria"),
            task.get("shortest_path_count_rendered_aria"),
            task.get("first_hop_count_rendered_aria"),
        )
        passed = all_stats == expected_all and aria_stats == expected_aria
        validation_rows.append({
            "task_id": task["task_id"],
            "site": task["site"],
            "expected_d_all": expected_all[0],
            "computed_d_all": all_stats[0],
            "expected_d_aria": expected_aria[0],
            "computed_d_aria": aria_stats[0],
            "expected_sp_all": expected_all[1],
            "computed_sp_all": all_stats[1],
            "expected_sp_aria": expected_aria[1],
            "computed_sp_aria": aria_stats[1],
            "expected_fh_all": expected_all[2],
            "computed_fh_all": all_stats[2],
            "expected_fh_aria": expected_aria[2],
            "computed_fh_aria": aria_stats[2],
            "retained_d_aria": retained_stats[0],
            "retained_sp_aria": retained_stats[1],
            "retained_fh_aria": retained_stats[2],
            "retained_matches_full_aria": retained_stats == aria_stats,
            "validation_pass": passed,
        })

    pass_count = sum(bool(row["validation_pass"]) for row in validation_rows)
    if pass_count != len(tasks):
        failed = [str(row["task_id"]) for row in validation_rows if not row["validation_pass"]]
        raise RuntimeError(f"Formal graph validation failed: {pass_count}/{len(tasks)} PASS; failed={failed}")

    payload = {
        "schema_version": "1.0",
        "source": "formal_agent_frozen_snapshots",
        "snapshot_root": str(snapshot_root),
        "task_file": str(task_file),
        "representations": {
            "baseline": "rendered_all",
            "observed_full": "rendered_aria_followable",
            "observed_deletion_component": "rendered_aria_followable ∩ rendered_all",
        },
        "sites": sites,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    _write_csv(validation_output, validation_rows)
    _write_csv(aria_only_output, aria_only_rows)

    return {
        "task_count": len(tasks),
        "pass_count": pass_count,
        "aria_only_edge_count": len(aria_only_rows),
        "retained_task_difference_count": sum(not bool(row["retained_matches_full_aria"]) for row in validation_rows),
        "output": str(output),
        "validation_output": str(validation_output),
        "aria_only_output": str(aria_only_output),
    }


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--snapshot-root", default=str(DEFAULT_SNAPSHOT_ROOT))
    p.add_argument("--tasks", default=str(DEFAULT_TASKS))
    p.add_argument("--output", default=str(DEFAULT_OUTPUT))
    p.add_argument("--validation-output", default=str(DEFAULT_VALIDATION))
    p.add_argument("--aria-only-output", default=str(DEFAULT_ARIA_ONLY))
    return p


def main() -> None:
    args = build_parser().parse_args()
    result = export_formal_graphs(
        Path(args.snapshot_root), Path(args.tasks), Path(args.output),
        Path(args.validation_output), Path(args.aria_only_output),
    )
    print(f"Formal graph validation: {result['pass_count']}/{result['task_count']} PASS")
    print(f"ARIA-only internal edges: {result['aria_only_edge_count']}")
    print(f"Formal tasks changed by deletion projection: {result['retained_task_difference_count']}/{result['task_count']}")
    print(f"Graph export: {result['output']}")


if __name__ == "__main__":
    main()
