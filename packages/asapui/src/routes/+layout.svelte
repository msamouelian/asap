<script lang="ts">
	import '../app.css';
	import { onMount } from 'svelte';
	import { auth } from '$lib/stores/auth.svelte';
	import { claimTab } from '$lib/tabLock';

	const { children } = $props();

	// Block child rendering until auth is resolved so components don't fire
	// unauthenticated API requests during the async PKCE redirect.
	let ready = $state(false);
	// True when another tab of this browser already has ASAP open.
	let blocked = $state(false);

	onMount(async () => {
		// One tab at a time: tokens are memory-only and not shared, and Keycloak
		// rotates refresh tokens, so a second tab would invalidate this one.
		if (!(await claimTab())) {
			blocked = true;
			return;
		}

		auth.hydrate();

		// Let the callback page handle itself — it has no auth requirement.
		if (window.location.pathname === '/callback') {
			ready = true;
			return;
		}

		// Always pre-load OIDC config so auth.logout() can build the Keycloak
		// logout URL regardless of whether the user was already authenticated.
		await auth.loadConfig().catch(() => {});

		// Tokens live in memory only, so a fresh tab always goes through
		// Keycloak (silent while the Keycloak session cookie is alive).
		if (!auth.isAuthenticated) {
			await auth.login(); // redirects to Keycloak — never returns
			return;
		}

		ready = true;
	});
</script>

{#if blocked}
	<div class="min-h-screen bg-cream flex items-center justify-center p-4">
		<div class="bg-white rounded-2xl shadow-sm border border-sand p-8 max-w-sm w-full text-center">
			<h1 class="text-base font-semibold text-charcoal mb-2">ASAP is already open</h1>
			<p class="text-sm text-muted">
				ASAP works in one browser tab at a time. Switch to the tab that is already
				open, or close it and reload this one.
			</p>
		</div>
	</div>
{:else if ready}
	{@render children()}
{/if}
