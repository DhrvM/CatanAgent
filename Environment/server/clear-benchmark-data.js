import { benchmarkStore } from './benchmarkStore.js';

async function main() {
  await benchmarkStore.init();
  await benchmarkStore.clearAllData();
  console.log('Benchmark data cleared.');
}

main().catch(error => {
  console.error('Failed to clear benchmark data', error);
  process.exit(1);
});
