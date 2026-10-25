#!/usr/bin/env python3
"""Site-level collective structural effect analysis for RIEW.

Scientific question
-------------------
The existing Figure 3 analysis shows that the *set* of edges removed by the real
agent-facing interface can cause more path inflation / route-redundancy loss than a
nodewise out-degree matched random deletion. A separate edge-role analysis on the
formal Agent graph suggested that this damage is not simply explained by preferential
removal of individually high-importance edges.

This script tests that idea on the EXACT site-level graph universe and null model used
by Figure 3:

    results/step0_5_interface_validation_v2_four_site/
        combined_per_page_link_records.jsonl

For every site it:
  1. reconstructs rendered_all and rendered_aria_followable using the same loader as
     analysis/network/matched_random_deletion_null.py;
  2. computes exact marginal structural roles for every rendered_all edge;
  3. regenerates the SAME nodewise out-degree matched random deletion sets by using
     the same seed schedule as Figure 3;
  4. for each real/null deletion set, records both:
       - marginal edge importance of the removed edges, and
       - collective network damage after removing them together;
  5. tests whether the observed interface produces unusually high collective damage
     even conditional on having similar marginal edge importance;
  6. also measures destination concentration of the removed-edge set, a simple
     collective/correlated-deletion descriptor not fixed by the nodewise null.

Important interpretation
------------------------
A result such as "low/ordinary marginal importance but high realized damage" supports
an interaction/collective-effect interpretation. It does NOT by itself identify the
specific joint deletion mechanism. This script is intentionally designed to test
that narrower claim without adding a large collection of unrelated network metrics.

Dependencies
------------
- Python 3.9+
- numpy

- the existing sibling script: analysis/network/matched_random_deletion_null.py

Recommended run from the project root
-------------------------------------
    python analysis/network/collective_structural_effect_sitelevel.py --draws 1000

The defaults intentionally match the Figure 3 site-level null analysis:
    seed = 20260928
    sites = mdn_js,wikipedia,usa_gov
    null = nodewise_outdegree

Outputs
-------
results/analysis/collective_structural_effect_sitelevel/
    edge_marginal_roles.csv
    observed_collective_summary.csv
    null_collective_draws.csv
    collective_conditional_tests.csv
    set_structure_comparison.csv
    core_collective_hypotheses.csv
    aria_only_edges_excluded.csv
    analysis_metadata.json
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import random
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, MutableMapping, Optional, Sequence, Tuple

try:
    import numpy as np
except ImportError as exc:  # pragma: no cover
    raise SystemExit("This analysis requires numpy. Install it with: pip install numpy") from exc

# Import the exact graph loader/null/metrics used by Figure 3.
SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
try:
    import matched_random_deletion_null as mrd
except ImportError as exc:  # pragma: no cover
    raise SystemExit(
        "Could not import sibling analysis/network/matched_random_deletion_null.py. "
        "Place this file in analysis/network/ and run it from the project."
    ) from exc

Edge = Tuple[str, str]

DEFAULT_INPUT = Path(
    "results/step0_5_interface_validation_v2_four_site/combined_per_page_link_records.jsonl"
)
DEFAULT_FIG3_NULL = Path("results/analysis/network_null/null_draws_nodewise_outdegree.csv")
DEFAULT_FIG3_OBS = Path("results/analysis/network_null/observed_site_metrics.csv")
DEFAULT_FIG3_META = Path("results/analysis/network_null/analysis_metadata.json")
DEFAULT_OUTPUT = Path("results/analysis/collective_structural_effect_sitelevel")
DEFAULT_SITES = ("mdn_js", "wikipedia", "usa_gov")

ROLE_METRICS = (
    "fractional_shortest_path_load",
    "shortest_pair_participation_fraction",
    "critical_shortest_pair_fraction",
    "redundancy_contribution_fraction",
)

# All of these are "higher = more structural damage" under mrd.graph_metrics.
DAMAGE_METRICS = (
    "mean_path_stretch",
    "fraction_delta_d_ge2",
    "fraction_sp_reduced_equal_d",
    "fraction_fh_reduced_equal_d",
)

# Primary pairings were chosen *before* looking at the collective output and correspond
# directly to the already-established Figure 3 site-level phenomena.
CORE_TESTS = (
    # site, marginal-role x, realized-damage y, interpretation
    (
        "mdn_js",
        "removed_mean_fractional_shortest_path_load",
        "mean_path_stretch",
        "MDN: can unusually large path inflation be explained by the average shortest-path role of individually removed edges?",
    ),
    (
        "mdn_js",
        "removed_mean_critical_shortest_pair_fraction",
        "fraction_delta_d_ge2",
        "MDN: can severe distance inflation be explained by preferential removal of individually critical shortest-path edges?",
    ),
    (
        "wikipedia",
        "removed_mean_redundancy_contribution_fraction",
        "fraction_sp_reduced_equal_d",
        "Wikipedia: can shortest-path redundancy collapse be explained by the average marginal redundancy contribution of removed edges?",
    ),
    (
        "wikipedia",
        "removed_mean_redundancy_contribution_fraction",
        "fraction_fh_reduced_equal_d",
        "Wikipedia: can first-hop redundancy collapse be explained by the average marginal redundancy contribution of removed edges?",
    ),
)


def finite_float(value: object) -> Optional[float]:
    try:
        x = float(value)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def write_csv(path: Path, rows: Sequence[Mapping[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fieldnames: List[str] = []
    seen = set()
    for row in rows:
        for key in row.keys():
            if key not in seen:
                seen.add(key)
                fieldnames.append(key)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def read_csv(path: Path) -> List[Dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def quantile(values: Sequence[float], q: float) -> float:
    xs = sorted(values)
    if not xs:
        return float("nan")
    if len(xs) == 1:
        return xs[0]
    pos = (len(xs) - 1) * q
    lo, hi = math.floor(pos), math.ceil(pos)
    if lo == hi:
        return xs[lo]
    w = pos - lo
    return xs[lo] * (1.0 - w) + xs[hi] * w


def mean_sd(values: Sequence[float]) -> Tuple[float, float]:
    if not values:
        return float("nan"), float("nan")
    mean = statistics.fmean(values)
    sd = statistics.stdev(values) if len(values) > 1 else 0.0
    return mean, sd


def rankdata(values: Sequence[float]) -> List[float]:
    """Average ranks for ties, 1-based; enough for Spearman without scipy."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i + 1
        while j < len(order) and values[order[j]] == values[order[i]]:
            j += 1
        avg_rank = (i + 1 + j) / 2.0
        for k in range(i, j):
            ranks[order[k]] = avg_rank
        i = j
    return ranks


def pearson(x: Sequence[float], y: Sequence[float]) -> float:
    if len(x) != len(y) or len(x) < 2:
        return float("nan")
    mx = statistics.fmean(x)
    my = statistics.fmean(y)
    sx = sum((v - mx) ** 2 for v in x)
    sy = sum((v - my) ** 2 for v in y)
    if sx <= 0 or sy <= 0:
        return float("nan")
    return sum((a - mx) * (b - my) for a, b in zip(x, y)) / math.sqrt(sx * sy)


def spearman(x: Sequence[float], y: Sequence[float]) -> float:
    return pearson(rankdata(x), rankdata(y))


def fit_linear(x: Sequence[float], y: Sequence[float]) -> Tuple[float, float]:
    """Least-squares y = intercept + slope*x without scipy."""
    mx = statistics.fmean(x)
    my = statistics.fmean(y)
    denom = sum((v - mx) ** 2 for v in x)
    if denom <= 0:
        return my, 0.0
    slope = sum((a - mx) * (b - my) for a, b in zip(x, y)) / denom
    return my - slope * mx, slope


def bh_fdr(pvalues: Sequence[float]) -> List[float]:
    """Benjamini-Hochberg adjusted q-values."""
    n = len(pvalues)
    if n == 0:
        return []
    order = sorted(range(n), key=lambda i: pvalues[i])
    q = [1.0] * n
    running = 1.0
    for reverse_rank, idx in enumerate(reversed(order), 1):
        rank = n - reverse_rank + 1
        candidate = pvalues[idx] * n / rank
        running = min(running, candidate)
        q[idx] = min(1.0, running)
    return q


def graph_to_matrices(graph: mrd.Graph) -> Tuple[List[str], np.ndarray, np.ndarray, int]:
    """All-pairs shortest distance and shortest-path-count matrices."""
    nodes = list(graph.nodes)
    index = {node: i for i, node in enumerate(nodes)}
    n = len(nodes)
    inf = n + 1_000_000
    dist = np.full((n, n), inf, dtype=np.int32)
    sigma = np.zeros((n, n), dtype=np.float64)

    reachable_ordered_pairs = 0
    for i, source in enumerate(nodes):
        d, sp, _ = mrd.bfs_shortest_stats(graph, source)
        for node, value in d.items():
            j = index[node]
            dist[i, j] = int(value)
            sigma[i, j] = float(sp.get(node, 0))
            if node != source:
                reachable_ordered_pairs += 1
    return nodes, dist, sigma, reachable_ordered_pairs


def compute_edge_marginal_roles(graph: mrd.Graph, site: str) -> Tuple[List[Dict[str, object]], Dict[Edge, Dict[str, float]]]:
    """Compute exact shortest-path roles for every directed edge in rendered_all.

    For pair (s,t), the number of shortest paths through directed edge (u,v) is
        sigma(s,u) * sigma(v,t)
    whenever d(s,u)+1+d(v,t)=d(s,t).

    The four reported roles are normalized by the number of reachable ordered node
    pairs so values are comparable within a site and have a direct interpretation as
    pair-level fractions/average shortest-path shares.
    """
    nodes, dist, sigma, reachable_pairs = graph_to_matrices(graph)
    index = {node: i for i, node in enumerate(nodes)}
    n = len(nodes)
    inf = n + 1_000_000
    valid_pairs = dist < inf
    np.fill_diagonal(valid_pairs, False)

    rows: List[Dict[str, object]] = []
    role_map: Dict[Edge, Dict[str, float]] = {}
    edges = sorted(graph.edges)

    for edge_idx, (u, v) in enumerate(edges, 1):
        iu, iv = index[u], index[v]
        ds_u = dist[:, iu].astype(np.int64)
        dv_t = dist[iv, :].astype(np.int64)
        total_dist = dist.astype(np.int64, copy=False)

        # Edge lies on a shortest s->t path iff distances compose exactly through u->v.
        on_shortest = (
            valid_pairs
            & (ds_u[:, None] < inf)
            & (dv_t[None, :] < inf)
            & ((ds_u[:, None] + 1 + dv_t[None, :]) == total_dist)
        )

        via = sigma[:, iu][:, None] * sigma[iv, :][None, :]
        total = sigma
        positive = on_shortest & (via > 0) & (total > 0)

        if np.any(positive):
            fractional_load = float(np.sum(via[positive] / total[positive])) / reachable_pairs
            participation = float(np.count_nonzero(positive)) / reachable_pairs
            # Floating counts are exact for these small graphs but use isclose defensively.
            critical_mask = positive & np.isclose(via, total, rtol=0.0, atol=1e-9)
            redundancy_mask = positive & (via < (total - 1e-9))
            critical = float(np.count_nonzero(critical_mask)) / reachable_pairs
            redundancy = float(np.count_nonzero(redundancy_mask)) / reachable_pairs
        else:
            fractional_load = participation = critical = redundancy = 0.0

        roles = {
            "fractional_shortest_path_load": fractional_load,
            "shortest_pair_participation_fraction": participation,
            "critical_shortest_pair_fraction": critical,
            "redundancy_contribution_fraction": redundancy,
        }
        role_map[(u, v)] = roles
        rows.append({
            "site_id": site,
            "source_url": u,
            "target_url": v,
            **roles,
        })

        if edge_idx % 1000 == 0 or edge_idx == len(edges):
            print(f"    {site}: marginal edge roles {edge_idx}/{len(edges)}")

    return rows, role_map


def summarize_removed_set(
    all_graph: mrd.Graph,
    candidate: mrd.Graph,
    role_map: Mapping[Edge, Mapping[str, float]],
) -> Dict[str, float]:
    removed = all_graph.edges - candidate.edges
    if not removed:
        result = {f"removed_mean_{metric}": 0.0 for metric in ROLE_METRICS}
        result.update({
            "removed_edge_count": 0.0,
            "removed_unique_target_count": 0.0,
            "removed_target_coverage_fraction": 0.0,
            "removed_target_hhi": 0.0,
            "removed_target_max_share": 0.0,
        })
        return result

    result: Dict[str, float] = {"removed_edge_count": float(len(removed))}
    for metric in ROLE_METRICS:
        values = [float(role_map[e][metric]) for e in removed]
        result[f"removed_mean_{metric}"] = statistics.fmean(values)
        result[f"removed_sum_{metric}"] = sum(values)

    target_counts = Counter(v for _, v in removed)
    total = len(removed)
    shares = [count / total for count in target_counts.values()]
    result.update({
        "removed_unique_target_count": float(len(target_counts)),
        "removed_target_coverage_fraction": len(target_counts) / len(all_graph.nodes) if all_graph.nodes else 0.0,
        "removed_target_hhi": sum(s * s for s in shares),
        "removed_target_max_share": max(shares) if shares else 0.0,
    })
    return result


def load_saved_damage(
    null_path: Path,
    observed_path: Path,
) -> Tuple[Dict[Tuple[str, int, int], Dict[str, float]], Dict[str, Dict[str, float]]]:
    null_lookup: Dict[Tuple[str, int, int], Dict[str, float]] = {}
    for row in read_csv(null_path):
        if row.get("null_model") != "nodewise_outdegree":
            continue
        site = row.get("site_id", "")
        try:
            draw = int(row.get("draw", ""))
            seed = int(row.get("seed", ""))
        except ValueError:
            continue
        vals = {metric: finite_float(row.get(metric)) for metric in DAMAGE_METRICS}
        null_lookup[(site, draw, seed)] = {k: v for k, v in vals.items() if v is not None}

    observed_lookup: Dict[str, Dict[str, float]] = {}
    for row in read_csv(observed_path):
        site = row.get("site_id", "")
        vals = {metric: finite_float(row.get(metric)) for metric in DAMAGE_METRICS}
        observed_lookup[site] = {k: v for k, v in vals.items() if v is not None}
    return null_lookup, observed_lookup


def conditional_test(
    site: str,
    x_metric: str,
    y_metric: str,
    observed: Mapping[str, object],
    null_rows: Sequence[Mapping[str, object]],
    neighbors: int,
) -> Dict[str, object]:
    obs_x = finite_float(observed.get(x_metric))
    obs_y = finite_float(observed.get(y_metric))
    pairs: List[Tuple[float, float]] = []
    for row in null_rows:
        x = finite_float(row.get(x_metric))
        y = finite_float(row.get(y_metric))
        if x is not None and y is not None:
            pairs.append((x, y))
    if obs_x is None or obs_y is None or not pairs:
        return {
            "site_id": site,
            "x_metric": x_metric,
            "y_metric": y_metric,
            "status": "insufficient_data",
        }

    xs = [p[0] for p in pairs]
    ys = [p[1] for p in pairs]
    x_mean, x_sd = mean_sd(xs)
    y_mean, y_sd = mean_sd(ys)

    x_percentile = sum(x <= obs_x for x in xs) / len(xs)
    y_percentile = sum(y <= obs_y for y in ys) / len(ys)
    p_y_high = (1 + sum(y >= obs_y for y in ys)) / (len(ys) + 1)
    p_x_low = (1 + sum(x <= obs_x for x in xs)) / (len(xs) + 1)
    p_joint_lowx_highy = (
        1 + sum((x <= obs_x) and (y >= obs_y) for x, y in pairs)
    ) / (len(pairs) + 1)

    # Linear residual test: is damage high relative to what marginal importance predicts?
    intercept, slope = fit_linear(xs, ys)
    residuals = [y - (intercept + slope * x) for x, y in pairs]
    obs_pred = intercept + slope * obs_x
    obs_resid = obs_y - obs_pred
    resid_mean, resid_sd = mean_sd(residuals)
    resid_z = (obs_resid - resid_mean) / resid_sd if resid_sd > 0 else float("nan")
    p_resid_high = (1 + sum(r >= obs_resid for r in residuals)) / (len(residuals) + 1)

    # Local/nearest-x conditional comparison; robust to a nonlinear null cloud.
    x_scale = x_sd if x_sd > 0 else 1.0
    ordered = sorted(pairs, key=lambda pair: abs(pair[0] - obs_x) / x_scale)
    k = min(max(20, neighbors), len(ordered))
    local = ordered[:k]
    local_y = [p[1] for p in local]
    local_mean, local_sd = mean_sd(local_y)
    local_z = (obs_y - local_mean) / local_sd if local_sd > 0 else float("nan")
    p_local_high = (1 + sum(y >= obs_y for y in local_y)) / (len(local_y) + 1)
    local_x_min = min(x for x, _ in local)
    local_x_max = max(x for x, _ in local)

    return {
        "site_id": site,
        "x_metric": x_metric,
        "y_metric": y_metric,
        "status": "ok",
        "observed_x": obs_x,
        "observed_y": obs_y,
        "null_x_mean": x_mean,
        "null_x_sd": x_sd,
        "observed_x_percentile": x_percentile,
        "null_y_mean": y_mean,
        "null_y_sd": y_sd,
        "observed_y_percentile": y_percentile,
        "unconditional_damage_p_high": p_y_high,
        "marginal_importance_p_low": p_x_low,
        "joint_low_importance_high_damage_p": p_joint_lowx_highy,
        "null_spearman_rho_x_y": spearman(xs, ys),
        "linear_intercept": intercept,
        "linear_slope": slope,
        "observed_linear_predicted_y": obs_pred,
        "observed_damage_residual": obs_resid,
        "residual_z": resid_z,
        "residual_empirical_p_high": p_resid_high,
        "conditional_neighbor_count": k,
        "conditional_x_min": local_x_min,
        "conditional_x_max": local_x_max,
        "conditional_null_y_mean": local_mean,
        "conditional_null_y_sd": local_sd,
        "conditional_damage_excess": obs_y - local_mean,
        "conditional_z": local_z,
        "conditional_empirical_p_high": p_local_high,
        "n_null_draws": len(pairs),
    }


def compare_set_structure(
    site: str,
    observed: Mapping[str, object],
    null_rows: Sequence[Mapping[str, object]],
) -> List[Dict[str, object]]:
    rows: List[Dict[str, object]] = []
    metrics = (
        "removed_unique_target_count",
        "removed_target_coverage_fraction",
        "removed_target_hhi",
        "removed_target_max_share",
    )
    for metric in metrics:
        obs = finite_float(observed.get(metric))
        vals = [finite_float(r.get(metric)) for r in null_rows]
        vals = [v for v in vals if v is not None]
        if obs is None or not vals:
            continue
        mean, sd = mean_sd(vals)
        z = (obs - mean) / sd if sd > 0 else float("nan")
        p_high = (1 + sum(v >= obs for v in vals)) / (len(vals) + 1)
        p_low = (1 + sum(v <= obs for v in vals)) / (len(vals) + 1)
        p_two = min(1.0, 2 * min(p_high, p_low))
        rows.append({
            "site_id": site,
            "metric": metric,
            "observed": obs,
            "null_mean": mean,
            "null_sd": sd,
            "null_q025": quantile(vals, 0.025),
            "null_q975": quantile(vals, 0.975),
            "z_score": z,
            "p_high": p_high,
            "p_low": p_low,
            "two_sided_empirical_p": p_two,
            "n_null_draws": len(vals),
        })
    return rows






def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--input", default=str(DEFAULT_INPUT), help="Step 0.5 combined per-page link records JSONL")
    p.add_argument("--sites", default=",".join(DEFAULT_SITES), help="comma-separated site IDs")
    p.add_argument("--draws", type=int, default=1000, help="nodewise matched random deletion draws per site")
    p.add_argument("--seed", type=int, default=20260928, help="must match Figure 3 seed to reuse its null draws")
    p.add_argument("--outdir", default=str(DEFAULT_OUTPUT), help="output directory")
    p.add_argument("--figure3-null", default=str(DEFAULT_FIG3_NULL), help="existing Figure 3 nodewise null-draw CSV")
    p.add_argument("--figure3-observed", default=str(DEFAULT_FIG3_OBS), help="existing Figure 3 observed site-metric CSV")
    p.add_argument("--figure3-metadata", default=str(DEFAULT_FIG3_META), help="existing Figure 3 metadata JSON")
    p.add_argument("--conditional-neighbors", type=int, default=100, help="nearest-null draws used for conditional damage test")
    p.add_argument("--verify-draws", type=int, default=3, help="recompute this many saved null draws/site and verify exact Figure 3 damage metrics")
    p.add_argument("--force-recompute-damage", action="store_true", help="ignore saved Figure 3 damage CSVs and recompute graph metrics for every draw")
    return p


def main() -> None:
    args = build_parser().parse_args()
    input_path = Path(args.input)
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    sites = tuple(s.strip() for s in args.sites.split(",") if s.strip())

    if not input_path.exists():
        raise SystemExit(f"Input not found: {input_path}")

    # Check Figure 3 metadata if available. Refuse silent seed/draw mismatch.
    meta_path = Path(args.figure3_metadata)
    if meta_path.exists() and not args.force_recompute_damage:
        old_meta = json.loads(meta_path.read_text(encoding="utf-8"))
        old_seed = old_meta.get("seed")
        old_draws = old_meta.get("draws_per_site_per_null")
        if old_seed is not None and int(old_seed) != args.seed:
            raise SystemExit(
                f"Figure 3 metadata seed={old_seed}, but this run requested seed={args.seed}. "
                "Use the same seed or --force-recompute-damage."
            )
        if old_draws is not None and int(old_draws) < args.draws:
            print(
                f"Existing Figure 3 file has only {old_draws} draws; requested {args.draws}. "
                "Missing damage rows will be recomputed."
            )

    print("Loading EXACT Figure 3 site-level graphs...")
    graphs, aria_only_rows = mrd.load_graphs(input_path, sites, subset_policy="intersect")
    write_csv(outdir / "aria_only_edges_excluded.csv", aria_only_rows)

    saved_null: Dict[Tuple[str, int, int], Dict[str, float]] = {}
    saved_obs: Dict[str, Dict[str, float]] = {}
    if not args.force_recompute_damage:
        saved_null, saved_obs = load_saved_damage(Path(args.figure3_null), Path(args.figure3_observed))
        if saved_null:
            print(f"Reusing Figure 3 damage metrics from {args.figure3_null}")
        else:
            print("Existing Figure 3 null metrics not found/usable; damage will be recomputed.")

    edge_role_rows: List[Dict[str, object]] = []
    observed_rows: List[Dict[str, object]] = []
    null_rows: List[Dict[str, object]] = []
    baseline_stats_by_site: Dict[str, object] = {}

    for site_idx, site in enumerate(sites):
        all_graph = graphs[site][mrd.ALL_REP]
        aria_graph = graphs[site][mrd.ARIA_REP]
        print(
            f"\n{site}: nodes={len(all_graph.nodes)}, ALL edges={len(all_graph.edges)}, "
            f"ARIA retained={len(aria_graph.edges)}, removed={len(all_graph.edges - aria_graph.edges)}"
        )

        print("  Computing exact marginal edge structural roles...")
        site_edge_rows, role_map = compute_edge_marginal_roles(all_graph, site)
        aria_edges = aria_graph.edges
        for row in site_edge_rows:
            edge = (str(row["source_url"]), str(row["target_url"]))
            row["survives_observed_interface"] = int(edge in aria_edges)
            row["removed_observed_interface"] = int(edge not in aria_edges)
        edge_role_rows.extend(site_edge_rows)

        baseline_stats = mrd.all_pairs_shortest_stats(all_graph)
        baseline_stats_by_site[site] = baseline_stats

        observed_set = summarize_removed_set(all_graph, aria_graph, role_map)
        observed_damage = saved_obs.get(site)
        if not observed_damage or any(metric not in observed_damage for metric in DAMAGE_METRICS):
            observed_damage = {
                k: float(v)
                for k, v in mrd.graph_metrics(aria_graph, all_graph, baseline_stats).items()
                if k in DAMAGE_METRICS and isinstance(v, (int, float)) and math.isfinite(float(v))
            }
        observed_rows.append({
            "site_id": site,
            "condition": "observed_interface",
            **observed_set,
            **observed_damage,
        })

        print(f"  Regenerating {args.draws} nodewise out-degree matched deletion sets...")
        verify_remaining = max(0, args.verify_draws)
        for draw in range(args.draws):
            seed = args.seed + site_idx * 1_000_003 + draw * 97 + 50_000_003
            candidate = mrd.nodewise_outdegree_null(all_graph, aria_graph, random.Random(seed))
            row: Dict[str, object] = {
                "site_id": site,
                "null_model": "nodewise_outdegree",
                "draw": draw,
                "seed": seed,
                **summarize_removed_set(all_graph, candidate, role_map),
            }

            saved = saved_null.get((site, draw, seed))
            if saved and all(metric in saved for metric in DAMAGE_METRICS):
                row.update(saved)
                if verify_remaining > 0:
                    check = mrd.graph_metrics(candidate, all_graph, baseline_stats)
                    for metric in DAMAGE_METRICS:
                        a = finite_float(check.get(metric))
                        b = finite_float(saved.get(metric))
                        if a is None or b is None:
                            continue
                        if not math.isclose(a, b, rel_tol=1e-10, abs_tol=1e-12):
                            raise RuntimeError(
                                f"Figure 3 null reproduction mismatch: site={site}, draw={draw}, "
                                f"metric={metric}, regenerated={a}, saved={b}."
                            )
                    verify_remaining -= 1
            else:
                metrics = mrd.graph_metrics(candidate, all_graph, baseline_stats)
                for metric in DAMAGE_METRICS:
                    value = finite_float(metrics.get(metric))
                    if value is not None:
                        row[metric] = value

            null_rows.append(row)
            if (draw + 1) % 100 == 0 or draw + 1 == args.draws:
                print(f"    {site}: null draws {draw + 1}/{args.draws}")

    write_csv(outdir / "edge_marginal_roles.csv", edge_role_rows)
    write_csv(outdir / "observed_collective_summary.csv", observed_rows)
    write_csv(outdir / "null_collective_draws.csv", null_rows)

    observed_by_site = {str(r["site_id"]): r for r in observed_rows}
    null_by_site: Dict[str, List[Mapping[str, object]]] = defaultdict(list)
    for row in null_rows:
        null_by_site[str(row["site_id"])].append(row)

    # Comprehensive role x damage tests. This is useful for diagnostics; the four
    # pre-specified CORE_TESTS below remain the interpretation focus.
    test_rows: List[Dict[str, object]] = []
    for site in sites:
        observed = observed_by_site[site]
        for role in ROLE_METRICS:
            x_metric = f"removed_mean_{role}"
            for damage in DAMAGE_METRICS:
                test_rows.append(
                    conditional_test(
                        site,
                        x_metric,
                        damage,
                        observed,
                        null_by_site[site],
                        args.conditional_neighbors,
                    )
                )

    valid_p = [
        float(r["conditional_empirical_p_high"])
        for r in test_rows
        if r.get("status") == "ok" and finite_float(r.get("conditional_empirical_p_high")) is not None
    ]
    qvals = bh_fdr(valid_p)
    q_iter = iter(qvals)
    for row in test_rows:
        if row.get("status") == "ok" and finite_float(row.get("conditional_empirical_p_high")) is not None:
            row["conditional_bh_q_all_role_damage_tests"] = next(q_iter)
    write_csv(outdir / "collective_conditional_tests.csv", test_rows)

    # Pre-specified core hypotheses only.
    core_rows: List[Dict[str, object]] = []
    test_lookup = {
        (str(r.get("site_id")), str(r.get("x_metric")), str(r.get("y_metric"))): r
        for r in test_rows
    }
    for site, x_metric, y_metric, question in CORE_TESTS:
        if site not in observed_by_site:
            continue
        base = dict(test_lookup.get((site, x_metric, y_metric), {}))
        base["scientific_question"] = question
        base["pre_specified_core_test"] = True
        # A descriptive flag, not a replacement for reading effect size + p-value.
        oxp = finite_float(base.get("observed_x_percentile"))
        oyp = finite_float(base.get("observed_y_percentile"))
        cp = finite_float(base.get("conditional_empirical_p_high"))
        base["low_or_ordinary_marginal_high_collective_damage_pattern"] = bool(
            oxp is not None and oyp is not None and cp is not None
            and oxp <= 0.50 and oyp >= 0.95 and cp <= 0.05
        )
        core_rows.append(base)
    if core_rows:
        core_q = bh_fdr([
            float(r.get("conditional_empirical_p_high", 1.0))
            if finite_float(r.get("conditional_empirical_p_high")) is not None else 1.0
            for r in core_rows
        ])
        for row, q in zip(core_rows, core_q):
            row["conditional_bh_q_core_tests"] = q
    write_csv(outdir / "core_collective_hypotheses.csv", core_rows)

    # Collective set-structure descriptors, especially target concentration.
    set_rows: List[Dict[str, object]] = []
    for site in sites:
        set_rows.extend(compare_set_structure(site, observed_by_site[site], null_by_site[site]))
    if set_rows:
        qs = bh_fdr([float(r["two_sided_empirical_p"]) for r in set_rows])
        for row, q in zip(set_rows, qs):
            row["bh_q_all_set_structure_tests"] = q
    write_csv(outdir / "set_structure_comparison.csv", set_rows)


    metadata = {
        "schema_version": "1.0",
        "analysis": "collective_structural_effect_sitelevel",
        "scientific_scope": "site-level collective damage conditional on marginal edge importance",
        "site_level_graph_source": str(input_path),
        "graph_loader_and_null_implementation": "analysis/network/matched_random_deletion_null.py",
        "sites": list(sites),
        "draws_per_site": args.draws,
        "seed": args.seed,
        "null_model": "nodewise_outdegree",
        "seed_schedule": "seed + site_idx*1,000,003 + draw*97 + 50,000,003; identical to Figure 3 nodewise null",
        "figure3_saved_null_source": str(args.figure3_null),
        "figure3_saved_observed_source": str(args.figure3_observed),
        "reused_saved_damage_metrics": bool(saved_null) and not args.force_recompute_damage,
        "aria_subset_policy": "intersect",
        "aria_only_internal_edges_excluded": len(aria_only_rows),
        "marginal_edge_roles": list(ROLE_METRICS),
        "collective_damage_metrics": list(DAMAGE_METRICS),
        "conditional_test": {
            "method_1": "linear residual relative to the nodewise-null x-y relationship",
            "method_2": f"nearest-{args.conditional_neighbors} null draws in marginal-role space",
            "joint_test": "empirical probability of null draw having x <= observed x and y >= observed y",
        },
        "interpretation_limit": (
            "High collective damage conditional on ordinary/low marginal edge importance supports a "
            "marginal-vs-collective mismatch, but does not by itself identify the exact interaction mechanism."
        ),
        "outputs": [
            "edge_marginal_roles.csv",
            "observed_collective_summary.csv",
            "null_collective_draws.csv",
            "collective_conditional_tests.csv",
            "core_collective_hypotheses.csv",
            "set_structure_comparison.csv",
            "aria_only_edges_excluded.csv",
        ],
    }
    (outdir / "analysis_metadata.json").write_text(
        json.dumps(metadata, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    print("\nCollective structural-effect analysis complete.")
    print(f"Output: {outdir.resolve()}")
    print("\nCore tests:")
    for row in core_rows:
        site = str(row.get("site_id", ""))
        x = finite_float(row.get("observed_x_percentile"))
        y = finite_float(row.get("observed_y_percentile"))
        p = finite_float(row.get("conditional_empirical_p_high"))
        q = finite_float(row.get("conditional_bh_q_core_tests"))
        excess = finite_float(row.get("conditional_damage_excess"))
        print(
            f"  {site:10s} | {row.get('x_metric')} -> {row.get('y_metric')} | "
            f"x_pct={x if x is not None else float('nan'):.3f} "
            f"y_pct={y if y is not None else float('nan'):.3f} "
            f"cond_excess={excess if excess is not None else float('nan'):.6g} "
            f"p={p if p is not None else float('nan'):.4f} "
            f"q_core={q if q is not None else float('nan'):.4f}"
        )


if __name__ == "__main__":
    main()
