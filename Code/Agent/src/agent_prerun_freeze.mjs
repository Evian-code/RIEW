import { createHash } from 'node:crypto';
import { access, readdir, readFile, stat, writeFile } from 'node:fs/promises';
import { join, relative, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { readSimpleYaml } from './config.mjs';
import { auditAgentTaskSuite } from './audit_agent_task_suite.mjs';

export const PRE_RUN_FREEZE_PATH = 'docs/agent_experiment_prerun_freeze_v1.json';
export const ANALYSIS_PLAN_PATH = 'docs/agent_experiment_analysis_plan_v1.json';
export const OFFICIAL_OUTCOME_DIR = 'results/agent_experiment';
const sha256 = value => createHash('sha256').update(value).digest('hex');
const exists = async path => { try { await access(path); return true; } catch { return false; } };
const execFileAsync = promisify(execFile);
const gitCommit = async root => (await execFileAsync('git', ['rev-parse', 'HEAD'], { cwd: root })).stdout.trim();

export async function fileFingerprint(root, path) {
  const absolute = resolve(root, path);
  if (!await exists(absolute)) return { path, sha256: null, status: 'MISSING' };
  const canonicalText = (await readFile(absolute, 'utf8')).replace(/\r\n/g, '\n');
  return { path, sha256: sha256(canonicalText), status: 'PRESENT' };
}

export async function directoryFingerprint(root, path) {
  const absolute = resolve(root, path);
  if (!await exists(absolute)) throw new Error(`Frozen snapshot is missing: ${path}`);
  const files = [];
  async function walk(dir) {
    for (const entry of (await readdir(dir, { withFileTypes: true })).sort((a, b) => a.name.localeCompare(b.name))) {
      const child = resolve(dir, entry.name);
      if (entry.isDirectory()) await walk(child);
      else if (entry.isFile()) files.push({ path: relative(absolute, child).replaceAll('\\', '/'), sha256: sha256(await readFile(child)) });
    }
  }
  await walk(absolute);
  return { path, file_count: files.length, aggregate_sha256: sha256(files.map(file => `${file.path}\0${file.sha256}\n`).join('')) };
}

export async function findOfficialOutcomeArtifacts(root, outputDir = OFFICIAL_OUTCOME_DIR) {
  const absolute = resolve(root, outputDir);
  if (!await exists(absolute)) return [];
  const found = [];
  async function walk(dir) {
    for (const entry of await readdir(dir, { withFileTypes: true })) {
      const child = resolve(dir, entry.name);
      if (entry.isDirectory()) await walk(child);
      else if (entry.isFile()) found.push(relative(root, child).replaceAll('\\', '/'));
    }
  }
  await walk(absolute);
  return found.sort();
}

export function modelLocked(config) {
  return !/^PLACEHOLDER_/i.test(String(config.model || '')) && !/^PLACEHOLDER_/i.test(String(config.model_version || ''));
}

export async function createPreRunFreeze({ root = process.cwd(), configPath = 'config/agent_validation.yaml', replace = false } = {}) {
  const config = await readSimpleYaml(resolve(root, configPath));
  const outcomes = await findOfficialOutcomeArtifacts(root, config.agent_run_output_dir || OFFICIAL_OUTCOME_DIR);
  if (outcomes.length) throw new Error(`Refusing pre-run freeze: official Agent outcome artifacts found:\n${outcomes.join('\n')}`);
  await auditAgentTaskSuite({ root, configPath });
  const taskArtifacts = [config.evaluator_task_file, config.runner_task_file, config.task_suite_manifest_file, config.provenance_snapshot_file, config.task_freeze_file];
  const implementation = ['src/agent_runner.mjs', 'src/agent_environment.mjs', 'src/agent_prompt_loader.mjs', 'src/agent_run_log.mjs', 'src/frozen_snapshot_loader.mjs', 'src/agent_task_evaluator.mjs', 'src/config.mjs', 'src/normalize.mjs', 'src/node_identity.mjs', 'src/agent_prerun_freeze.mjs'];
  const snapshots = Object.fromEntries(await Promise.all(config.sites.map(async site => {
    const path = join(config.frozen_snapshots[site], site).replaceAll('\\', '/');
    return [site, await directoryFingerprint(root, path)];
  })));
  const freeze = {
    schema_version: '1.0', status: modelLocked(config) ? 'READY_FOR_AGENT_RUN' : 'READY_EXCEPT_MODEL_LOCK', generated_at: new Date().toISOString(),
    git_commit: await gitCommit(root), runtime_metadata: { node_version: process.version, platform: process.platform, package_manager_lock: 'pnpm-lock.yaml is present; package-lock.json is absent at freeze time.' },
    task_artifacts: await Promise.all(taskArtifacts.map(path => fileFingerprint(root, path))),
    experiment_configuration: { file: await fileFingerprint(root, configPath), values: Object.fromEntries(['model','model_version','temperature','max_tool_steps','token_budget','timeout_ms','repetitions','conditions','sites','external_search','url_guessing','system_prompt_file','runner_task_file','evaluator_task_file','task_suite_manifest_file','provenance_snapshot_file','task_freeze_file','frozen_snapshots','agent_run_output_dir'].map(key => [key, config[key] ?? null])), system_prompt: await fileFingerprint(root, config.system_prompt_file), package_json: await fileFingerprint(root, 'package.json'), package_lock_json: await fileFingerprint(root, 'package-lock.json'), pnpm_lock_yaml: await fileFingerprint(root, 'pnpm-lock.yaml') },
    implementation: await Promise.all(implementation.map(path => fileFingerprint(root, path))), web_snapshots: snapshots,
    analysis_plan: await fileFingerprint(root, ANALYSIS_PLAN_PATH),
    pre_run_outcome_audit: { official_output_directory: config.agent_run_output_dir || OFFICIAL_OUTCOME_DIR, detected_artifacts: outcomes, agent_experiment_run: false, pre_run_integrity_audit: 'PASS' },
    model_lock: { locked: modelLocked(config), model: config.model, model_version: config.model_version }
  };
  const output = resolve(root, PRE_RUN_FREEZE_PATH);
  if (await exists(output) && !replace) throw new Error(`Refusing to overwrite existing pre-run freeze: ${PRE_RUN_FREEZE_PATH}`);
  await writeFile(output, JSON.stringify(freeze, null, 2) + '\n');
  return freeze;
}

export async function auditPreRunFreeze({ root = process.cwd(), configPath = 'config/agent_validation.yaml' } = {}) {
  const config = await readSimpleYaml(resolve(root, configPath));
  const freeze = JSON.parse(await readFile(resolve(root, PRE_RUN_FREEZE_PATH), 'utf8'));
  const failures = [], checks = []; const check = (name, ok, detail) => { checks.push({ name, passed: ok, detail }); if (!ok) failures.push({ name, detail }); };
  try { await auditAgentTaskSuite({ root, configPath }); check('task_level_audit', true, 'PASS'); } catch (error) { check('task_level_audit', false, error.message); }
  for (const group of ['task_artifacts', 'implementation']) for (const frozen of freeze[group] || []) { const actual = await fileFingerprint(root, frozen.path); check(`${group}:${frozen.path}`, actual.sha256 === frozen.sha256 && actual.status === frozen.status, actual); }
  for (const frozen of [freeze.experiment_configuration.file, freeze.experiment_configuration.system_prompt, freeze.experiment_configuration.package_json, freeze.experiment_configuration.package_lock_json, freeze.experiment_configuration.pnpm_lock_yaml, freeze.analysis_plan]) { const actual = await fileFingerprint(root, frozen.path); check(`frozen_file:${frozen.path}`, actual.sha256 === frozen.sha256 && actual.status === frozen.status, actual); }
  for (const [site, frozen] of Object.entries(freeze.web_snapshots || {})) { const actual = await directoryFingerprint(root, frozen.path); check(`snapshot:${site}`, actual.aggregate_sha256 === frozen.aggregate_sha256, { expected: frozen.aggregate_sha256, actual: actual.aggregate_sha256 }); }
  const outcomes = await findOfficialOutcomeArtifacts(root, config.agent_run_output_dir || OFFICIAL_OUTCOME_DIR); check('no_official_agent_outcomes', outcomes.length === 0, outcomes);
  const locked = modelLocked(config); check('model_lock_status_matches_freeze', locked === freeze.model_lock.locked, { locked, model: config.model, model_version: config.model_version });
  const status = failures.length ? 'FAIL' : locked ? 'READY_FOR_AGENT_RUN' : 'READY_EXCEPT_MODEL_LOCK';
  return { status, checks, failures, task_freeze: failures.some(x => x.name === 'task_level_audit') ? 'FAIL' : 'PASS_TASK_FREEZE', no_agent_outcome: outcomes.length ? 'FAIL_NO_AGENT_OUTCOME' : 'PASS_NO_AGENT_OUTCOME', environment_integrity: failures.length ? 'FAIL_ENVIRONMENT_INTEGRITY' : 'PASS_ENVIRONMENT_INTEGRITY', model_lock: locked ? 'PASS_MODEL_LOCKED' : 'BLOCKED_MODEL_NOT_LOCKED' };
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) console.log(JSON.stringify(await auditPreRunFreeze(), null, 2));
