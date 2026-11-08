import { fileURLToPath } from 'node:url';
import { defineConfig } from 'vitest/config';

// Separate from vite.config.ts on purpose: these tests cover plain .ts modules
// and need neither the SvelteKit plugin nor a DOM. Anything that has to render
// belongs in tools/panel/tests, which drives real Chrome.
export default defineConfig({
	resolve: {
		alias: { $lib: fileURLToPath(new URL('./src/lib', import.meta.url)) }
	},
	test: {
		environment: 'node',
		include: ['src/**/*.test.ts'],
		restoreMocks: true,
		unstubGlobals: true,
		unstubEnvs: true
	}
});
