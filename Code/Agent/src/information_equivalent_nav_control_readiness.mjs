import { access, readFile, writeFile } from 'node:fs/promises';
import { join, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { readSimpleYaml } from './config.mjs';
import { loadAgentTaskPrompts } from './agent_prompt_loader.mjs';
import { loadEvaluatorTaskIndex } from './agent_task_evaluator.mjs';
import { buildInformationEquivalentNavControl, CONTROL_TASK_IDS } from './build_information_equivalent_nav_control.mjs';
import { prepareInformationEquivalentNavControl } from './prepare_information_equivalent_nav_control.mjs';
import { directoryFingerprint, fileFingerprint, findOfficialOutcomeArtifacts } from './agent_prerun_freeze.mjs';

const EXPECTED_CONDITIONS = Object.freeze([
  'information_all_unrestricted_navigation',
  'information_all_aria_restricted_navigation'
]);
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
  trajectory_schema_version: '1.2',
  conditions: EXPECTED_CONDITIONS,
  repetitions: 5,
  external_search: false,
  url_guessing: false
});
const FORMAL_SHARED_FIELDS = Object.freeze([
  'provider', 'region', 'api_protocol', 'api_base_url', 'model', 'model_version',
  'thinking_mode', 'temperature', 'max_tool_steps', 'token_budget', 'timeout_ms',
  'answer_format', 'system_prompt_file', 'sites', 'external_search', 'url_guessing',
  'frozen_snapshots', 'interface_snapshot_output_dir'
]);
const CANONICAL_IMPLEMENTATION_FILES = Object.freeze([
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
  'src/build_information_equivalent_nav_control.mjs',
  'src/prepare_information_equivalent_nav_control.mjs',
  'src/information_equivalent_nav_control_readiness.mjs',
  'src/smoke_information_equivalent_nav_control.mjs',
  'src/run_information_equivalent_nav_control.mjs',
  'src/config.mjs',
  'src/normalize.mjs',
  'src/node_identity.mjs'
]);

const exists = async path => { try { await access(path); return true; } catch { return false; } };
const same = (left, right) => JSON.stringify(left) === JSON.stringify(right);
const countBy = values => {
  const counts = {};
  for (const value of values) counts[value] = (counts[value] || 0) + 1;
  return counts;
};

export async function auditInformationEquivalentNavControlReadiness({
  root = process.cwd(),
  configPath = 'config/information_equivalent_nav_control_v1.yaml',
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

  const [prompts, evaluator, rebuilt, prepared, formalConfig, outcomeArtifacts] = await Promise.all([
    loadAgentTaskPrompts(resolve(root, config.runner_task_file)),
    loadEvaluatorTaskIndex({ root, configPath }),
    buildInformationEquivalentNavControl({ root, write: false }),
    prepareInformationEquivalentNavControl({ root, configPath, write: false }),
    readSimpleYaml(resolve(root, 'config/agent_formal_aria_v1.yaml')),
    findOfficialOutcomeArtifacts(root, config.agent_run_output_dir)
  ]);
  const taskManifest = JSON.parse(await readFile(resolve(root, config.task_suite_manifest_file), 'utf8'));
  const runManifest = JSON.parse(await readFile(resolve(root, config.run_manifest_file), 'utf8'));
  const evaluatorIds = evaluator.tasks.map(task => task.task_id);
  const promptIds = prompts.map(task => task.task_id);

  check('control_task_ids_are_exact_predeclared_subset', same(evaluatorIds, CONTROL_TASK_IDS) && same(promptIds, CONTROL_TASK_IDS), { evaluator_ids: evaluatorIds, prompt_ids: promptIds });
  check('control_tasks_and_prompts_match_frozen_formal_subset', same(evaluator.tasks, rebuilt.tasks) && same(prompts, rebuilt.prompts), 'The nine tasks and public prompts are exact ordered subsets of formal-aria-v1.');
  check('three_sites_by_three_delta_d_tiers', same(countBy(evaluator.tasks.map(task => task.site)), { mdn_js: 3, usa_gov: 3, wikipedia: 3 }) && same(countBy(evaluator.tasks.map(task => task.delta_d)), { 0: 3, 1: 3, 2: 3 }), { sites: countBy(evaluator.tasks.map(task => task.site)), delta_d: countBy(evaluator.tasks.map(task => task.delta_d)) });
  check('task_manifest_matches_prepared_control', same(taskManifest, prepared.task_manifest), { suite_id: taskManifest.suite_id, task_count: taskManifest.task_count });
  check('run_manifest_has_exact_90_run_plan', same(runManifest, prepared.run_manifest) && runManifest.run_count === 90, { run_count: runManifest.run_count });

  const perTaskRuns = countBy(runManifest.runs.map(run => run.task_id));
  const perConditionRuns = countBy(runManifest.runs.map(run => run.condition));
  const repetitionCoverage = evaluatorIds.every(taskId => config.conditions.every(condition => {
    const repetitions = runManifest.runs.filter(run => run.task_id === taskId && run.condition === condition).map(run => run.repetition).sort((a, b) => a - b);
    return same(repetitions, [1, 2, 3, 4, 5]);
  }));
  check('each_task_has_10_runs', Object.values(perTaskRuns).length === 9 && Object.values(perTaskRuns).every(count => count === 10), perTaskRuns);
  check('each_condition_has_45_runs', same(perConditionRuns, { information_all_unrestricted_navigation: 45, information_all_aria_restricted_navigation: 45 }), perConditionRuns);
  check('each_task_condition_has_repetitions_1_to_5', repetitionCoverage, repetitionCoverage);

  const protocolActual = Object.fromEntries(Object.keys(EXPECTED_PROTOCOL).map(key => [key, config[key]]));
  check('control_model_and_protocol_are_locked', same(protocolActual, EXPECTED_PROTOCOL), { expected: EXPECTED_PROTOCOL, actual: protocolActual });
  const formalDrift = FORMAL_SHARED_FIELDS.filter(field => !same(config[field], formalConfig[field]));
  check('formal_model_prompt_budget_scoring_inputs_and_snapshots_unchanged', formalDrift.length === 0, { compared_fields: FORMAL_SHARED_FIELDS, drift: formalDrift });
  check('control_output_namespace_is_new_and_empty', config.agent_run_output_dir !== formalConfig.agent_run_output_dir && outcomeArtifacts.length === 0, { control_output_dir: config.agent_run_output_dir, formal_output_dir: formalConfig.agent_run_output_dir, artifacts: outcomeArtifacts });

  const canonicalPaths = [
    configPath,
    config.system_prompt_file,
    config.runner_task_file,
    config.evaluator_task_file,
    config.task_suite_manifest_file,
    config.run_manifest_file,
    'package.json',
    'pnpm-lock.yaml',
    ...CANONICAL_IMPLEMENTATION_FILES
  ];
  const canonicalFiles = await Promise.all(canonicalPaths.map(path => fileFingerprint(root, path)));
  check('all_control_canonical_files_present', canonicalFiles.every(file => file.status === 'PRESENT'), canonicalFiles.filter(file => file.status !== 'PRESENT'));
  const snapshotFingerprints = Object.fromEntries(await Promise.all(config.sites.map(async site => {
    const path = join(config.frozen_snapshots[site], site).replaceAll('\\', '/');
    return [site, await directoryFingerprint(root, path)];
  })));

  const freezePath = resolve(root, config.formal_freeze_file);
  let freeze = null;
  if (await exists(freezePath)) freeze = JSON.parse(await readFile(freezePath, 'utf8'));
  if (createFreeze) {
    check('control_freeze_does_not_already_exist', freeze === null, config.formal_freeze_file);
    if (failures.length === 0) {
      freeze = {
        schema_version: '1.0',
        status: 'FROZEN_READY_FOR_INFORMATION_EQUIVALENT_NAV_CONTROL',
        suite_id: taskManifest.suite_id,
        generated_at: new Date().toISOString(),
        config_path: configPath,
        protocol: protocolActual,
        task_count: 9,
        run_count: 90,
        canonical_files: canonicalFiles,
        web_snapshots: snapshotFingerprints
      };
      await writeFile(freezePath, `${JSON.stringify(freeze, null, 2)}\n`, { flag: 'wx' });
    }
  }
  check('control_freeze_present', freeze !== null, config.formal_freeze_file);
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
    check('control_freeze_matches_canonical_files', freeze.status === 'FROZEN_READY_FOR_INFORMATION_EQUIVALENT_NAV_CONTROL' && fileMismatches.length === 0, fileMismatches);
    check('control_freeze_matches_web_snapshots', snapshotMismatches.length === 0, snapshotMismatches);
  }

  const report = {
    schema_version: '1.0',
    status: failures.length === 0 ? 'READY_FOR_INFORMATION_EQUIVALENT_NAV_CONTROL' : 'NOT_READY',
    audited_at: new Date().toISOString(),
    api_or_model_calls_made: false,
    config_path: configPath,
    task_count: evaluator.tasks.length,
    run_count: runManifest.run_count,
    checks,
    failures
  };
  if (writeReport) await writeFile(resolve(root, config.readiness_report_file), `${JSON.stringify(report, null, 2)}\n`);
  if (failures.length) throw new Error(`Control readiness audit failed: ${failures.map(failure => failure.name).join(', ')}`);
  return report;
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  const report = await auditInformationEquivalentNavControlReadiness({ createFreeze: process.argv.includes('--create-freeze'), writeReport: true });
  console.log(JSON.stringify({ status: report.status, task_count: report.task_count, run_count: report.run_count, api_or_model_calls_made: report.api_or_model_calls_made }, null, 2));
}
