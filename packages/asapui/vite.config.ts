/// <reference types="vitest/config" />
import { defineConfig } from 'vite';
import { sveltekit } from '@sveltejs/kit/vite';
import tailwindcss from '@tailwindcss/vite';

export default defineConfig({
	plugins: [tailwindcss(), sveltekit()],
	test: {
		// Unit tests live next to the code they cover as *.test.ts.
		include: ['src/**/*.test.ts'],
		// jsdom gives DOMPurify (and anything else touching the DOM) a real
		// document; SvelteKit's browser-only modules are mocked per test.
		environment: 'jsdom',
	},
	server: {
		proxy: {
			'/api': {
				target: process.env.VITE_BACKEND_URL ?? 'http://localhost:8001',
				rewrite: (path) => path.replace(/^\/api/, ''),
				changeOrigin: true,
			},
		},
	},
});
