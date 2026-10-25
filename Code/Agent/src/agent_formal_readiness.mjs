import { access, readFile, writeFile } from 'node:fs/promises';
import { join, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { readSimpleYaml } from './config.mjs';
import { loadAgentTaskPrompts } from './agent_prompt_loader.mjs';
import { loadEvaluatorTaskIndex } from './agent_task_evaluator.mjs';
import { buildFormalTaskSuite } from './build_formal_task_suite.mjs';
import { prepareFormalExperiment } from './prepare_agent_formal.mjs';
import { directoryFingerprint, fileFingerprint, findOfficialOutcomeArtifacts } from './agent_prerun_freeze.mjs';

const EXPECTED_PROTOCOL = Object.freeze({
  provider: 'alibaba_model_studio',
  region: 'cn-beijing',
  api_protocol: 'openai_chat_completions',
  api_base_url: 'https://dashscope.aliyuncs.com/compatible-mode/v1',
  model: 'qwen3.7-plus-2026-05-26',
  model_version: 'qwen3.7-plus-2026-05-26',
  thinking_mode: 'disabled',
  temperature: 0,
  max_tool_steps: 10,
  token_budget: 100000,
  timeout_ms: 120000,
  answer_format: 'plain_text',
  conditions: ['rendered_all', 'rendered_aria_followable'],
  repetitions: 5,
  external_search: false,
  url_guessing: false
});

const EXPECTED_TIER_COUNTS = Object.freeze({
  delta_0_stable: 3,
  delta_0_redundancy_reduced: 3,
  delta_plus_1: 6,
  delta_plus_2: 6
});

const EXPECTED_SITE_COUNTS = Object.freeze({ mdn_js: 7, usa_gov: 5, wikipedia: 6 });

const FROZEN_IMPLEMENTATION_FILES = Object.freeze([
  'src/agent_runner.mjs',
  'src/agent_environment.mjs',
  'src/agent_executor.mjs',
  'src/agent_run_log.mjs',
  'src/agent_trajectory_metrics.mjs',
  'src/agent_task_evaluator.mjs',
  'src/agent_prompt_loader.mjs',
  'src/agent_batch_runner.mjs',
  'src/agent_result_writer.mjs',
  'src/analyze_agent_runs.mjs',
  'src/frozen_snapshot_loader.mjs',
  'src/providers/configured_agent_provider.mjs',
  'src/providers/alibaba_model_studio_provider.mjs',
  'src/providers/openai_compatible_agent_provider.mjs',
  'src/local_env.mjs',
  'src/run_agent_formal.mjs',
  'src/agent_formal_readiness.mjs',
  'src/config.mjs',
  'src/normalize.mjs',
  'src/node_identity.mjs'
]);

const exists = async path => { try { await access(path); return true; } catch { return false; } };
const same = (left, right) => JSON.stringify(left) === JSON.stringify(right);

function countBy(values) {
  const counts = {};
  for (const value of values) counts[value] = (counts[value] || 0) + 1;
  return counts;
}

async function fingerprints(root, paths) {
  return Promise.all(paths.map(path => fileFingerprint(root, path)));
}

export async function auditFormalReadiness({
  root = process.cwd(),
  configPath = 'config/agent_formal_aria_v1.yaml',
  createFreeze = false,
  writeReport = false
} = {}) {
  const config = await readSimpleYaml(resolve(root, configPath));
  const failures = [];
  const checks = [];
  const check = (name, passed, detail) => {
    checks.push({ name, passed, detail });
    if (!passed) failures.push({ name, detail });
  };

  const [prompts, evaluator, rebuilt, prepared, pilotConfig, outcomeArtifacts] = await Promise.all([
    loadAgentTaskPrompts(resolve(root, config.runner_task_file)),
    loadEvaluatorTaskIndex({ root, configPath }),
    buildFormalTaskSuite({ root, write: false }),
    prepareFormalExperiment({ root, configPath, write: false }),
    readSimpleYaml(resolve(root, 'config/agent_pilot_aria.yaml')),
    findOfficialOutcomeArtifacts(root, config.agent_run_output_dir)
  ]);
  const taskManifest = JSON.parse(await readFile(resolve(root, config.task_suite_manifest_file), 'utf8'));
  const runManifest = JSON.parse(await readFile(resolve(root, config.run_manifest_file), 'utf8'));

  const evaluatorIds = evaluator.tasks.map(task => task.task_id);
  const promptIds = prompts.map(task => task.task_id);
  check('formal_task_count_is_18', evaluator.tasks.length === 18 && prompts.length === 18, { evaluator: evaluator.tasks.length, prompts: prompts.length });
  check('task_ids_unique_and_matched', new Set(evaluatorIds).size === 18 && same(evaluatorIds, promptIds), { evaluator_ids: evaluatorIds, prompt_ids: promptIds });
  check('structural_tier_counts_match_confirmed_suite', same(countBy(evaluator.tasks.map(task => task.structural_tier)), EXPECTED_TIER_COUNTS), countBy(evaluator.tasks.map(task => task.structural_tier)));
  check('site_counts_match_confirmed_suite', same(countBy(evaluator.tasks.map(task => task.site)), EXPECTED_SITE_COUNTS), countBy(evaluator.tasks.map(task => task.site)));
  check('suite_rebuild_matches_frozen_snapshot', same(evaluator.tasks, rebuilt.tasks), 'All endpoints, answers, paths, d, SP, and FH values match the configured frozen snapshot.');
  check('task_manifest_matches_suite', same(taskManifest, prepared.task_manifest), { suite_id: taskManifest.suite_id, task_count: taskManifest.task_count });
  check('run_manifest_has_exact_180_run_plan', same(runManifest, prepared.run_manifest) && runManifest.run_count === 180, { run_count: runManifest.run_count });

  const perTaskRuns = countBy(runManifest.runs.map(run => run.task_id));
  const perConditionRuns = countBy(runManifest.runs.map(run => run.condition));
  const repetitionCoverage = evaluatorIds.every(taskId => config.conditions.every(condition => {
    const repetitions = runManifest.runs.filter(run => run.task_id === taskId && run.condition === condition).map(run => run.repetition).sort((a, b) => a - b);
    return same(repetitions, [1, 2, 3, 4, 5]);
  }));
  check('each_task_has_10_runs', Object.values(perTaskRuns).length === 18 && Object.values(perTaskRuns).every(count => count === 10), perTaskRuns);
  check('each_condition_has_90_runs', same(perConditionRuns, { rendered_all: 90, rendered_aria_followable: 90 }), perConditionRuns);
  check('each_task_condition_has_repetitions_1_to_5', repetitionCoverage, repetitionCoverage);

  const protocolActual = Object.fromEntries(Object.keys(EXPECTED_PROTOCOL).map(key => [key, config[key]]));
  check('model_and_protocol_are_locked', same(protocolActual, EXPECTED_PROTOCOL), { expected: EXPECTED_PROTOCOL, actual: protocolActual });
  const unchangedFields = ['provider', 'region', 'api_protocol', 'api_base_url', 'model', 'model_version', 'thinking_mode', 'temperature', 'max_tool_steps', 'token_budget', 'timeout_ms', 'answer_format', 'system_prompt_file', 'conditions', 'robustness_condition', 'sites', 'external_search', 'url_guessing', 'frozen_snapshots'];
  const drift = unchangedFields.filter(field => !same(config[field], pilotConfig[field]));
  check('pilot_prompt_tool_budget_and_snapshot_protocol_unchanged', drift.length === 0, { compared_fields: unchangedFields, drift });
  check('formal_output_directory_is_separate_and_empty', config.agent_run_output_dir !== pilotConfig.agent_run_output_dir && outcomeArtifacts.length === 0, { formal_output_dir: config.agent_run_output_dir, pilot_output_dir: pilotConfig.agent_run_output_dir, artifacts: outcomeArtifacts });
  check('formal_config_does_not_reference_old_task_files', ![config.runner_task_file, config.evaluator_task_file, config.task_suite_manifest_file, config.run_manifest_file].some(path => /final_polished|aria_validated|25/i.test(path)), { files: [config.runner_task_file, config.evaluator_task_file, config.task_suite_manifest_file, config.run_manifest_file] });

  const canonicalPaths = [
    configPath,
    config.system_prompt_file,
    config.runner_task_file,
    config.evaluator_task_file,
    config.task_suite_manifest_file,
    config.run_manifest_file,
    'package.json',
    'pnpm-lock.yaml',
    ...FROZEN_IMPLEMENTATION_FILES
  ];
  const canonicalFiles = await fingerprints(root, canonicalPaths);
  check('all_canonical_files_present', canonicalFiles.every(file => file.status === 'PRESENT'), canonicalFiles.filter(file => file.status !== 'PRESENT'));

  const snapshotFingerprints = Object.fromEntries(await Promise.all(config.sites.map(async site => {
    const path = join(config.frozen_snapshots[site], site).replaceAll('\\', '/');
    return [site, await directoryFingerprint(root, path)];
  })));

  const freezePath = resolve(root, config.formal_freeze_file);
  let freeze = null;
  if (await exists(freezePath)) freeze = JSON.parse(await readFile(freezePath, 'utf8'));
  if (createFreeze) {
    check('formal_freeze_does_not_already_exist', freeze === null, config.formal_freeze_file);
    if (failures.length === 0) {
      freeze = {
        schema_version: '1.0',
        status: 'FROZEN_READY_FOR_FORMAL_RUN',
        suite_id: taskManifest.suite_id,
        generated_at: new Date().toISOString(),
        config_path: configPath,
        protocol: protocolActual,
        task_count: 18,
        run_count: 180,
        canonical_files: canonicalFiles,
        web_snapshots: snapshotFingerprints
      };
      await writeFile(freezePath, `${JSON.stringify(freeze, null, 2)}\n`, { flag: 'wx' });
    }
  }

  check('formal_freeze_present', freeze !== null, config.formal_freeze_file);
  if (freeze) {
    const frozenFiles = new Map((freeze.canonical_files || []).map(file => [file.path, file]));
    const fileMismatches = canonicalFiles.filter(actual => {
      const frozen = frozenFiles.get(actual.path);
      return !frozen || frozen.status !== actual.status || frozen.sha256 !== actual.sha256;
    });
    const snapshotMismatches = Object.entries(snapshotFingerprints).filter(([site, actual]) => {
      const frozen = freeze.web_snapshots?.[site];
      return !frozen || frozen.file_count !== actual.file_count || frozen.aggregate_sha256 !== actual.aggregate_sha256;
    });
    check('formal_freeze_matches_canonical_files', freeze.status === 'FROZEN_READY_FOR_FORMAL_RUN' && fileMismatches.length === 0, fileMismatches);
    check('formal_freeze_matches_web_snapshots', snapshotMismatches.length === 0, snapshotMismatches);
  }

  const report = {
    schema_version: '1.0',
    status: failures.length === 0 ? 'READY_FOR_FORMAL_RUN' : 'NOT_READY',
    audited_at: new Date().toISOString(),
    api_or_model_calls_made: false,
    config_path: configPath,
    task_count: evaluator.tasks.length,
    run_count: runManifest.run_count,
    checks,
    failures
  };
  if (writeReport) await writeFile(resolve(root, config.readiness_report_file), `${JSON.stringify(report, null, 2)}\n`);
  if (failures.length) throw new Error(`Formal readiness audit failed: ${failures.map(failure => failure.name).join(', ')}`);
  return report;
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  const report = await auditFormalReadiness({ createFreeze: process.argv.includes('--create-freeze'), writeReport: true });
  console.log(JSON.stringify({ status: report.status, task_count: report.task_count, run_count: report.run_count, api_or_model_calls_made: report.api_or_model_calls_made }, null, 2));
}
