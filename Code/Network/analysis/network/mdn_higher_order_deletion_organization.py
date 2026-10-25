#!/usr/bin/env python3
"""MDN joint deletion organization analysis for RIEW (legacy filename).

Scientific question
-------------------
The source+target deletion-degree matched null showed that, on MDN, fixing both
margins of the deletion matrix explains much of the observed path inflation but
still leaves a strong residual. This script asks what joint deletion organization
of the deleted edges remains after those first-order degree constraints are fixed.

The analysis is deliberately narrow. It does NOT compute a large centrality
catalogue. Instead it tests three mechanism-focused structures that can explain
why MDN loses short replacement routes even when source and target deletion
degrees are held fixed:

1. Two-hop replacement disruption
   For each deleted direct edge s->t in rendered_all, consider every baseline
   two-hop alternative s->x->t. We measure how many of those alternatives remain
   fully retained, how many are blocked, and how often both legs are deleted.

2. Deleted transitive-triad concentration
   A triple s->t, s->x, x->t with all three links deleted is a joint deletion
   pattern that destroys the direct link together with one of its two-hop detours.
   The fixed-margin null is an appropriate baseline because all source and target
   deletion degrees are identical in every draw.

3. Block / biclique-like co-deletion
   We measure overlap between the deleted target-neighborhoods of source pages and
   count deleted K_{2,2} blocks. These capture coordinated deletion of common target
   sets beyond what is implied by the fixed margins.

For every fixed-margin MCMC draw the script also recomputes the Figure-3 navigation
metrics. This allows us to ask whether the joint-organization statistics are associated
with the residual path damage within the strict null ensemble itself.

Expected project location
-------------------------
Place this file at:
    analysis/network/mdn_higher_order_deletion_organization.py

Required sibling scripts
------------------------
    analysis/network/matched_random_deletion_null.py
    analysis/network/source_target_degree_matched_null.py

Run from the project root
-------------------------
    python analysis/network/mdn_higher_order_deletion_organization.py --draws 1000

Default input
-------------
    results/step0_5_interface_validation_v2_four_site/combined_per_page_link_records.jsonl

Default output
--------------
    results/analysis/mdn_higher_order_deletion_organization/

Main outputs
------------
    observed_higher_order_metrics.csv
    fixed_margin_null_draws.csv
    higher_order_null_comparison.csv
    damage_association.csv
    core_hypotheses.csv
    degree_constraint_validation.csv
    chain_diagnostics.csv
    analysis_metadata.json

Interpretation guardrail
------------------------
A significant result shows that the observed deletion labeling has joint
organization not explained by the fixed source/target deletion degrees. It does
not by itself prove a unique causal mechanism. Metrics tied directly to two-hop
replacement are considered primary; generic block-overlap metrics are secondary.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import random
import statistics
import sys
from collections import defaultdict
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

try:
    import matched_random_deletion_null as mrd
except ImportError as exc:  # pragma: no cover
    raise SystemExit(
        "Could not import analysis/network/matched_random_deletion_null.py. "
        "Place this script in analysis/network/."
    ) from exc

try:
    import source_target_degree_matched_null as st
except ImportError as exc:  # pragma: no cover
    raise SystemExit(
        "Could not import analysis/network/source_target_degree_matched_null.py. "
        "Keep the previously used source_target_degree_matched_null.py in analysis/network/."
    ) from exc

Edge = Tuple[str, str]

DEFAULT_INPUT = Path(
    "results/step0_5_interface_validation_v2_four_site/combined_per_page_link_records.jsonl"
)
DEFAULT_OUTPUT = Path("results/analysis/mdn_higher_order_deletion_organization")
DEFAULT_SITE = "mdn_js"

# Primary joint-organization hypotheses. Direction is expected disruption severity.
# stronger coordinated disruption. These are specified before inspecting the new run.
PRIMARY_METRIC_DIRECTIONS: Mapping[str, str] = {
    # Primary mechanism: real deletions cover two-hop replacement bundles more
    # efficiently than fixed-margin random deletions.
    "weighted_twohop_survival_fraction": "lower",
    "weighted_twohop_block_efficiency": "higher",
}

SECONDARY_METRIC_DIRECTIONS: Mapping[str, str] = {
    "mean_twohop_survival_fraction": "lower",
    "weighted_twohop_exactly_one_hit_fraction": "higher",
    "fraction_deleted_edges_zero_twohop_survivor": "higher",
    "fraction_deleted_edges_twohop_survival_le_10pct": "higher",
    # These diagnose whether the organization is clustered/redundant rather than
    # efficient coverage. They are secondary because a strong mechanism does NOT
    # require both legs of the same detour to be deleted.
    "weighted_twohop_double_hit_fraction": "lower",
    "deleted_transitive_triad_count": "higher",
    "deleted_k22_count": "higher",
    "mean_source_pair_deleted_target_jaccard": "higher",
    "fraction_source_pairs_share_2plus_deleted_targets": "higher",
}

DAMAGE_METRICS = ("mean_path_stretch", "fraction_delta_d_ge2", "mean_delta_d")


# -----------------------------------------------------------------------------
# Generic utilities
# -----------------------------------------------------------------------------

def write_csv(path: Path, rows: Sequence[Mapping[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields: List[str] = []
    seen: Set[str] = set()
    for row in rows:
        for key in row.keys():
            if key not in seen:
                seen.add(key)
                fields.append(key)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def finite_float(value: object) -> Optional[float]:
    try:
        x = float(value)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


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
    return statistics.fmean(values), statistics.stdev(values) if len(values) > 1 else 0.0


def rankdata(values: Sequence[float]) -> List[float]:
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i + 1
        v = values[order[i]]
        while j < len(order) and values[order[j]] == v:
            j += 1
        rank = (i + 1 + j) / 2.0
        for k in range(i, j):
            ranks[order[k]] = rank
        i = j
    return ranks


def pearson(x: Sequence[float], y: Sequence[float]) -> float:
    if len(x) != len(y) or len(x) < 2:
        return float("nan")
    mx, my = statistics.fmean(x), statistics.fmean(y)
    sx = sum((v - mx) ** 2 for v in x)
    sy = sum((v - my) ** 2 for v in y)
    if sx <= 0 or sy <= 0:
        return float("nan")
    return sum((a - mx) * (b - my) for a, b in zip(x, y)) / math.sqrt(sx * sy)


def spearman(x: Sequence[float], y: Sequence[float]) -> float:
    if len(x) != len(y) or len(x) < 3:
        return float("nan")
    return pearson(rankdata(x), rankdata(y))


def lag1(values: Sequence[float]) -> float:
    return pearson(values[:-1], values[1:]) if len(values) >= 3 else float("nan")


def bh_fdr(pvalues: Sequence[float]) -> List[float]:
    n = len(pvalues)
    if n == 0:
        return []
    order = sorted(range(n), key=lambda i: pvalues[i])
    out = [1.0] * n
    running = 1.0
    for reverse_rank, idx in enumerate(reversed(order), 1):
        rank = n - reverse_rank + 1
        running = min(running, pvalues[idx] * n / rank)
        out[idx] = min(1.0, running)
    return out


def one_sided_summary(observed: float, null_values: Sequence[float], direction: str) -> Dict[str, float]:
    vals = [v for v in null_values if math.isfinite(v)]
    mean, sd = mean_sd(vals)
    if direction == "higher":
        extreme = sum(v >= observed for v in vals)
    elif direction == "lower":
        extreme = sum(v <= observed for v in vals)
    else:
        raise ValueError(f"Unknown direction: {direction}")
    return {
        "observed": observed,
        "null_mean": mean,
        "null_sd": sd,
        "null_q025": quantile(vals, 0.025),
        "null_q05": quantile(vals, 0.05),
        "null_median": quantile(vals, 0.50),
        "null_q95": quantile(vals, 0.95),
        "null_q975": quantile(vals, 0.975),
        "observed_percentile": sum(v <= observed for v in vals) / len(vals) if vals else float("nan"),
        "one_sided_empirical_p": (extreme + 1) / (len(vals) + 1) if vals else float("nan"),
        "z_score": (observed - mean) / sd if sd > 0 else float("nan"),
        "n_draws": len(vals),
    }


# -----------------------------------------------------------------------------
# Bit-mask representation of deletion neighborhoods
# -----------------------------------------------------------------------------

def build_all_masks(nodes: Sequence[str], edges: Iterable[Edge]):
    index = {node: i for i, node in enumerate(nodes)}
    out_masks = {u: 0 for u in nodes}
    in_masks = {v: 0 for v in nodes}
    for u, v in edges:
        out_masks[u] |= 1 << index[v]
        in_masks[v] |= 1 << index[u]
    return index, out_masks, in_masks


def build_deleted_masks(nodes: Sequence[str], node_index: Mapping[str, int], deleted: Iterable[Edge]):
    out_masks = {u: 0 for u in nodes}
    in_masks = {v: 0 for v in nodes}
    for u, v in deleted:
        out_masks[u] |= 1 << node_index[v]
        in_masks[v] |= 1 << node_index[u]
    return out_masks, in_masks


# -----------------------------------------------------------------------------
# Joint deletion-organization statistics
# -----------------------------------------------------------------------------

def twohop_replacement_metrics(
    nodes: Sequence[str],
    all_out: Mapping[str, int],
    all_in: Mapping[str, int],
    del_out: Mapping[str, int],
    del_in: Mapping[str, int],
    deleted: Set[Edge],
) -> Dict[str, float]:
    """Quantify coordinated destruction of two-hop detours around deleted direct edges.

    For each deleted direct edge s->t, baseline alternatives are intermediates x such
    that s->x and x->t both exist in rendered_all. A two-hop alternative survives only
    when BOTH legs are retained. A double-hit occurs when BOTH legs are also deleted.
    """
    macro_survival: List[float] = []
    macro_double: List[float] = []
    remaining_counts: List[int] = []
    baseline_counts: List[int] = []

    total_baseline = 0
    total_survive = 0
    total_double = 0
    total_exactly_one = 0
    zero_survivor = 0
    survival_le_10pct = 0
    eligible = 0

    for s, t in deleted:
        baseline_mask = all_out[s] & all_in[t]
        baseline_n = baseline_mask.bit_count()
        if baseline_n <= 0:
            continue

        retained_out_s = all_out[s] & ~del_out[s]
        retained_in_t = all_in[t] & ~del_in[t]
        survive_n = (baseline_mask & retained_out_s & retained_in_t).bit_count()
        double_n = (baseline_mask & del_out[s] & del_in[t]).bit_count()

        eligible += 1
        total_baseline += baseline_n
        total_survive += survive_n
        exactly_one_n = baseline_n - survive_n - double_n
        total_double += double_n
        total_exactly_one += exactly_one_n
        baseline_counts.append(baseline_n)
        remaining_counts.append(survive_n)

        sf = survive_n / baseline_n
        df = double_n / baseline_n
        macro_survival.append(sf)
        macro_double.append(df)
        zero_survivor += int(survive_n == 0)
        survival_le_10pct += int(sf <= 0.10)

    if eligible == 0 or total_baseline == 0:
        return {
            "deleted_edges_with_baseline_twohop": 0.0,
            "mean_baseline_twohop_count": float("nan"),
            "mean_remaining_twohop_count": float("nan"),
            "mean_twohop_survival_fraction": float("nan"),
            "weighted_twohop_survival_fraction": float("nan"),
            "mean_twohop_double_hit_fraction": float("nan"),
            "weighted_twohop_double_hit_fraction": float("nan"),
            "weighted_twohop_exactly_one_hit_fraction": float("nan"),
            "weighted_twohop_block_efficiency": float("nan"),
            "fraction_deleted_edges_zero_twohop_survivor": float("nan"),
            "fraction_deleted_edges_twohop_survival_le_10pct": float("nan"),
            "deleted_transitive_triad_count": 0.0,
            "baseline_transitive_triad_opportunities": 0.0,
        }

    total_blocked = total_baseline - total_survive
    # Every blocked two-hop detour consumes either one or two deleted legs.
    # Efficiency=1 means no deletion is "wasted" by double-hitting an already
    # blocked detour; lower values mean more redundant double hits.
    leg_hit_incidences = total_exactly_one + 2 * total_double
    block_efficiency = total_blocked / leg_hit_incidences if leg_hit_incidences else float("nan")

    return {
        "deleted_edges_with_baseline_twohop": float(eligible),
        "mean_baseline_twohop_count": statistics.fmean(baseline_counts),
        "mean_remaining_twohop_count": statistics.fmean(remaining_counts),
        "mean_twohop_survival_fraction": statistics.fmean(macro_survival),
        "weighted_twohop_survival_fraction": total_survive / total_baseline,
        "mean_twohop_double_hit_fraction": statistics.fmean(macro_double),
        "weighted_twohop_double_hit_fraction": total_double / total_baseline,
        "weighted_twohop_exactly_one_hit_fraction": total_exactly_one / total_baseline,
        "weighted_twohop_block_efficiency": block_efficiency,
        "fraction_deleted_edges_zero_twohop_survivor": zero_survivor / eligible,
        "fraction_deleted_edges_twohop_survival_le_10pct": survival_le_10pct / eligible,
        # Every double-hit around a deleted direct edge is a fully deleted transitive triad.
        "deleted_transitive_triad_count": float(total_double),
        "baseline_transitive_triad_opportunities": float(total_baseline),
    }


def block_overlap_metrics(
    nodes: Sequence[str],
    del_out: Mapping[str, int],
) -> Dict[str, float]:
    """Deleted-neighborhood overlap and deleted K2,2 blocks.

    A deleted K2,2 is a pair of source pages that both lose links to the same pair of
    target pages. Because both source and target deletion degrees are fixed by the null,
    excess K2,2 counts reflect joint arrangement beyond degree-margin effects.
    """
    jaccards: List[float] = []
    cosines: List[float] = []
    common_counts: List[int] = []
    share2 = 0
    eligible_pairs = 0
    k22 = 0

    for i, u in enumerate(nodes):
        mu = del_out[u]
        du = mu.bit_count()
        if du == 0:
            continue
        for v in nodes[i + 1 :]:
            mv = del_out[v]
            dv = mv.bit_count()
            if dv == 0:
                continue
            common = (mu & mv).bit_count()
            union = du + dv - common
            eligible_pairs += 1
            common_counts.append(common)
            jaccards.append(common / union if union else 0.0)
            cosines.append(common / math.sqrt(du * dv))
            share2 += int(common >= 2)
            if common >= 2:
                k22 += common * (common - 1) // 2

    if eligible_pairs == 0:
        return {
            "eligible_source_pairs": 0.0,
            "mean_shared_deleted_targets": float("nan"),
            "mean_source_pair_deleted_target_jaccard": float("nan"),
            "mean_source_pair_deleted_target_cosine": float("nan"),
            "fraction_source_pairs_share_2plus_deleted_targets": float("nan"),
            "deleted_k22_count": 0.0,
        }

    return {
        "eligible_source_pairs": float(eligible_pairs),
        "mean_shared_deleted_targets": statistics.fmean(common_counts),
        "mean_source_pair_deleted_target_jaccard": statistics.fmean(jaccards),
        "mean_source_pair_deleted_target_cosine": statistics.fmean(cosines),
        "fraction_source_pairs_share_2plus_deleted_targets": share2 / eligible_pairs,
        "deleted_k22_count": float(k22),
    }


def higher_order_metrics(
    nodes: Sequence[str],
    node_index: Mapping[str, int],
    all_out: Mapping[str, int],
    all_in: Mapping[str, int],
    deleted: Set[Edge],
) -> Dict[str, float]:
    del_out, del_in = build_deleted_masks(nodes, node_index, deleted)
    out = {}
    out.update(twohop_replacement_metrics(nodes, all_out, all_in, del_out, del_in, deleted))
    out.update(block_overlap_metrics(nodes, del_out))
    return out


# -----------------------------------------------------------------------------
# Main analysis
# -----------------------------------------------------------------------------

def run(args: argparse.Namespace) -> None:
    site = args.site
    if site != "mdn_js":
        print(
            f"Warning: this script was designed for MDN residual path inflation; site={site} "
            "is allowed for diagnostics but should not be treated as a pre-specified primary test."
        )

    input_path = Path(args.input)
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    graphs, aria_only = mrd.load_graphs(input_path, [site], subset_policy="intersect")
    write_csv(output_dir / "aria_only_edges_excluded.csv", aria_only)

    all_graph = graphs[site][mrd.ALL_REP]
    aria_graph = graphs[site][mrd.ARIA_REP]
    observed_deleted = set(all_graph.edges - aria_graph.edges)
    baseline_stats = mrd.all_pairs_shortest_stats(all_graph)
    observed_damage = mrd.graph_metrics(aria_graph, all_graph, baseline_stats)

    nodes = all_graph.nodes
    node_index, all_out, all_in = build_all_masks(nodes, all_graph.edges)
    observed_ho = higher_order_metrics(nodes, node_index, all_out, all_in, observed_deleted)

    print(
        f"{site}: nodes={len(nodes)}, ALL={len(all_graph.edges)}, ARIA={len(aria_graph.edges)}, "
        f"deleted={len(observed_deleted)}"
    )
    print("Observed joint-organization metrics:")
    for key in (
        "weighted_twohop_survival_fraction",
        "weighted_twohop_exactly_one_hit_fraction",
        "weighted_twohop_double_hit_fraction",
        "weighted_twohop_block_efficiency",
        "fraction_deleted_edges_zero_twohop_survivor",
        "deleted_transitive_triad_count",
        "deleted_k22_count",
    ):
        print(f"  {key}: {observed_ho[key]}")

    observed_row: Dict[str, object] = {
        "site_id": site,
        "node_count": len(nodes),
        "all_edge_count": len(all_graph.edges),
        "aria_edge_count": len(aria_graph.edges),
        "deleted_edge_count": len(observed_deleted),
        **observed_ho,
    }
    for metric in DAMAGE_METRICS:
        observed_row[metric] = observed_damage[metric]
    write_csv(output_dir / "observed_higher_order_metrics.csv", [observed_row])

    # Strict fixed-margin MCMC, identical null family to source_target_degree_matched_null.py.
    state = st._make_chain_state(set(all_graph.edges), observed_deleted, nodes)
    initial_valid_source_pairs, initial_valid_switches = st.count_valid_checkerboard_switches(state)
    if initial_valid_switches < args.min_initial_valid_switches:
        raise RuntimeError(
            f"Fixed-margin fiber appears nearly rigid: initial checkerboard switches={initial_valid_switches}."
        )

    movable_scale = max(1, min(len(state.deleted), len(state.retained)))
    burnin_successes = max(args.min_burnin_switches, args.burnin_factor * movable_scale)
    thin_successes = max(args.min_thin_switches, args.thin_factor * movable_scale)
    rng = random.Random(args.seed)

    print(
        f"Fixed-margin switch chain: initial switches={initial_valid_switches}, "
        f"burn-in={burnin_successes} accepted switches, thinning={thin_successes}/draw"
    )

    b_attempts, b_accepted = st.advance_successful_switches(
        state, rng, burnin_successes, args.max_attempt_factor
    )
    if b_accepted < burnin_successes:
        raise RuntimeError(
            f"Could not complete burn-in: accepted {b_accepted}/{burnin_successes} "
            f"after {b_attempts} attempts."
        )

    null_rows: List[Dict[str, object]] = []
    validation_rows: List[Dict[str, object]] = []
    acceptance_rates: List[float] = []
    hamming_series: List[float] = []

    for draw in range(args.draws):
        attempts, accepted = st.advance_successful_switches(
            state, rng, thin_successes, args.max_attempt_factor
        )
        if accepted < thin_successes:
            raise RuntimeError(
                f"Chain stalled at draw={draw}: accepted {accepted}/{thin_successes} after {attempts} attempts."
            )

        sampled_deleted = set(state.deleted)
        out_mm, in_mm, degree_delta = st.validate_degrees(nodes, observed_deleted, sampled_deleted)
        if out_mm or in_mm or degree_delta:
            raise AssertionError(
                f"Degree constraint violation at draw={draw}: out={out_mm}, in={in_mm}, delta={degree_delta}"
            )

        sampled_retained = set(all_graph.edges) - sampled_deleted
        candidate = mrd._make_graph(nodes, sampled_retained)
        damage = mrd.graph_metrics(candidate, all_graph, baseline_stats)
        ho = higher_order_metrics(nodes, node_index, all_out, all_in, sampled_deleted)

        hamming = len(observed_deleted.symmetric_difference(sampled_deleted)) / len(all_graph.edges)
        accept_rate = accepted / attempts if attempts else 0.0
        acceptance_rates.append(accept_rate)
        hamming_series.append(hamming)

        row: Dict[str, object] = {
            "site_id": site,
            "draw": draw,
            "null_model": "source_target_deleted_degree_matched_switch_chain",
            "acceptance_rate_since_previous_draw": accept_rate,
            "hamming_label_fraction_from_observed": hamming,
            **ho,
        }
        for metric in DAMAGE_METRICS:
            row[metric] = damage[metric]
        null_rows.append(row)

        validation_rows.append({
            "site_id": site,
            "draw": draw,
            "out_degree_mismatch_nodes": out_mm,
            "in_degree_mismatch_nodes": in_mm,
            "total_degree_abs_delta": degree_delta,
            "constraint_pass": out_mm == 0 and in_mm == 0 and degree_delta == 0,
        })

        if (draw + 1) % max(1, args.progress_every) == 0 or draw + 1 == args.draws:
            print(
                f"  draws {draw + 1}/{args.draws} | "
                f"accept={statistics.fmean(acceptance_rates[-min(20, len(acceptance_rates)):]):.4f} | "
                f"hamming={hamming:.3f}"
            )

    write_csv(output_dir / "fixed_margin_null_draws.csv", null_rows)
    write_csv(output_dir / "degree_constraint_validation.csv", validation_rows)

    # ------------------------------------------------------------------
    # Null comparison for joint deletion organization
    # ------------------------------------------------------------------
    comparison_rows: List[Dict[str, object]] = []
    metric_directions = dict(PRIMARY_METRIC_DIRECTIONS)
    metric_directions.update(SECONDARY_METRIC_DIRECTIONS)

    for metric, direction in metric_directions.items():
        observed = finite_float(observed_ho.get(metric))
        vals = [finite_float(r.get(metric)) for r in null_rows]
        vals2 = [v for v in vals if v is not None]
        if observed is None or not vals2:
            continue
        summary = one_sided_summary(observed, vals2, direction)
        comparison_rows.append({
            "site_id": site,
            "metric": metric,
            "role": "primary" if metric in PRIMARY_METRIC_DIRECTIONS else "secondary",
            "pre_specified_direction": direction,
            **summary,
        })

    primary_indices = [i for i, r in enumerate(comparison_rows) if r["role"] == "primary"]
    primary_p = [float(comparison_rows[i]["one_sided_empirical_p"]) for i in primary_indices]
    primary_q = bh_fdr(primary_p)
    for i, q in zip(primary_indices, primary_q):
        comparison_rows[i]["q_primary_bh"] = q
    for i, row in enumerate(comparison_rows):
        if "q_primary_bh" not in row:
            row["q_primary_bh"] = ""

    write_csv(output_dir / "higher_order_null_comparison.csv", comparison_rows)

    # Compact primary-hypothesis table.
    core_rows = [
        {
            "site_id": r["site_id"],
            "metric": r["metric"],
            "direction": r["pre_specified_direction"],
            "observed": r["observed"],
            "null_mean": r["null_mean"],
            "z_score": r["z_score"],
            "one_sided_empirical_p": r["one_sided_empirical_p"],
            "q_primary_bh": r["q_primary_bh"],
            "observed_percentile": r["observed_percentile"],
        }
        for r in comparison_rows if r["role"] == "primary"
    ]
    write_csv(output_dir / "core_hypotheses.csv", core_rows)

    # ------------------------------------------------------------------
    # Within-null association: does joint deletion organization predict damage?
    # ------------------------------------------------------------------
    assoc_metrics = [
        "weighted_twohop_survival_fraction",
        "weighted_twohop_block_efficiency",
        "weighted_twohop_exactly_one_hit_fraction",
        "weighted_twohop_double_hit_fraction",
        "deleted_transitive_triad_count",
        "deleted_k22_count",
        "mean_source_pair_deleted_target_jaccard",
    ]
    association_rows: List[Dict[str, object]] = []
    for x_metric in assoc_metrics:
        xs = [finite_float(r.get(x_metric)) for r in null_rows]
        for y_metric in ("mean_path_stretch", "fraction_delta_d_ge2"):
            ys = [finite_float(r.get(y_metric)) for r in null_rows]
            pairs = [(x, y) for x, y in zip(xs, ys) if x is not None and y is not None]
            if len(pairs) < 3:
                continue
            xv = [p[0] for p in pairs]
            yv = [p[1] for p in pairs]
            association_rows.append({
                "site_id": site,
                "x_metric": x_metric,
                "y_metric": y_metric,
                "n_draws": len(pairs),
                "spearman_rho": spearman(xv, yv),
                "pearson_r": pearson(xv, yv),
            })
    write_csv(output_dir / "damage_association.csv", association_rows)

    # Chain diagnostics, including the primary joint-organization metrics.
    chain_row: Dict[str, object] = {
        "site_id": site,
        "status": "ok",
        "seed": args.seed,
        "initial_valid_source_pairs": initial_valid_source_pairs,
        "initial_valid_checkerboard_switches": initial_valid_switches,
        "burnin_target_accepted_switches": burnin_successes,
        "burnin_proposal_attempts": b_attempts,
        "burnin_accepted_switches": b_accepted,
        "burnin_acceptance_rate": b_accepted / b_attempts if b_attempts else 0.0,
        "thin_target_accepted_switches_per_draw": thin_successes,
        "mean_draw_acceptance_rate": statistics.fmean(acceptance_rates),
        "mean_hamming_label_fraction_from_observed": statistics.fmean(hamming_series),
        "min_hamming_label_fraction_from_observed": min(hamming_series),
        "max_hamming_label_fraction_from_observed": max(hamming_series),
    }
    for metric in list(PRIMARY_METRIC_DIRECTIONS) + ["mean_path_stretch", "fraction_delta_d_ge2"]:
        vals = [finite_float(r.get(metric)) for r in null_rows]
        vals2 = [v for v in vals if v is not None]
        chain_row[f"lag1_autocorr_{metric}"] = lag1(vals2)
    write_csv(output_dir / "chain_diagnostics.csv", [chain_row])

    # Metadata.
    metadata = {
        "analysis": "mdn_joint_deletion_organization_legacy_path",
        "site": site,
        "input": str(input_path),
        "output": str(output_dir),
        "draws": args.draws,
        "seed": args.seed,
        "null_model": "source_target_deleted_degree_matched_switch_chain",
        "burnin_factor": args.burnin_factor,
        "thin_factor": args.thin_factor,
        "scientific_unit": "site-level deletion labeling over rendered_all support",
        "primary_mechanism": (
            "coverage-efficient coordinated disruption of two-hop replacement paths: "
            "fixed-margin deletions may spread across detours so that many alternatives are "
            "hit at least once rather than redundantly double-hitting the same detour"
        ),
        "primary_metrics": list(PRIMARY_METRIC_DIRECTIONS.keys()),
        "secondary_metrics": list(SECONDARY_METRIC_DIRECTIONS.keys()),
        "guardrail": (
            "Significance establishes joint deletion organization beyond fixed in/out deletion degrees; "
            "it does not uniquely identify causality."
        ),
    }
    (output_dir / "analysis_metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")


    print("\nMDN joint deletion organization analysis complete.")
    print(f"Output: {output_dir.resolve()}")
    print("\nCore tests:")
    for row in core_rows:
        print(
            f"  {row['metric']:<45s} obs={float(row['observed']):.6g} "
            f"null={float(row['null_mean']):.6g} z={float(row['z_score']):.2f} "
            f"p={float(row['one_sided_empirical_p']):.4f} "
            f"q={float(row['q_primary_bh']):.4f}"
        )


# -----------------------------------------------------------------------------
# Figures
# -----------------------------------------------------------------------------



# -----------------------------------------------------------------------------
# CLI
# -----------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", default=str(DEFAULT_INPUT))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--site", default=DEFAULT_SITE)
    parser.add_argument("--draws", type=int, default=1000)
    parser.add_argument("--seed", type=int, default=20260930)
    parser.add_argument(
        "--burnin-factor", type=int, default=20,
        help="accepted switch burn-in multiplier on min(deleted, retained) edge count",
    )
    parser.add_argument(
        "--thin-factor", type=int, default=2,
        help="accepted switches between saved draws, multiplier on min(deleted, retained)",
    )
    parser.add_argument("--min-burnin-switches", type=int, default=2000)
    parser.add_argument("--min-thin-switches", type=int, default=500)
    parser.add_argument("--min-initial-valid-switches", type=int, default=10)
    parser.add_argument("--max-attempt-factor", type=int, default=500)
    parser.add_argument("--progress-every", type=int, default=100)
    return parser


if __name__ == "__main__":
    run(build_parser().parse_args())
