import { createAlibabaModelStudioProvider } from './alibaba_model_studio_provider.mjs';

export function createConfiguredAgentProvider({ protocol, env = process.env, fetchImpl, now }) {
  if (!protocol || typeof protocol !== 'object') throw new Error('protocol is required');
  if (protocol.provider !== 'alibaba_model_studio') {
    throw new Error(`Unsupported agent provider: ${protocol.provider ?? 'missing'}`);
  }
  if (protocol.api_protocol !== 'openai_chat_completions') {
    throw new Error(`Unsupported API protocol: ${protocol.api_protocol ?? 'missing'}`);
  }
  if (protocol.thinking_mode !== 'disabled') {
    throw new Error('The frozen pilot requires thinking_mode: disabled');
  }
  return createAlibabaModelStudioProvider({
    apiKey: env.DASHSCOPE_API_KEY,
    baseUrl: protocol.api_base_url,
    model: protocol.model,
    temperature: protocol.temperature,
    region: protocol.region,
    enableThinking: false,
    fetchImpl,
    now
  });
}

