import { readFile } from 'node:fs/promises';
import { resolve } from 'node:path';

function unquote(value) {
  if (value.length >= 2 && ((value.startsWith('"') && value.endsWith('"')) || (value.startsWith("'") && value.endsWith("'")))) {
    return value.slice(1, -1);
  }
  return value;
}

export async function loadLocalEnvironment({ root = process.cwd(), env = process.env } = {}) {
  const merged = { ...env };
  if (merged.DASHSCOPE_API_KEY) return merged;

  let content;
  try {
    content = await readFile(resolve(root, '.env.local'), 'utf8');
  } catch (error) {
    if (error.code === 'ENOENT') return merged;
    throw error;
  }

  for (const line of content.split(/\r?\n/)) {
    const match = line.match(/^\s*DASHSCOPE_API_KEY\s*=\s*(.*?)\s*$/);
    if (!match) continue;
    const value = unquote(match[1]);
    if (value) merged.DASHSCOPE_API_KEY = value;
    break;
  }
  return merged;
}

