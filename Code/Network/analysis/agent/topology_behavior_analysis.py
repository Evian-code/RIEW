#!/usr/bin/env python3
"""Unified task-level topology -> Agent behavior analysis for RIEW.

Inputs
------
- Frozen 18-task formal suite.
- 180-run formal ALL-vs-ARIA Agent analysis summary.
- 90-run information-equivalent navigation-control summary.
- Formal-task matched random-deletion null summary (optional but recommended).

Outputs are paper-analysis tables only. This script never modifies frozen runs.
It uses the task as the scientific unit; the five repetitions remain within-task
replicates and are summarized before topology-behavior associations are computed.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import random
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

DEFAULT_TASKS = Path("docs/agent_formal_task_suite_v1.json")
DEFAULT_FORMAL = Path("data/runs/agent/formal_aria_v1_attempt01/analysis_summary.json")
DEFAULT_CONTROL = Path(
    "data/runs/agent/information_equivalent_nav_control_v1_attempt01/task_level_summary.json"
)
DEFAULT_NULL = Path("results/analysis/network_null/task_null_comparison_summary.csv")
DEFAULT_NULL_METADATA = Path("results/analysis/network_null/task_analysis_metadata.json")
DEFAULT_OUTPUT = Path("results/analysis/agent")

FORMAL_CONTROL = "rendered_all"
FORMAL_TREATMENT = "rendered_aria_followable"
INFO_CONTROL = "information_all_unrestricted_navigation"
INFO_TREATMENT = "information_all_aria_restricted_navigation"

RUN_METRICS = (
    "grounded_success",
    "target_reached",
    "answer_correct",
    "steps_to_first_target",
    "total_link_steps",
    "tool_steps",
    "visited_pages",
    "unique_pages",
    "revisit_count",
    "token_usage",
    "navigation_excess",
    "budget_exhausted",
)


def _mean(values: Iterable[object]) -> Optional[float]:
    xs = [float(v) for v in values if isinstance(v, (int, float)) and math.isfinite(float(v))]
    return statistics.fmean(xs) if xs else None


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
        writer = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _load_tasks(path: Path) -> List[Dict[str, object]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, dict):
        raw = raw.get("tasks", raw.get("items", []))
    if not isinstance(raw, list):
        raise ValueError(f"Expected task list in {path}")
    required = {"task_id", "site", "structural_tier", "start_url", "target_url", "d_all", "d_aria", "delta_d"}
    out = []
    for item in raw:
        missing = required - set(item)
        if missing:
            raise ValueError(f"Task missing {sorted(missing)}: {item}")
        out.append(dict(item))
    return out


def _structural_row(task: Mapping[str, object]) -> Dict[str, object]:
    d_all = task.get("d_all")
    d_aria = task.get("d_aria")
    sp_all = task.get("shortest_path_count_rendered_all")
    sp_aria = task.get("shortest_path_count_rendered_aria")
    fh_all = task.get("first_hop_count_rendered_all")
    fh_aria = task.get("first_hop_count_rendered_aria")
    return {
        "task_id": task["task_id"],
        "site": task["site"],
        "structural_tier": task["structural_tier"],
        "start_url": task["start_url"],
        "target_url": task["target_url"],
        "d_all": d_all,
        "d_aria": d_aria,
        "delta_d": task.get("delta_d"),
        "path_stretch": (float(d_aria) / float(d_all)) if isinstance(d_all, (int, float)) and d_all else None,
        "sp_all": sp_all,
        "sp_aria": sp_aria,
        "sp_retention": (float(sp_aria) / float(sp_all)) if isinstance(sp_all, (int, float)) and sp_all else None,
        "sp_loss_fraction": (1.0 - min(1.0, float(sp_aria) / float(sp_all))) if isinstance(sp_all, (int, float)) and sp_all and isinstance(sp_aria, (int, float)) else None,
        "fh_all": fh_all,
        "fh_aria": fh_aria,
        "fh_retention": (float(fh_aria) / float(fh_all)) if isinstance(fh_all, (int, float)) and fh_all else None,
        "fh_loss_fraction": (1.0 - min(1.0, float(fh_aria) / float(fh_all))) if isinstance(fh_all, (int, float)) and fh_all and isinstance(fh_aria, (int, float)) else None,
    }


def _aggregate_formal(formal_path: Path, tasks: Sequence[Mapping[str, object]]) -> List[Dict[str, object]]:
    data = json.loads(formal_path.read_text(encoding="utf-8"))
    runs = data.get("run_metrics", [])
    groups: Dict[Tuple[str, str], List[Mapping[str, object]]] = defaultdict(list)
    for run in runs:
        groups[(str(run.get("task_id")), str(run.get("condition")))].append(run)

    rows = []
    for task in tasks:
        base = _structural_row(task)
        task_id = str(task["task_id"])
        condition_summaries: Dict[str, Dict[str, object]] = {}
        for condition in (FORMAL_CONTROL, FORMAL_TREATMENT):
            rs = groups.get((task_id, condition), [])
            if not rs:
                raise ValueError(f"Missing formal runs for {task_id} / {condition}")
            summary: Dict[str, object] = {"n": len(rs)}
            for metric in RUN_METRICS:
                summary[metric] = _mean(r.get(metric) for r in rs)
            condition_summaries[condition] = summary
            prefix = "all" if condition == FORMAL_CONTROL else "aria"
            base[f"{prefix}_n"] = len(rs)
            for metric in RUN_METRICS:
                base[f"{prefix}_{metric}"] = summary[metric]

        all_s = condition_summaries[FORMAL_CONTROL]
        aria_s = condition_summaries[FORMAL_TREATMENT]
        for metric in RUN_METRICS:
            a, b = aria_s.get(metric), all_s.get(metric)
            base[f"delta_{metric}"] = (float(a) - float(b)) if isinstance(a, (int, float)) and isinstance(b, (int, float)) else None
        rows.append(base)
    return rows


def _load_info_control(path: Path) -> List[Dict[str, object]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    tasks = data.get("tasks", []) if isinstance(data, dict) else data
    rows: List[Dict[str, object]] = []
    for task in tasks:
        row = {
            "task_id": task["task_id"],
            "site": task["site"],
            "structural_tier": task["structural_tier"],
            "d_all": task["d_all"],
            "d_aria": task["d_aria"],
            "delta_d": task["delta_d"],
            "sp_all": task.get("shortest_path_count_all"),
            "sp_aria": task.get("shortest_path_count_aria"),
            "fh_all": task.get("first_hop_count_all"),
            "fh_aria": task.get("first_hop_count_aria"),
        }
        for condition, prefix in ((INFO_CONTROL, "unrestricted"), (INFO_TREATMENT, "restricted")):
            s = task.get(condition, {})
            for key, value in s.items():
                if key == "termination_counts":
                    continue
                row[f"{prefix}_{key}"] = value
        diff = task.get("difference_restricted_minus_unrestricted") or task.get("difference_treatment_minus_control") or {}
        for key, value in diff.items():
            row[f"delta_{key}"] = value
        rows.append(row)
    return rows


def _load_task_null(
    path: Path, expected_task_ids: set[str], metadata_path: Path
) -> Tuple[Dict[Tuple[str, str], Dict[str, object]], List[str]]:
    warnings: List[str] = []
    if not path.exists():
        return {}, [f"Task-null file not found: {path}"]
    if not metadata_path.exists():
        return {}, [
            f"Task-null metadata not found: {metadata_path}. Refusing to merge because graph provenance cannot be verified."
        ]
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return {}, [f"Invalid task-null metadata JSON: {metadata_path}: {exc}"]
    if metadata.get("task_graph_source") != "formal_agent_graph_export":
        return {}, [
            "Task-null output was not generated from the formal Agent frozen graph. "
            "Run analysis/common/export_formal_agent_graphs.py and then matched_random_deletion_null.py --task-only."
        ]
    passed = metadata.get("formal_graph_validation_passed")
    expected = metadata.get("formal_graph_validation_expected")
    if passed != expected or passed != 18:
        return {}, [f"Formal graph validation in task-null metadata is not 18/18: {passed}/{expected}"]

    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        rows = [dict(r) for r in csv.DictReader(fh)]
    found = {r.get("task_id", "") for r in rows}
    if not expected_task_ids.issubset(found):
        missing = sorted(expected_task_ids - found)
        warnings.append(
            "Task-null output does not match the formal 18-task suite; rerun matched_random_deletion_null.py. "
            f"Missing formal tasks: {missing}"
        )
        return {}, warnings
    lookup = {(r["task_id"], r["null_model"]): r for r in rows}
    return lookup, warnings


def _merge_null(rows: List[Dict[str, object]], lookup: Mapping[Tuple[str, str], Mapping[str, object]]) -> None:
    numeric_fields = (
        "null_reachability_rate",
        "null_mean_distance_when_reachable",
        "null_median_distance_when_reachable",
        "null_mean_delta_d_when_reachable",
        "null_probability_delta_d_gt0_or_unreachable",
        "null_probability_delta_d_ge2_or_unreachable",
        "null_probability_sp_reduced_given_equal_distance",
        "null_probability_fh_reduced_given_equal_distance",
        "distance_or_unreachable_one_sided_p",
    )
    for row in rows:
        task_id = str(row["task_id"])
        for model, short in (("global_edge_count", "global"), ("nodewise_outdegree", "nodewise")):
            nrow = lookup.get((task_id, model))
            if not nrow:
                continue
            for field in numeric_fields:
                value = nrow.get(field)
                try:
                    row[f"null_{short}_{field}"] = float(value) if value not in (None, "") else None
                except (TypeError, ValueError):
                    row[f"null_{short}_{field}"] = value


def _rank(values: Sequence[float]) -> List[float]:
    indexed = sorted(enumerate(values), key=lambda x: x[1])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(indexed):
        j = i + 1
        while j < len(indexed) and indexed[j][1] == indexed[i][1]:
            j += 1
        avg = (i + 1 + j) / 2.0
        for k in range(i, j):
            ranks[indexed[k][0]] = avg
        i = j
    return ranks


def _pearson(x: Sequence[float], y: Sequence[float]) -> float:
    if len(x) != len(y) or len(x) < 2:
        return float("nan")
    mx, my = statistics.fmean(x), statistics.fmean(y)
    dx = [v - mx for v in x]
    dy = [v - my for v in y]
    den = math.sqrt(sum(v * v for v in dx) * sum(v * v for v in dy))
    if den == 0:
        return 0.0
    return sum(a * b for a, b in zip(dx, dy)) / den


def _spearman(x: Sequence[float], y: Sequence[float]) -> float:
    return _pearson(_rank(x), _rank(y))


def _association_rows(rows: Sequence[Mapping[str, object]], permutations: int, seed: int) -> List[Dict[str, object]]:
    predictors = ("delta_d", "path_stretch", "sp_loss_fraction", "fh_loss_fraction")
    outcomes = (
        "delta_tool_steps",
        "delta_total_link_steps",
        "delta_token_usage",
        "delta_revisit_count",
        "delta_target_reached",
        "delta_grounded_success",
    )
    out = []
    rng = random.Random(seed)
    for p in predictors:
        for yname in outcomes:
            pairs = [
                (float(r[p]), float(r[yname]))
                for r in rows
                if isinstance(r.get(p), (int, float)) and math.isfinite(float(r[p]))
                and isinstance(r.get(yname), (int, float)) and math.isfinite(float(r[yname]))
            ]
            if len(pairs) < 3:
                continue
            x = [a for a, _ in pairs]
            y = [b for _, b in pairs]
            rho = _spearman(x, y)
            extreme = 0
            for _ in range(permutations):
                yp = list(y)
                rng.shuffle(yp)
                if abs(_spearman(x, yp)) >= abs(rho) - 1e-15:
                    extreme += 1
            pval = (extreme + 1) / (permutations + 1)
            out.append({
                "predictor": p,
                "outcome": yname,
                "n_tasks": len(pairs),
                "spearman_rho": rho,
                "two_sided_permutation_p": pval,
                "permutations": permutations,
            })
    return out


def _group_summary(rows: Sequence[Mapping[str, object]], group_field: str) -> List[Dict[str, object]]:
    groups: Dict[str, List[Mapping[str, object]]] = defaultdict(list)
    for row in rows:
        groups[str(row.get(group_field))].append(row)
    metrics = (
        "delta_d",
        "delta_grounded_success",
        "delta_target_reached",
        "delta_steps_to_first_target",
        "delta_total_link_steps",
        "delta_tool_steps",
        "delta_unique_pages",
        "delta_revisit_count",
        "delta_token_usage",
    )
    out = []
    for group, rs in sorted(groups.items()):
        row: Dict[str, object] = {group_field: group, "n_tasks": len(rs)}
        for metric in metrics:
            row[f"mean_{metric}"] = _mean(r.get(metric) for r in rs)
        out.append(row)
    return out


def run(args: argparse.Namespace) -> None:
    tasks = _load_tasks(Path(args.tasks))
    task_ids = {str(t["task_id"]) for t in tasks}
    formal = _aggregate_formal(Path(args.formal), tasks)
    control = _load_info_control(Path(args.control))
    null_lookup, warnings = _load_task_null(Path(args.task_null), task_ids, Path(args.task_null_metadata))
    _merge_null(formal, null_lookup)

    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    _write_csv(output / "formal_18_task_topology_behavior_master.csv", formal)
    _write_csv(output / "formal_summary_by_structural_tier.csv", _group_summary(formal, "structural_tier"))
    _write_csv(output / "formal_summary_by_site.csv", _group_summary(formal, "site"))
    _write_csv(output / "formal_task_level_associations.csv", _association_rows(formal, args.permutations, args.seed))
    _write_csv(output / "information_equivalent_control_master.csv", control)

    metadata = {
        "schema_version": "1.0",
        "analysis": "formal_task_topology_behavior",
        "scientific_unit": "task",
        "formal_task_count": len(formal),
        "information_equivalent_control_task_count": len(control),
        "formal_tasks": str(args.tasks),
        "formal_agent_summary": str(args.formal),
        "information_equivalent_control_summary": str(args.control),
        "task_null_summary": str(args.task_null),
        "task_null_metadata": str(args.task_null_metadata),
        "formal_structural_source_of_truth": str(args.tasks),
        "task_null_merged": bool(null_lookup),
        "association_method": f"Spearman rank correlation with {args.permutations} two-sided label permutations; descriptive/exploratory because n=18 and predictors are correlated",
        "difference_direction": "ARIA minus ALL for formal experiment; Restricted minus Unrestricted for information-equivalent control",
        "warnings": warnings,
        "outputs": [
            "formal_18_task_topology_behavior_master.csv",
            "formal_summary_by_structural_tier.csv",
            "formal_summary_by_site.csv",
            "formal_task_level_associations.csv",
            "information_equivalent_control_master.csv",
        ],
    }
    (output / "analysis_metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    print(f"Topology-behavior analysis complete: {output}")
    print(f"  formal tasks: {len(formal)}")
    print(f"  information-equivalent control tasks: {len(control)}")
    if null_lookup:
        print("  formal task-level matched-null results: merged")
    else:
        print("  formal task-level matched-null results: NOT merged")
    for warning in warnings:
        print(f"WARNING: {warning}")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--tasks", default=str(DEFAULT_TASKS))
    p.add_argument("--formal", default=str(DEFAULT_FORMAL))
    p.add_argument("--control", default=str(DEFAULT_CONTROL))
    p.add_argument("--task-null", default=str(DEFAULT_NULL))
    p.add_argument("--task-null-metadata", default=str(DEFAULT_NULL_METADATA))
    p.add_argument("--output", default=str(DEFAULT_OUTPUT))
    p.add_argument("--permutations", type=int, default=10000)
    p.add_argument("--seed", type=int, default=20260928)
    return p


if __name__ == "__main__":
    run(build_parser().parse_args())
