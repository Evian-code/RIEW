#!/usr/bin/env python3
"""Matched random-deletion null analysis for Representation-Induced Effective Web.

This script compares the observed rendered_aria_followable graph with two deletion nulls
constructed from rendered_all:

1. global_edge_count: retain the same total number of directed edges as ARIA.
2. nodewise_outdegree: for every captured source page, retain exactly the same number
   of outgoing edges as ARIA, but choose their destinations uniformly from rendered_all.

The primary graph universe is the set of successfully captured source pages for each
site. Only directed edges whose source and target are both in that captured-page
universe are used for the navigability analysis. This avoids treating uncrawled leaf
URLs as fully observed navigation states.

No third-party Python packages are required.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import random
import statistics
from collections import defaultdict, deque
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, MutableMapping, Optional, Sequence, Set, Tuple

Edge = Tuple[str, str]

DEFAULT_INPUT = Path(
    "results/step0_5_interface_validation_v2_four_site/combined_per_page_link_records.jsonl"
)
DEFAULT_TASKS = Path("docs/agent_formal_task_suite_v1.json")
DEFAULT_FORMAL_GRAPHS = Path("data/derived/formal_agent_graphs_v1.json")
DEFAULT_OUTPUT = Path("results/analysis/network_null")

PRIMARY_SITES = ("mdn_js", "wikipedia", "usa_gov", "hacker_news")
ALL_REP = "rendered_all"
ARIA_REP = "rendered_aria_followable"


@dataclass(frozen=True)
class Graph:
    nodes: Tuple[str, ...]
    edges: frozenset[Edge]
    adjacency: Mapping[str, Tuple[str, ...]]


def _make_graph(nodes: Iterable[str], edges: Iterable[Edge]) -> Graph:
    node_tuple = tuple(sorted(set(nodes)))
    node_set = set(node_tuple)
    edge_set = frozenset((u, v) for u, v in edges if u in node_set and v in node_set and u != v)
    adj: Dict[str, List[str]] = {u: [] for u in node_tuple}
    for u, v in sorted(edge_set):
        adj[u].append(v)
    return Graph(node_tuple, edge_set, {u: tuple(vs) for u, vs in adj.items()})


def load_graphs(path: Path, sites: Sequence[str], subset_policy: str = "intersect") -> Tuple[Dict[str, Dict[str, Graph]], List[Dict[str, str]]]:
    site_filter = set(sites)
    source_nodes: Dict[str, Set[str]] = defaultdict(set)
    edges: Dict[Tuple[str, str], Set[Edge]] = defaultdict(set)

    with path.open("r", encoding="utf-8") as handle:
        for line_no, raw in enumerate(handle, 1):
            raw = raw.strip()
            if not raw:
                continue
            row = json.loads(raw)
            site = row.get("site_id")
            if site not in site_filter:
                continue
            rep = row.get("representation")
            if rep not in {ALL_REP, ARIA_REP}:
                continue
            if row.get("graph_edge") is False:
                continue
            source = row.get("source_url") or row.get("source")
            target = row.get("absolute_url")
            if not source or not target:
                raise ValueError(f"Missing source/target at {path}:{line_no}")
            source_nodes[site].add(source)
            edges[(site, rep)].add((source, target))

    out: Dict[str, Dict[str, Graph]] = {}
    aria_only_rows: List[Dict[str, str]] = []
    for site in sites:
        nodes = source_nodes.get(site, set())
        if not nodes:
            raise ValueError(f"No captured source pages found for site={site}")
        all_graph = _make_graph(nodes, edges.get((site, ALL_REP), set()))
        aria_graph = _make_graph(nodes, edges.get((site, ARIA_REP), set()))
        extra = aria_graph.edges - all_graph.edges
        if extra:
            for u, v in sorted(extra):
                aria_only_rows.append({"site_id": site, "source_url": u, "target_url": v})
            if subset_policy == "error":
                sample = list(sorted(extra))[:5]
                raise ValueError(
                    f"ARIA is not a deletion-only subgraph for {site}: "
                    f"{len(extra)} ARIA edges absent from rendered_all; examples={sample}"
                )
            if subset_policy != "intersect":
                raise ValueError(f"Unknown ARIA subset policy: {subset_policy}")
            # Matched-deletion nulls can only randomize deletion from rendered_all.
            # Project the observed ARIA graph onto rendered_all and report the excluded
            # ARIA-only edges explicitly instead of silently treating them as deletions.
            aria_graph = _make_graph(nodes, aria_graph.edges & all_graph.edges)
        out[site] = {ALL_REP: all_graph, ARIA_REP: aria_graph}
    return out, aria_only_rows


def _load_formal_task_definitions(path: Path) -> List[Dict[str, object]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, dict):
        raw = raw.get("tasks", raw.get("items", []))
    if not isinstance(raw, list):
        raise ValueError(f"Expected task list in {path}")
    return [dict(item) for item in raw]


def load_formal_graphs(path: Path) -> Tuple[Dict[str, Dict[str, Graph]], List[Dict[str, str]]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if raw.get("source") != "formal_agent_frozen_snapshots":
        raise ValueError(f"Not a formal Agent graph export: {path}")
    site_payload = raw.get("sites", {})
    out: Dict[str, Dict[str, Graph]] = {}
    aria_only_rows: List[Dict[str, str]] = []
    for site, payload in site_payload.items():
        nodes = payload.get("nodes", [])
        all_graph = _make_graph(nodes, payload.get("rendered_all_edges", []))
        aria_full = _make_graph(nodes, payload.get("rendered_aria_full_edges", []))
        aria_retained = _make_graph(nodes, payload.get("rendered_aria_retained_edges", []))
        for edge in payload.get("aria_only_edges", []):
            if len(edge) == 2:
                aria_only_rows.append({"site_id": site, "source_url": edge[0], "target_url": edge[1]})
        out[site] = {ALL_REP: all_graph, ARIA_REP: aria_full, "aria_deletion_component": aria_retained}
    return out, aria_only_rows


def validate_formal_task_graphs(
    graphs: Mapping[str, Mapping[str, Graph]],
    task_definitions: Sequence[Mapping[str, object]],
) -> List[Dict[str, object]]:
    task_rows = [
        {
            "site_id": str(t["site"]),
            "task_id": str(t["task_id"]),
            "start_url": str(t["start_url"]),
            "target_url": str(t["target_url"]),
        }
        for t in task_definitions
    ]
    observed: Dict[Tuple[str, str, str], Mapping[str, object]] = {}
    retained: Dict[Tuple[str, str], Mapping[str, object]] = {}
    for site in sorted({row["site_id"] for row in task_rows}):
        if site not in graphs:
            raise ValueError(f"Formal graph export is missing site={site}")
        site_tasks = [row for row in task_rows if row["site_id"] == site]
        for condition in (ALL_REP, ARIA_REP):
            for row in task_metrics(graphs[site][condition], site_tasks):
                observed[(site, str(row["task_id"]), condition)] = row
        for row in task_metrics(graphs[site]["aria_deletion_component"], site_tasks):
            retained[(site, str(row["task_id"]))] = row

    rows: List[Dict[str, object]] = []
    for task in task_definitions:
        site, task_id = str(task["site"]), str(task["task_id"])
        all_row = observed[(site, task_id, ALL_REP)]
        aria_row = observed[(site, task_id, ARIA_REP)]
        retained_row = retained[(site, task_id)]
        expected_all = (task.get("d_all"), task.get("shortest_path_count_rendered_all"), task.get("first_hop_count_rendered_all"))
        expected_aria = (task.get("d_aria"), task.get("shortest_path_count_rendered_aria"), task.get("first_hop_count_rendered_aria"))
        computed_all = (all_row.get("distance"), all_row.get("shortest_path_count"), all_row.get("first_hop_count"))
        computed_aria = (aria_row.get("distance"), aria_row.get("shortest_path_count"), aria_row.get("first_hop_count"))
        retained_stats = (retained_row.get("distance"), retained_row.get("shortest_path_count"), retained_row.get("first_hop_count"))
        rows.append({
            "task_id": task_id, "site": site,
            "expected_d_all": expected_all[0], "computed_d_all": computed_all[0],
            "expected_d_aria": expected_aria[0], "computed_d_aria": computed_aria[0],
            "expected_sp_all": expected_all[1], "computed_sp_all": computed_all[1],
            "expected_sp_aria": expected_aria[1], "computed_sp_aria": computed_aria[1],
            "expected_fh_all": expected_all[2], "computed_fh_all": computed_all[2],
            "expected_fh_aria": expected_aria[2], "computed_fh_aria": computed_aria[2],
            "retained_d_aria": retained_stats[0], "retained_sp_aria": retained_stats[1],
            "retained_fh_aria": retained_stats[2],
            "retained_matches_full_aria": retained_stats == computed_aria,
            "validation_pass": computed_all == expected_all and computed_aria == expected_aria,
        })
    failures = [row["task_id"] for row in rows if not row["validation_pass"]]
    if failures:
        raise ValueError(f"Formal graph validation failed for {len(failures)} tasks: {failures}")
    return rows


def bfs_shortest_stats(graph: Graph, source: str) -> Tuple[Dict[str, int], Dict[str, int], Dict[str, int]]:
    """Return shortest distance, number of shortest paths, first-hop diversity."""
    dist: Dict[str, int] = {source: 0}
    sp_count: Dict[str, int] = {source: 1}
    first_hops: Dict[str, Set[str]] = {source: set()}
    q: deque[str] = deque([source])

    while q:
        u = q.popleft()
        du = dist[u]
        for v in graph.adjacency.get(u, ()):  # directed
            nd = du + 1
            if v not in dist:
                dist[v] = nd
                sp_count[v] = sp_count[u]
                first_hops[v] = {v} if u == source else set(first_hops[u])
                q.append(v)
            elif dist[v] == nd:
                sp_count[v] += sp_count[u]
                if u == source:
                    first_hops[v].add(v)
                else:
                    first_hops[v].update(first_hops[u])

    fh_count = {node: len(hops) for node, hops in first_hops.items()}
    return dist, sp_count, fh_count


def all_pairs_shortest_stats(graph: Graph) -> Dict[str, Tuple[Dict[str, int], Dict[str, int], Dict[str, int]]]:
    return {source: bfs_shortest_stats(graph, source) for source in graph.nodes}


def graph_metrics(candidate: Graph, baseline: Graph, baseline_stats=None) -> Dict[str, float]:
    if candidate.nodes != baseline.nodes:
        raise ValueError("Candidate and baseline node universes differ")
    if baseline_stats is None:
        baseline_stats = all_pairs_shortest_stats(baseline)
    cand_stats = all_pairs_shortest_stats(candidate)

    total_pairs = len(baseline.nodes) * (len(baseline.nodes) - 1)
    baseline_reachable = 0
    candidate_reachable = 0
    preserved_reachable = 0
    unreachable_from_baseline = 0
    stretches: List[float] = []
    delta_d: List[float] = []
    sp_retention_equal_d: List[float] = []
    fh_retention_equal_d: List[float] = []
    sp_reduced_equal_d = 0
    fh_reduced_equal_d = 0
    equal_d_pairs = 0

    for source in baseline.nodes:
        base_dist, base_sp, base_fh = baseline_stats[source]
        cand_dist, cand_sp, cand_fh = cand_stats[source]
        for target in baseline.nodes:
            if target == source:
                continue
            bd = base_dist.get(target)
            cd = cand_dist.get(target)
            if bd is not None:
                baseline_reachable += 1
            if cd is not None:
                candidate_reachable += 1
            if bd is None:
                continue
            if cd is None:
                unreachable_from_baseline += 1
                continue
            preserved_reachable += 1
            stretches.append(cd / bd)
            delta_d.append(cd - bd)
            if cd == bd:
                equal_d_pairs += 1
                bsp = max(1, base_sp.get(target, 1))
                csp = cand_sp.get(target, 0)
                bfh = max(1, base_fh.get(target, 1))
                cfh = cand_fh.get(target, 0)
                sp_retention_equal_d.append(min(1.0, csp / bsp))
                fh_retention_equal_d.append(min(1.0, cfh / bfh))
                sp_reduced_equal_d += int(csp < bsp)
                fh_reduced_equal_d += int(cfh < bfh)

    def safe_mean(values: Sequence[float]) -> float:
        return statistics.fmean(values) if values else float("nan")

    def quantile(values: Sequence[float], q: float) -> float:
        if not values:
            return float("nan")
        xs = sorted(values)
        if len(xs) == 1:
            return xs[0]
        pos = (len(xs) - 1) * q
        lo = math.floor(pos)
        hi = math.ceil(pos)
        if lo == hi:
            return xs[lo]
        w = pos - lo
        return xs[lo] * (1 - w) + xs[hi] * w

    return {
        "node_count": float(len(baseline.nodes)),
        "edge_count": float(len(candidate.edges)),
        "edge_retention_ratio": len(candidate.edges) / len(baseline.edges) if baseline.edges else 1.0,
        "baseline_reachable_pairs": float(baseline_reachable),
        "candidate_reachable_pairs": float(candidate_reachable),
        "reachability_fraction": candidate_reachable / total_pairs if total_pairs else 1.0,
        "reachability_preservation": preserved_reachable / baseline_reachable if baseline_reachable else 1.0,
        "reachability_loss": unreachable_from_baseline / baseline_reachable if baseline_reachable else 0.0,
        "mean_path_stretch": safe_mean(stretches),
        "median_path_stretch": quantile(stretches, 0.5),
        "p90_path_stretch": quantile(stretches, 0.9),
        "mean_delta_d": safe_mean(delta_d),
        "fraction_delta_d_gt0": safe_mean([1.0 if x > 0 else 0.0 for x in delta_d]),
        "fraction_delta_d_ge2": safe_mean([1.0 if x >= 2 else 0.0 for x in delta_d]),
        "equal_distance_pair_count": float(equal_d_pairs),
        "mean_sp_retention_equal_d": safe_mean(sp_retention_equal_d),
        "fraction_sp_reduced_equal_d": sp_reduced_equal_d / equal_d_pairs if equal_d_pairs else float("nan"),
        "mean_fh_retention_equal_d": safe_mean(fh_retention_equal_d),
        "fraction_fh_reduced_equal_d": fh_reduced_equal_d / equal_d_pairs if equal_d_pairs else float("nan"),
    }


def global_edge_count_null(all_graph: Graph, aria_graph: Graph, rng: random.Random) -> Graph:
    edges = list(all_graph.edges)
    keep = len(aria_graph.edges)
    if keep > len(edges):
        raise ValueError("ARIA edge count exceeds rendered_all edge count")
    if keep == len(edges):
        sampled = edges
    else:
        sampled = rng.sample(edges, keep)
    return _make_graph(all_graph.nodes, sampled)


def nodewise_outdegree_null(all_graph: Graph, aria_graph: Graph, rng: random.Random) -> Graph:
    sampled: List[Edge] = []
    aria_degree = {u: len(aria_graph.adjacency.get(u, ())) for u in all_graph.nodes}
    for source in all_graph.nodes:
        available = list(all_graph.adjacency.get(source, ()))
        keep = aria_degree[source]
        if keep > len(available):
            raise ValueError(
                f"ARIA out-degree exceeds rendered_all for source={source}: {keep}>{len(available)}"
            )
        chosen = available if keep == len(available) else rng.sample(available, keep)
        sampled.extend((source, target) for target in chosen)
    return _make_graph(all_graph.nodes, sampled)


def _parse_task_rows(path: Optional[Path], sites: Sequence[str]) -> List[Dict[str, str]]:
    if path is None or not path.exists():
        return []
    rows: List[Dict[str, str]] = []
    if path.suffix.lower() == ".json":
        raw = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            raw = raw.get("tasks", raw.get("items", []))
        for item in raw:
            rows.append({str(k): "" if v is None else str(v) for k, v in item.items()})
    else:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            rows = [dict(row) for row in csv.DictReader(handle)]

    # Existing combined task-path file has one row per representation; keep one task row.
    dedup: Dict[Tuple[str, str], Dict[str, str]] = {}
    for row in rows:
        site = row.get("site_id") or row.get("site")
        task_id = row.get("task_id") or row.get("id")
        start = row.get("start_url") or row.get("start")
        target = row.get("target_url") or row.get("target")
        rep = row.get("representation")
        if not site or site not in sites or not task_id or not start or not target:
            continue
        if rep and rep != ALL_REP:
            continue
        dedup[(site, task_id)] = {
            "site_id": site,
            "task_id": task_id,
            "start_url": start,
            "target_url": target,
        }
    return [dedup[key] for key in sorted(dedup)]


def task_metrics(graph: Graph, tasks: Sequence[Mapping[str, str]]) -> List[Dict[str, object]]:
    stats_cache: Dict[str, Tuple[Dict[str, int], Dict[str, int], Dict[str, int]]] = {}
    node_set = set(graph.nodes)
    rows: List[Dict[str, object]] = []
    for task in tasks:
        start, target = task["start_url"], task["target_url"]
        if start not in node_set or target not in node_set:
            rows.append({
                **task,
                "start_in_graph": start in node_set,
                "target_in_graph": target in node_set,
                "reachable": False,
                "distance": None,
                "shortest_path_count": None,
                "first_hop_count": None,
            })
            continue
        if start not in stats_cache:
            stats_cache[start] = bfs_shortest_stats(graph, start)
        dist, sp, fh = stats_cache[start]
        d = dist.get(target)
        rows.append({
            **task,
            "start_in_graph": True,
            "target_in_graph": True,
            "reachable": d is not None,
            "distance": d,
            "shortest_path_count": sp.get(target) if d is not None else None,
            "first_hop_count": fh.get(target) if d is not None else None,
        })
    return rows


def _write_csv(path: Path, rows: Sequence[Mapping[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fieldnames: List[str] = []
    seen: Set[str] = set()
    for row in rows:
        for key in row.keys():
            if key not in seen:
                seen.add(key)
                fieldnames.append(key)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _finite(values: Iterable[object]) -> List[float]:
    out = []
    for value in values:
        if isinstance(value, (int, float)) and math.isfinite(float(value)):
            out.append(float(value))
    return out


def _quantile(values: Sequence[float], q: float) -> float:
    if not values:
        return float("nan")
    xs = sorted(values)
    if len(xs) == 1:
        return xs[0]
    pos = (len(xs) - 1) * q
    lo, hi = math.floor(pos), math.ceil(pos)
    if lo == hi:
        return xs[lo]
    w = pos - lo
    return xs[lo] * (1 - w) + xs[hi] * w


WORSE_HIGH = {
    "reachability_loss",
    "mean_path_stretch",
    "median_path_stretch",
    "p90_path_stretch",
    "mean_delta_d",
    "fraction_delta_d_gt0",
    "fraction_delta_d_ge2",
    "fraction_sp_reduced_equal_d",
    "fraction_fh_reduced_equal_d",
}
WORSE_LOW = {
    "reachability_preservation",
    "mean_sp_retention_equal_d",
    "mean_fh_retention_equal_d",
}


def compare_observed_to_null(
    observed_rows: Sequence[Mapping[str, object]],
    null_rows: Sequence[Mapping[str, object]],
    null_name: str,
) -> List[Dict[str, object]]:
    observed_by_site = {str(row["site_id"]): row for row in observed_rows}
    null_by_site: Dict[str, List[Mapping[str, object]]] = defaultdict(list)
    for row in null_rows:
        null_by_site[str(row["site_id"])].append(row)

    metrics = sorted(WORSE_HIGH | WORSE_LOW)
    out: List[Dict[str, object]] = []
    for site, observed in observed_by_site.items():
        draws = null_by_site.get(site, [])
        for metric in metrics:
            obs = observed.get(metric)
            if not isinstance(obs, (int, float)) or not math.isfinite(float(obs)):
                continue
            vals = _finite(draw.get(metric) for draw in draws)
            if not vals:
                continue
            mean = statistics.fmean(vals)
            sd = statistics.stdev(vals) if len(vals) > 1 else 0.0
            if metric in WORSE_HIGH:
                extreme = sum(v >= float(obs) for v in vals)
                direction = "higher_is_more_distorted"
            else:
                extreme = sum(v <= float(obs) for v in vals)
                direction = "lower_is_more_distorted"
            empirical_p = (extreme + 1) / (len(vals) + 1)
            percentile = sum(v <= float(obs) for v in vals) / len(vals)
            z = (float(obs) - mean) / sd if sd > 0 else 0.0 if float(obs) == mean else math.copysign(float("inf"), float(obs) - mean)
            out.append({
                "site_id": site,
                "null_model": null_name,
                "metric": metric,
                "distortion_direction": direction,
                "observed": obs,
                "null_mean": mean,
                "null_sd": sd,
                "null_q025": _quantile(vals, 0.025),
                "null_q05": _quantile(vals, 0.05),
                "null_median": _quantile(vals, 0.50),
                "null_q95": _quantile(vals, 0.95),
                "null_q975": _quantile(vals, 0.975),
                "observed_percentile": percentile,
                "one_sided_empirical_p": empirical_p,
                "z_score": z,
                "n_draws": len(vals),
            })
    return out


def summarize_edge_survival(all_graph: Graph, aria_graph: Graph, site: str) -> List[Dict[str, object]]:
    rows = []
    for source in all_graph.nodes:
        all_out = set(all_graph.adjacency.get(source, ()))
        aria_out = set(aria_graph.adjacency.get(source, ()))
        removed = all_out - aria_out
        rows.append({
            "site_id": site,
            "source_url": source,
            "all_out_degree": len(all_out),
            "aria_out_degree": len(aria_out),
            "removed_out_edges": len(removed),
            "out_degree_retention": len(aria_out) / len(all_out) if all_out else 1.0,
        })
    return rows


def _task_null_summary(observed_task_rows, null_task_rows, null_name):
    """Compare each frozen formal task with its matched-null distribution.

    Distance is the primary task-level statistic. Redundancy summaries are conditional
    on the null draw preserving the rendered_all shortest distance, mirroring the
    site-level definition used by graph_metrics().
    """
    obs = {(r["site_id"], r["task_id"], r["condition"]): r for r in observed_task_rows}
    draws = defaultdict(list)
    for r in null_task_rows:
        draws[(r["site_id"], r["task_id"])].append(r)

    out = []
    for (site, task_id), rs in sorted(draws.items()):
        all_row = obs.get((site, task_id, ALL_REP))
        aria_row = obs.get((site, task_id, ARIA_REP))
        if not all_row or not aria_row:
            continue

        all_d = all_row.get("distance")
        aria_d = aria_row.get("distance")
        all_sp = all_row.get("shortest_path_count")
        aria_sp = aria_row.get("shortest_path_count")
        all_fh = all_row.get("first_hop_count")
        aria_fh = aria_row.get("first_hop_count")

        null_distances = [r.get("distance") for r in rs]
        null_reach = [1 if d is not None else 0 for d in null_distances]
        finite_d = [float(d) for d in null_distances if isinstance(d, int)]

        aria_reachable = aria_d is not None
        if aria_reachable:
            # More distance / unreachable is more distorted. Treat unreachable null draws as +inf.
            extreme = sum((d is None) or (d >= aria_d) for d in null_distances)
            distance_p = (extreme + 1) / (len(null_distances) + 1)
        else:
            extreme = sum(d is None for d in null_distances)
            distance_p = (extreme + 1) / (len(null_distances) + 1)

        if isinstance(all_d, int):
            null_delta = [None if d is None else d - all_d for d in null_distances]
            finite_delta = [float(v) for v in null_delta if isinstance(v, int)]
            prob_delta_gt0 = statistics.fmean([1.0 if (v is None or v > 0) else 0.0 for v in null_delta]) if null_delta else float("nan")
            prob_delta_ge2 = statistics.fmean([1.0 if (v is None or v >= 2) else 0.0 for v in null_delta]) if null_delta else float("nan")
            observed_delta_d = None if aria_d is None else aria_d - all_d
        else:
            finite_delta = []
            prob_delta_gt0 = float("nan")
            prob_delta_ge2 = float("nan")
            observed_delta_d = None

        same_distance_draws = [r for r in rs if isinstance(all_d, int) and r.get("distance") == all_d]
        null_sp_reduced = [
            1.0 if isinstance(r.get("shortest_path_count"), int) and isinstance(all_sp, int) and r["shortest_path_count"] < all_sp else 0.0
            for r in same_distance_draws
        ]
        null_fh_reduced = [
            1.0 if isinstance(r.get("first_hop_count"), int) and isinstance(all_fh, int) and r["first_hop_count"] < all_fh else 0.0
            for r in same_distance_draws
        ]

        observed_sp_reduced_equal_d = (
            bool(aria_d == all_d and isinstance(all_sp, int) and isinstance(aria_sp, int) and aria_sp < all_sp)
            if isinstance(all_d, int) else False
        )
        observed_fh_reduced_equal_d = (
            bool(aria_d == all_d and isinstance(all_fh, int) and isinstance(aria_fh, int) and aria_fh < all_fh)
            if isinstance(all_d, int) else False
        )

        out.append({
            "site_id": site,
            "task_id": task_id,
            "null_model": null_name,
            "all_distance": all_d,
            "aria_distance": aria_d,
            "observed_delta_d": observed_delta_d,
            "aria_reachable": aria_reachable,
            "all_shortest_path_count": all_sp,
            "aria_shortest_path_count": aria_sp,
            "all_first_hop_count": all_fh,
            "aria_first_hop_count": aria_fh,
            "observed_sp_reduced_equal_distance": observed_sp_reduced_equal_d,
            "observed_fh_reduced_equal_distance": observed_fh_reduced_equal_d,
            "null_reachability_rate": statistics.fmean(null_reach) if null_reach else float("nan"),
            "null_mean_distance_when_reachable": statistics.fmean(finite_d) if finite_d else float("nan"),
            "null_median_distance_when_reachable": _quantile(finite_d, 0.5),
            "null_mean_delta_d_when_reachable": statistics.fmean(finite_delta) if finite_delta else float("nan"),
            "null_probability_delta_d_gt0_or_unreachable": prob_delta_gt0,
            "null_probability_delta_d_ge2_or_unreachable": prob_delta_ge2,
            "null_same_distance_draws": len(same_distance_draws),
            "null_probability_sp_reduced_given_equal_distance": statistics.fmean(null_sp_reduced) if null_sp_reduced else float("nan"),
            "null_probability_fh_reduced_given_equal_distance": statistics.fmean(null_fh_reduced) if null_fh_reduced else float("nan"),
            "distance_or_unreachable_one_sided_p": distance_p,
            "n_draws": len(null_distances),
        })
    return out


def run(args: argparse.Namespace) -> None:
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Formal task-level nulls must use the exact same-state frozen Agent graph that
    # generated docs/agent_formal_task_suite_v1.json. Site-level measurement nulls
    # remain on the broader Step 0.5 four-site graph. These two graph sources are
    # deliberately separate.
    task_path = Path(args.tasks) if args.tasks else None
    task_definitions = _load_formal_task_definitions(task_path) if task_path else []
    formal_sites = tuple(sorted({str(task["site"]) for task in task_definitions}))
    formal_graphs, formal_aria_only_rows = load_formal_graphs(Path(args.formal_graphs))
    validation_rows = validate_formal_task_graphs(formal_graphs, task_definitions) if task_definitions else []
    _write_csv(output_dir / "formal_graph_validation.csv", validation_rows)
    _write_csv(output_dir / "formal_aria_only_edges.csv", formal_aria_only_rows)

    tasks = _parse_task_rows(task_path, formal_sites) if task_path else []
    observed_task_rows: List[Dict[str, object]] = []
    for site in formal_sites:
        site_tasks = [t for t in tasks if t["site_id"] == site]
        for condition in (ALL_REP, ARIA_REP):
            for row in task_metrics(formal_graphs[site][condition], site_tasks):
                observed_task_rows.append({**row, "condition": condition})
    if observed_task_rows:
        _write_csv(output_dir / "task_observed_metrics.csv", observed_task_rows)

    # Task-level matched nulls: null edge counts/out-degrees are matched to the
    # deletion component ARIA∩ALL, while observed task metrics use full frozen ARIA.
    task_null_outputs = {}
    for null_name, generator in (("global_edge_count", global_edge_count_null), ("nodewise_outdegree", nodewise_outdegree_null)):
        null_task_rows: List[Dict[str, object]] = []
        for site_idx, site in enumerate(formal_sites):
            all_graph = formal_graphs[site][ALL_REP]
            aria_deletion = formal_graphs[site]["aria_deletion_component"]
            site_tasks = [t for t in tasks if t["site_id"] == site]
            for draw in range(args.draws):
                seed = args.seed + site_idx * 1_000_003 + draw * 97 + (0 if null_name == "global_edge_count" else 50_000_003)
                candidate = generator(all_graph, aria_deletion, random.Random(seed))
                for row in task_metrics(candidate, site_tasks):
                    null_task_rows.append({**row, "null_model": null_name, "draw": draw, "seed": seed})
        if null_task_rows:
            _write_csv(output_dir / f"task_null_draws_{null_name}.csv", null_task_rows)
        task_null_outputs[null_name] = null_task_rows

    task_summary_rows: List[Dict[str, object]] = []
    for null_name, null_task_rows in task_null_outputs.items():
        if null_task_rows:
            task_summary_rows.extend(_task_null_summary(observed_task_rows, null_task_rows, null_name))
    if task_summary_rows:
        _write_csv(output_dir / "task_null_comparison_summary.csv", task_summary_rows)

    observed_rows: List[Dict[str, object]] = []
    site_aria_only_rows: List[Dict[str, str]] = []
    site_null_outputs = {}
    sites = tuple(s.strip() for s in args.sites.split(",") if s.strip())
    if not args.task_only:
        graphs, site_aria_only_rows = load_graphs(Path(args.input), sites, args.aria_subset_policy)
        edge_rows: List[Dict[str, object]] = []
        baseline_stats_by_site = {}
        for site in sites:
            all_graph = graphs[site][ALL_REP]
            aria_graph = graphs[site][ARIA_REP]
            baseline_stats = all_pairs_shortest_stats(all_graph)
            baseline_stats_by_site[site] = baseline_stats
            observed_rows.append({"site_id": site, "condition": ARIA_REP, **graph_metrics(aria_graph, all_graph, baseline_stats)})
            edge_rows.extend(summarize_edge_survival(all_graph, aria_graph, site))
        _write_csv(output_dir / "observed_site_metrics.csv", observed_rows)
        _write_csv(output_dir / "aria_only_edges_excluded.csv", site_aria_only_rows)
        _write_csv(output_dir / "edge_survival_by_source.csv", edge_rows)

        for null_name, generator in (("global_edge_count", global_edge_count_null), ("nodewise_outdegree", nodewise_outdegree_null)):
            null_rows: List[Dict[str, object]] = []
            for site_idx, site in enumerate(sites):
                all_graph = graphs[site][ALL_REP]
                aria_graph = graphs[site][ARIA_REP]
                baseline_stats = baseline_stats_by_site[site]
                for draw in range(args.draws):
                    seed = args.seed + site_idx * 1_000_003 + draw * 97 + (0 if null_name == "global_edge_count" else 50_000_003)
                    candidate = generator(all_graph, aria_graph, random.Random(seed))
                    null_rows.append({"site_id": site, "null_model": null_name, "draw": draw, "seed": seed, **graph_metrics(candidate, all_graph, baseline_stats)})
            _write_csv(output_dir / f"null_draws_{null_name}.csv", null_rows)
            site_null_outputs[null_name] = null_rows

        comparison_rows: List[Dict[str, object]] = []
        for null_name, null_rows in site_null_outputs.items():
            comparison_rows.extend(compare_observed_to_null(observed_rows, null_rows, null_name))
        _write_csv(output_dir / "null_comparison_summary.csv", comparison_rows)

    metadata = {
        "schema_version": "2.0",
        "analysis": "matched_random_deletion_null",
        "site_level_graph_source": str(args.input),
        "task_graph_source": "formal_agent_graph_export",
        "formal_graph_file": str(args.formal_graphs),
        "task_file": str(args.tasks) if args.tasks else None,
        "formal_graph_validation_passed": len(validation_rows),
        "formal_graph_validation_expected": len(task_definitions),
        "formal_aria_only_internal_edges": len(formal_aria_only_rows),
        "formal_tasks_changed_by_deletion_projection": sum(not bool(r["retained_matches_full_aria"]) for r in validation_rows),
        "site_level_sites": list(sites),
        "formal_task_sites": list(formal_sites),
        "draws_per_site_per_null": args.draws,
        "seed": args.seed,
        "task_only": bool(args.task_only),
        "task_null_observed_graph": "full frozen rendered_aria_followable",
        "task_null_matching_graph": "formal rendered_aria_followable ∩ formal rendered_all; deletion nulls cannot simulate added edges",
        "null_models": {
            "global_edge_count": "uniformly retain the same total number of internal directed edges as observed ARIA deletion component",
            "nodewise_outdegree": "for each captured page, uniformly retain exactly the observed ARIA deletion-component out-degree from that page's rendered_all outgoing edges",
        },
        "primary_interpretation": "nodewise_outdegree is the stricter null because it controls how many links survive on every page; the remaining random variable is which destinations survive",
    }
    meta_name = "task_analysis_metadata.json" if args.task_only else "analysis_metadata.json"
    (output_dir / meta_name).write_text(json.dumps(metadata, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print(f"Matched-null analysis complete: {output_dir}")
    print(f"  formal graph validation: {len(validation_rows)}/{len(task_definitions)} PASS")
    print(f"  formal ARIA-only internal edges: {len(formal_aria_only_rows)}")
    print(f"  formal tasks changed by deletion projection: {metadata['formal_tasks_changed_by_deletion_projection']}/{len(task_definitions)}")
    if not args.task_only:
        for row in observed_rows:
            print(f"  {row['site_id']}: edges={int(row['edge_count'])}, retention={row['edge_retention_ratio']:.3f}, reachability_loss={row['reachability_loss']:.3f}, mean_stretch={row['mean_path_stretch']:.3f}")

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", default=str(DEFAULT_INPUT), help="Step 0.5 four-site graph input for site-level null only")
    parser.add_argument("--formal-graphs", default=str(DEFAULT_FORMAL_GRAPHS), help="formal Agent frozen graph export for 18-task null")
    parser.add_argument(
        "--tasks",
        default=str(DEFAULT_TASKS),
        help="optional task CSV/JSON. Existing combined task-path CSV is supported; use '' to disable.",
    )
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help="output directory")
    parser.add_argument("--sites", default=",".join(PRIMARY_SITES), help="comma-separated site IDs")
    parser.add_argument("--draws", type=int, default=1000, help="random realizations per site per null")
    parser.add_argument(
        "--aria-subset-policy",
        choices=("intersect", "error"),
        default="intersect",
        help="matched deletion requires ARIA to be a subset of rendered_all; intersect conservatively projects rare ARIA-only internal edges out and reports them",
    )
    parser.add_argument(
        "--task-only",
        action="store_true",
        help="recompute only formal task-level nulls from the formal Agent frozen graph; skip site-level Step 0.5 nulls",
    )
    parser.add_argument("--seed", type=int, default=20260928, help="base random seed")
    return parser


if __name__ == "__main__":
    parser = build_parser()
    ns = parser.parse_args()
    if ns.tasks == "":
        ns.tasks = None
    run(ns)
