import { readFile, writeFile } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { buildAgentRunPlan } from './agent_batch_runner.mjs';
import { readSimpleYaml } from './config.mjs';
import { loadAgentTaskPrompts } from './agent_prompt_loader.mjs';
import { loadEvaluatorTaskIndex } from './agent_task_evaluator.mjs';

const sha256 = text => createHash('sha256').update(String(text).replace(/\r\n/g, '\n')).digest('hex');

function countBy(values) {
  const counts = {};
  for (const value of values) counts[value] = (counts[value] || 0) + 1;
  return counts;
}

export async function prepareFormalExperiment({ root = process.cwd(), configPath = 'config/agent_formal_aria_v1.yaml', write = false } = {}) {
  const config = await readSimpleYaml(resolve(root, configPath));
  const [prompts, evaluator, evaluatorText] = await Promise.all([
    loadAgentTaskPrompts(resolve(root, config.runner_task_file)),
    loadEvaluatorTaskIndex({ root, configPath }),
    readFile(resolve(root, config.evaluator_task_file), 'utf8')
  ]);
  const plan = buildAgentRunPlan({
    batchId: config.batch_id,
    tasks: prompts,
    conditions: config.conditions,
    repetitions: config.repetitions
  });
  const siteCounts = countBy(evaluator.tasks.map(task => task.site));
  const tierCounts = countBy(evaluator.tasks.map(task => task.structural_tier));
  const taskManifest = {
    schema_version: '1.0',
    suite_id: 'agent-formal-aria-v1',
    status: 'FROZEN',
    supervisor_confirmation_date: '2026-09-28',
    snapshot_ref: config.interface_snapshot_output_dir,
    task_count: evaluator.tasks.length,
    per_site_count: siteCounts,
    structural_tier_count: tierCounts,
    conditions: config.conditions,
    repetitions_per_task_condition: config.repetitions,
    trajectory_count: plan.length,
    evaluator_task_file: config.evaluator_task_file,
    evaluator_task_file_sha256: sha256(evaluatorText),
    tasks: evaluator.tasks.map(task => ({
      task_id: task.task_id,
      site: task.site,
      structural_tier: task.structural_tier,
      start_url: task.start_url,
      target_url: task.target_url,
      d_all: task.d_all,
      d_aria: task.d_aria,
      delta_d: task.delta_d,
      shortest_path_count_rendered_all: task.shortest_path_count_rendered_all,
      shortest_path_count_rendered_aria: task.shortest_path_count_rendered_aria,
      first_hop_count_rendered_all: task.first_hop_count_rendered_all,
      first_hop_count_rendered_aria: task.first_hop_count_rendered_aria
    }))
  };
  const runManifest = {
    schema_version: '1.0',
    experiment_kind: config.experiment_kind,
    suite_id: taskManifest.suite_id,
    batch_id: config.batch_id,
    config_path: configPath,
    provider: config.provider,
    region: config.region,
    model: config.model,
    model_version: config.model_version,
    thinking_mode: config.thinking_mode,
    temperature: config.temperature,
    max_tool_steps: config.max_tool_steps,
    token_budget: config.token_budget,
    conditions: config.conditions,
    repetitions: config.repetitions,
    task_count: prompts.length,
    run_count: plan.length,
    runs: plan
  };
  if (write) {
    await Promise.all([
      writeFile(resolve(root, config.task_suite_manifest_file), `${JSON.stringify(taskManifest, null, 2)}\n`, { flag: 'wx' }),
      writeFile(resolve(root, config.run_manifest_file), `${JSON.stringify(runManifest, null, 2)}\n`, { flag: 'wx' })
    ]);
  }
  return Object.freeze({ task_manifest: taskManifest, run_manifest: runManifest, wrote_files: write });
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  const result = await prepareFormalExperiment({ write: process.argv.includes('--write') });
  console.log(JSON.stringify({
    task_count: result.task_manifest.task_count,
    structural_tier_count: result.task_manifest.structural_tier_count,
    run_count: result.run_manifest.run_count,
    wrote_files: result.wrote_files
  }, null, 2));
}
