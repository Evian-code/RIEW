import { readFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { readSimpleYaml } from './config.mjs';
import { AGENT_TASK_FIELDS, buildAgentContext, loadAgentTaskPrompts } from './agent_prompt_loader.mjs';
import { AGENT_OBSERVATION_SCHEMA_VERSION, AGENT_TOOL_SCHEMA, SnapshotInterface } from './agent_environment.mjs';
import { loadConfiguredFrozenSnapshots } from './frozen_snapshot_loader.mjs';
import { AgentTrajectoryLog } from './agent_run_log.mjs';

export async function loadRunnerTaskInputs({ root = process.cwd(), configPath = 'config/agent_validation.yaml' } = {}) {
  const config = await readSimpleYaml(resolve(root, configPath));
  if (!config.runner_task_file) throw new Error('runner_task_file is required');
  const taskFile = resolve(root, config.runner_task_file);
  const tasks = await loadAgentTaskPrompts(taskFile);
  return { taskFile, tasks };
}

export function contextForAgent(task) {
  return buildAgentContext(task);
}

export async function loadRunnerProtocol({ root = process.cwd(), configPath = 'config/agent_validation.yaml' } = {}) {
  const config = await readSimpleYaml(resolve(root, configPath));
  const systemPrompt = await readFile(resolve(root, config.system_prompt_file), 'utf8');
  return Object.freeze({
    provider: config.provider,
    region: config.region,
    api_protocol: config.api_protocol,
    api_base_url: config.api_base_url,
    model: config.model,
    model_version: config.model_version,
    thinking_mode: config.thinking_mode,
    temperature: config.temperature,
    max_tool_steps: config.max_tool_steps,
    token_budget: config.token_budget,
    timeout_ms: config.timeout_ms,
    answer_format: config.answer_format,
    system_prompt: systemPrompt.trim(),
    tools: AGENT_TOOL_SCHEMA.filter(tool => tool.name !== 'start')
  });
}

export async function loadAgentRunnerResources({ root = process.cwd(), configPath = 'config/agent_validation.yaml' } = {}) {
  const [{ config, snapshots }, protocol] = await Promise.all([
    loadConfiguredFrozenSnapshots({ root, configPath }),
    loadRunnerProtocol({ root, configPath })
  ]);
  return Object.freeze({ config, snapshots, protocol });
}

export function createAgentRunSessionFromResources({ runId, task, condition, resources }) {
  const { config, snapshots, protocol } = resources;
  if (!config.conditions.includes(condition)) throw new Error('Condition is not configured for this experiment');
  const snapshot = snapshots.get(task.site);
  if (!snapshot) throw new Error(`No frozen snapshot loaded for ${task.site}`);
  const logger = new AgentTrajectoryLog({
    schema_version: config.trajectory_schema_version || '1.1',
    run_id: runId,
    task_id: task.task_id,
    site: task.site,
    condition,
    provider: protocol.provider,
    region: protocol.region,
    api_protocol: protocol.api_protocol,
    model: protocol.model,
    model_version: protocol.model_version,
    thinking_mode: protocol.thinking_mode,
    temperature: protocol.temperature,
    snapshot_ref: config.frozen_snapshots[task.site],
    observation_schema_version: AGENT_OBSERVATION_SCHEMA_VERSION,
    limits: {
      max_tool_steps: protocol.max_tool_steps,
      token_budget: protocol.token_budget,
      timeout_ms: protocol.timeout_ms
    }
  });
  const environment = new SnapshotInterface({ pages: snapshot.pages, condition, startUrl: task.start_url, onTransition: event => logger.recordStep(event) });
  const agentPayload = Object.freeze({
    system_prompt: protocol.system_prompt,
    task: contextForAgent(task),
    tools: protocol.tools,
    answer_format: protocol.answer_format,
    limits: Object.freeze({ max_tool_steps: protocol.max_tool_steps, token_budget: protocol.token_budget, timeout_ms: protocol.timeout_ms })
  });
  return { agentPayload, environment, logger, protocol };
}

export async function createAgentRunSession({ runId, task, condition, root = process.cwd(), configPath = 'config/agent_validation.yaml' }) {
  const resources = await loadAgentRunnerResources({ root, configPath });
  return createAgentRunSessionFromResources({ runId, task, condition, resources });
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  const [{ taskFile, tasks }, protocol] = await Promise.all([loadRunnerTaskInputs(), loadRunnerProtocol()]);
  console.log(JSON.stringify({ runner_task_file: taskFile, task_count: tasks.length, agent_visible_fields: AGENT_TASK_FIELDS, tool_names: protocol.tools.map(tool => tool.name), limits: { max_tool_steps: protocol.max_tool_steps, token_budget: protocol.token_budget, timeout_ms: protocol.timeout_ms } }, null, 2));
}
