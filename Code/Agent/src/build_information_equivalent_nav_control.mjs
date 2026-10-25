import { readFile, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';

export const CONTROL_TASK_IDS = Object.freeze([
  'mdn_formal_01',
  'usa_gov_formal_01',
  'wikipedia_formal_01',
  'mdn_formal_03',
  'usa_gov_formal_03',
  'wikipedia_formal_03',
  'mdn_formal_05',
  'usa_gov_formal_05',
  'wikipedia_formal_05'
]);

const SOURCE_TASKS = 'docs/agent_formal_task_suite_v1.json';
const SOURCE_PROMPTS = 'docs/agent_formal_task_prompts_v1.json';
export const CONTROL_TASKS = 'docs/information_equivalent_nav_control_tasks_v1.json';
export const CONTROL_PROMPTS = 'docs/information_equivalent_nav_control_prompts_v1.json';

const publicPrompt = task => Object.freeze(Object.fromEntries(
  ['task_id', 'site', 'start_url', 'question'].map(field => [field, task[field]])
));

export async function buildInformationEquivalentNavControl({ root = process.cwd(), write = false } = {}) {
  const [sourceTasks, sourcePrompts] = await Promise.all([
    readFile(resolve(root, SOURCE_TASKS), 'utf8').then(JSON.parse),
    readFile(resolve(root, SOURCE_PROMPTS), 'utf8').then(JSON.parse)
  ]);
  const taskById = new Map(sourceTasks.map(task => [task.task_id, task]));
  const promptById = new Map(sourcePrompts.map(task => [task.task_id, task]));
  const tasks = CONTROL_TASK_IDS.map(taskId => {
    const task = taskById.get(taskId);
    if (!task) throw new Error(`Missing frozen formal task: ${taskId}`);
    return task;
  });
  const prompts = CONTROL_TASK_IDS.map(taskId => {
    const prompt = promptById.get(taskId);
    if (!prompt) throw new Error(`Missing frozen formal prompt: ${taskId}`);
    const expected = publicPrompt(taskById.get(taskId));
    if (JSON.stringify(prompt) !== JSON.stringify(expected)) throw new Error(`Formal prompt drift for ${taskId}`);
    return prompt;
  });

  for (const site of ['mdn_js', 'usa_gov', 'wikipedia']) {
    for (const deltaD of [0, 1, 2]) {
      const first = sourceTasks.find(task => task.site === site && task.delta_d === deltaD);
      if (!first || !CONTROL_TASK_IDS.includes(first.task_id)) {
        throw new Error(`Control selection is not the first frozen ${site} task at delta_d=${deltaD}`);
      }
    }
  }
  const deltaCounts = Object.fromEntries([0, 1, 2].map(delta => [delta, tasks.filter(task => task.delta_d === delta).length]));
  if (tasks.length !== 9 || Object.values(deltaCounts).some(count => count !== 3)) throw new Error('Control suite must contain three tasks at each delta_d tier');

  if (write) {
    await Promise.all([
      writeFile(resolve(root, CONTROL_TASKS), `${JSON.stringify(tasks, null, 2)}\n`, { flag: 'wx' }),
      writeFile(resolve(root, CONTROL_PROMPTS), `${JSON.stringify(prompts, null, 2)}\n`, { flag: 'wx' })
    ]);
  }
  return Object.freeze({ tasks: Object.freeze(tasks), prompts: Object.freeze(prompts), delta_d_counts: Object.freeze(deltaCounts), wrote_files: write });
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  const result = await buildInformationEquivalentNavControl({ write: process.argv.includes('--write') });
  console.log(JSON.stringify({ task_ids: result.tasks.map(task => task.task_id), delta_d_counts: result.delta_d_counts, wrote_files: result.wrote_files }, null, 2));
}
