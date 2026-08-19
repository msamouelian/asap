import { defineConfig } from 'vite';
import { sveltekit } from '@sveltejs/kit/vite';
import tailwindcss from '@tailwindcss/vite';

export default defineConfig({
	plugins: [tailwindcss(), sveltekit()],
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
