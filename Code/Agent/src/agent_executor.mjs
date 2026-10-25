import { assertProvider } from './agent_model_provider.mjs';
import { parseAgentResponse } from './agent_response_parser.mjs';
import { evaluateFinalAnswer } from './agent_task_evaluator.mjs';
import { RecoverableToolError } from './agent_environment.mjs';
import { deriveRestrictedLinkAttemptMetrics, deriveTrajectoryNavigationMetrics } from './agent_trajectory_metrics.mjs';

const invoke = (environment, action) => action.tool === 'start' ? environment.start() : action.tool === 'read_current_page' ? environment.read_current_page() : environment.follow_link(action.arguments.link_id);

function recordBlockedAction({ logger, environment, observation, action, reason }) {
  const selectedLinkId = action.tool === 'follow_link' ? action.arguments.link_id : null;
  const selectedLink = action.tool === 'follow_link' ? observation.links?.find(link => link.link_id === selectedLinkId) : null;
  logger.recordStep({
    step_index: environment.transitionLog.length,
    current_url: environment.currentUrl,
    action: action.tool,
    selected_link_id: selectedLinkId,
    destination_url: action.tool === 'read_current_page' ? environment.currentUrl : selectedLink?.url ?? null,
    tool_result_status: 'blocked',
    tool_result_error: reason,
    visited_urls: environment.visitedUrls
  });
}

export async function executeAgentSession({ provider, session, evaluationTask, repetition = 1, now = () => Date.now() }) {
  assertProvider(provider); const { agentPayload, environment, logger, protocol } = session;
  const startedAt = now(); let observation; let toolSteps = 0, totalTokens = 0, modelCallIndex = 0;
  const finish = (answer, reason) => {
    const score = evaluateFinalAnswer({ task: evaluationTask, finalAnswer: answer, visitedUrls: environment.visitedUrls });
    const trajectory = logger.finalize({ final_answer: answer, ...score, termination_reason: reason });
    const navigation = deriveTrajectoryNavigationMetrics({
      steps: trajectory.steps,
      visitedUrls: environment.visitedUrls,
      targetUrl: evaluationTask.target_url
    });
    const restrictedLinkAttempts = trajectory.schema_version === '1.2'
      ? deriveRestrictedLinkAttemptMetrics({ steps: trajectory.steps })
      : {};
    return {
      ...trajectory,
      ...navigation,
      ...restrictedLinkAttempts,
      repetition,
      timestamps: { started_at_ms: startedAt, ended_at_ms: now() },
      token_usage: totalTokens || 'unavailable_mock'
    };
  };
  try { observation = environment.start(); } catch (error) { return finish('', `environment_error:${error.message}`); }
  const history = [{ kind: 'observation', observation }];
  while (true) {
    if (now() - startedAt > protocol.timeout_ms) return finish('', 'timeout');
    modelCallIndex += 1;
    let generated;
    try {
      generated = await provider.generate({ systemPrompt: agentPayload.system_prompt, task: agentPayload.task, observation, history: Object.freeze([...history]), tools: agentPayload.tools, limits: agentPayload.limits });
    } catch (error) {
      if (error?.provider_call && typeof logger.recordModelCall === 'function') logger.recordModelCall({ call_index: modelCallIndex, ...error.provider_call });
      return finish('', `provider_error:${error.message}`);
    }
    if (generated?.provider_call && typeof logger.recordModelCall === 'function') logger.recordModelCall({ call_index: modelCallIndex, ...generated.provider_call });
    if (!generated?.mock && (!Number.isInteger(generated?.usage?.total_tokens) || generated.usage.total_tokens < 0)) return finish('', 'provider_usage_unavailable');
    totalTokens += generated?.usage?.total_tokens || 0;
    const response = generated.response;
    let action; try { action = parseAgentResponse(response); } catch (error) { return finish('', `malformed_response:${error.message}`); }
    if (action.type === 'final') return finish(action.answer, 'final_answer');
    if (totalTokens >= protocol.token_budget) {
      recordBlockedAction({ logger, environment, observation, action, reason: 'token budget reached before tool execution' });
      return finish('', 'token_budget');
    }
    if (action.tool === 'start') {
      recordBlockedAction({ logger, environment, observation, action, reason: 'start tool cannot be called after initialization' });
      return finish('', 'invalid_tool:start_already_called');
    }
    if (toolSteps >= protocol.max_tool_steps) {
      recordBlockedAction({ logger, environment, observation, action, reason: 'maximum tool steps reached before tool execution' });
      return finish('', 'max_tool_steps');
    }
    try {
      observation = invoke(environment, action);
      toolSteps += 1;
      history.push({ kind: 'action', action }, { kind: 'observation', observation });
    } catch (error) {
      if (error instanceof RecoverableToolError && error.recoverable_tool_error === true) {
        toolSteps += 1;
        observation = error.observation;
        history.push(
          { kind: 'action', action },
          { kind: 'tool_error', error: error.tool_error },
          { kind: 'observation', observation }
        );
        continue;
      }
      return finish('', `environment_error:${error.message}`);
    }
  }
}
