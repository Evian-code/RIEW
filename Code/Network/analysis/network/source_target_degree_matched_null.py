#!/usr/bin/env python3
"""Source+target deletion-degree matched null for RIEW site-level Web graphs.

Purpose
-------
The existing Figure 3 null fixes, for every source page, how many outgoing edges
survive under the interface, but randomizes their destinations. The collective-effect
analysis then showed that real interface deletions are unusually concentrated on a
small set of destination pages. This script asks the next question:

    If we ALSO fix how many deleted incoming edges each target page receives,
    does the observed network-level damage remain unusual?

In other words, the null preserves BOTH margins of the observed deletion matrix:

    deleted out-degree of every source u
    deleted in-degree  of every target v

while randomizing which allowed rendered_all edges are the deleted ones.

Sampling method
---------------
The allowed support is the rendered_all edge set. We initialize at the observed
ARIA deletion set D_obs = E_all \\ E_aria and run a degree-preserving 2x2 switch
Markov chain on the binary deleted/retained labeling of the allowed edges.

A valid switch changes

    deleted:  (u1,v1), (u2,v2)
    retained: (u1,v2), (u2,v1)

to the opposite labeling. This preserves the deleted out-degree of u1/u2 and the
deleted in-degree of v1/v2 exactly, never creates an edge absent from rendered_all,
and preserves the total retained/deleted edge counts.

Because this is a Markov-chain null rather than exact independent uniform sampling,
the script writes mixing diagnostics (acceptance rate, Hamming distance from the
observed deletion set, lag-1 autocorrelation of key metrics). Use the defaults below
as a practical baseline; if diagnostics show poor movement, increase --burnin-factor
and --thin-factor.

Primary interpretation
----------------------
- If the observed damage is no longer extreme under this stricter null, destination
  concentration / target deletion-degree heterogeneity explains much of the earlier
  source-only null deviation.
- If the observed damage remains extreme, then fixing both source and target deletion
  degrees is insufficient; the specific joint organization of deleted edges
  matters.

This does NOT prove causality or identify a unique structural mechanism by itself.

Expected project location
-------------------------
Place this file at:
    analysis/network/source_target_degree_matched_null.py

Run from the project root:
    python analysis/network/source_target_degree_matched_null.py --draws 1000

Default inputs
--------------
    results/step0_5_interface_validation_v2_four_site/combined_per_page_link_records.jsonl
    results/analysis/network_null/null_draws_nodewise_outdegree.csv   (optional comparison)
    results/analysis/network_null/observed_site_metrics.csv            (optional validation)

Default output
--------------
    results/analysis/source_target_degree_matched_null/

Outputs
-------
    observed_site_metrics.csv
    source_target_degree_null_draws.csv
    source_target_degree_null_comparison.csv
    source_only_vs_source_target.csv
    degree_constraint_validation.csv
    chain_diagnostics.csv
    core_hypotheses.csv
    analysis_metadata.json

Dependencies
------------
- Python 3.9+

- Uses the existing sibling script analysis/network/matched_random_deletion_null.py
  for the exact Figure 3 graph loader and network metrics.
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
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, MutableMapping, Optional, Sequence, Set, Tuple

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

try:
    import matched_random_deletion_null as mrd
except ImportError as exc:  # pragma: no cover
    raise SystemExit(
        "Could not import sibling analysis/network/matched_random_deletion_null.py. "
        "Place this script in analysis/network/."
    ) from exc

Edge = Tuple[str, str]

DEFAULT_INPUT = Path(
    "results/step0_5_interface_validation_v2_four_site/combined_per_page_link_records.jsonl"
)
DEFAULT_SOURCE_ONLY_NULL = Path("results/analysis/network_null/null_draws_nodewise_outdegree.csv")
DEFAULT_SOURCE_ONLY_OBS = Path("results/analysis/network_null/observed_site_metrics.csv")
DEFAULT_OUTPUT = Path("results/analysis/source_target_degree_matched_null")
DEFAULT_SITES = ("mdn_js", "wikipedia", "usa_gov")

# Metric-specific distortion directions. Retention/preservation is worse when lower;
# loss, inflation, and reduction metrics are worse when higher.
CORE_METRICS: Mapping[str, Tuple[str, ...]] = {
    "mdn_js": ("mean_path_stretch", "fraction_delta_d_ge2"),
    "wikipedia": ("fraction_sp_reduced_equal_d", "fraction_fh_reduced_equal_d"),
    # USA.gov is retained as a supporting low-distortion comparison, not a primary hypothesis.
    "usa_gov": ("mean_path_stretch", "fraction_sp_reduced_equal_d"),
}

DAMAGE_METRICS = (
    "reachability_loss",
    "mean_path_stretch",
    "median_path_stretch",
    "p90_path_stretch",
    "mean_delta_d",
    "fraction_delta_d_gt0",
    "fraction_delta_d_ge2",
    "mean_sp_retention_equal_d",
    "fraction_sp_reduced_equal_d",
    "mean_fh_retention_equal_d",
    "fraction_fh_reduced_equal_d",
)

METRIC_DISTORTION_DIRECTIONS: Mapping[str, str] = {
    "reachability_loss": "higher",
    "mean_path_stretch": "higher",
    "median_path_stretch": "higher",
    "p90_path_stretch": "higher",
    "mean_delta_d": "higher",
    "fraction_delta_d_gt0": "higher",
    "fraction_delta_d_ge2": "higher",
    "mean_sp_retention_equal_d": "lower",
    "fraction_sp_reduced_equal_d": "higher",
    "mean_fh_retention_equal_d": "lower",
    "fraction_fh_reduced_equal_d": "higher",
}


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


def read_csv(path: Path) -> List[Dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


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
    mean = statistics.fmean(values)
    sd = statistics.stdev(values) if len(values) > 1 else 0.0
    return mean, sd


def pearson(x: Sequence[float], y: Sequence[float]) -> float:
    if len(x) != len(y) or len(x) < 2:
        return float("nan")
    mx, my = statistics.fmean(x), statistics.fmean(y)
    sx = sum((v - mx) ** 2 for v in x)
    sy = sum((v - my) ** 2 for v in y)
    if sx <= 0 or sy <= 0:
        return float("nan")
    return sum((a - mx) * (b - my) for a, b in zip(x, y)) / math.sqrt(sx * sy)


def lag1_autocorrelation(values: Sequence[float]) -> float:
    if len(values) < 3:
        return float("nan")
    return pearson(values[:-1], values[1:])


def bh_fdr(pvalues: Sequence[float]) -> List[float]:
    n = len(pvalues)
    if n == 0:
        return []
    order = sorted(range(n), key=lambda i: pvalues[i])
    qvals = [1.0] * n
    running = 1.0
    for reverse_rank, idx in enumerate(reversed(order), 1):
        rank = n - reverse_rank + 1
        candidate = pvalues[idx] * n / rank
        running = min(running, candidate)
        qvals[idx] = min(1.0, running)
    return qvals


def metric_summary(observed: float, null_values: Sequence[float], direction: str = "higher") -> Dict[str, float]:
    vals = [v for v in null_values if math.isfinite(v)]
    mean, sd = mean_sd(vals)
    if direction == "higher":
        extreme = sum(v >= observed for v in vals)
    elif direction == "lower":
        extreme = sum(v <= observed for v in vals)
    else:
        raise ValueError(f"Unknown metric direction: {direction}")
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
        "z_score": (observed - mean) / sd if sd > 0 else (0.0 if observed == mean else math.copysign(float("inf"), observed - mean)),
        "n_draws": float(len(vals)),
    }


# -----------------------------------------------------------------------------
# Exact degree-margin bookkeeping
# -----------------------------------------------------------------------------

def deletion_degrees(nodes: Sequence[str], deleted: Iterable[Edge]) -> Tuple[Dict[str, int], Dict[str, int]]:
    out_d = {u: 0 for u in nodes}
    in_d = {v: 0 for v in nodes}
    for u, v in deleted:
        out_d[u] += 1
        in_d[v] += 1
    return out_d, in_d


def target_concentration(nodes: Sequence[str], deleted: Set[Edge]) -> Dict[str, float]:
    counts = Counter(v for _, v in deleted)
    total = len(deleted)
    if total == 0:
        return {
            "deleted_unique_target_count": 0.0,
            "deleted_target_coverage_fraction": 0.0,
            "deleted_target_hhi": 0.0,
            "deleted_target_max_share": 0.0,
        }
    shares = [c / total for c in counts.values()]
    return {
        "deleted_unique_target_count": float(len(counts)),
        "deleted_target_coverage_fraction": len(counts) / len(nodes) if nodes else 0.0,
        "deleted_target_hhi": sum(s * s for s in shares),
        "deleted_target_max_share": max(shares),
    }


def hamming_fraction(a: Set[Edge], b: Set[Edge]) -> float:
    """Fraction of deletion labels changed relative to observed, normalized by |E_all| later."""
    return float(len(a.symmetric_difference(b)))


@dataclass
class SwitchChainState:
    all_edges: Set[Edge]
    nodes: Tuple[str, ...]
    deleted: Set[Edge]
    retained: Set[Edge]
    deleted_targets: Dict[str, Set[str]]
    retained_targets: Dict[str, Set[str]]
    attempts: int = 0
    accepted: int = 0


def _make_chain_state(all_edges: Set[Edge], deleted: Set[Edge], nodes: Sequence[str]) -> SwitchChainState:
    retained = set(all_edges) - set(deleted)
    deleted_targets: Dict[str, Set[str]] = {u: set() for u in nodes}
    retained_targets: Dict[str, Set[str]] = {u: set() for u in nodes}
    for u, v in deleted:
        deleted_targets[u].add(v)
    for u, v in retained:
        retained_targets[u].add(v)
    return SwitchChainState(
        all_edges=set(all_edges),
        nodes=tuple(nodes),
        deleted=set(deleted),
        retained=retained,
        deleted_targets=deleted_targets,
        retained_targets=retained_targets,
    )


def _flip_edge_label(state: SwitchChainState, edge: Edge, deleted_to_retained: bool) -> None:
    u, v = edge
    if deleted_to_retained:
        state.deleted.remove(edge)
        state.retained.add(edge)
        state.deleted_targets[u].remove(v)
        state.retained_targets[u].add(v)
    else:
        state.retained.remove(edge)
        state.deleted.add(edge)
        state.retained_targets[u].remove(v)
        state.deleted_targets[u].add(v)


def attempt_switch(state: SwitchChainState, rng: random.Random) -> bool:
    """Attempt one symmetric fixed-margin 2x2 switch.

    Pick two source rows uniformly. For those rows, define
        A = deleted in row1 AND retained in row2
        B = retained in row1 AND deleted in row2.
    Choosing v1 in A and v2 in B gives an immediately valid checkerboard switch.

    For the selected source pair, |A| and |B| are unchanged by the switch, so the
    forward and reverse proposal probabilities are equal. This is much more efficient
    than proposing arbitrary edge pairs when the deletion labeling is highly imbalanced.
    """
    state.attempts += 1
    if len(state.nodes) < 2:
        return False
    u1, u2 = rng.sample(state.nodes, 2)

    a = state.deleted_targets[u1] & state.retained_targets[u2]
    if not a:
        return False
    b = state.retained_targets[u1] & state.deleted_targets[u2]
    if not b:
        return False

    v1 = rng.choice(tuple(a))
    v2 = rng.choice(tuple(b))
    # A and B are disjoint by construction, hence v1 != v2.

    e11 = (u1, v1)  # D -> R
    e21 = (u2, v1)  # R -> D
    e12 = (u1, v2)  # R -> D
    e22 = (u2, v2)  # D -> R

    _flip_edge_label(state, e11, True)
    _flip_edge_label(state, e21, False)
    _flip_edge_label(state, e12, False)
    _flip_edge_label(state, e22, True)

    state.accepted += 1
    return True


def advance_successful_switches(
    state: SwitchChainState,
    rng: random.Random,
    target_successes: int,
    max_attempt_factor: int,
) -> Tuple[int, int]:
    """Advance until target_successes accepted switches or a generous attempt cap."""
    if target_successes <= 0:
        return 0, 0
    start_attempts, start_accepted = state.attempts, state.accepted
    max_attempts = max(10_000, target_successes * max_attempt_factor)
    for _ in range(max_attempts):
        attempt_switch(state, rng)
        if state.accepted - start_accepted >= target_successes:
            break
    return state.attempts - start_attempts, state.accepted - start_accepted


def validate_degrees(
    nodes: Sequence[str],
    observed_deleted: Set[Edge],
    sampled_deleted: Set[Edge],
) -> Tuple[int, int, int]:
    obs_out, obs_in = deletion_degrees(nodes, observed_deleted)
    smp_out, smp_in = deletion_degrees(nodes, sampled_deleted)
    out_mismatches = sum(obs_out[u] != smp_out[u] for u in nodes)
    in_mismatches = sum(obs_in[v] != smp_in[v] for v in nodes)
    total_delta = sum(abs(obs_out[u] - smp_out[u]) for u in nodes) + sum(abs(obs_in[v] - smp_in[v]) for v in nodes)
    return out_mismatches, in_mismatches, total_delta


def count_valid_checkerboard_switches(state: SwitchChainState) -> Tuple[int, int]:
    """Return (# source pairs with at least one switch, # concrete 2x2 switches)."""
    valid_pairs = 0
    total_switches = 0
    nodes = state.nodes
    for i, u1 in enumerate(nodes):
        for u2 in nodes[i + 1:]:
            a = state.deleted_targets[u1] & state.retained_targets[u2]
            if not a:
                continue
            b = state.retained_targets[u1] & state.deleted_targets[u2]
            if not b:
                continue
            valid_pairs += 1
            total_switches += len(a) * len(b)
    return valid_pairs, total_switches



# -----------------------------------------------------------------------------
# Existing Figure 3 source-only null comparison
# -----------------------------------------------------------------------------

def load_source_only_null(path: Path) -> Dict[Tuple[str, str], List[float]]:
    out: Dict[Tuple[str, str], List[float]] = defaultdict(list)
    for row in read_csv(path):
        if row.get("null_model") != "nodewise_outdegree":
            continue
        site = row.get("site_id", "")
        for metric in DAMAGE_METRICS:
            value = finite_float(row.get(metric))
            if value is not None:
                out[(site, metric)].append(value)
    return out


def load_saved_observed(path: Path) -> Dict[Tuple[str, str], float]:
    out: Dict[Tuple[str, str], float] = {}
    for row in read_csv(path):
        site = row.get("site_id", "")
        for metric in DAMAGE_METRICS:
            value = finite_float(row.get(metric))
            if value is not None:
                out[(site, metric)] = value
    return out


# -----------------------------------------------------------------------------
# Main analysis
# -----------------------------------------------------------------------------

def run(args: argparse.Namespace) -> None:
    input_path = Path(args.input)
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)
    sites = tuple(s.strip() for s in args.sites.split(",") if s.strip())

    graphs, aria_only = mrd.load_graphs(input_path, sites, subset_policy="intersect")
    write_csv(output_dir / "aria_only_edges_excluded.csv", aria_only)

    source_only_lookup = load_source_only_null(Path(args.source_only_null))
    saved_obs = load_saved_observed(Path(args.source_only_observed))

    all_observed_rows: List[Dict[str, object]] = []
    all_null_rows: List[Dict[str, object]] = []
    validation_rows: List[Dict[str, object]] = []
    chain_rows: List[Dict[str, object]] = []

    master_rng = random.Random(args.seed)

    for site_idx, site in enumerate(sites):
        all_graph = graphs[site][mrd.ALL_REP]
        aria_graph = graphs[site][mrd.ARIA_REP]
        observed_deleted = set(all_graph.edges - aria_graph.edges)
        observed_retained = set(aria_graph.edges)
        baseline_stats = mrd.all_pairs_shortest_stats(all_graph)
        obs_metrics = mrd.graph_metrics(aria_graph, all_graph, baseline_stats)
        obs_conc = target_concentration(all_graph.nodes, observed_deleted)

        print(
            f"{site}: nodes={len(all_graph.nodes)}, ALL={len(all_graph.edges)}, "
            f"ARIA={len(aria_graph.edges)}, deleted={len(observed_deleted)}"
        )

        # Cross-check against previously saved Figure 3 observed metrics when available.
        max_obs_abs_diff = 0.0
        compared_obs = 0
        for metric in DAMAGE_METRICS:
            prior = saved_obs.get((site, metric))
            current = finite_float(obs_metrics.get(metric))
            if prior is not None and current is not None:
                max_obs_abs_diff = max(max_obs_abs_diff, abs(prior - current))
                compared_obs += 1

        obs_row: Dict[str, object] = {
            "site_id": site,
            "node_count": len(all_graph.nodes),
            "all_edge_count": len(all_graph.edges),
            "aria_edge_count": len(aria_graph.edges),
            "deleted_edge_count": len(observed_deleted),
            "saved_figure3_observed_metrics_compared": compared_obs,
            "saved_figure3_observed_max_abs_diff": max_obs_abs_diff,
            **obs_conc,
        }
        for metric in DAMAGE_METRICS:
            obs_row[metric] = obs_metrics.get(metric)
        all_observed_rows.append(obs_row)

        # Initialize the fixed-margin Markov chain at the observed deletion labeling.
        state = _make_chain_state(set(all_graph.edges), observed_deleted, all_graph.nodes)
        site_seed = args.seed + 1_000_003 * (site_idx + 1)
        rng = random.Random(site_seed)
        initial_valid_source_pairs, initial_valid_switches = count_valid_checkerboard_switches(state)

        # If the exact source+target margins almost determine the deletion pattern,
        # a switch-chain null has too little state-space mobility for meaningful Monte
        # Carlo inference. Record this transparently and skip rather than fabricating
        # pseudo-replicates. USA.gov is expected to be close to this regime because it
        # has very few deleted edges concentrated on very few targets.
        if initial_valid_switches < args.min_initial_valid_switches:
            print(
                f"  {site}: strict margins are nearly rigid "
                f"(valid source pairs={initial_valid_source_pairs}, "
                f"initial checkerboard switches={initial_valid_switches}); skipping MCMC inference."
            )
            chain_rows.append({
                "site_id": site,
                "proposal_mode": "row_pair_checkerboard",
                "site_seed": site_seed,
                "status": "insufficient_fixed_margin_mobility",
                "initial_valid_source_pairs": initial_valid_source_pairs,
                "initial_valid_checkerboard_switches": initial_valid_switches,
                "min_required_initial_switches": args.min_initial_valid_switches,
            })
            continue

        # Scale mixing steps by the smaller label class: those are the edges whose
        # placements are hardest to randomize in sparse/dense regimes.
        movable_scale = max(1, min(len(state.deleted), len(state.retained)))
        burnin_successes = max(args.min_burnin_switches, args.burnin_factor * movable_scale)
        thin_successes = max(args.min_thin_switches, args.thin_factor * movable_scale)

        print(
            f"  switch chain mode={"row_pair_checkerboard"}, movable_scale={movable_scale}, "
            f"burn-in={burnin_successes} accepted switches, thinning={thin_successes}/draw"
        )

        b_attempts, b_accepted = advance_successful_switches(
            state, rng, burnin_successes, args.max_attempt_factor
        )
        if b_accepted < burnin_successes:
            raise RuntimeError(
                f"{site}: switch chain could not complete burn-in: "
                f"accepted {b_accepted}/{burnin_successes} after {b_attempts} attempts. "
                "The fixed-margin fiber may be poorly connected under this proposal."
            )

        initial_deleted = set(observed_deleted)
        draw_metric_series: Dict[str, List[float]] = defaultdict(list)
        draw_hamming: List[float] = []
        acceptance_rates: List[float] = []

        for draw in range(args.draws):
            attempts, accepted = advance_successful_switches(
                state, rng, thin_successes, args.max_attempt_factor
            )
            if accepted < thin_successes:
                raise RuntimeError(
                    f"{site}: switch chain stalled at draw={draw}: "
                    f"accepted {accepted}/{thin_successes} after {attempts} attempts."
                )

            sampled_deleted = set(state.deleted)
            sampled_retained = set(all_graph.edges) - sampled_deleted
            candidate = mrd._make_graph(all_graph.nodes, sampled_retained)
            metrics = mrd.graph_metrics(candidate, all_graph, baseline_stats)
            conc = target_concentration(all_graph.nodes, sampled_deleted)

            out_mm, in_mm, degree_delta = validate_degrees(
                all_graph.nodes, observed_deleted, sampled_deleted
            )
            if out_mm or in_mm or degree_delta:
                raise AssertionError(
                    f"{site} draw {draw}: fixed-margin violation: "
                    f"out_mm={out_mm}, in_mm={in_mm}, degree_delta={degree_delta}"
                )

            hamming_edges = hamming_fraction(initial_deleted, sampled_deleted)
            hamming_label_fraction = hamming_edges / len(all_graph.edges) if all_graph.edges else 0.0
            acceptance_rate = accepted / attempts if attempts else 0.0
            draw_hamming.append(hamming_label_fraction)
            acceptance_rates.append(acceptance_rate)

            row: Dict[str, object] = {
                "site_id": site,
                "null_model": "source_target_deleted_degree_matched_switch_chain",
                "draw": draw,
                "site_seed": site_seed,
                "proposal_mode": "row_pair_checkerboard",
                "accepted_switches_since_previous_draw": accepted,
                "proposal_attempts_since_previous_draw": attempts,
                "acceptance_rate_since_previous_draw": acceptance_rate,
                "hamming_label_fraction_from_observed": hamming_label_fraction,
                **conc,
            }
            for metric in DAMAGE_METRICS:
                row[metric] = metrics.get(metric)
                value = finite_float(metrics.get(metric))
                if value is not None:
                    draw_metric_series[metric].append(value)
            all_null_rows.append(row)

            validation_rows.append({
                "site_id": site,
                "draw": draw,
                "deleted_edge_count_observed": len(observed_deleted),
                "deleted_edge_count_sampled": len(sampled_deleted),
                "out_degree_mismatch_nodes": out_mm,
                "in_degree_mismatch_nodes": in_mm,
                "total_degree_abs_delta": degree_delta,
                "constraint_pass": out_mm == 0 and in_mm == 0 and degree_delta == 0,
            })

            if (draw + 1) % max(1, args.progress_every) == 0 or draw + 1 == args.draws:
                print(
                    f"  {site}: draws {draw + 1}/{args.draws} | "
                    f"accept={statistics.fmean(acceptance_rates[-min(20, len(acceptance_rates)):]):.4f} | "
                    f"hamming={hamming_label_fraction:.3f}"
                )

        chain_row: Dict[str, object] = {
            "site_id": site,
            "proposal_mode": "row_pair_checkerboard",
            "site_seed": site_seed,
            "status": "ok",
            "initial_valid_source_pairs": initial_valid_source_pairs,
            "initial_valid_checkerboard_switches": initial_valid_switches,
            "burnin_target_accepted_switches": burnin_successes,
            "burnin_proposal_attempts": b_attempts,
            "burnin_accepted_switches": b_accepted,
            "burnin_acceptance_rate": b_accepted / b_attempts if b_attempts else 0.0,
            "thin_target_accepted_switches_per_draw": thin_successes,
            "mean_draw_acceptance_rate": statistics.fmean(acceptance_rates) if acceptance_rates else float("nan"),
            "mean_hamming_label_fraction_from_observed": statistics.fmean(draw_hamming) if draw_hamming else float("nan"),
            "min_hamming_label_fraction_from_observed": min(draw_hamming) if draw_hamming else float("nan"),
            "max_hamming_label_fraction_from_observed": max(draw_hamming) if draw_hamming else float("nan"),
        }
        for metric in CORE_METRICS.get(site, ()):
            chain_row[f"lag1_autocorr_{metric}"] = lag1_autocorrelation(draw_metric_series.get(metric, []))
        chain_rows.append(chain_row)

    write_csv(output_dir / "observed_site_metrics.csv", all_observed_rows)
    write_csv(output_dir / "source_target_degree_null_draws.csv", all_null_rows)
    write_csv(output_dir / "degree_constraint_validation.csv", validation_rows)
    write_csv(output_dir / "chain_diagnostics.csv", chain_rows)

    # ------------------------------------------------------------------
    # Observed-vs-new-null comparison
    # ------------------------------------------------------------------
    observed_by_site = {str(r["site_id"]): r for r in all_observed_rows}
    null_by_site: Dict[str, List[Mapping[str, object]]] = defaultdict(list)
    for row in all_null_rows:
        null_by_site[str(row["site_id"])].append(row)

    comparison_rows: List[Dict[str, object]] = []
    for site in sites:
        obs = observed_by_site[site]
        draws = null_by_site[site]
        for metric in DAMAGE_METRICS:
            observed = finite_float(obs.get(metric))
            vals = [finite_float(r.get(metric)) for r in draws]
            vals2 = [v for v in vals if v is not None]
            if observed is None or not vals2:
                continue
            direction = METRIC_DISTORTION_DIRECTIONS[metric]
            summary = metric_summary(observed, vals2, direction)
            comparison_rows.append({
                "site_id": site,
                "null_model": "source_target_deleted_degree_matched_switch_chain",
                "metric": metric,
                "distortion_direction": f"{direction}_is_more_distorted",
                **summary,
            })
    write_csv(output_dir / "source_target_degree_null_comparison.csv", comparison_rows)

    # ------------------------------------------------------------------
    # Compare source-only vs stricter source+target null.
    # This is descriptive attenuation, not a formal causal decomposition.
    # ------------------------------------------------------------------
    st_lookup = {(str(r["site_id"]), str(r["metric"])): r for r in comparison_rows}
    source_vs_target_rows: List[Dict[str, object]] = []
    for site in sites:
        for metric in DAMAGE_METRICS:
            observed = finite_float(observed_by_site[site].get(metric))
            source_vals = source_only_lookup.get((site, metric), [])
            st_row = st_lookup.get((site, metric))
            if observed is None or not source_vals or not st_row:
                continue
            source_mean = statistics.fmean(source_vals)
            source_sd = statistics.stdev(source_vals) if len(source_vals) > 1 else 0.0
            direction = METRIC_DISTORTION_DIRECTIONS[metric]
            source_extreme = sum(v >= observed for v in source_vals) if direction == "higher" else sum(v <= observed for v in source_vals)
            source_p = (source_extreme + 1) / (len(source_vals) + 1)
            target_mean = float(st_row["null_mean"])
            target_p = float(st_row["one_sided_empirical_p"])
            old_excess = observed - source_mean
            new_excess = observed - target_mean
            attenuation = (
                1.0 - new_excess / old_excess
                if old_excess > 0 and math.isfinite(old_excess) and math.isfinite(new_excess)
                else float("nan")
            )
            source_vs_target_rows.append({
                "site_id": site,
                "metric": metric,
                "observed": observed,
                "source_only_null_mean": source_mean,
                "source_only_null_sd": source_sd,
                "source_only_one_sided_p": source_p,
                "source_target_null_mean": target_mean,
                "source_target_null_sd": st_row["null_sd"],
                "source_target_one_sided_p": target_p,
                "observed_minus_source_only_mean": old_excess,
                "observed_minus_source_target_mean": new_excess,
                "excess_attenuation_fraction_descriptive": attenuation,
                "interpretation_note": (
                    "attenuation toward 1 means fixing target deletion degrees moves the null toward the observed damage; "
                    "values near 0 mean little attenuation; negative values mean the stricter null moves away from observed."
                ),
            })
    write_csv(output_dir / "source_only_vs_source_target.csv", source_vs_target_rows)

    # ------------------------------------------------------------------
    # Pre-specified core hypothesis summary + BH FDR across primary MDN/Wiki tests.
    # USA.gov remains supporting/descriptive.
    # ------------------------------------------------------------------
    core_rows: List[Dict[str, object]] = []
    primary_p: List[float] = []
    primary_indices: List[int] = []
    source_target_lookup = {(str(r["site_id"]), str(r["metric"])): r for r in comparison_rows}
    svt_lookup = {(str(r["site_id"]), str(r["metric"])): r for r in source_vs_target_rows}

    for site in sites:
        for metric in CORE_METRICS.get(site, ()):
            row = source_target_lookup.get((site, metric))
            if not row:
                continue
            svt = svt_lookup.get((site, metric), {})
            p = float(row["one_sided_empirical_p"])
            core = {
                "site_id": site,
                "metric": metric,
                "observed": row["observed"],
                "source_target_null_mean": row["null_mean"],
                "source_target_observed_percentile": row["observed_percentile"],
                "source_target_one_sided_p": p,
                "source_only_null_mean": svt.get("source_only_null_mean", ""),
                "source_only_one_sided_p": svt.get("source_only_one_sided_p", ""),
                "excess_attenuation_fraction_descriptive": svt.get("excess_attenuation_fraction_descriptive", ""),
                "role": "primary" if site in {"mdn_js", "wikipedia"} else "supporting",
                "decision_rule": (
                    "If the source+target-matched p is no longer small and the null mean approaches observed, "
                    "target concentration/deletion in-degree heterogeneity explains much of the source-only anomaly. "
                    "If p remains small, both degree margins are insufficient and joint deletion organization remains implicated."
                ),
            }
            core_rows.append(core)
            if site in {"mdn_js", "wikipedia"}:
                primary_indices.append(len(core_rows) - 1)
                primary_p.append(p)

    qvals = bh_fdr(primary_p)
    for idx, q in zip(primary_indices, qvals):
        core_rows[idx]["q_core_primary_BH"] = q
    write_csv(output_dir / "core_hypotheses.csv", core_rows)

    # ------------------------------------------------------------------

    # ------------------------------------------------------------------

    metadata = {
        "analysis": "source_target_deleted_degree_matched_null",
        "input": str(input_path),
        "output": str(output_dir),
        "sites": list(sites),
        "draws": args.draws,
        "seed": args.seed,
        "sampling": {
            "method": "2x2 degree-preserving switch Markov chain on deleted/retained labels over rendered_all support",
            "preserved_exactly": [
                "rendered_all support",
                "deleted out-degree of every source",
                "deleted in-degree of every target",
                "total deleted edge count",
                "total retained edge count",
            ],
            "burnin_factor": args.burnin_factor,
            "thin_factor": args.thin_factor,
            "min_burnin_switches": args.min_burnin_switches,
            "min_thin_switches": args.min_thin_switches,
            "max_attempt_factor": args.max_attempt_factor,
            "min_initial_valid_switches": args.min_initial_valid_switches,
        },
        "important_limitations": [
            "This is an MCMC switch-chain null, not exact independent uniform sampling from the fixed-margin fiber.",
            "Interpret chain diagnostics before relying on p-values; poor movement/autocorrelation calls for larger burn-in/thinning.",
            "The null fixes source and target deletion degree but not semantic/DOM attributes or joint edge arrangement.",
            "Attenuation from source-only to source+target null is descriptive, not a causal variance decomposition.",
        ],
    }
    (output_dir / "analysis_metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\nSource+target deletion-degree matched analysis complete.")
    print(f"Output: {output_dir.resolve()}")
    print("\nCore tests:")
    for row in core_rows:
        q = row.get("q_core_primary_BH", "")
        attenuation = row.get("excess_attenuation_fraction_descriptive", "")
        print(
            f"  {row['site_id']:11s} | {row['metric']:34s} | "
            f"obs={float(row['observed']):.6g} null={float(row['source_target_null_mean']):.6g} "
            f"p={float(row['source_target_one_sided_p']):.4f} "
            f"q={q if q == '' else f'{float(q):.4f}'} "
            f"atten={attenuation if attenuation == '' else f'{float(attenuation):.3f}'}"
        )




def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", default=str(DEFAULT_INPUT))
    parser.add_argument("--source-only-null", default=str(DEFAULT_SOURCE_ONLY_NULL))
    parser.add_argument("--source-only-observed", default=str(DEFAULT_SOURCE_ONLY_OBS))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--sites", default=",".join(DEFAULT_SITES))
    parser.add_argument("--draws", type=int, default=1000)
    parser.add_argument("--seed", type=int, default=20260929)
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
    parser.add_argument(
        "--min-initial-valid-switches", type=int, default=10,
        help="skip MCMC inference when the exact fixed-margin fiber is nearly rigid at initialization",
    )
    parser.add_argument(
        "--max-attempt-factor", type=int, default=500,
        help="maximum proposal attempts per requested accepted switch before declaring chain stall",
    )
    parser.add_argument("--progress-every", type=int, default=100)
    return parser


if __name__ == "__main__":
    run(build_parser().parse_args())
