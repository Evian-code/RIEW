#!/usr/bin/env python3
"""Interface-induced structural selection analysis for RIEW.

Scientific question
-------------------
The existing matched-deletion analysis shows that the observed ARIA transformation is
not equivalent to generic sparsification.  This script asks the next network-science
question: *which structural roles are preferentially preserved or removed?*

For every directed edge in the frozen ``rendered_all`` graph, the script computes
navigation-relevant structural roles on the SAME baseline graph, then labels the edge
as survived/removed according to the frozen ARIA deletion component.  The primary
roles are:

1. fractional_shortest_path_load
   Fractional all-pairs shortest-path flow carried by the edge (directed, normalized).
2. critical_shortest_pair_fraction
   Fraction of reachable ordered node pairs for which every shortest path uses the
   edge.  Removing such an edge must increase shortest distance or destroy reachability.
3. redundancy_contribution_fraction
   Fraction of reachable ordered node pairs for which the edge participates in some,
   but not all, shortest paths.  Removing such an edge reduces shortest-path
   multiplicity while leaving shortest distance unchanged.
4. replacement_unreachable / replacement_distance_capped
   Whether the direct edge has any alternative u->v route when removed, and the
   shortest replacement length (unreachable is capped at n+1 only for the null summary).
5. intercommunity
   Whether the edge crosses Louvain communities in the undirected projection of the
   baseline graph.  This is secondary/exploratory and requires NetworkX.

Inference uses the SAME nodewise out-degree matched null philosophy as Figure 3:
for every source page, each null draw retains exactly the observed number of outgoing
ARIA edges, but uniformly randomizes which destinations survive.  Edge structural
roles are always computed once on the frozen rendered_all graph; only the survival
labels are randomized.  This tests structural selection without recomputing a new
network metric after each deletion draw.

Outputs
-------
results/analysis/structural_selection/
  edge_structural_roles.csv
  site_observed_selection_summary.csv
  nodewise_null_draws.csv
  selection_null_comparison.csv
  task_relevant_edge_roles.csv
  community_summary.csv
  core_hypotheses.csv
  analysis_metadata.json

Run from the project root:

    python analysis/network/interface_structural_selection.py --draws 1000

The script intentionally does NOT test generic PageRank/clustering/assortativity
collections.  It is restricted to structural quantities with a direct navigation
interpretation.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import random
import statistics
import sys
from collections import defaultdict, deque
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, MutableMapping, Optional, Sequence, Set, Tuple

# Make the repository root importable when executed as a file.
PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from analysis.network.matched_random_deletion_null import (  # noqa: E402
    ALL_REP,
    Graph,
    bfs_shortest_stats,
    load_formal_graphs,
    nodewise_outdegree_null,
)


try:
    import numpy as np
except Exception as exc:  # pragma: no cover
    raise RuntimeError("This analysis requires numpy (already in requirements-publication.txt).") from exc

try:  # optional; only the community-role analysis depends on it
    import networkx as nx
except Exception:  # pragma: no cover
    nx = None


DEFAULT_GRAPHS = Path("data/derived/formal_agent_graphs_v1.json")
DEFAULT_TASKS = Path("docs/agent_formal_task_suite_v1.json")
DEFAULT_OUTPUT = Path("results/analysis/structural_selection")
DEFAULT_SITES = ("mdn_js", "wikipedia", "usa_gov")
SITE_LABELS = {"mdn_js": "MDN", "wikipedia": "Wikipedia", "usa_gov": "USA.gov"}

Edge = Tuple[str, str]


@dataclass
class EdgeRole:
    site_id: str
    source_url: str
    target_url: str
    survived: int
    source_out_degree: int
    target_in_degree: int
    shortest_pair_participation_count: int
    fractional_shortest_path_load: float
    critical_shortest_pair_count: int
    critical_shortest_pair_fraction: float
    redundancy_contribution_count: int
    redundancy_contribution_fraction: float
    replacement_distance: Optional[int]
    replacement_unreachable: int
    replacement_distance_capped: float
    community_source: Optional[int]
    community_target: Optional[int]
    intercommunity: Optional[int]
    task_pair_participation_count: int
    task_fractional_shortest_path_load: float
    task_critical_count: int
    task_redundancy_count: int

    def as_dict(self) -> Dict[str, object]:
        return self.__dict__.copy()


# ---------------------------------------------------------------------------
# Basic utilities
# ---------------------------------------------------------------------------


def _write_csv(path: Path, rows: Sequence[Mapping[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields: List[str] = []
    seen: Set[str] = set()
    for row in rows:
        for key in row:
            if key not in seen:
                seen.add(key)
                fields.append(key)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def _load_tasks(path: Path) -> List[Dict[str, object]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, dict):
        raw = raw.get("tasks", raw.get("items", []))
    if not isinstance(raw, list):
        raise ValueError(f"Expected a task list in {path}")
    return [dict(x) for x in raw]


def _quantile(values: Sequence[float], q: float) -> float:
    if not values:
        return float("nan")
    xs = sorted(float(x) for x in values)
    if len(xs) == 1:
        return xs[0]
    pos = (len(xs) - 1) * q
    lo, hi = math.floor(pos), math.ceil(pos)
    if lo == hi:
        return xs[lo]
    w = pos - lo
    return xs[lo] * (1 - w) + xs[hi] * w


def _safe_mean(values: Sequence[float]) -> float:
    return statistics.fmean(values) if values else float("nan")


def _safe_median(values: Sequence[float]) -> float:
    return statistics.median(values) if values else float("nan")


def _reverse_graph(graph: Graph) -> Graph:
    nodes = tuple(graph.nodes)
    adj: Dict[str, List[str]] = {u: [] for u in nodes}
    rev_edges = []
    for u, v in graph.edges:
        rev_edges.append((v, u))
        adj[v].append(u)
    return Graph(
        nodes=nodes,
        edges=frozenset(rev_edges),
        adjacency={u: tuple(sorted(vs)) for u, vs in adj.items()},
    )


def _bfs_distance_excluding_edge(graph: Graph, source: str, target: str, excluded: Edge) -> Optional[int]:
    """Shortest directed source->target distance after removing exactly one edge."""
    if source == target:
        return 0
    q: deque[str] = deque([source])
    dist = {source: 0}
    while q:
        u = q.popleft()
        nd = dist[u] + 1
        for v in graph.adjacency.get(u, ()):
            if (u, v) == excluded:
                continue
            if v in dist:
                continue
            if v == target:
                return nd
            dist[v] = nd
            q.append(v)
    return None


def _all_pairs_stats(graph: Graph):
    return {s: bfs_shortest_stats(graph, s) for s in graph.nodes}


def _reachable_pair_count(stats) -> int:
    total = 0
    for s, (dist, _sp, _fh) in stats.items():
        total += sum(1 for t in dist if t != s)
    return total


# ---------------------------------------------------------------------------
# Community role (secondary / exploratory)
# ---------------------------------------------------------------------------


def _community_partition(graph: Graph, seed: int, skip: bool) -> Tuple[Dict[str, int], Dict[str, object]]:
    if skip:
        return {}, {"available": False, "reason": "disabled_by_flag"}
    if nx is None:
        return {}, {
            "available": False,
            "reason": "networkx_not_installed",
            "install": "python -m pip install 'networkx>=3.2'",
        }

    ug = nx.Graph()
    ug.add_nodes_from(graph.nodes)
    ug.add_edges_from((u, v) for u, v in graph.edges)

    # Louvain is deterministic for a fixed seed and directly available in modern NetworkX.
    communities = nx.community.louvain_communities(ug, seed=seed, weight=None)
    mapping: Dict[str, int] = {}
    for cid, members in enumerate(sorted(communities, key=lambda c: (-len(c), sorted(c)[0] if c else ""))):
        for node in members:
            mapping[node] = cid

    modularity = nx.community.modularity(ug, communities, weight=None) if communities else float("nan")
    return mapping, {
        "available": True,
        "algorithm": "networkx_louvain_communities_on_undirected_projection",
        "n_communities": len(communities),
        "community_sizes": sorted([len(c) for c in communities], reverse=True),
        "modularity": modularity,
        "seed": seed,
    }


# ---------------------------------------------------------------------------
# Exact structural roles on the frozen rendered_all graph
# ---------------------------------------------------------------------------


def compute_edge_roles(
    site: str,
    all_graph: Graph,
    aria_graph: Graph,
    tasks: Sequence[Mapping[str, object]],
    *,
    community_seed: int,
    skip_community: bool,
) -> Tuple[List[EdgeRole], Dict[str, object]]:
    """Compute exact baseline structural roles for every rendered_all directed edge.

    All roles are measured on ``all_graph``.  ARIA is used only for the observed
    survived/removed label, preventing treatment-induced redefinition of the predictor.
    """
    nodes = tuple(all_graph.nodes)
    n = len(nodes)
    edge_list = sorted(all_graph.edges)
    edge_set = set(edge_list)
    aria_edges = set(aria_graph.edges)
    if not aria_edges <= edge_set:
        raise ValueError(f"ARIA deletion component is not a subset of rendered_all for {site}")

    stats = _all_pairs_stats(all_graph)
    reachable_pairs = _reachable_pair_count(stats)
    if reachable_pairs <= 0:
        raise ValueError(f"No reachable ordered pairs for site={site}")

    indegree = {u: 0 for u in nodes}
    for u, v in edge_list:
        indegree[v] += 1

    communities, community_meta = _community_partition(all_graph, community_seed, skip_community)

    # Pre-extract formal task pairs that genuinely exist in this site's baseline graph.
    site_tasks: List[Tuple[str, str, str]] = []
    for task in tasks:
        if str(task.get("site")) != site:
            continue
        s = str(task.get("start_url"))
        t = str(task.get("target_url"))
        task_id = str(task.get("task_id"))
        if s in stats and t in stats[s][0] and s != t:
            site_tasks.append((task_id, s, t))

    rows: List[EdgeRole] = []
    for idx, (u, v) in enumerate(edge_list, 1):
        pair_participation = 0
        fractional_load = 0.0
        critical_count = 0
        redundancy_count = 0

        # Exact ordered-pair shortest-path role.
        # Number of s->t shortest paths using e=(u,v) is
        # sigma(s,u) * sigma(v,t) whenever d(s,u)+1+d(v,t)=d(s,t).
        v_dist, v_sp, _ = stats[v]
        for s in nodes:
            s_dist, s_sp, _ = stats[s]
            d_su = s_dist.get(u)
            sigma_su = s_sp.get(u)
            if d_su is None or sigma_su is None:
                continue
            for t, d_vt in v_dist.items():
                if s == t:
                    continue
                d_st = s_dist.get(t)
                if d_st is None or d_su + 1 + d_vt != d_st:
                    continue
                sigma_st = s_sp[t]
                sigma_vt = v_sp[t]
                using = sigma_su * sigma_vt
                if using <= 0:
                    continue
                pair_participation += 1
                frac = using / sigma_st
                fractional_load += frac
                if using == sigma_st:
                    critical_count += 1
                elif using < sigma_st:
                    # Removing e reduces shortest-path multiplicity but at least one
                    # shortest path of the same length remains.
                    redundancy_count += 1

        # Task-relevant role is SECONDARY because formal tasks were structurally stratified.
        task_pair_count = 0
        task_frac_load = 0.0
        task_critical = 0
        task_redundant = 0
        for _task_id, s, t in site_tasks:
            s_dist, s_sp, _ = stats[s]
            v_dist, v_sp, _ = stats[v]
            d_su = s_dist.get(u)
            d_vt = v_dist.get(t)
            d_st = s_dist.get(t)
            if d_su is None or d_vt is None or d_st is None:
                continue
            if d_su + 1 + d_vt != d_st:
                continue
            using = s_sp[u] * v_sp[t]
            sigma_st = s_sp[t]
            task_pair_count += 1
            task_frac_load += using / sigma_st
            if using == sigma_st:
                task_critical += 1
            elif using < sigma_st:
                task_redundant += 1

        replacement = _bfs_distance_excluding_edge(all_graph, u, v, (u, v))
        replacement_unreachable = int(replacement is None)
        replacement_capped = float(n + 1 if replacement is None else replacement)

        if communities:
            cu, cv = communities[u], communities[v]
            intercommunity: Optional[int] = int(cu != cv)
        else:
            cu = cv = None
            intercommunity = None

        rows.append(
            EdgeRole(
                site_id=site,
                source_url=u,
                target_url=v,
                survived=int((u, v) in aria_edges),
                source_out_degree=len(all_graph.adjacency.get(u, ())),
                target_in_degree=indegree[v],
                shortest_pair_participation_count=pair_participation,
                fractional_shortest_path_load=fractional_load / reachable_pairs,
                critical_shortest_pair_count=critical_count,
                critical_shortest_pair_fraction=critical_count / reachable_pairs,
                redundancy_contribution_count=redundancy_count,
                redundancy_contribution_fraction=redundancy_count / reachable_pairs,
                replacement_distance=replacement,
                replacement_unreachable=replacement_unreachable,
                replacement_distance_capped=replacement_capped,
                community_source=cu,
                community_target=cv,
                intercommunity=intercommunity,
                task_pair_participation_count=task_pair_count,
                task_fractional_shortest_path_load=task_frac_load,
                task_critical_count=task_critical,
                task_redundancy_count=task_redundant,
            )
        )

        if idx % 1000 == 0 or idx == len(edge_list):
            print(f"    {site}: edge roles {idx}/{len(edge_list)}")

    meta = {
        "site_id": site,
        "node_count": n,
        "rendered_all_edge_count": len(edge_list),
        "aria_survived_edge_count": len(aria_edges),
        "aria_removed_edge_count": len(edge_list) - len(aria_edges),
        "reachable_ordered_pair_count": reachable_pairs,
        "formal_task_count": len(site_tasks),
        "community": community_meta,
    }
    return rows, meta


# ---------------------------------------------------------------------------
# Observed structural selection and matched null
# ---------------------------------------------------------------------------


PRIMARY_METRICS = (
    "fractional_shortest_path_load",
    "critical_shortest_pair_fraction",
    "redundancy_contribution_fraction",
    "replacement_unreachable",
    "replacement_distance_capped",
)
SECONDARY_METRICS = ("intercommunity",)
TASK_METRICS = (
    "task_pair_participation_count",
    "task_fractional_shortest_path_load",
    "task_critical_count",
    "task_redundancy_count",
)


def _selection_summary(
    site: str,
    edge_rows: Sequence[EdgeRole],
    metric: str,
    survivor_mask: Optional[np.ndarray] = None,
) -> Dict[str, object]:
    if survivor_mask is None:
        survivor_mask = np.array([r.survived == 1 for r in edge_rows], dtype=bool)
    vals_obj = [getattr(r, metric) for r in edge_rows]
    valid = np.array([v is not None and math.isfinite(float(v)) for v in vals_obj], dtype=bool)
    vals = np.array([float(v) if ok else np.nan for v, ok in zip(vals_obj, valid)], dtype=float)

    sm = survivor_mask & valid
    rm = (~survivor_mask) & valid
    surv = vals[sm]
    rem = vals[rm]
    if len(surv) == 0 or len(rem) == 0:
        return {
            "site_id": site,
            "metric": metric,
            "n_survived": int(len(surv)),
            "n_removed": int(len(rem)),
            "survived_mean": float("nan"),
            "removed_mean": float("nan"),
            "removed_minus_survived": float("nan"),
            "survived_median": float("nan"),
            "removed_median": float("nan"),
            "removed_minus_survived_median": float("nan"),
        }
    return {
        "site_id": site,
        "metric": metric,
        "n_survived": int(len(surv)),
        "n_removed": int(len(rem)),
        "survived_mean": float(np.mean(surv)),
        "removed_mean": float(np.mean(rem)),
        "removed_minus_survived": float(np.mean(rem) - np.mean(surv)),
        "survived_median": float(np.median(surv)),
        "removed_median": float(np.median(rem)),
        "removed_minus_survived_median": float(np.median(rem) - np.median(surv)),
    }


def _bh_adjust(pvalues: Sequence[float]) -> List[float]:
    """Benjamini-Hochberg FDR adjustment, preserving input order."""
    n = len(pvalues)
    if n == 0:
        return []
    order = sorted(range(n), key=lambda i: pvalues[i])
    q = [1.0] * n
    running = 1.0
    for rank_from_end, idx in enumerate(reversed(order), 1):
        rank = n - rank_from_end + 1
        value = min(1.0, pvalues[idx] * n / rank)
        running = min(running, value)
        q[idx] = running
    return q


def run_nodewise_null(
    site: str,
    all_graph: Graph,
    aria_graph: Graph,
    edge_rows: Sequence[EdgeRole],
    metrics: Sequence[str],
    *,
    draws: int,
    seed: int,
) -> Tuple[List[Dict[str, object]], List[Dict[str, object]]]:
    edge_index = {(r.source_url, r.target_url): i for i, r in enumerate(edge_rows)}
    obs = {m: _selection_summary(site, edge_rows, m) for m in metrics}

    null_rows: List[Dict[str, object]] = []
    null_by_metric: Dict[str, List[float]] = defaultdict(list)

    for draw in range(draws):
        draw_seed = seed + draw * 97
        candidate = nodewise_outdegree_null(all_graph, aria_graph, random.Random(draw_seed))
        mask = np.zeros(len(edge_rows), dtype=bool)
        for edge in candidate.edges:
            idx = edge_index.get(edge)
            if idx is not None:
                mask[idx] = True

        for metric in metrics:
            s = _selection_summary(site, edge_rows, metric, survivor_mask=mask)
            diff = float(s["removed_minus_survived"])
            null_by_metric[metric].append(diff)
            null_rows.append(
                {
                    "site_id": site,
                    "draw": draw,
                    "seed": draw_seed,
                    "metric": metric,
                    "survived_mean": s["survived_mean"],
                    "removed_mean": s["removed_mean"],
                    "removed_minus_survived": diff,
                }
            )

        if (draw + 1) % max(1, draws // 10) == 0 or draw + 1 == draws:
            print(f"    {site}: null draws {draw + 1}/{draws}")

    comparison: List[Dict[str, object]] = []
    for metric in metrics:
        observed = float(obs[metric]["removed_minus_survived"])
        vals = [v for v in null_by_metric[metric] if math.isfinite(v)]
        if not vals or not math.isfinite(observed):
            continue
        mean = statistics.fmean(vals)
        sd = statistics.stdev(vals) if len(vals) > 1 else 0.0
        if observed >= mean:
            tail = (sum(v >= observed for v in vals) + 1) / (len(vals) + 1)
            direction = "removed_higher_than_null_expectation"
        else:
            tail = (sum(v <= observed for v in vals) + 1) / (len(vals) + 1)
            direction = "removed_lower_than_null_expectation"
        two_sided = min(1.0, 2.0 * tail)
        percentile = sum(v <= observed for v in vals) / len(vals)
        z = (observed - mean) / sd if sd > 0 else (0.0 if observed == mean else math.copysign(float("inf"), observed - mean))
        comparison.append(
            {
                "site_id": site,
                "metric": metric,
                "observed_removed_minus_survived": observed,
                "observed_survived_mean": obs[metric]["survived_mean"],
                "observed_removed_mean": obs[metric]["removed_mean"],
                "observed_survived_median": obs[metric]["survived_median"],
                "observed_removed_median": obs[metric]["removed_median"],
                "null_mean_difference": mean,
                "null_sd_difference": sd,
                "null_q025": _quantile(vals, 0.025),
                "null_q05": _quantile(vals, 0.05),
                "null_median": _quantile(vals, 0.50),
                "null_q95": _quantile(vals, 0.95),
                "null_q975": _quantile(vals, 0.975),
                "observed_percentile": percentile,
                "z_score": z,
                "direction_relative_to_null": direction,
                "one_sided_empirical_p": tail,
                "two_sided_empirical_p": two_sided,
                "n_draws": len(vals),
            }
        )
    return null_rows, comparison


def _add_fdr(rows: List[Dict[str, object]]) -> None:
    # Within-site correction across the predeclared structural metrics.
    by_site: Dict[str, List[int]] = defaultdict(list)
    for i, row in enumerate(rows):
        by_site[str(row["site_id"])].append(i)
    for indices in by_site.values():
        ps = [float(rows[i]["two_sided_empirical_p"]) for i in indices]
        qs = _bh_adjust(ps)
        for i, q in zip(indices, qs):
            rows[i]["q_bh_within_site"] = q

    ps = [float(row["two_sided_empirical_p"]) for row in rows]
    qs = _bh_adjust(ps)
    for row, q in zip(rows, qs):
        row["q_bh_global"] = q


# ---------------------------------------------------------------------------
# Publication-oriented plots
# ---------------------------------------------------------------------------








# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------


def run(args: argparse.Namespace) -> None:
    root = Path(args.project_root).resolve()
    graph_path = (root / args.graphs).resolve() if not Path(args.graphs).is_absolute() else Path(args.graphs)
    task_path = (root / args.tasks).resolve() if not Path(args.tasks).is_absolute() else Path(args.tasks)
    output = (root / args.output).resolve() if not Path(args.output).is_absolute() else Path(args.output)
    output.mkdir(parents=True, exist_ok=True)

    sites = tuple(s.strip() for s in args.sites.split(",") if s.strip())
    tasks = _load_tasks(task_path)
    formal_graphs, aria_only = load_formal_graphs(graph_path)
    if aria_only:
        print(f"WARNING: formal export contains {len(aria_only)} ARIA-only internal edge(s).")
        print("Structural-selection analysis uses the deletion component ARIA∩ALL only.")

    all_edge_rows: List[EdgeRole] = []
    site_meta: Dict[str, object] = {}
    observed_summary: List[Dict[str, object]] = []
    null_rows_all: List[Dict[str, object]] = []
    comparisons_all: List[Dict[str, object]] = []
    community_rows: List[Dict[str, object]] = []

    print("Computing exact baseline edge structural roles...")
    for site_idx, site in enumerate(sites):
        if site not in formal_graphs:
            raise ValueError(f"Site {site!r} not found in {graph_path}")
        all_graph = formal_graphs[site][ALL_REP]
        aria_graph = formal_graphs[site]["aria_deletion_component"]
        print(f"  {site}: nodes={len(all_graph.nodes)}, ALL edges={len(all_graph.edges)}, ARIA retained={len(aria_graph.edges)}")
        edge_rows, meta = compute_edge_roles(
            site,
            all_graph,
            aria_graph,
            tasks,
            community_seed=args.community_seed + site_idx,
            skip_community=args.skip_community,
        )
        all_edge_rows.extend(edge_rows)
        site_meta[site] = meta

        metrics = list(PRIMARY_METRICS)
        if any(r.intercommunity is not None for r in edge_rows):
            metrics.extend(SECONDARY_METRICS)
        for metric in metrics:
            observed_summary.append(_selection_summary(site, edge_rows, metric))

        community_meta = dict(meta.get("community", {}))
        community_rows.append({"site_id": site, **community_meta})

        print(f"  {site}: nodewise out-degree matched null ({args.draws} draws)")
        null_rows, comparisons = run_nodewise_null(
            site,
            all_graph,
            aria_graph,
            edge_rows,
            metrics,
            draws=args.draws,
            seed=args.seed + site_idx * 1_000_003,
        )
        null_rows_all.extend(null_rows)
        comparisons_all.extend(comparisons)

    _add_fdr(comparisons_all)

    _write_csv(output / "edge_structural_roles.csv", [r.as_dict() for r in all_edge_rows])
    _write_csv(output / "site_observed_selection_summary.csv", observed_summary)
    _write_csv(output / "nodewise_null_draws.csv", null_rows_all)
    _write_csv(output / "selection_null_comparison.csv", comparisons_all)
    _write_csv(output / "community_summary.csv", community_rows)

    task_rows: List[Dict[str, object]] = []
    for r in all_edge_rows:
        if (
            r.task_pair_participation_count
            or r.task_fractional_shortest_path_load
            or r.task_critical_count
            or r.task_redundancy_count
        ):
            task_rows.append(
                {
                    "site_id": r.site_id,
                    "source_url": r.source_url,
                    "target_url": r.target_url,
                    "survived": r.survived,
                    "task_pair_participation_count": r.task_pair_participation_count,
                    "task_fractional_shortest_path_load": r.task_fractional_shortest_path_load,
                    "task_critical_count": r.task_critical_count,
                    "task_redundancy_count": r.task_redundancy_count,
                }
            )
    _write_csv(output / "task_relevant_edge_roles.csv", task_rows)

    core_hypotheses = [
        {
            "site_id": "mdn_js",
            "existing_topology_phenomenon": "path inflation",
            "predeclared_structural_metric": "critical_shortest_pair_fraction",
            "mechanism_consistent_result": "removed edges exceed nodewise-null expectation in critical shortest-pair role",
        },
        {
            "site_id": "wikipedia",
            "existing_topology_phenomenon": "shortest-path redundancy loss at equal distance",
            "predeclared_structural_metric": "redundancy_contribution_fraction",
            "mechanism_consistent_result": "removed edges exceed nodewise-null expectation in redundancy-supporting role",
        },
        {
            "site_id": "usa_gov",
            "existing_topology_phenomenon": "weak overall distortion",
            "predeclared_structural_metric": "critical_shortest_pair_fraction; redundancy_contribution_fraction",
            "mechanism_consistent_result": "weak or null structural-selection deviation is consistent with the weak-distortion comparison case",
        },
    ]
    _write_csv(output / "core_hypotheses.csv", core_hypotheses)

    metadata = {
        "schema_version": "1.0",
        "analysis": "interface_induced_structural_selection",
        "graph_source": str(graph_path),
        "task_source": str(task_path),
        "sites": list(sites),
        "draws_per_site": args.draws,
        "seed": args.seed,
        "community_seed": args.community_seed,
        "community_analysis_enabled": not args.skip_community,
        "community_requires_networkx": True,
        "aria_role": "survival label only; edge structural roles are computed on frozen rendered_all",
        "observed_survival_graph": "formal aria_deletion_component = rendered_aria_followable ∩ rendered_all",
        "null_model": "nodewise out-degree matched retention: each source keeps exactly observed ARIA out-degree, destinations randomized",
        "primary_metrics": list(PRIMARY_METRICS),
        "secondary_metrics": list(SECONDARY_METRICS),
        "task_metrics": list(TASK_METRICS),
        "task_metric_warning": "task-relevant roles are secondary/descriptive because formal tasks were selected by structural tier",
        "inference_unit": "whole site graph under matched randomization; edges are not treated as independent replicates",
        "multiple_testing": "two-sided empirical p values plus Benjamini-Hochberg q within site and globally",
        "site_metadata": site_meta,
    }
    (output / "analysis_metadata.json").write_text(json.dumps(metadata, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


    print("\nStructural-selection analysis complete.")
    print(f"Output: {output}")
    print("\nCore hypothesis checks:")
    for site, metric in [
        ("mdn_js", "critical_shortest_pair_fraction"),
        ("wikipedia", "redundancy_contribution_fraction"),
        ("usa_gov", "critical_shortest_pair_fraction"),
        ("usa_gov", "redundancy_contribution_fraction"),
    ]:
        row = next((r for r in comparisons_all if r["site_id"] == site and r["metric"] == metric), None)
        if row:
            print(
                f"  {SITE_LABELS.get(site, site):10s} {metric:36s} "
                f"diff={float(row['observed_removed_minus_survived']): .6g} "
                f"z={float(row['z_score']): .2f} "
                f"p2={float(row['two_sided_empirical_p']):.4f} "
                f"q_site={float(row['q_bh_within_site']):.4f}"
            )


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--project-root", default=str(PROJECT_ROOT), help="RIEW project root")
    p.add_argument("--graphs", default=str(DEFAULT_GRAPHS), help="formal graph export, relative to project root")
    p.add_argument("--tasks", default=str(DEFAULT_TASKS), help="formal task suite, relative to project root")
    p.add_argument("--output", default=str(DEFAULT_OUTPUT), help="output directory, relative to project root")
    p.add_argument("--sites", default=",".join(DEFAULT_SITES), help="comma-separated site IDs")
    p.add_argument("--draws", type=int, default=1000, help="nodewise matched-null draws per site")
    p.add_argument("--seed", type=int, default=20260929, help="base random seed")
    p.add_argument("--community-seed", type=int, default=20260929, help="Louvain community seed")
    p.add_argument("--skip-community", action="store_true", help="skip optional community-role analysis")
    p.add_argument("--no-plots", action="store_true", help="write CSV/JSON only")
    return p


if __name__ == "__main__":
    run(build_parser().parse_args())
