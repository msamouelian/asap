<script lang="ts">
	import AppLogo from './AppLogo.svelte';
	import { auth } from '$lib/stores/auth.svelte';
	import { chat } from '$lib/stores/chat.svelte';

	function logout() {
		// Ask BEFORE clearing any auth state: logout destroys tokens and then
		// navigates, so a browser-level cancel at the navigation stage would
		// leave the app running with no credentials.
		if (chat.busy && !confirm(
			'A response is still being generated. Signing out will cancel it. Sign out anyway?',
		)) return;
		auth.logout(); // clears tokens and redirects to Keycloak logout endpoint
	}
</script>

<header class="flex items-center justify-between px-6 py-3 bg-navy border-b-2 border-gold shrink-0">
	<!-- Left: logo + name + tagline -->
	<div class="flex items-center gap-4">
		<AppLogo size={44} />
		<div>
			<div class="flex items-baseline gap-2">
				<h1 class="text-2xl font-sans font-bold text-cream tracking-wide leading-none">ASAP</h1>
				<span class="text-sand text-xs font-light leading-none hidden sm:inline">
					ArchivesSpace Analytics Platform
				</span>
			</div>
		</div>
	</div>

	<!-- Right: user info + logout -->
	{#if auth.user}
		<div class="flex items-center gap-3">
			{#if auth.isAdmin}
				<span class="hidden sm:inline text-xs font-medium bg-crimson text-white px-2 py-0.5 rounded-full">
					Admin
				</span>
			{/if}
			<span class="text-sand text-sm">{auth.user.full_name || auth.user.email}</span>
			<button
				onclick={logout}
				class="text-muted-light hover:text-cream text-sm border border-navy-light px-2.5 py-1 rounded transition-colors hover:border-sand"
			>
				Sign out
			</button>
		</div>
	{/if}
</header>
