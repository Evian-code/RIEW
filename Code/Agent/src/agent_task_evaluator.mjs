import { readFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { readSimpleYaml } from './config.mjs';

export async function loadEvaluatorTaskIndex({ root = process.cwd(), configPath = 'config/agent_validation.yaml' } = {}) {
  const config = await readSimpleYaml(resolve(root, configPath));
  if (!config.evaluator_task_file) throw new Error('evaluator_task_file is required');
  const taskFile = resolve(root, config.evaluator_task_file);
  const tasks = JSON.parse(await readFile(taskFile, 'utf8'));
  if (!Array.isArray(tasks)) throw new Error('Evaluator task file must contain a JSON array');
  const byTaskId = new Map();
  for (const [index, task] of tasks.entries()) {
    if (!task || typeof task !== 'object' || typeof task.task_id !== 'string') throw new Error(`Evaluator task ${index} has no task_id`);
    if (byTaskId.has(task.task_id)) throw new Error(`Duplicate evaluator task_id: ${task.task_id}`);
    if (typeof task.target_url !== 'string' || !task.answer_key || !Array.isArray(task.reference_path_rendered_all)) {
      throw new Error(`Evaluator task ${task.task_id} is missing ground truth`);
    }
    byTaskId.set(task.task_id, Object.freeze(task));
  }
  return { taskFile, tasks, byTaskId };
}

export function groundTruthForTask(byTaskId, taskId) {
  const task = byTaskId.get(taskId);
  if (!task) throw new Error(`Unknown evaluator task_id: ${taskId}`);
  return task;
}

// This deliberately accepts no semantic paraphrase: every frozen answer_key is
// a single captured field value, so deterministic normalized equality is auditable.
export function normalizeAnswerForEvaluation(value) {
  return String(value ?? '').normalize('NFKC').replace(/\s+/g, ' ').trim().toLocaleLowerCase('en-US');
}

export function evaluateFinalAnswer({ task, finalAnswer, visitedUrls }) {
  if (!task?.answer_key || !Object.hasOwn(task.answer_key, 'value') || !Array.isArray(visitedUrls)) throw new Error('Task ground truth and visitedUrls are required');
  const target_reached = visitedUrls.includes(task.target_url);
  const answer_correct = normalizeAnswerForEvaluation(finalAnswer) === normalizeAnswerForEvaluation(task.answer_key.value);
  return Object.freeze({ target_reached, answer_correct, grounded_success: target_reached && answer_correct });
}
