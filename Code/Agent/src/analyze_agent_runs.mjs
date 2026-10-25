import { readdir, readFile, writeFile } from 'node:fs/promises';
import { join, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { cliOptions, readSimpleYaml } from './config.mjs';
import { loadEvaluatorTaskIndex } from './agent_task_evaluator.mjs';
import { deriveRestrictedLinkAttemptMetrics, deriveTrajectoryNavigationMetrics } from './agent_trajectory_metrics.mjs';

const METRIC_FIELDS = Object.freeze([
  'grounded_success',
  'target_reached',
  'answer_correct',
  'steps_to_first_target',
  'total_link_steps',
  'tool_steps',
  'visited_pages',
  'unique_pages',
  'revisit_count',
  'token_usage',
  'navigation_excess',
  'restricted_link_attempt_count',
  'token_budget_terminated'
]);

function mean(values) {
  const finite = values.filter(Number.isFinite);
  return finite.length ? finite.reduce((sum, value) => sum + value, 0) / finite.length : null;
}

function standardDeviation(values) {
  const finite = values.filter(Number.isFinite);
  if (finite.length < 2) return null;
  const average = mean(finite);
  return Math.sqrt(finite.reduce((sum, value) => sum + (value - average) ** 2, 0) / (finite.length - 1));
}

function countBy(values) {
  const counts = {};
  for (const value of values) counts[value] = (counts[value] || 0) + 1;
  return counts;
}

function graphDistance(task, condition) {
  if (condition === 'rendered_all' || condition === 'information_all_unrestricted_navigation') return task.d_all ?? task.all_shortest_distance_live_validation ?? task.shortest_distance_rendered_all ?? null;
  if (condition === 'rendered_aria_followable' || condition === 'information_all_aria_restricted_navigation') return task.d_aria ?? task.aria_shortest_distance_live_validation ?? task.shortest_distance_rendered_aria ?? null;
  if (condition === 'rendered_visible_followable') return task.visible_shortest_distance_live_validation ?? task.shortest_distance_visible_followable ?? null;
  return null;
}

function assertRunShape(run, filename = run?.run_id || 'unknown') {
  if (!['1.0', '1.1', '1.2'].includes(run?.schema_version)) throw new Error(`Unsupported trajectory schema in ${filename}`);
  for (const field of ['run_id', 'task_id', 'site', 'condition', 'termination_reason']) {
    if (typeof run[field] !== 'string' || !run[field]) throw new Error(`Missing ${field} in ${filename}`);
  }
  for (const field of ['grounded_success', 'target_reached', 'answer_correct']) {
    if (typeof run[field] !== 'boolean') throw new Error(`Missing ${field} in ${filename}`);
  }
  if (!Array.isArray(run.steps) || !Array.isArray(run.visited_urls)) throw new Error(`Missing trajectory arrays in ${filename}`);
  if (!Number.isInteger(run.repetition) || run.repetition < 1) throw new Error(`Missing repetition in ${filename}`);
  if (['1.1', '1.2'].includes(run.schema_version)) {
    for (const field of ['first_target_reached_step', 'steps_to_first_target']) {
      if (run[field] !== null && (!Number.isInteger(run[field]) || run[field] < 0)) throw new Error(`Invalid ${field} in ${filename}`);
    }
    for (const field of ['total_link_steps', 'unique_pages', 'revisit_count']) {
      if (!Number.isInteger(run[field]) || run[field] < 0) throw new Error(`Invalid ${field} in ${filename}`);
    }
    if (!Array.isArray(run.visited_url_sequence) || JSON.stringify(run.visited_url_sequence) !== JSON.stringify(run.visited_urls)) {
      throw new Error(`Invalid visited_url_sequence in ${filename}`);
    }
  }
  if (run.schema_version === '1.2') {
    if (!Number.isInteger(run.restricted_link_attempt_count) || run.restricted_link_attempt_count < 0) throw new Error(`Invalid restricted_link_attempt_count in ${filename}`);
    if (!Array.isArray(run.restricted_link_attempts) || run.restricted_link_attempts.length !== run.restricted_link_attempt_count) throw new Error(`Invalid restricted_link_attempts in ${filename}`);
  }
}

function structuralMetadata(task) {
  const dAll = task.d_all ?? task.all_shortest_distance_live_validation ?? task.shortest_distance_rendered_all ?? null;
  const dAria = task.d_aria ?? task.aria_shortest_distance_live_validation ?? task.shortest_distance_rendered_aria ?? null;
  return Object.freeze({
    structural_tier: task.structural_tier ?? task.task_structural_class_aria ?? task.task_structural_class ?? null,
    d_all: dAll,
    d_aria: dAria,
    delta_d: Number.isFinite(dAll) && Number.isFinite(dAria) ? dAria - dAll : null,
    shortest_path_count_all: task.shortest_path_count_all ?? task.shortest_path_count_rendered_all ?? null,
    shortest_path_count_aria: task.shortest_path_count_aria ?? task.shortest_path_count_rendered_aria ?? null,
    first_hop_count_all: task.first_hop_count_all ?? task.first_hop_count_rendered_all ?? null,
    first_hop_count_aria: task.first_hop_count_aria ?? task.first_hop_count_rendered_aria ?? null
  });
}

export function deriveAgentRunMetrics(run, task) {
  assertRunShape(run);
  if (!task || task.task_id !== run.task_id) throw new Error(`Ground truth mismatch for ${run.task_id}`);
  const navigation = deriveTrajectoryNavigationMetrics({ steps: run.steps, visitedUrls: run.visited_urls, targetUrl: task.target_url });
  if (['1.1', '1.2'].includes(run.schema_version)) {
    for (const field of ['first_target_reached_step', 'steps_to_first_target', 'total_link_steps', 'visited_url_sequence', 'unique_pages', 'revisit_count']) {
      if (JSON.stringify(run[field]) !== JSON.stringify(navigation[field])) throw new Error(`Stored ${field} does not match trajectory for ${run.run_id}`);
    }
  }
  const restrictedLinkAttempts = deriveRestrictedLinkAttemptMetrics({ steps: run.steps });
  if (run.schema_version === '1.2') {
    for (const field of ['restricted_link_attempt_count', 'restricted_link_attempts']) {
      if (JSON.stringify(run[field]) !== JSON.stringify(restrictedLinkAttempts[field])) throw new Error(`Stored ${field} does not match trajectory for ${run.run_id}`);
    }
  }
  const toolSteps = run.steps.filter(step => step.action !== 'start').length;
  const distance = graphDistance(task, run.condition);
  const structure = structuralMetadata(task);
  return Object.freeze({
    run_id: run.run_id,
    task_id: run.task_id,
    site: run.site,
    structural_class: structure.structural_tier,
    ...structure,
    condition: run.condition,
    repetition: run.repetition,
    grounded_success: Number(run.grounded_success),
    target_reached: Number(run.target_reached),
    answer_correct: Number(run.answer_correct),
    first_target_reached_step: navigation.first_target_reached_step,
    steps_to_first_target: navigation.steps_to_first_target,
    total_link_steps: navigation.total_link_steps,
    follow_link_steps: navigation.total_link_steps,
    tool_steps: toolSteps,
    visited_pages: run.visited_urls.length,
    unique_pages: navigation.unique_pages,
    revisit_count: navigation.revisit_count,
    revisits: navigation.revisit_count,
    token_usage: Number.isFinite(run.token_usage) ? run.token_usage : null,
    graph_shortest_distance: distance,
    navigation_excess: Number.isFinite(distance) ? navigation.total_link_steps - distance : null,
    restricted_link_attempt_count: restrictedLinkAttempts.restricted_link_attempt_count,
    token_budget_terminated: Number(run.termination_reason === 'token_budget'),
    budget_exhausted: ['token_budget', 'max_tool_steps'].includes(run.termination_reason),
    termination_reason: run.termination_reason
  });
}

function summarizeGroup(records, dimensions = {}) {
  const summary = { ...dimensions, n: records.length };
  for (const field of METRIC_FIELDS) {
    summary[`mean_${field}`] = mean(records.map(record => record[field]));
    summary[`sd_${field}`] = standardDeviation(records.map(record => record[field]));
  }
  summary.budget_exhaustion_rate = mean(records.map(record => Number(record.budget_exhausted)));
  summary.termination_counts = countBy(records.map(record => record.termination_reason));
  return summary;
}

function conditionSummary(summary) {
  if (!summary) return null;
  return Object.freeze({
    n: summary.n,
    target_reach_rate: summary.mean_target_reached,
    grounded_success_rate: summary.mean_grounded_success,
    answer_correct_rate: summary.mean_answer_correct,
    mean_steps_to_first_target: summary.mean_steps_to_first_target,
    sd_steps_to_first_target: summary.sd_steps_to_first_target,
    mean_total_link_steps: summary.mean_total_link_steps,
    sd_total_link_steps: summary.sd_total_link_steps,
    mean_tool_steps: summary.mean_tool_steps,
    mean_unique_pages: summary.mean_unique_pages,
    mean_revisit_count: summary.mean_revisit_count,
    mean_token_usage: summary.mean_token_usage,
    mean_restricted_link_attempt_count: summary.mean_restricted_link_attempt_count,
    token_budget_termination_rate: summary.mean_token_budget_terminated,
    termination_counts: summary.termination_counts
  });
}

function groupedSummaries(records, keys) {
  const groups = new Map();
  for (const record of records) {
    const values = keys.map(key => record[key]);
    const groupKey = JSON.stringify(values);
    if (!groups.has(groupKey)) groups.set(groupKey, { values, records: [] });
    groups.get(groupKey).records.push(record);
  }
  return [...groups.values()].map(group => summarizeGroup(group.records, Object.fromEntries(keys.map((key, index) => [key, group.values[index]]))));
}

export function analyzeAgentRuns({ runs, taskById, conditions }) {
  if (!Array.isArray(runs) || runs.length === 0) throw new Error('At least one trajectory is required');
  if (!(taskById instanceof Map)) throw new Error('taskById must be a Map');
  if (!Array.isArray(conditions) || conditions.length !== 2) throw new Error('Exactly two primary conditions are required');
  const runIds = new Set();
  const metrics = runs.map(run => {
    assertRunShape(run);
    if (runIds.has(run.run_id)) throw new Error(`Duplicate run_id: ${run.run_id}`);
    runIds.add(run.run_id);
    return deriveAgentRunMetrics(run, taskById.get(run.task_id));
  });
  const taskCondition = groupedSummaries(metrics, ['task_id', 'site', 'structural_class', 'condition']);
  const taskGroups = new Map();
  for (const summary of taskCondition) {
    if (!taskGroups.has(summary.task_id)) taskGroups.set(summary.task_id, []);
    taskGroups.get(summary.task_id).push(summary);
  }
  const pairedTaskDifferences = [];
  const taskLevelSummary = [];
  for (const [taskId, summaries] of taskGroups) {
    const control = summaries.find(summary => summary.condition === conditions[0]);
    const treatment = summaries.find(summary => summary.condition === conditions[1]);
    if (!control || !treatment) continue;
    const differences = {};
    for (const field of METRIC_FIELDS) {
      const key = `mean_${field}`;
      differences[field] = Number.isFinite(control[key]) && Number.isFinite(treatment[key]) ? treatment[key] - control[key] : null;
    }
    pairedTaskDifferences.push({
      task_id: taskId,
      site: control.site,
      structural_class: control.structural_class,
      control_condition: conditions[0],
      treatment_condition: conditions[1],
      difference_treatment_minus_control: differences
    });
    const task = taskById.get(taskId);
    const structure = structuralMetadata(task);
    const differenceSummary = {
      target_reach_rate: differences.target_reached,
      grounded_success_rate: differences.grounded_success,
      answer_correct_rate: differences.answer_correct,
      mean_steps_to_first_target: differences.steps_to_first_target,
      mean_total_link_steps: differences.total_link_steps,
      mean_tool_steps: differences.tool_steps,
      mean_unique_pages: differences.unique_pages,
      mean_revisit_count: differences.revisit_count,
      mean_token_usage: differences.token_usage,
      mean_restricted_link_attempt_count: differences.restricted_link_attempt_count,
      token_budget_termination_rate: differences.token_budget_terminated
    };
    const namedDifferenceKey = conditions[0] === 'information_all_unrestricted_navigation'
      && conditions[1] === 'information_all_aria_restricted_navigation'
      ? 'difference_restricted_minus_unrestricted'
      : 'difference_aria_minus_all';
    taskLevelSummary.push({
      task_id: taskId,
      site: control.site,
      ...structure,
      [conditions[0]]: conditionSummary(control),
      [conditions[1]]: conditionSummary(treatment),
      difference_treatment_minus_control: differenceSummary,
      [namedDifferenceKey]: differenceSummary
    });
  }
  return Object.freeze({
    schema_version: '1.2',
    analysis_kind: 'agent_experiment_descriptive',
    difference_direction: `${conditions[1]} minus ${conditions[0]}`,
    run_count: metrics.length,
    task_count: new Set(metrics.map(record => record.task_id)).size,
    conditions,
    condition_summaries: groupedSummaries(metrics, ['condition']),
    task_condition_summaries: taskCondition,
    task_level_summary: taskLevelSummary,
    paired_task_differences: pairedTaskDifferences,
    site_condition_summaries: groupedSummaries(metrics, ['site', 'condition']),
    structural_class_condition_summaries: groupedSummaries(metrics, ['structural_class', 'condition']),
    delta_d_condition_summaries: groupedSummaries(metrics, ['delta_d', 'condition']),
    run_metrics: metrics
  });
}

export async function loadAgentRunsFromDirectory(inputDir) {
  const metadataFiles = new Set(['batch_plan.json', 'batch_completion.json', 'analysis_summary.json', 'task_level_summary.json']);
  const filenames = (await readdir(inputDir)).filter(filename => filename.endsWith('.json') && !metadataFiles.has(filename)).sort();
  if (filenames.length === 0) throw new Error(`No trajectory JSON files found in ${inputDir}`);
  const runs = [];
  for (const filename of filenames) {
    const run = JSON.parse(await readFile(join(inputDir, filename), 'utf8'));
    assertRunShape(run, filename);
    runs.push(run);
  }
  return runs;
}

export async function analyzeAgentRunDirectory({ root = process.cwd(), configPath = 'config/agent_pilot_aria.yaml', inputDir, outputPath = null }) {
  if (!inputDir) throw new Error('inputDir is required');
  const absoluteInput = resolve(root, inputDir);
  const [runs, config, evaluator] = await Promise.all([
    loadAgentRunsFromDirectory(absoluteInput),
    readSimpleYaml(resolve(root, configPath)),
    loadEvaluatorTaskIndex({ root, configPath })
  ]);
  const analysis = analyzeAgentRuns({ runs, taskById: evaluator.byTaskId, conditions: config.conditions });
  const absoluteOutput = outputPath ? resolve(root, outputPath) : join(absoluteInput, 'analysis_summary.json');
  await writeFile(absoluteOutput, `${JSON.stringify(analysis, null, 2)}\n`);
  const taskLevelOutput = join(absoluteInput, 'task_level_summary.json');
  await writeFile(taskLevelOutput, `${JSON.stringify({
    schema_version: analysis.schema_version,
    difference_direction: analysis.difference_direction,
    task_count: analysis.task_count,
    tasks: analysis.task_level_summary
  }, null, 2)}\n`);
  return Object.freeze({ output_path: absoluteOutput, task_level_output_path: taskLevelOutput, run_count: analysis.run_count, task_count: analysis.task_count, difference_direction: analysis.difference_direction });
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  const options = cliOptions(process.argv.slice(2));
  if (typeof options['input-dir'] !== 'string') throw new Error('--input-dir is required');
  console.log(JSON.stringify(await analyzeAgentRunDirectory({
    configPath: typeof options.config === 'string' ? options.config : 'config/agent_pilot_aria.yaml',
    inputDir: options['input-dir'],
    outputPath: typeof options.output === 'string' ? options.output : null
  }), null, 2));
}
