<script lang="ts">
	import '../app.css';
	import { onMount } from 'svelte';
	import { auth } from '$lib/stores/auth.svelte';

	const { children } = $props();

	// Block child rendering until auth is resolved so components don't fire
	// unauthenticated API requests during the async PKCE redirect.
	let ready = $state(false);

	onMount(async () => {
		auth.hydrate();

		// Let the callback page handle itself — it has no auth requirement.
		if (window.location.pathname === '/callback') {
			ready = true;
			return;
		}

		// Always pre-load OIDC config so auth.logout() can build the Keycloak
		// logout URL regardless of whether the user was already authenticated.
		await auth.loadConfig().catch(() => {});

		if (!auth.isAuthenticated) {
			await auth.login(); // redirects to Keycloak — never returns
			return;
		}

		ready = true;
	});
</script>

{#if ready}
	{@render children()}
{/if}
