import { mkdir, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import { executeAgentSession } from './agent_executor.mjs';
import { writeAgentResult } from './agent_result_writer.mjs';

const BATCH_ID_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._-]*$/;

export function buildAgentRunPlan({ batchId, tasks, conditions, repetitions }) {
  if (!BATCH_ID_PATTERN.test(batchId || '')) throw new Error('batchId must use only letters, numbers, dot, underscore, and hyphen');
  if (!Array.isArray(tasks) || tasks.length === 0) throw new Error('At least one Agent task is required');
  if (!Array.isArray(conditions) || conditions.length === 0) throw new Error('At least one condition is required');
  if (!Number.isInteger(repetitions) || repetitions < 1) throw new Error('repetitions must be a positive integer');

  const runs = [];
  for (const task of tasks) {
    for (let repetition = 1; repetition <= repetitions; repetition += 1) {
      for (const condition of conditions) {
        runs.push(Object.freeze({
          run_id: `${batchId}__${task.task_id}__${condition}__r${String(repetition).padStart(2, '0')}`,
          task_id: task.task_id,
          site: task.site,
          condition,
          repetition
        }));
      }
    }
  }
  return Object.freeze(runs);
}

function countBy(values) {
  const counts = {};
  for (const value of values) counts[value] = (counts[value] || 0) + 1;
  return counts;
}

export async function executeAgentRunPlan({ plan, provider, sessionFactory, evaluationTaskFor, outputDir }) {
  if (!Array.isArray(plan) || plan.length === 0) throw new Error('A non-empty run plan is required');
  if (typeof sessionFactory !== 'function') throw new Error('sessionFactory is required');
  if (typeof evaluationTaskFor !== 'function') throw new Error('evaluationTaskFor is required');

  const results = [];
  const resultPaths = [];
  for (const run of plan) {
    const session = await sessionFactory(run);
    const result = await executeAgentSession({
      provider,
      session,
      evaluationTask: evaluationTaskFor(run.task_id),
      repetition: run.repetition
    });
    resultPaths.push(await writeAgentResult({ outputDir, result }));
    results.push(result);
  }
  return Object.freeze({
    results: Object.freeze(results),
    result_paths: Object.freeze(resultPaths),
    termination_counts: Object.freeze(countBy(results.map(result => result.termination_reason)))
  });
}

export async function writeAgentBatchPlan({ outputDir, manifest }) {
  await mkdir(outputDir, { recursive: true });
  const path = join(outputDir, 'batch_plan.json');
  await writeFile(path, `${JSON.stringify(manifest, null, 2)}\n`, { flag: 'wx' });
  return path;
}

export async function writeAgentBatchCompletion({ outputDir, completion }) {
  const path = join(outputDir, 'batch_completion.json');
  await writeFile(path, `${JSON.stringify(completion, null, 2)}\n`, { flag: 'wx' });
  return path;
}
