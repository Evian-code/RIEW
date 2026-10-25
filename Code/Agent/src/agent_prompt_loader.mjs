import { readFile } from 'node:fs/promises';

export const AGENT_TASK_FIELDS = Object.freeze(['task_id', 'site', 'start_url', 'question']);

function isPlainObject(value) {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

function assertAgentTaskShape(task, index) {
  if (!isPlainObject(task)) throw new Error(`Agent task ${index} must be an object`);
  const keys = Object.keys(task).sort();
  const expected = [...AGENT_TASK_FIELDS].sort();
  if (JSON.stringify(keys) !== JSON.stringify(expected)) {
    throw new Error(`Agent task ${index} must contain only: ${AGENT_TASK_FIELDS.join(', ')}`);
  }
  for (const field of AGENT_TASK_FIELDS) {
    if (typeof task[field] !== 'string' || task[field].trim() === '') throw new Error(`Agent task ${index} has invalid ${field}`);
  }
}

export async function loadAgentTaskPrompts(path) {
  const tasks = JSON.parse(await readFile(path, 'utf8'));
  if (!Array.isArray(tasks)) throw new Error('Agent prompt file must contain a JSON array');
  const ids = new Set();
  return tasks.map((task, index) => {
    assertAgentTaskShape(task, index);
    if (ids.has(task.task_id)) throw new Error(`Duplicate Agent task_id: ${task.task_id}`);
    ids.add(task.task_id);
    return Object.freeze(Object.fromEntries(AGENT_TASK_FIELDS.map(field => [field, task[field]])));
  });
}

export function buildAgentContext(task) {
  assertAgentTaskShape(task, task?.task_id || 'unknown');
  return Object.freeze(Object.fromEntries(AGENT_TASK_FIELDS.map(field => [field, task[field]])));
}
