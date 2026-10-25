import { mkdir, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
export async function writeAgentResult({ outputDir, result }) {
  if (!outputDir) throw new Error('outputDir is required');
  await mkdir(outputDir, { recursive: true });
  const path = join(outputDir, `${result.run_id}.json`);
  await writeFile(path, JSON.stringify(result, null, 2) + '\n', { flag: 'wx' }); return path;
}
