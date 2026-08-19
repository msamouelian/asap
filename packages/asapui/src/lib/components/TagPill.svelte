<script lang="ts">
	const {
		name,
		active = false,
		onclick,
	}: { name: string; active?: boolean; onclick?: () => void } = $props();
</script>

<button
	type="button"
	{onclick}
	class={[
		'inline-flex items-center px-3 py-1 rounded-full text-xs font-medium border transition-colors whitespace-nowrap',
		active
			? 'bg-navy text-cream border-navy'
			: 'bg-parchment text-muted border-sand hover:border-navy hover:text-navy',
	].join(' ')}
>
	{name}
</button>
