<script lang="ts">
	import { onMount, onDestroy } from 'svelte';
	import type { ExtractorStatus, JobRun, TriggerResponse } from '$lib/api/extractor';

	interface JobApi {
		trigger: () => Promise<TriggerResponse>;
		status: () => Promise<ExtractorStatus>;
		history: () => Promise<JobRun[]>;
	}

	let { title, buttonLabel, runningLabel, runningNote, api }: {
		title: string;
		buttonLabel: string;
		runningLabel: string;
		runningNote: string;
		api: JobApi;
	} = $props();

	let status     = $state<ExtractorStatus | null>(null);
	let history    = $state<JobRun[]>([]);
	let loading    = $state(true);
	let triggering = $state(false);
	let error      = $state('');
	let elapsed    = $state(0); // seconds since current job started (cosmetic counter)

	let pollTimer:    ReturnType<typeof setInterval> | null = null;
	let elapsedTimer: ReturnType<typeof setInterval> | null = null;

	const POLL_INTERVAL_MS = 30_000;

	onMount(async () => {
		await refresh();
	});

	onDestroy(() => {
		clearTimers();
	});

	function clearTimers() {
		if (pollTimer)    { clearInterval(pollTimer);    pollTimer    = null; }
		if (elapsedTimer) { clearInterval(elapsedTimer); elapsedTimer = null; }
	}

	async function refresh() {
		try {
			[status, history] = await Promise.all([api.status(), api.history()]);
			error = '';
		} catch (e) {
			error = e instanceof Error ? e.message : `Failed to load ${title} status.`;
		} finally {
			loading = false;
		}

		if (status?.is_running) {
			startElapsedCounter();
			if (!pollTimer) {
				pollTimer = setInterval(async () => {
					try {
						[status, history] = await Promise.all([api.status(), api.history()]);
					} catch {
						// silent — keep polling
					}
					if (!status?.is_running) clearTimers();
				}, POLL_INTERVAL_MS);
			}
		} else {
			clearTimers();
		}
	}

	function startElapsedCounter() {
		if (elapsedTimer) return;
		updateElapsed();
		elapsedTimer = setInterval(updateElapsed, 1000);
	}

	function updateElapsed() {
		if (!status?.is_running || !status?.last_run?.started_at) { elapsed = 0; return; }
		elapsed = Math.floor((Date.now() - new Date(status.last_run.started_at).getTime()) / 1000);
	}

	async function trigger() {
		triggering = true;
		error = '';
		try {
			await api.trigger();
			await refresh();
		} catch (e) {
			error = e instanceof Error ? e.message : `Failed to start ${title}.`;
		} finally {
			triggering = false;
		}
	}

	// ── Formatting helpers ────────────────────────────────────────────────────

	function fmtDuration(secs: number | null): string {
		if (secs == null) return '—';
		const h = Math.floor(secs / 3600);
		const m = Math.floor((secs % 3600) / 60);
		const s = secs % 60;
		if (h > 0) return `${h}h ${m}m`;
		if (m > 0) return `${m}m ${s}s`;
		return `${s}s`;
	}

	function fmtDate(iso: string | null): string {
		if (!iso) return '—';
		return new Date(iso).toLocaleString(undefined, {
			month: 'short', day: 'numeric', year: 'numeric',
			hour: 'numeric', minute: '2-digit',
		});
	}

	function fmtRelative(iso: string | null): string {
		if (!iso) return '—';
		const diffMs  = Date.now() - new Date(iso).getTime();
		const diffMin = Math.floor(diffMs / 60_000);
		if (diffMin < 1)  return 'just now';
		if (diffMin < 60) return `${diffMin}m ago`;
		const diffH = Math.floor(diffMin / 60);
		if (diffH  < 24) return `${diffH}h ago`;
		const diffD = Math.floor(diffH / 24);
		if (diffD  < 30) return `${diffD}d ago`;
		return new Date(iso).toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' });
	}

	type Badge = { label: string; cls: string };
	function badge(s: string): Badge {
		switch (s) {
			case 'succeeded': return { label: 'Succeeded', cls: 'bg-emerald-50 text-emerald-700' };
			case 'failed':    return { label: 'Failed',    cls: 'bg-red-50 text-red-700'         };
			case 'running':   return { label: 'Running',   cls: 'bg-amber-50 text-amber-700'     };
			default:          return { label: 'Pending',   cls: 'bg-sand/60 text-muted'          };
		}
	}

	// The most recently completed run (not the active one)
	const lastCompleted = $derived(
		history.find(j => j.status === 'succeeded' || j.status === 'failed') ?? null,
	);
</script>

<div class="flex flex-col gap-3">
	<h3 class="text-xs font-semibold text-muted uppercase tracking-wider">{title}</h3>

	{#if loading}
		<div class="flex items-center justify-center py-8 text-muted text-sm">
			Loading {title.toLowerCase()} status…
		</div>

	{:else}

		<!-- ── Status + Trigger ──────────────────────────────────────────────── -->
		<div class="rounded-xl border border-sand bg-white px-5 py-4 flex flex-col gap-3">

			<div class="flex items-center justify-between gap-4 flex-wrap">
				<div class="flex items-center gap-2.5">
					{#if status?.is_running}
						<span class="relative flex h-3 w-3 shrink-0">
							<span class="animate-ping absolute inline-flex h-full w-full rounded-full bg-amber-400 opacity-75"></span>
							<span class="relative inline-flex rounded-full h-3 w-3 bg-amber-500"></span>
						</span>
						<div>
							<span class="text-sm font-semibold text-amber-700">Running</span>
							{#if status.current_job}
								<span class="text-xs text-muted ml-1.5 font-mono">{status.current_job}</span>
							{/if}
							<div class="text-xs text-muted mt-0.5">
								Started {fmtRelative(status.last_run?.started_at ?? null)}
								· {fmtDuration(elapsed)} elapsed
							</div>
						</div>
					{:else}
						<span class="inline-flex h-3 w-3 rounded-full bg-emerald-500 shrink-0"></span>
						<span class="text-sm font-semibold text-emerald-700">Ready</span>
					{/if}
				</div>

				<button
					onclick={trigger}
					disabled={triggering || status?.is_running}
					class="flex items-center gap-1.5 px-4 py-2 rounded-lg text-sm font-medium transition-colors
						bg-navy text-cream hover:bg-navy-light
						disabled:opacity-50 disabled:cursor-not-allowed"
				>
					{#if triggering}
						<svg class="w-3.5 h-3.5 animate-spin" fill="none" viewBox="0 0 24 24">
							<circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"/>
							<path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"/>
						</svg>
						Starting…
					{:else if status?.is_running}
						{runningLabel}
					{:else}
						<svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
							<path stroke-linecap="round" stroke-linejoin="round" d="M5 3l14 9-14 9V3z"/>
						</svg>
						{buttonLabel}
					{/if}
				</button>
			</div>

			{#if status?.is_running}
				<p class="text-xs text-muted">{runningNote}</p>
			{/if}

			{#if error}
				<div class="bg-red-50 border border-red-200 rounded-lg px-3 py-2 text-xs text-red-700 flex items-start gap-2">
					<svg class="w-3.5 h-3.5 mt-0.5 shrink-0" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
						<path stroke-linecap="round" stroke-linejoin="round" d="M12 9v4m0 4h.01M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z"/>
					</svg>
					{error}
				</div>
			{/if}

			{#if !status?.is_running && lastCompleted}
				{@const b = badge(lastCompleted.status)}
				<div class="border-t border-sand/60 pt-3 flex items-center gap-3 text-xs text-muted flex-wrap">
					<span class="font-medium text-charcoal">Last run:</span>
					<span class="px-2 py-0.5 rounded-full font-medium text-xs {b.cls}">{b.label}</span>
					<span title={fmtDate(lastCompleted.started_at)}>Started {fmtRelative(lastCompleted.started_at)}</span>
					{#if lastCompleted.duration_seconds != null}
						<span>· Duration {fmtDuration(lastCompleted.duration_seconds)}</span>
					{/if}
				</div>
			{/if}
		</div>

		<!-- ── Recent Runs ────────────────────────────────────────────────────── -->
		{#if history.length > 0}
			<table class="w-full text-sm">
				<thead>
					<tr class="border-b border-sand text-left">
						<th class="pb-2 font-medium text-charcoal">Job</th>
						<th class="pb-2 font-medium text-charcoal">Started</th>
						<th class="pb-2 font-medium text-charcoal">Duration</th>
						<th class="pb-2 font-medium text-charcoal">Status</th>
					</tr>
				</thead>
				<tbody>
					{#each history as run (run.name)}
						{@const b = badge(run.status)}
						<tr class="border-b border-sand/50 hover:bg-sand/20 transition-colors">
							<td class="py-2.5 pr-4 font-mono text-xs text-charcoal">{run.name}</td>
							<td class="py-2.5 pr-4 text-muted whitespace-nowrap">
								{fmtDate(run.started_at)}
								<span class="text-muted-light">({fmtRelative(run.started_at)})</span>
							</td>
							<td class="py-2.5 pr-4 text-muted tabular-nums">{fmtDuration(run.duration_seconds)}</td>
							<td class="py-2.5">
								<span class="text-xs font-medium px-2 py-0.5 rounded-full {b.cls}">
									{b.label}
								</span>
							</td>
						</tr>
					{/each}
				</tbody>
			</table>
		{:else}
			<div class="text-center py-4 text-muted-light text-sm">No runs yet.</div>
		{/if}
	{/if}
</div>
