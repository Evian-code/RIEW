import { readFile, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { createHash } from 'node:crypto';
import { readSimpleYaml } from './config.mjs';
import { AGENT_TASK_FIELDS, loadAgentTaskPrompts } from './agent_prompt_loader.mjs';
import { loadEvaluatorTaskIndex } from './agent_task_evaluator.mjs';

const EXPECTED_SITES = ['guardian', 'wikipedia', 'mdn_js', 'hacker_news', 'usa_gov'];
const FORBIDDEN_AGENT_FIELDS = ['target_url', 'answer_key', 'reference_path', 'reference_path_rendered_all', 'reference_path_visible_followable', 'shortest_distance', 'shortest_distance_rendered_all', 'shortest_distance_visible_followable', 'structural_class', 'task_structural_class', 'distortion_level'];
const PROVENANCE_FIELDS = ['site', 'start_url', 'target_url', 'reachable_rendered_all', 'reachable_visible_followable', 'shortest_distance_rendered_all', 'shortest_distance_visible_followable', 'distance_inflation', 'task_structural_class', 'reference_path', 'reference_path_rendered_all', 'reference_path_visible_followable'];
const same = (left, right) => JSON.stringify(left) === JSON.stringify(right);
const sha256 = value => createHash('sha256').update(value).digest('hex');

export async function auditAgentTaskSuite({ root = process.cwd(), configPath = 'config/agent_validation.yaml', writeReport = false, verifyFreeze = true } = {}) {
  const config = await readSimpleYaml(resolve(root, configPath));
  const promptPath = resolve(root, config.runner_task_file);
  const evaluatorPath = resolve(root, config.evaluator_task_file);
  const manifestPath = resolve(root, config.task_suite_manifest_file);
  const provenancePath = resolve(root, config.provenance_snapshot_file);
  const freezePath = resolve(root, config.task_freeze_file);
  const [prompts, evaluator, manifestText, promptText, evaluatorText, provenanceText] = await Promise.all([
    loadAgentTaskPrompts(promptPath), loadEvaluatorTaskIndex({ root, configPath }),
    readFile(manifestPath, 'utf8'), readFile(promptPath, 'utf8'),
    readFile(evaluatorPath, 'utf8'), readFile(provenancePath, 'utf8')
  ]);
  const manifest = JSON.parse(manifestText);
  const provenance = JSON.parse(provenanceText);
  const failures = [], checks = [];
  const check = (name, passed, detail) => { checks.push({ name, passed, detail }); if (!passed) failures.push({ name, detail }); };

  check('exactly_25_evaluator_tasks', evaluator.tasks.length === 25, { actual: evaluator.tasks.length });
  check('exactly_25_agent_prompts', prompts.length === 25, { actual: prompts.length });
  const perSite = Object.fromEntries(EXPECTED_SITES.map(site => [site, evaluator.tasks.filter(task => task.site === site).length]));
  check('exactly_5_tasks_per_site', EXPECTED_SITES.every(site => perSite[site] === 5), perSite);
  const evaluatorIds = evaluator.tasks.map(task => task.task_id);
  const promptIds = prompts.map(task => task.task_id);
  check('evaluator_task_ids_unique', new Set(evaluatorIds).size === evaluatorIds.length, { unique: new Set(evaluatorIds).size });
  check('prompt_and_evaluator_task_ids_match', same([...promptIds].sort(), [...evaluatorIds].sort()), { prompt_count: promptIds.length, evaluator_count: evaluatorIds.length });

  const promptById = new Map(prompts.map(task => [task.task_id, task]));
  const crossFileMismatches = evaluator.tasks.flatMap(task => {
    const prompt = promptById.get(task.task_id);
    if (!prompt) return [{ task_id: task.task_id, field: 'task_id' }];
    return ['start_url', 'question'].filter(field => prompt[field] !== task[field]).map(field => ({ task_id: task.task_id, field }));
  });
  check('start_url_and_question_match_exactly', crossFileMismatches.length === 0, crossFileMismatches);
  const forbiddenKeyPattern = new RegExp('"(?:' + FORBIDDEN_AGENT_FIELDS.join('|') + ')"\\s*:', 'i');
  check('agent_prompts_have_only_public_fields', prompts.every(task => same(Object.keys(task), AGENT_TASK_FIELDS)), { allowed_fields: AGENT_TASK_FIELDS });
  check('agent_prompts_contain_no_evaluator_only_keys', !forbiddenKeyPattern.test(promptText), { forbidden_fields: FORBIDDEN_AGENT_FIELDS });

  const provenanceById = new Map((provenance.tasks || []).map(task => [task.task_id, task]));
  const provenanceMismatches = [];
  for (const task of evaluator.tasks) {
    const snapshot = provenanceById.get(task.task_id);
    if (!snapshot) { provenanceMismatches.push({ task_id: task.task_id, field: 'provenance_snapshot', issue: 'missing' }); continue; }
    for (const field of PROVENANCE_FIELDS) if (!same(task[field], snapshot[field])) provenanceMismatches.push({ task_id: task.task_id, field });
  }
  check('evaluator_suite_matches_frozen_provenance_snapshot', provenanceMismatches.length === 0, provenanceMismatches);
  check('manifest_status_is_frozen', manifest.status === 'FROZEN', { actual: manifest.status });
  check('manifest_binds_canonical_evaluator_file', manifest.sha256 === sha256(evaluatorText), { manifest_sha256: manifest.sha256, actual_sha256: sha256(evaluatorText) });

  if (verifyFreeze) {
    const freeze = JSON.parse(await readFile(freezePath, 'utf8'));
    const expectedFiles = [
      { path: config.evaluator_task_file, sha256: sha256(evaluatorText) },
      { path: config.runner_task_file, sha256: sha256(promptText) },
      { path: config.task_suite_manifest_file, sha256: sha256(manifestText) },
      { path: config.provenance_snapshot_file, sha256: sha256(provenanceText) }
    ];
    const frozenFiles = new Map((freeze.canonical_files || []).map(file => [file.path, file.sha256]));
    const hashMismatches = expectedFiles.filter(file => frozenFiles.get(file.path) !== file.sha256);
    check('canonical_file_sha256_matches_freeze_record', freeze.status === 'FROZEN' && hashMismatches.length === 0, { freeze_status: freeze.status, mismatches: hashMismatches });
    check('freeze_record_binds_provenance_snapshot', freeze.provenance_snapshot_sha256 === sha256(provenanceText), { freeze_sha256: freeze.provenance_snapshot_sha256, actual_sha256: sha256(provenanceText) });
  }
  const report = {
    status: failures.length === 0 ? 'PASS' : 'FAIL', audited_at: new Date().toISOString(),
    inputs: { runner_task_file: config.runner_task_file, evaluator_task_file: config.evaluator_task_file, task_suite_manifest_file: config.task_suite_manifest_file, provenance_snapshot_file: config.provenance_snapshot_file, freeze_record_file: config.task_freeze_file },
    task_count: evaluator.tasks.length, per_site_count: perSite, task_ids: evaluatorIds, checks, failures, content_modified_by_audit: false
  };
  if (writeReport) await writeFile(resolve(root, 'docs/agent_task_integrity_audit.json'), JSON.stringify(report, null, 2) + '\n');
  if (failures.length) throw new Error('Agent task integrity audit failed: ' + failures.map(item => item.name).join(', '));
  return report;
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) console.log(JSON.stringify(await auditAgentTaskSuite({ writeReport: true }), null, 2));
