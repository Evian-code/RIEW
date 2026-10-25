import { readFile } from 'node:fs/promises';
import YAML from 'yaml';

export async function readCsv(path) {
  const [header, ...lines] = (await readFile(path, 'utf8')).trim().split(/\r?\n/);
  const names = header.split(',');
  return lines.filter(Boolean).map(line => Object.fromEntries(names.map((name, i) => [name, (line.split(',')[i] ?? '').trim()])));
}

export async function readSimpleYaml(path) {
  return YAML.parse(await readFile(path, 'utf8'));
}

export function cliOptions(args) {
  const result = {};
  for (let i = 0; i < args.length; i += 1) if (args[i].startsWith('--')) result[args[i].slice(2)] = args[i + 1] && !args[i + 1].startsWith('--') ? args[++i] : true;
  return result;
}
