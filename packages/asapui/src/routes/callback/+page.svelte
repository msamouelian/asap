<script lang="ts">
	import { onMount } from 'svelte';
	import { goto } from '$app/navigation';
	import { exchangeCode, fetchOidcConfig } from '$lib/api/auth';
	import { auth } from '$lib/stores/auth.svelte';

	let error = $state('');

	onMount(async () => {
		const params = new URLSearchParams(window.location.search);
		const code  = params.get('code');
		const state = params.get('state');

		if (!code) {
			error = 'No authorization code received from Keycloak.';
			return;
		}

		const savedState = sessionStorage.getItem('pkce_state');
		if (savedState && state !== savedState) {
			error = 'State mismatch — possible CSRF attack. Please try again.';
			sessionStorage.removeItem('pkce_state');
			sessionStorage.removeItem('pkce_verifier');
			return;
		}

		try {
			const config = await fetchOidcConfig();
			const tokens = await exchangeCode(code, config);
			auth.oidcConfig = config;
			auth.setTokens(tokens);
			await auth.loadUser();
			goto('/');
		} catch (err) {
			error = err instanceof Error ? err.message : 'Authentication failed. Please try again.';
		}
	});
</script>

<div class="min-h-screen bg-cream flex items-center justify-center">
	{#if error}
		<div class="bg-white rounded-2xl shadow-sm border border-sand p-8 max-w-sm w-full text-center">
			<p class="text-red-700 text-sm font-medium mb-4">{error}</p>
			<button
				onclick={() => auth.login()}
				class="bg-navy text-cream rounded-xl px-4 py-2 text-sm font-medium hover:bg-navy-light transition-colors"
			>
				Try again
			</button>
		</div>
	{:else}
		<div class="text-charcoal text-sm">Completing sign-in…</div>
	{/if}
</div>
