const TOOL_NAMES = new Set(['start', 'read_current_page', 'follow_link']);
export function parseAgentResponse(response) {
  if (!response || typeof response !== 'object' || Array.isArray(response)) throw new Error('Malformed model response');
  if (response.type === 'final') {
    if (Object.keys(response).length !== 2 || typeof response.answer !== 'string') throw new Error('Malformed final response');
    return Object.freeze({ type: 'final', answer: response.answer });
  }
  if (response.type === 'tool') {
    if (Object.keys(response).length !== 3 || !TOOL_NAMES.has(response.tool) || !response.arguments || typeof response.arguments !== 'object' || Array.isArray(response.arguments)) throw new Error('Malformed tool response');
    const keys = Object.keys(response.arguments);
    if (response.tool === 'follow_link') {
      if (keys.length !== 1 || keys[0] !== 'link_id' || !Number.isInteger(response.arguments.link_id) || response.arguments.link_id < 1) throw new Error('Invalid follow_link arguments');
    } else if (keys.length !== 0) throw new Error('Tool takes no arguments');
    return Object.freeze({ type: 'tool', tool: response.tool, arguments: Object.freeze({ ...response.arguments }) });
  }
  throw new Error('Unknown model response type');
}
