#!/usr/bin/env python3
"""Multi-representation topology audit for RIEW.

This analysis extends the existing site-level topology audit without changing the
frozen ARIA analysis.  It compares three task-independent link interfaces that
were already captured from the same live browser page state:

  - rendered_all: all usable rendered anchors (reference/full link-list support)
  - rendered_visible_followable: rendered anchors passing the frozen CSS/geometry
    visibility + usable-href + non-disabled heuristic
  - rendered_aria_followable: links exposed by Playwright getByRole("link") with
    a usable HTTP(S) href and not disabled/aria-disabled

Important: `rendered_visible_followable` is intentionally named a DOM-visible
heuristic, not "browser-actionable" or "executable".  The frozen extractor does
not test occlusion, pointer-event hit testing, or Playwright click actionability.

The loader intentionally mirrors the graph-universe and subset policy used by
`matched_random_deletion_null.py`, then validates that the ARIA metrics reproduce
`results/analysis/network_null/observed_site_metrics.csv`.  This guarantees that
adding the DOM-visible condition does not silently redefine the existing result.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Dict, Iterable, Mapping, Sequence, Set, Tuple

try:
    from analysis.network import matched_random_deletion_null as mrd
except ImportError:  # direct execution from project root
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import matched_random_deletion_null as mrd

Edge = Tuple[str, str]

DEFAULT_INPUT = Path("results/step0_5_interface_validation_v2_four_site/combined_per_page_link_records.jsonl")
DEFAULT_OBSERVED = Path("results/analysis/network_null/observed_site_metrics.csv")
DEFAULT_OUTPUT = Path("results/analysis/multi_representation_topology")
DEFAULT_SITES = ("mdn_js", "wikipedia", "usa_gov")

ALL_REP = "rendered_all"
VISIBLE_REP = "rendered_visible_followable"
ARIA_REP = "rendered_aria_followable"
REPRESENTATIONS = (ALL_REP, VISIBLE_REP, ARIA_REP)

REP_LABELS = {
    ALL_REP: "Rendered-all",
    VISIBLE_REP: "DOM-visible-followable",
    ARIA_REP: "ARIA-followable",
}

PRIMARY_METRICS = (
    "edge_retention_ratio",
    "reachability_preservation",
    "mean_path_stretch",
    "fraction_delta_d_ge2",
    "fraction_sp_reduced_equal_d",
)


def _write_csv(path: Path, rows: Sequence[Mapping[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields = []
    seen = set()
    for row in rows:
        for key in row:
            if key not in seen:
                seen.add(key)
                fields.append(key)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def _jaccard(a: Set[Edge], b: Set[Edge]) -> float:
    union = a | b
    return len(a & b) / len(union) if union else 1.0


def load_multi_graphs(
    path: Path,
    sites: Sequence[str],
    subset_policy: str = "intersect",
) -> tuple[Dict[str, Dict[str, mrd.Graph]], list[dict[str, object]]]:
    """Load the three frozen representations using the existing site-level semantics.

    The node universe is the set of source pages contributing at least one valid
    rendered-all edge, matching the existing site-level loader.  Restricted
    representations are projected onto rendered_all when an extraction mismatch
    creates an edge absent from the reference support; such exclusions are reported.
    """
    site_filter = set(sites)
    source_nodes: Dict[str, Set[str]] = defaultdict(set)
    edges: Dict[tuple[str, str], Set[Edge]] = defaultdict(set)

    with path.open("r", encoding="utf-8") as handle:
        for line_no, raw in enumerate(handle, 1):
            raw = raw.strip()
            if not raw:
                continue
            row = json.loads(raw)
            site = row.get("site_id")
            rep = row.get("representation")
            if site not in site_filter or rep not in REPRESENTATIONS:
                continue
            if row.get("graph_edge") is False:
                continue
            source = row.get("source_url") or row.get("source")
            target = row.get("absolute_url")
            if not source or not target:
                raise ValueError(f"Missing source/target at {path}:{line_no}")
            if rep == ALL_REP:
                source_nodes[site].add(source)
            edges[(site, rep)].add((source, target))

    out: Dict[str, Dict[str, mrd.Graph]] = {}
    exclusions: list[dict[str, object]] = []
    for site in sites:
        nodes = source_nodes.get(site, set())
        if not nodes:
            raise ValueError(f"No rendered-all source pages found for site={site}")
        all_graph = mrd._make_graph(nodes, edges.get((site, ALL_REP), set()))
        site_graphs = {ALL_REP: all_graph}
        for rep in (VISIBLE_REP, ARIA_REP):
            graph = mrd._make_graph(nodes, edges.get((site, rep), set()))
            extra = graph.edges - all_graph.edges
            if extra:
                for u, v in sorted(extra):
                    exclusions.append({
                        "site_id": site,
                        "representation": rep,
                        "source_url": u,
                        "target_url": v,
                        "reason": "restricted_edge_absent_from_rendered_all_support",
                    })
                if subset_policy == "error":
                    raise ValueError(
                        f"{rep} is not a deletion-only subgraph for {site}: {len(extra)} extra edges"
                    )
                if subset_policy != "intersect":
                    raise ValueError(f"Unknown subset_policy={subset_policy}")
                graph = mrd._make_graph(nodes, graph.edges & all_graph.edges)
            site_graphs[rep] = graph
        out[site] = site_graphs
    return out, exclusions


def validate_against_existing_aria(
    rows: Sequence[Mapping[str, object]],
    observed_path: Path,
    *,
    tolerance: float = 1e-10,
) -> list[dict[str, object]]:
    if not observed_path.exists():
        return [{"status": "SKIP", "reason": f"missing {observed_path}"}]
    expected_rows = {r["site_id"]: r for r in _read_csv(observed_path)}
    checks: list[dict[str, object]] = []
    for row in rows:
        if row["representation"] != ARIA_REP:
            continue
        site = str(row["site_id"])
        exp = expected_rows.get(site)
        if not exp:
            raise ValueError(f"Existing ARIA result missing site={site}")
        for metric in ("node_count", "edge_count", *PRIMARY_METRICS):
            actual = float(row[metric])
            expected = float(exp[metric])
            ok = math.isclose(actual, expected, rel_tol=0.0, abs_tol=tolerance)
            checks.append({
                "site_id": site,
                "metric": metric,
                "actual": actual,
                "existing_aria": expected,
                "absolute_difference": abs(actual - expected),
                "status": "PASS" if ok else "FAIL",
            })
            if not ok:
                raise AssertionError(
                    f"Multi-representation extension changed frozen ARIA result: "
                    f"{site}/{metric}: {actual} != {expected}"
                )
    return checks


def run(
    *,
    input_path: Path = DEFAULT_INPUT,
    observed_path: Path = DEFAULT_OBSERVED,
    output: Path = DEFAULT_OUTPUT,
    sites: Sequence[str] = DEFAULT_SITES,
) -> dict[str, object]:
    graphs, exclusions = load_multi_graphs(input_path, sites)
    metric_rows: list[dict[str, object]] = []
    overlap_rows: list[dict[str, object]] = []
    discrepancy_rows: list[dict[str, object]] = []

    for site in sites:
        all_graph = graphs[site][ALL_REP]
        baseline_stats = mrd.all_pairs_shortest_stats(all_graph)
        for rep in REPRESENTATIONS:
            g = graphs[site][rep]
            metrics = mrd.graph_metrics(g, all_graph, baseline_stats)
            metric_rows.append({
                "site_id": site,
                "representation": rep,
                "representation_label": REP_LABELS[rep],
                "reference_edge_count": len(all_graph.edges),
                **metrics,
            })

        for left, right in (
            (ALL_REP, VISIBLE_REP),
            (ALL_REP, ARIA_REP),
            (VISIBLE_REP, ARIA_REP),
        ):
            a, b = set(graphs[site][left].edges), set(graphs[site][right].edges)
            overlap_rows.append({
                "site_id": site,
                "representation_a": left,
                "representation_b": right,
                "edge_count_a": len(a),
                "edge_count_b": len(b),
                "intersection_edges": len(a & b),
                "union_edges": len(a | b),
                "edge_jaccard": _jaccard(a, b),
                "fraction_a_retained_in_b": len(a & b) / len(a) if a else 1.0,
                "fraction_b_retained_in_a": len(a & b) / len(b) if b else 1.0,
            })

        visible = set(graphs[site][VISIBLE_REP].edges)
        aria = set(graphs[site][ARIA_REP].edges)
        for label, edge_set in (
            ("dom_visible_only", visible - aria),
            ("aria_only_after_reference_projection", aria - visible),
        ):
            for u, v in sorted(edge_set)[:100]:
                discrepancy_rows.append({
                    "site_id": site,
                    "difference_type": label,
                    "source_url": u,
                    "target_url": v,
                })

    checks = validate_against_existing_aria(metric_rows, observed_path)
    output.mkdir(parents=True, exist_ok=True)
    _write_csv(output / "multi_representation_site_metrics.csv", metric_rows)
    _write_csv(output / "representation_overlap.csv", overlap_rows)
    _write_csv(output / "representation_discrepancy_examples.csv", discrepancy_rows)
    _write_csv(output / "reference_projection_exclusions.csv", exclusions)
    _write_csv(output / "aria_regression_check.csv", checks)

    metadata = {
        "schema_version": "1.0",
        "analysis": "multi_representation_topology",
        "input": str(input_path),
        "existing_aria_validation_source": str(observed_path),
        "sites": list(sites),
        "representations": {
            ALL_REP: "All usable rendered anchors; serves as reference/full link-list support.",
            VISIBLE_REP: "Rendered anchors passing the frozen CSS/geometry visibility, usable-href, and non-disabled heuristic. This is a DOM-visible heuristic, not full browser actionability.",
            ARIA_REP: "Links exposed by Playwright getByRole('link') with usable HTTP(S) href and non-disabled state.",
        },
        "primary_metrics_frozen_before_new_result_inspection": list(PRIMARY_METRICS),
        "node_universe": "Mirrors existing site-level matched-null loader: captured source pages contributing valid rendered-all graph edges.",
        "restricted_subset_policy": "Project restricted edges onto rendered_all support and report exclusions.",
        "task_information_used": False,
        "aria_regression_check": "PASS" if checks and all(c.get("status") == "PASS" for c in checks) else "SKIP",
    }
    (output / "analysis_metadata.json").write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    (output / "README.md").write_text(
        "# Multi-representation topology audit\n\n"
        "This directory compares the frozen rendered-all, DOM-visible-followable, and ARIA-followable link interfaces on the same site-level graph analysis semantics.\n\n"
        "The DOM-visible condition is the already-captured CSS/geometry heuristic from `src/extract.mjs`; it must not be described as full Playwright actionability.\n\n"
        "`aria_regression_check.csv` must remain all PASS. That check guarantees this extension reproduces the previously frozen ARIA site-level result before adding the DOM-visible comparison.\n",
        encoding="utf-8",
    )
    return {"output": str(output), "rows": len(metric_rows), "exclusions": len(exclusions), "aria_check": metadata["aria_regression_check"]}


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    p.add_argument("--observed-aria", type=Path, default=DEFAULT_OBSERVED)
    p.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    p.add_argument("--sites", default=",".join(DEFAULT_SITES))
    return p.parse_args()


if __name__ == "__main__":
    args = parse_args()
    sites = tuple(x.strip() for x in args.sites.split(",") if x.strip())
    print(json.dumps(run(input_path=args.input, observed_path=args.observed_aria, output=args.output, sites=sites), indent=2))
