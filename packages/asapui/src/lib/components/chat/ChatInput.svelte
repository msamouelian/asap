<script lang="ts">
	import { onMount } from 'svelte';
	import { chat, TEMP_STOPS } from '$lib/stores/chat.svelte';

	const {
		onsubmit,
		disabled = false,
		streaming = false,
		oncancel,
	}: {
		onsubmit:   (message: string) => void;
		disabled?:  boolean;
		// A response is streaming in THIS conversation — show stop instead of send.
		streaming?: boolean;
		oncancel?:  () => void;
	} = $props();

	let value    = $state('');
	let textarea = $state<HTMLTextAreaElement | null>(null);
	let tempMenuOpen = $state(false);
	const currentStop = $derived(TEMP_STOPS.find(s => s.key === chat.tempStop) ?? TEMP_STOPS[0]);

	// Auto-grow textarea
	function resize() {
		if (!textarea) return;
		textarea.style.height = 'auto';
		textarea.style.height = Math.min(textarea.scrollHeight, 200) + 'px';
	}

	function submit() {
		const msg = value.trim();
		if (!msg || disabled) return;
		value = '';
		if (textarea) textarea.style.height = 'auto';
		onsubmit(msg);
	}

	function handleKeydown(e: KeyboardEvent) {
		if (e.key === 'Enter' && !e.shiftKey) {
			e.preventDefault();
			submit();
		}
	}

	// Listen for prompt inserts from the left panel
	onMount(() => {
		function handleInsert(e: Event) {
			value = (e as CustomEvent<string>).detail;
			textarea?.focus();
			setTimeout(resize, 0);
		}
		window.addEventListener('asap:insert-prompt', handleInsert);
		return () => window.removeEventListener('asap:insert-prompt', handleInsert);
	});
</script>

<div class="border-t border-sand bg-cream px-4 py-3">
	<div class="flex items-end gap-3 bg-white border border-sand rounded-2xl px-4 py-3 shadow-sm focus-within:border-navy/50 focus-within:shadow-md transition-all">
		<textarea
			bind:this={textarea}
			bind:value
			rows={1}
			placeholder="What would you like to explore today?"
			{disabled}
			oninput={resize}
			onkeydown={handleKeydown}
			class="flex-1 resize-none bg-transparent text-sm text-charcoal placeholder-muted-light focus:outline-none leading-relaxed min-h-[1.5rem] max-h-[200px] disabled:opacity-50"
		></textarea>

		<!-- Response-style (temperature) selector: applies to the NEXT message -->
		<div class="relative shrink-0">
			<button
				onclick={() => (tempMenuOpen = !tempMenuOpen)}
				class="h-8 px-2.5 flex items-center gap-1 rounded-full border border-sand text-xs text-muted hover:text-charcoal hover:bg-sand/30 transition-colors select-none"
				aria-label="Response style"
				title="Response style: {currentStop.label} — {currentStop.desc}"
			>
				{currentStop.label}
				<svg class="w-3 h-3" fill="none" stroke="currentColor" stroke-width="2.5" viewBox="0 0 24 24">
					<path stroke-linecap="round" stroke-linejoin="round" d="M6 9l6 6 6-6" />
				</svg>
			</button>
			{#if tempMenuOpen}
				<!-- click-away backdrop -->
				<button class="fixed inset-0 z-10 cursor-default" aria-label="Close menu"
					onclick={() => (tempMenuOpen = false)}></button>
				<div class="absolute bottom-10 right-0 w-72 bg-white border border-sand rounded-xl shadow-lg p-1.5 z-20">
					<p class="px-3 pt-1.5 pb-1 text-[10px] uppercase tracking-wide text-muted-light">Response style</p>
					{#each TEMP_STOPS as s (s.key)}
						<button
							onclick={() => { chat.setTempStop(s.key); tempMenuOpen = false; }}
							class="w-full text-left px-3 py-2 rounded-lg hover:bg-sand/30 transition-colors {chat.tempStop === s.key ? 'bg-sand/40' : ''}"
						>
							<span class="flex items-center justify-between">
								<span class="text-sm font-medium text-charcoal">{s.label}</span>
								{#if chat.tempStop === s.key}
									<svg class="w-3.5 h-3.5 text-navy" fill="none" stroke="currentColor" stroke-width="3" viewBox="0 0 24 24">
										<path stroke-linecap="round" stroke-linejoin="round" d="M5 13l4 4L19 7" />
									</svg>
								{/if}
							</span>
							<span class="block text-xs text-muted mt-0.5">{s.desc}</span>
						</button>
					{/each}
				</div>
			{/if}
		</div>

		{#if streaming}
			<!-- Stop the in-flight response -->
			<button
				onclick={() => oncancel?.()}
				class="shrink-0 w-8 h-8 flex items-center justify-center rounded-full bg-red-600 text-white hover:bg-red-700 transition-colors"
				aria-label="Stop generating"
				title="Stop generating"
			>
				<svg class="w-3.5 h-3.5" fill="currentColor" viewBox="0 0 24 24">
					<rect x="6" y="6" width="12" height="12" rx="1.5" />
				</svg>
			</button>
		{:else}
			<button
				onclick={submit}
				disabled={disabled || !value.trim()}
				class="shrink-0 w-8 h-8 flex items-center justify-center rounded-full bg-navy text-cream disabled:opacity-30 disabled:cursor-not-allowed hover:bg-navy-light transition-colors"
				aria-label="Send message"
			>
				<svg class="w-4 h-4 translate-x-px" fill="none" stroke="currentColor" stroke-width="2.5" viewBox="0 0 24 24">
					<path stroke-linecap="round" stroke-linejoin="round" d="M5 12h14M12 5l7 7-7 7" />
				</svg>
			</button>
		{/if}
	</div>
	<p class="text-center text-[10px] text-muted-light mt-2">
		Press <kbd class="font-mono bg-sand px-1 py-0.5 rounded text-[9px]">Enter</kbd> to send ·
		<kbd class="font-mono bg-sand px-1 py-0.5 rounded text-[9px]">Shift+Enter</kbd> for newline
	</p>
</div>
