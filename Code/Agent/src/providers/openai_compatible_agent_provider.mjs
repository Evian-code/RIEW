import { assertProvider } from '../agent_model_provider.mjs';

const RESERVED_BODY_FIELDS = new Set(['model', 'temperature', 'messages', 'tools', 'tool_choice', 'parallel_tool_calls']);

function normalizeBaseUrl(baseUrl) {
  if (typeof baseUrl !== 'string' || !baseUrl.startsWith('https://')) {
    throw new Error('A HTTPS OpenAI-compatible base URL is required');
  }
  return baseUrl.replace(/\/+$/, '');
}

function requireFixedModel(model) {
  if (typeof model !== 'string' || model.trim() === '' || /latest/i.test(model)) {
    throw new Error('A fixed, non-latest model identifier is required');
  }
  return model;
}

function normalizeUsage(usage) {
  if (!usage || typeof usage !== 'object') return null;
  const integerOrNull = value => Number.isInteger(value) && value >= 0 ? value : null;
  return Object.freeze({
    prompt_tokens: integerOrNull(usage.prompt_tokens),
    completion_tokens: integerOrNull(usage.completion_tokens),
    total_tokens: integerOrNull(usage.total_tokens),
    cached_tokens: integerOrNull(usage.prompt_tokens_details?.cached_tokens ?? usage.cached_tokens),
    reasoning_tokens: integerOrNull(usage.completion_tokens_details?.reasoning_tokens ?? usage.reasoning_tokens)
  });
}

function responseHeader(response, names) {
  if (!response?.headers || typeof response.headers.get !== 'function') return null;
  for (const name of names) {
    const value = response.headers.get(name);
    if (value) return value;
  }
  return null;
}

async function providerErrorDetail(response) {
  try {
    const body = await response.json();
    const code = body?.error?.code ?? body?.code;
    const message = body?.error?.message ?? body?.message;
    return [code, message].filter(value => typeof value === 'string' && value).join(': ');
  } catch {
    return '';
  }
}

export class ProviderRequestError extends Error {
  constructor(message, providerCall) {
    super(message);
    this.name = 'ProviderRequestError';
    this.provider_call = Object.freeze(providerCall);
  }
}

export class OpenAICompatibleChatProvider {
  constructor({
    apiKey,
    baseUrl,
    model,
    temperature = 0,
    providerName,
    region,
    additionalBody = {},
    parallelToolCalls = false,
    fetchImpl = fetch,
    now = () => Date.now()
  }) {
    if (typeof apiKey !== 'string' || apiKey.length === 0) throw new Error('Provider API key is required');
    if (typeof providerName !== 'string' || providerName.trim() === '') throw new Error('providerName is required');
    if (typeof region !== 'string' || region.trim() === '') throw new Error('region is required');
    if (typeof temperature !== 'number' || temperature < 0 || temperature >= 2) throw new Error('temperature must be in [0, 2)');
    if (!additionalBody || typeof additionalBody !== 'object' || Array.isArray(additionalBody)) throw new Error('additionalBody must be an object');
    for (const field of Object.keys(additionalBody)) {
      if (RESERVED_BODY_FIELDS.has(field)) throw new Error(`additionalBody cannot override ${field}`);
    }
    if (typeof fetchImpl !== 'function') throw new Error('fetchImpl must be a function');
    if (typeof now !== 'function') throw new Error('now must be a function');

    this.apiKey = apiKey;
    this.endpoint = `${normalizeBaseUrl(baseUrl)}/chat/completions`;
    this.model = requireFixedModel(model);
    this.temperature = temperature;
    this.providerName = providerName;
    this.region = region;
    this.additionalBody = Object.freeze({ ...additionalBody });
    this.parallelToolCalls = parallelToolCalls;
    this.fetchImpl = fetchImpl;
    this.now = now;
  }

  baseProviderCall({ startedAt, response = null, body = null, outcome, errorType = null }) {
    return {
      provider: this.providerName,
      region: this.region,
      api_protocol: 'openai_chat_completions',
      requested_model: this.model,
      response_model: typeof body?.model === 'string' ? body.model : null,
      response_id: typeof body?.id === 'string' ? body.id : null,
      request_id: responseHeader(response, ['x-request-id', 'x-dashscope-request-id']) ?? (typeof body?.request_id === 'string' ? body.request_id : null),
      finish_reason: typeof body?.choices?.[0]?.finish_reason === 'string' ? body.choices[0].finish_reason : null,
      latency_ms: Math.max(0, this.now() - startedAt),
      usage: normalizeUsage(body?.usage),
      outcome,
      error_type: errorType,
      response_type: null,
      tool_name: null
    };
  }

  async generate({ systemPrompt, task, history = [], tools = [] }) {
    const nativeTools = tools.map(tool => ({
      type: 'function',
      function: {
        name: tool.name,
        description: tool.description,
        parameters: tool.input_schema
      }
    }));
    const requestBody = {
      ...this.additionalBody,
      model: this.model,
      temperature: this.temperature,
      messages: [
        { role: 'system', content: systemPrompt },
        { role: 'user', content: JSON.stringify({ task, history }) }
      ]
    };
    if (nativeTools.length > 0) {
      Object.assign(requestBody, {
        tools: nativeTools,
        tool_choice: 'auto',
        parallel_tool_calls: this.parallelToolCalls
      });
    }

    const startedAt = this.now();
    let response;
    try {
      response = await this.fetchImpl(this.endpoint, {
        method: 'POST',
        headers: {
          authorization: `Bearer ${this.apiKey}`,
          'content-type': 'application/json'
        },
        body: JSON.stringify(requestBody)
      });
    } catch (error) {
      const providerCall = this.baseProviderCall({ startedAt, outcome: 'error', errorType: 'network_error' });
      throw new ProviderRequestError(`${this.providerName} network error: ${error.message}`, providerCall);
    }

    if (!response.ok) {
      const detail = await providerErrorDetail(response);
      const suffix = detail ? `: ${detail}` : '';
      const providerCall = this.baseProviderCall({ startedAt, response, outcome: 'error', errorType: 'http_error' });
      throw new ProviderRequestError(`${this.providerName} HTTP ${response.status}${suffix}`, providerCall);
    }

    let body;
    try {
      body = await response.json();
    } catch {
      const providerCall = this.baseProviderCall({ startedAt, response, outcome: 'error', errorType: 'invalid_json' });
      throw new ProviderRequestError(`${this.providerName} returned invalid JSON`, providerCall);
    }

    const message = body.choices?.[0]?.message;
    if (!message) {
      const providerCall = this.baseProviderCall({ startedAt, response, body, outcome: 'error', errorType: 'malformed_response' });
      throw new ProviderRequestError(`Malformed ${this.providerName} response`, providerCall);
    }

    let agentResponse;
    if (message.tool_calls?.length) {
      if (message.tool_calls.length !== 1 || message.tool_calls[0].type !== 'function') {
        const providerCall = this.baseProviderCall({ startedAt, response, body, outcome: 'error', errorType: 'malformed_tool_call' });
        throw new ProviderRequestError(`Malformed ${this.providerName} tool call`, providerCall);
      }
      const call = message.tool_calls[0].function;
      let argumentsObject;
      try {
        argumentsObject = JSON.parse(call.arguments);
      } catch {
        const providerCall = this.baseProviderCall({ startedAt, response, body, outcome: 'error', errorType: 'malformed_tool_arguments' });
        throw new ProviderRequestError(`Malformed ${this.providerName} tool arguments`, providerCall);
      }
      agentResponse = { type: 'tool', tool: call.name, arguments: argumentsObject };
    } else if (typeof message.content === 'string') {
      agentResponse = { type: 'final', answer: message.content };
    } else {
      const providerCall = this.baseProviderCall({ startedAt, response, body, outcome: 'error', errorType: 'malformed_response' });
      throw new ProviderRequestError(`Malformed ${this.providerName} final response`, providerCall);
    }

    const providerCall = this.baseProviderCall({ startedAt, response, body, outcome: 'success' });
    providerCall.response_type = agentResponse.type;
    providerCall.tool_name = agentResponse.type === 'tool' ? agentResponse.tool : null;
    return {
      response: agentResponse,
      usage: providerCall.usage,
      provider_call: Object.freeze(providerCall)
    };
  }
}

export const createOpenAICompatibleProvider = options => assertProvider(new OpenAICompatibleChatProvider(options));

