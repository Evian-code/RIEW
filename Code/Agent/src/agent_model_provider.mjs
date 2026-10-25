export function assertProvider(provider) {
  if (!provider || typeof provider.generate !== 'function') throw new Error('Agent provider must implement generate(request)');
  return provider;
}
