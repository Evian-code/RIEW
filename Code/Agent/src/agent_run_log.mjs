const REQUIRED_STEP_FIELDS = ['step_index', 'current_url', 'action', 'selected_link_id', 'destination_url', 'tool_result_status', 'tool_result_error', 'visited_urls'];
const REQUIRED_MODEL_CALL_FIELDS = ['call_index', 'provider', 'region', 'api_protocol', 'requested_model', 'latency_ms', 'usage', 'outcome'];
const REQUIRED_LIMIT_FIELDS = ['max_tool_steps', 'token_budget', 'timeout_ms'];

export class AgentTrajectoryLog {
  #record;
  #finished = false;

  constructor({ schema_version = '1.1', run_id, task_id, site, condition, model, model_version, provider, region, api_protocol, thinking_mode, temperature, snapshot_ref, observation_schema_version, limits }) {
    for (const [field, value] of Object.entries({ run_id, task_id, site, condition, model, model_version, provider, region, api_protocol, thinking_mode, snapshot_ref, observation_schema_version })) {
      if (typeof value !== 'string' || value.trim() === '') throw new Error(`Missing trajectory field: ${field}`);
    }
    if (typeof temperature !== 'number') throw new Error('Missing trajectory field: temperature');
    if (!['1.1', '1.2'].includes(schema_version)) throw new Error('Unsupported trajectory schema version');
    for (const field of REQUIRED_LIMIT_FIELDS) {
      if (!Number.isInteger(limits?.[field]) || limits[field] <= 0) throw new Error(`Missing trajectory limit: ${field}`);
    }
    this.#record = {
      schema_version,
      run_id,
      task_id,
      site,
      condition,
      snapshot_ref,
      observation_schema_version,
      provider,
      region,
      api_protocol,
      model,
      model_version,
      thinking_mode,
      temperature,
      limits: structuredClone(limits),
      model_calls: [],
      steps: [],
      visited_urls: [],
      final_answer: null,
      target_reached: null,
      answer_correct: null,
      grounded_success: null,
      termination_reason: null
    };
  }

  recordModelCall(event) {
    if (this.#finished) throw new Error('Cannot append to a finalized trajectory');
    for (const field of REQUIRED_MODEL_CALL_FIELDS) if (!(field in event)) throw new Error(`Missing model call field: ${field}`);
    if (!Number.isInteger(event.call_index) || event.call_index < 1) throw new Error('call_index must be a positive integer');
    if (!['success', 'error'].includes(event.outcome)) throw new Error('Invalid model call outcome');
    if (!Number.isFinite(event.latency_ms) || event.latency_ms < 0) throw new Error('Invalid model call latency');
    this.#record.model_calls.push(structuredClone(event));
  }

  recordStep(event) {
    if (this.#finished) throw new Error('Cannot append to a finalized trajectory');
    for (const field of REQUIRED_STEP_FIELDS) if (!(field in event)) throw new Error(`Missing step field: ${field}`);
    if (!['ok', 'error', 'blocked'].includes(event.tool_result_status)) throw new Error('Invalid tool result status');
    if (event.tool_result_status === 'ok' && event.tool_result_error !== null) throw new Error('Successful tool result cannot include an error');
    if (event.tool_result_status !== 'ok' && (typeof event.tool_result_error !== 'string' || !event.tool_result_error)) throw new Error('Unsuccessful tool result must include an error');
    this.#record.steps.push(structuredClone(event));
    this.#record.visited_urls = [...event.visited_urls];
  }

  finalize({ final_answer, target_reached, answer_correct, grounded_success, termination_reason }) {
    if (this.#finished) throw new Error('Trajectory is already finalized');
    if (typeof final_answer !== 'string') throw new Error('final_answer must be a string');
    for (const [field, value] of Object.entries({ target_reached, answer_correct, grounded_success })) if (typeof value !== 'boolean') throw new Error(`${field} must be boolean`);
    if (typeof termination_reason !== 'string' || !termination_reason) throw new Error('termination_reason is required');
    Object.assign(this.#record, { final_answer, target_reached, answer_correct, grounded_success, termination_reason });
    this.#finished = true;
    return this.toJSON();
  }

  toJSON() { return structuredClone(this.#record); }
}
