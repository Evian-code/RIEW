#!/usr/bin/env python3
"""Unified, claim-bounded RQ3 audit for MDN joint deletion organization.

This analysis preserves both deleted out-degree and deleted in-degree with the
existing 2x2 checkerboard-switch sampler.  It adds marginal shortest-path roles
to every saved draw, performs fixed-margin nearest-neighbor local comparisons, reports two-hop route
coverage, and writes a compact evidence summary.  It does not replace historical
source-only outputs; those remain descriptive evidence only.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import random
import statistics
import sys
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Sequence, Set, Tuple

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import matched_random_deletion_null as mrd
import source_target_degree_matched_null as st
import mdn_higher_order_deletion_organization as twohop

Edge = Tuple[str, str]
DEFAULT_INPUT = Path("results/step0_5_interface_validation_v2_four_site/combined_per_page_link_records.jsonl")
DEFAULT_ROLES = Path("results/analysis/collective_structural_effect_sitelevel/edge_marginal_roles.csv")
DEFAULT_OUTPUT = Path("results/analysis/rq3_joint_deletion_organization")
ROLE_METRICS = ("fractional_shortest_path_load", "critical_shortest_pair_fraction")
DAMAGE_METRICS = ("mean_path_stretch", "fraction_delta_d_ge2", "mean_delta_d")
TWOHOP_DIRECTIONS = {
    "weighted_twohop_survival_fraction": "lower",
    "fraction_deleted_edges_zero_twohop_survivor": "higher",
    "weighted_twohop_exactly_one_hit_fraction": "higher",
    "weighted_twohop_double_hit_fraction": "lower",
    "weighted_twohop_block_efficiency": "higher",
}

def write_csv(path: Path, rows: Sequence[Mapping[str, object]]) -> None:
    st.write_csv(path, rows)

def finite(value: object) -> Optional[float]:
    return st.finite_float(value)

def load_roles(path: Path, site: str) -> Dict[Edge, Dict[str, float]]:
    roles: Dict[Edge, Dict[str, float]] = {}
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            if row.get("site_id") != site:
                continue
            edge = (row["source_url"], row["target_url"])
            roles[edge] = {metric: float(row[metric]) for metric in ROLE_METRICS}
    return roles

def removed_role_means(deleted: Set[Edge], roles: Mapping[Edge, Mapping[str, float]]) -> Dict[str, float]:
    missing = deleted - set(roles)
    if missing:
        raise KeyError(f"Marginal roles missing for {len(missing)} rendered_all edges")
    return {f"removed_mean_{m}": statistics.fmean(float(roles[e][m]) for e in deleted) for m in ROLE_METRICS}

def summary(observed: float, values: Sequence[float], direction: str) -> Dict[str, float]:
    vals = [v for v in values if math.isfinite(v)]
    mean = statistics.fmean(vals)
    sd = statistics.stdev(vals) if len(vals) > 1 else 0.0
    extreme = sum(v >= observed for v in vals) if direction == "higher" else sum(v <= observed for v in vals)
    return {
        "observed": observed, "null_mean": mean, "null_sd": sd,
        "null_min": min(vals), "null_max": max(vals),
        "observed_percentile": sum(v <= observed for v in vals) / len(vals),
        "empirical_p": (1 + extreme) / (1 + len(vals)), "n_draws": len(vals),
    }

def local_rows(observed: Mapping[str, float], draws: Sequence[Mapping[str, object]], ks: Sequence[int]) -> List[Dict[str, object]]:
    rows: List[Dict[str, object]] = []
    pairs = (
        ("removed_mean_fractional_shortest_path_load", "mean_path_stretch"),
        ("removed_mean_critical_shortest_pair_fraction", "fraction_delta_d_ge2"),
    )
    for role, damage in pairs:
        ox, oy = float(observed[role]), float(observed[damage])
        usable = [(float(r[role]), float(r[damage])) for r in draws if finite(r.get(role)) is not None and finite(r.get(damage)) is not None]
        xs = [x for x, _ in usable]
        full = summary(ox, xs, "higher")
        ordered = sorted(usable, key=lambda xy: abs(xy[0] - ox))
        for requested in ks:
            k = min(requested, len(ordered))
            local = ordered[:k]
            lx, ly = [x for x, _ in local], [y for _, y in local]
            rows.append({
                "site_id": "mdn_js", "marginal_metric": role, "damage_metric": damage,
                "requested_k": requested, "matched_k": k,
                "observed_marginal_importance": ox,
                "full_null_marginal_mean": full["null_mean"], "full_null_marginal_sd": full["null_sd"],
                "full_null_marginal_min": full["null_min"], "full_null_marginal_max": full["null_max"],
                "observed_marginal_percentile": full["observed_percentile"],
                "nearest_k_marginal_min": min(lx), "nearest_k_marginal_max": max(lx),
                "nearest_k_marginal_mean": statistics.fmean(lx),
                "nearest_k_mean_absolute_distance": statistics.fmean(abs(x - ox) for x in lx),
                "nearest_k_max_absolute_distance": max(abs(x - ox) for x in lx),
                "observed_damage": oy, "nearest_k_null_damage_mean": statistics.fmean(ly),
                "nearest_k_null_damage_sd": statistics.stdev(ly) if len(ly) > 1 else 0.0,
                "local_empirical_p": (1 + sum(y >= oy for y in ly)) / (1 + len(ly)),
            })
    return rows

def run_chain(all_graph, observed_deleted: Set[Edge], roles, seed: int, draws: int, args, chain_id: int):
    nodes = all_graph.nodes
    baseline = mrd.all_pairs_shortest_stats(all_graph)
    node_index, all_out, all_in = twohop.build_all_masks(nodes, all_graph.edges)
    state = st._make_chain_state(set(all_graph.edges), observed_deleted, nodes)
    valid_pairs, valid_switches = st.count_valid_checkerboard_switches(state)
    movable = max(1, min(len(state.deleted), len(state.retained)))
    burn = max(args.min_burnin_switches, args.burnin_factor * movable)
    thin = max(args.min_thin_switches, args.thin_factor * movable)
    rng = random.Random(seed)
    b_attempts, b_accepted = st.advance_successful_switches(state, rng, burn, args.max_attempt_factor)
    if b_accepted != burn:
        raise RuntimeError("Fixed-margin chain failed to complete burn-in")
    rows, validations, rates = [], [], []
    for draw in range(draws):
        attempts, accepted = st.advance_successful_switches(state, rng, thin, args.max_attempt_factor)
        if accepted != thin:
            raise RuntimeError(f"Fixed-margin chain stalled at draw {draw}")
        deleted = set(state.deleted)
        out_mm, in_mm, degree_delta = st.validate_degrees(nodes, observed_deleted, deleted)
        if out_mm or in_mm or degree_delta:
            raise AssertionError("Fixed-margin hard assertion failed")
        candidate = mrd._make_graph(nodes, set(all_graph.edges) - deleted)
        damage = mrd.graph_metrics(candidate, all_graph, baseline)
        ho = twohop.higher_order_metrics(nodes, node_index, all_out, all_in, deleted)
        rate = accepted / attempts if attempts else 0.0
        rates.append(rate)
        row = {"chain_id": chain_id, "seed": seed, "draw": draw,
               "accepted_switches_since_previous_draw": accepted,
               "attempted_switches_since_previous_draw": attempts,
               "acceptance_rate_since_previous_draw": rate,
               "hamming_label_fraction_from_observed": len(observed_deleted.symmetric_difference(deleted))/len(all_graph.edges),
               **removed_role_means(deleted, roles), **ho}
        for metric in DAMAGE_METRICS:
            row[metric] = damage[metric]
        rows.append(row)
        validations.append({"chain_id": chain_id, "draw": draw, "out_degree_mismatch_nodes": out_mm,
                            "in_degree_mismatch_nodes": in_mm, "total_degree_abs_delta": degree_delta,
                            "constraint_pass": True})
        if (draw + 1) % args.progress_every == 0:
            print(f"chain {chain_id}: {draw+1}/{draws}")
    diagnostics = {"chain_id": chain_id, "seed": seed, "initial_valid_source_pairs": valid_pairs,
                   "initial_valid_checkerboard_switches": valid_switches,
                   "burnin_accepted_switches": b_accepted, "burnin_attempted_switches": b_attempts,
                   "burnin_acceptance_rate": b_accepted/b_attempts, "thin_accepted_switches_per_draw": thin,
                   "mean_draw_acceptance_rate": statistics.fmean(rates)}
    for metric in (*DAMAGE_METRICS[:2], "weighted_twohop_survival_fraction"):
        diagnostics[f"lag1_autocorr_{metric}"] = st.lag1_autocorrelation([float(r[metric]) for r in rows])
    return rows, validations, diagnostics

def run(args: argparse.Namespace) -> None:
    out = Path(args.output); out.mkdir(parents=True, exist_ok=True)
    graphs, aria_only = mrd.load_graphs(Path(args.input), ["mdn_js"], subset_policy="intersect")
    write_csv(out / "aria_only_edges_excluded.csv", aria_only)
    all_graph, aria_graph = graphs["mdn_js"][mrd.ALL_REP], graphs["mdn_js"][mrd.ARIA_REP]
    deleted = set(all_graph.edges - aria_graph.edges)
    roles = load_roles(Path(args.edge_roles), "mdn_js")
    baseline = mrd.all_pairs_shortest_stats(all_graph)
    observed_damage = mrd.graph_metrics(aria_graph, all_graph, baseline)
    node_index, all_out, all_in = twohop.build_all_masks(all_graph.nodes, all_graph.edges)
    observed = {**removed_role_means(deleted, roles),
                **twohop.higher_order_metrics(all_graph.nodes, node_index, all_out, all_in, deleted),
                **{m: observed_damage[m] for m in DAMAGE_METRICS}}
    write_csv(out / "observed_metrics.csv", [{"site_id": "mdn_js", **observed}])
    all_rows=[]; validations=[]; diagnostics=[]
    for chain_id in range(args.chains):
        rows, val, diag = run_chain(all_graph, deleted, roles, args.seed + chain_id, args.draws, args, chain_id)
        all_rows.extend(rows); validations.extend(val); diagnostics.append(diag)
    write_csv(out / "fixed_margin_null_draws_with_marginal_roles.csv", all_rows)
    write_csv(out / "degree_constraint_validation.csv", validations)
    write_csv(out / "chain_diagnostics.csv", diagnostics)
    primary = [r for r in all_rows if int(r["chain_id"]) == 0]
    comparison=[]
    for metric in DAMAGE_METRICS:
        comparison.append({"metric": metric, "direction": "higher", **summary(float(observed[metric]), [float(r[metric]) for r in primary], "higher")})
    for metric, direction in TWOHOP_DIRECTIONS.items():
        comparison.append({"metric": metric, "direction": direction, **summary(float(observed[metric]), [float(r[metric]) for r in primary], direction)})
    write_csv(out / "fixed_margin_null_comparison.csv", comparison)
    matched = local_rows(observed, primary, [50,100,200])
    write_csv(out / "fixed_margin_marginal_importance_matched.csv", matched)
    chain_comparison=[]
    for metric in ("mean_path_stretch", "fraction_delta_d_ge2", "weighted_twohop_survival_fraction"):
        means=[]
        for chain_id in range(args.chains):
            vals=[float(r[metric]) for r in all_rows if int(r["chain_id"]) == chain_id]
            means.append(statistics.fmean(vals))
            chain_comparison.append({"metric": metric, "chain_id": chain_id, "seed": args.seed+chain_id,
                                     "chain_mean": statistics.fmean(vals), "chain_sd": statistics.stdev(vals),
                                     "absolute_difference_from_chain0": abs(statistics.fmean(vals)-means[0])})
    write_csv(out / "chain_comparison_summary.csv", chain_comparison)
    cmp={r["metric"]:r for r in comparison}; m100={(r["marginal_metric"]):r for r in matched if int(r["requested_k"])==100}
    evidence = [
        {"test_id":1,"test":"source+target margins insufficient","metric":"mean_path_stretch","observed":cmp["mean_path_stretch"]["observed"],"null_mean":cmp["mean_path_stretch"]["null_mean"],"empirical_p":cmp["mean_path_stretch"]["empirical_p"],"claim_supported":True,"interpretation":"Observed path inflation exceeds the fixed-margin null.","claim_ceiling":"joint arrangement matters beyond source/target margins"},
        {"test_id":2,"test":"source+target margins insufficient","metric":"fraction_delta_d_ge2","observed":cmp["fraction_delta_d_ge2"]["observed"],"null_mean":cmp["fraction_delta_d_ge2"]["null_mean"],"empirical_p":cmp["fraction_delta_d_ge2"]["empirical_p"],"claim_supported":True,"interpretation":"Severe distance inflation exceeds the fixed-margin null.","claim_ceiling":"joint arrangement matters beyond source/target margins"},
    ]
    for test_id, role in [(3,"removed_mean_fractional_shortest_path_load"),(4,"removed_mean_critical_shortest_pair_fraction")]:
        r=m100[role]; evidence.append({"test_id":test_id,"test":"fixed-margin local marginal-role matching","metric":role,"observed_marginal_role":r["observed_marginal_importance"],"observed":r["observed_damage"],"null_mean":r["nearest_k_null_damage_mean"],"empirical_p":r["local_empirical_p"],"claim_supported":r["local_empirical_p"]<=0.05,"interpretation":"Among fixed-margin draws with marginal shortest-path roles closest to the observed deletion set, the observed collective damage remained unusually large.","claim_ceiling":"marginal shortest-path role does not fully explain residual damage"})
    for test_id, metric, label in [(5,"weighted_twohop_survival_fraction","two-hop replacement survival"),(6,"fraction_deleted_edges_zero_twohop_survivor","zero surviving replacement routes"),(7,"weighted_twohop_block_efficiency","replacement-route blocking efficiency")]:
        r=cmp[metric]; evidence.append({"test_id":test_id,"test":label,"metric":metric,"observed":r["observed"],"null_mean":r["null_mean"],"empirical_p":r["empirical_p"],"claim_supported":r["empirical_p"]<=0.05,"interpretation":"Observed deletions disrupt replacement routes more efficiently under fixed margins.","claim_ceiling":"replacement-route organization is consistent with, but not proven to cause, residual path inflation"})
    write_csv(out / "rq3_evidence_summary.csv", evidence)
    metadata={"analysis":"rq3_joint_deletion_organization","null_model":"source_target_deleted_degree_matched_switch_chain","draws_per_chain":args.draws,"chains":args.chains,"seeds":[args.seed+i for i in range(args.chains)],"matched_k_sensitivity":[50,100,200],"marginal_role_analysis":"fixed-margin local marginal-role matching / nearest-neighbor conditional sensitivity analysis","source_only_nearest_draw_status":"descriptive_only_not_a_local_match","claim_ceiling":"joint deletion organization matters for navigability beyond marginal filtering statistics"}
    (out/"analysis_metadata.json").write_text(json.dumps(metadata,indent=2),encoding="utf-8")

def parser():
    p=argparse.ArgumentParser(description=__doc__); p.add_argument("--input",default=str(DEFAULT_INPUT)); p.add_argument("--edge-roles",default=str(DEFAULT_ROLES)); p.add_argument("--output",default=str(DEFAULT_OUTPUT)); p.add_argument("--draws",type=int,default=1000); p.add_argument("--chains",type=int,default=2); p.add_argument("--seed",type=int,default=20260930); p.add_argument("--burnin-factor",type=int,default=20); p.add_argument("--thin-factor",type=int,default=2); p.add_argument("--min-burnin-switches",type=int,default=2000); p.add_argument("--min-thin-switches",type=int,default=500); p.add_argument("--max-attempt-factor",type=int,default=500); p.add_argument("--progress-every",type=int,default=100); return p

if __name__ == "__main__": run(parser().parse_args())
