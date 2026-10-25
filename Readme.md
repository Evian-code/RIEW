# Representation-Induced Effective Webs

Code and frozen webpage data for the network analysis and two Agent navigation experiments.

The formal Agent experiment has 18 tasks, two conditions (`rendered_all` and `rendered_aria_followable`), and five runs per task and condition (180 planned runs). The information-equivalent navigation control has nine tasks, two conditions, and five runs per task and condition (90 planned runs).

## Files

- `Code/Network/analysis/` contains the navigation-graph and constrained randomization scripts.
- `Code/Agent/src/` contains the webpage snapshot loader, navigation environment, Agent runner, and experiment scripts.
- `Code/Agent/config/` and `Code/Agent/docs/` contain both experiments' original configurations, task suites, prompts, freeze records, and run manifests.
- `Code/Agent/data/runs/` contains the frozen rendered pages used in the Agent experiments.
- `Data/LinkRecords/` contains the original per-page link observations, compressed as JSONL.gz.
- `Data/SiteGraphs/` contains the page node IDs and the directed edge lists for the three websites and three exposure rules.

## Setup

Install the Python dependencies from `requirements.txt`. The Node.js experiments use `Code/Agent/package-lock.json`.

To inspect the frozen run plans without calling the model:

```bash
cd Code/Agent
npm ci
npm run plan:formal
npm run plan:control
```

The original execution commands are `npm run run:formal` and `npm run run:control`. They require access to the configured model API. Both also run pre-execution integrity checks tied to the original unredacted files, so these checks will not pass unchanged on the anonymized copy. The original prompt is in `config/agent_system_prompt.txt`; task-specific prompts are in the two experiment files under `docs/`.

To recompute website topology from the collected links, decompress `Data/LinkRecords/per_page_link_records.jsonl.gz` and supply the JSONL path to the topology analysis script with `--input`. The link records and `Data/SiteGraphs/` are provided separately because they correspond to different processing stages. The set of pages in the Agent snapshots need not match the topology-analysis node sets.

The pre-run freeze records are preserved from the original experiments. The publicly released HTML has redacted contact emails, and this repository has been trimmed for review, so the historical file fingerprints are not expected to match the release byte for byte. The frozen run manifests and task definitions have not been regenerated. Re-running the LLM may not reproduce the same answers and trajectories.

Third-party webpage content is subject to source-specific rights and attribution; see `THIRD_PARTY_NOTICES.md`.
