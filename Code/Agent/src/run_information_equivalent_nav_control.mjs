import { readFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { buildAgentRunPlan, executeAgentRunPlan, writeAgentBatchCompletion, writeAgentBatchPlan } from './agent_batch_runner.mjs';
import { auditInformationEquivalentNavControlReadiness } from './information_equivalent_nav_control_readiness.mjs';
import { readSimpleYaml } from './config.mjs';
import { loadLocalEnvironment } from './local_env.mjs';
import { loadRunnerTaskInputs, loadAgentRunnerResources, createAgentRunSessionFromResources } from './agent_runner.mjs';
import { groundTruthForTask, loadEvaluatorTaskIndex } from './agent_task_evaluator.mjs';
import { createConfiguredAgentProvider } from './providers/configured_agent_provider.mjs';

const CONFIG_PATH = 'config/information_equivalent_nav_control_v1.yaml';

export async function prepareInformationEquivalentNavControlPlan({ root = process.cwd(), configPath = CONFIG_PATH } = {}) {
  const [config, taskInputs] = await Promise.all([
    readSimpleYaml(resolve(root, configPath)),
    loadRunnerTaskInputs({ root, configPath })
  ]);
  const plan = buildAgentRunPlan({ batchId: config.batch_id, tasks: taskInputs.tasks, conditions: config.conditions, repetitions: config.repetitions });
  const frozenManifest = JSON.parse(await readFile(resolve(root, config.run_manifest_file), 'utf8'));
  if (JSON.stringify(plan) !== JSON.stringify(frozenManifest.runs)) throw new Error('Computed control run plan does not match the frozen manifest');
  return Object.freeze({ config, tasks: taskInputs.tasks, plan: Object.freeze(plan), manifest: Object.freeze(frozenManifest) });
}

export async function runInformationEquivalentNavControl({ root = process.cwd(), configPath = CONFIG_PATH, environment = process.env } = {}) {
  await auditInformationEquivalentNavControlReadiness({ root, configPath, createFreeze: false, writeReport: true });
  const prepared = await prepareInformationEquivalentNavControlPlan({ root, configPath });
  const [{ byTaskId }, resources] = await Promise.all([
    loadEvaluatorTaskIndex({ root, configPath }),
    loadAgentRunnerResources({ root, configPath })
  ]);
  for (const task of prepared.tasks) groundTruthForTask(byTaskId, task.task_id);
  const outputDir = resolve(root, prepared.config.agent_run_output_dir, prepared.config.batch_id);
  const planPath = await writeAgentBatchPlan({ outputDir, manifest: prepared.manifest });
  const provider = createConfiguredAgentProvider({ protocol: resources.protocol, env: environment });
  const taskById = new Map(prepared.tasks.map(task => [task.task_id, task]));
  const execution = await executeAgentRunPlan({
    plan: prepared.plan,
    provider,
    outputDir,
    sessionFactory: run => createAgentRunSessionFromResources({ runId: run.run_id, task: taskById.get(run.task_id), condition: run.condition, resources }),
    evaluationTaskFor: taskId => groundTruthForTask(byTaskId, taskId)
  });
  const completion = {
    schema_version: '1.0',
    batch_id: prepared.config.batch_id,
    status: 'complete',
    completed_run_count: execution.results.length,
    termination_counts: execution.termination_counts
  };
  const completionPath = await writeAgentBatchCompletion({ outputDir, completion });
  return Object.freeze({ output_dir: outputDir, plan_path: planPath, completion_path: completionPath, ...completion });
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  if (process.argv.includes('--execute')) {
    const env = await loadLocalEnvironment({ root: process.cwd(), env: process.env });
    console.log(JSON.stringify(await runInformationEquivalentNavControl({ environment: env }), null, 2));
  } else {
    const prepared = await prepareInformationEquivalentNavControlPlan();
    console.log(JSON.stringify({
      experiment_kind: prepared.config.experiment_kind,
      config_path: CONFIG_PATH,
      batch_id: prepared.config.batch_id,
      task_count: prepared.tasks.length,
      run_count: prepared.plan.length,
      conditions: prepared.config.conditions,
      repetitions: prepared.config.repetitions,
      api_or_model_calls_made: false
    }, null, 2));
  }
}
