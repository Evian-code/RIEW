import { assertProvider } from '../agent_model_provider.mjs';
import { OpenAICompatibleChatProvider } from './openai_compatible_agent_provider.mjs';

export const ALIBABA_MODEL_STUDIO_DEFAULT_BASE_URL = 'https://dashscope.aliyuncs.com/compatible-mode/v1';

function requireDatedQwenModel(model) {
  if (typeof model !== 'string' || !/-\d{4}-\d{2}-\d{2}$/.test(model)) {
    throw new Error('Alibaba Model Studio experiments require a dated model identifier');
  }
  return model;
}

export class AlibabaModelStudioProvider extends OpenAICompatibleChatProvider {
  constructor({
    apiKey = process.env.DASHSCOPE_API_KEY,
    baseUrl = ALIBABA_MODEL_STUDIO_DEFAULT_BASE_URL,
    model,
    temperature = 0,
    region = 'cn-beijing',
    enableThinking = false,
    fetchImpl = fetch,
    now
  }) {
    if (!apiKey) throw new Error('DASHSCOPE_API_KEY is required');
    if (typeof enableThinking !== 'boolean') throw new Error('enableThinking must be boolean');
    super({
      apiKey,
      baseUrl,
      model: requireDatedQwenModel(model),
      temperature,
      providerName: 'alibaba_model_studio',
      region,
      additionalBody: { enable_thinking: enableThinking },
      parallelToolCalls: false,
      fetchImpl,
      now
    });
  }
}

export const createAlibabaModelStudioProvider = options => assertProvider(new AlibabaModelStudioProvider(options));

