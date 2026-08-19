<script lang="ts">
	import { onMount } from 'svelte';
	import { ui } from '$lib/stores/ui.svelte';
	import {
		documentsApi,
		type CollectionInfo,
		type JobProgress,
		type ScopedCollection,
	} from '$lib/api/documents';

	let collections   = $state<CollectionInfo[]>([]);
	let userScopeIds  = $state<Set<string>>(new Set());
	let job           = $state<JobProgress | null>(null);
	let loading       = $state(true);
	let errorMsg      = $state<string | null>(null);
	let confirmDelete = $state<string | null>(null);

	// Upload form
	let title       = $state('');
	let description = $state('');
	let visibility  = $state<'private' | 'shared'>('private');
	let pickedFiles = $state<File[]>([]);
	let uploading   = $state(false);
	let fileInput   = $state<HTMLInputElement | null>(null);
	let folderInput = $state<HTMLInputElement | null>(null);

	const jobActive = $derived(job !== null && (job.status === 'pending' || job.status === 'running'));

	async function refresh() {
		try {
			const [colls, scope, j] = await Promise.all([
				documentsApi.list(),
				documentsApi.userScope(),
				documentsApi.currentJob(),
			]);
			collections  = colls;
			userScopeIds = new Set(scope.map((s: ScopedCollection) => s.id));
			job          = j;
			errorMsg     = null;
			ui.bumpDocs(); // chips rows re-fetch so stale attachments never linger
		} catch (e) {
			errorMsg = e instanceof Error ? e.message : 'Failed to load collections';
		} finally {
			loading = false;
		}
	}

	// Poll while a job is in flight so progress is live; the job itself runs
	// in the cluster, so navigating away never interrupts it.
	onMount(() => {
		refresh();
		const t = setInterval(async () => {
			if (!jobActive) return;
			const before = job?.status;
			job = await documentsApi.currentJob().catch(() => job);
			if (before !== job?.status) refresh();
		}, 2000);
		return () => clearInterval(t);
	});

	function onFilesPicked(e: Event) {
		const input = e.currentTarget as HTMLInputElement;
		pickedFiles = input.files ? Array.from(input.files) : [];
	}

	async function submitUpload() {
		if (!title.trim() || !description.trim() || pickedFiles.length === 0) return;
		uploading = true;
		errorMsg = null;
		try {
			await documentsApi.upload(
				{ title: title.trim(), description: description.trim(), visibility },
				pickedFiles,
			);
			title = ''; description = ''; pickedFiles = [];
			if (fileInput) fileInput.value = '';
			if (folderInput) folderInput.value = '';
			await refresh();
		} catch (e) {
			errorMsg = e instanceof Error ? e.message : 'Upload failed';
		} finally {
			uploading = false;
		}
	}

	async function toggleAccount(c: CollectionInfo) {
		try {
			if (userScopeIds.has(c.id)) await documentsApi.disableUserScope(c.id);
			else await documentsApi.enableUserScope(c.id);
			const scope = await documentsApi.userScope();
			userScopeIds = new Set(scope.map((s) => s.id));
			ui.bumpDocs();
		} catch (e) {
			errorMsg = e instanceof Error ? e.message : 'Could not update';
		}
	}

	async function toggleArchived(c: CollectionInfo) {
		try {
			await documentsApi.setArchived(c.id, !c.archived);
			await refresh();
		} catch (e) {
			errorMsg = e instanceof Error ? e.message : 'Could not update';
		}
	}

	async function hardDelete(c: CollectionInfo) {
		confirmDelete = null;
		try {
			await documentsApi.remove(c.id);
			await refresh();
		} catch (e) {
			// 409 carries the friendly archive-instead suggestion from the backend.
			errorMsg = e instanceof Error ? e.message : 'Could not delete';
		}
	}

	function handleKeydown(e: KeyboardEvent) {
		if (e.key === 'Escape') ui.closeDocuments();
	}
</script>

<svelte:window onkeydown={handleKeydown} />

<div class="fixed inset-0 z-50 flex items-center justify-center bg-charcoal/50 backdrop-blur-sm">
	<button type="button" class="absolute inset-0 w-full h-full cursor-default"
		aria-label="Close modal" onclick={() => ui.closeDocuments()}></button>

	<div role="dialog" aria-modal="true" aria-labelledby="documents-modal-title" tabindex="-1"
		class="relative z-10 w-full mx-4 bg-cream rounded-2xl shadow-2xl overflow-hidden flex flex-col max-h-[90vh] max-w-5xl">

		<div class="flex items-center justify-between px-6 py-4 bg-navy border-b border-navy-light">
			<h2 id="documents-modal-title" class="text-lg font-sans font-semibold text-cream">Document Collections</h2>
			<button onclick={() => ui.closeDocuments()} class="text-sand hover:text-cream transition-colors" aria-label="Close">
				<svg class="w-5 h-5" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
					<path stroke-linecap="round" stroke-linejoin="round" d="M6 18L18 6M6 6l12 12" />
				</svg>
			</button>
		</div>

		<div class="flex-1 overflow-y-auto p-6 flex flex-col gap-6">

			{#if errorMsg}
				<div class="bg-red-50 border border-red-200 rounded-lg px-4 py-2.5 text-sm text-red-700">{errorMsg}</div>
			{/if}

			<!-- ── Processing job progress ─────────────────────────────── -->
			{#if job && (jobActive || job.status === 'failed')}
				<div class={['rounded-xl border px-4 py-3 text-sm',
					job.status === 'failed' ? 'bg-red-50 border-red-200' : 'bg-white border-sand'].join(' ')}>
					{#if jobActive}
						<div class="flex items-center gap-2 font-medium text-navy">
							<span class="inline-block w-2 h-2 rounded-full bg-navy animate-pulse"></span>
							Processing “{job.collection_title}”
						</div>
						<div class="mt-2 flex items-center gap-3">
							<div class="flex-1 h-2 bg-sand rounded-full overflow-hidden">
								<!-- Floor of 4%: before totals are known (and at 0/N) the bar
								     shows a sliver instead of appearing to reset to nothing. -->
								<div class="h-full bg-navy rounded-full transition-all"
									style="width: {Math.max(4, job.total_documents ? Math.round(100 * job.processed_documents / job.total_documents) : 4)}%"></div>
							</div>
							<span class="text-xs text-muted tabular-nums">
								{job.processed_documents}/{job.total_documents} docs · {job.total_chunks} chunks
							</span>
						</div>
						{#if job.current_document}
							<p class="text-xs text-muted mt-1 truncate">Current: {job.current_document}</p>
						{/if}
						<p class="text-xs text-muted-light mt-1">
							Processing continues in the background — you can close this window.
						</p>
					{:else}
						<p class="font-medium text-red-700">Processing “{job.collection_title}” failed</p>
						<p class="text-xs text-red-600 mt-1">{job.error}</p>
					{/if}
				</div>
			{/if}

			<!-- ── Upload ───────────────────────────────────────────────── -->
			<div class="bg-white border border-sand rounded-xl p-4">
				<h3 class="text-sm font-semibold text-navy mb-3">Add a document collection</h3>
				<div class="grid grid-cols-1 sm:grid-cols-2 gap-3">
					<input bind:value={title} maxlength="100" placeholder="Title"
						class="px-3 py-2 text-sm bg-cream border border-sand rounded-lg focus:outline-none focus:ring-1 focus:ring-navy/30" />
					<select bind:value={visibility}
						class="px-3 py-2 text-sm bg-cream border border-sand rounded-lg focus:outline-none">
						<option value="private">Private — only me</option>
						<option value="shared">Shared — all users</option>
					</select>
					<textarea bind:value={description} maxlength="1000" rows="2" placeholder="Description"
						class="sm:col-span-2 px-3 py-2 text-sm bg-cream border border-sand rounded-lg resize-none focus:outline-none focus:ring-1 focus:ring-navy/30"></textarea>
				</div>
				<div class="flex flex-wrap items-center gap-2 mt-3">
					<input bind:this={fileInput} type="file" multiple class="hidden"
						accept=".pdf,.docx,.md,.txt,.html,.pptx,.xlsx" onchange={onFilesPicked} />
					<!-- webkitdirectory: browser folder picker; relative paths preserved -->
					<input bind:this={folderInput} type="file" webkitdirectory class="hidden" onchange={onFilesPicked} />
					<button onclick={() => fileInput?.click()}
						class="px-3 py-1.5 text-xs font-medium border border-navy/40 text-navy rounded-lg hover:bg-navy hover:text-cream transition-colors">
						Choose files…
					</button>
					<button onclick={() => folderInput?.click()}
						class="px-3 py-1.5 text-xs font-medium border border-navy/40 text-navy rounded-lg hover:bg-navy hover:text-cream transition-colors">
						Choose a folder…
					</button>
					{#if pickedFiles.length > 0}
						<span class="text-xs text-muted">{pickedFiles.length} file{pickedFiles.length === 1 ? '' : 's'} selected</span>
					{/if}
					<div class="flex-1"></div>
					<button onclick={submitUpload}
						disabled={uploading || jobActive || !title.trim() || !description.trim() || pickedFiles.length === 0}
						title={jobActive ? 'Wait for the current processing job to finish' : undefined}
						class="px-4 py-1.5 text-xs font-semibold bg-navy text-cream rounded-lg disabled:opacity-40 disabled:cursor-not-allowed hover:bg-navy-light transition-colors">
						{uploading ? 'Uploading…' : 'Upload & process'}
					</button>
				</div>
				<p class="text-[11px] text-muted-light mt-2">
					Supported: PDF, Word, Markdown, text, HTML, PowerPoint, Excel. Originals are not
					retained after processing.
				</p>
			</div>

			<!-- ── Library ─────────────────────────────────────────────── -->
			{#if loading}
				<p class="text-sm text-muted italic">Loading…</p>
			{:else if collections.length === 0}
				<p class="text-sm text-muted italic">No document collections yet.</p>
			{:else}
				<table class="w-full text-sm">
					<thead>
						<tr class="text-left text-xs uppercase tracking-wide text-muted-light border-b border-sand">
							<th class="py-2 pr-3">Collection</th>
							<th class="py-2 pr-3">Visibility</th>
							<th class="py-2 pr-3">Status</th>
							<th class="py-2 pr-3">Docs / Chunks</th>
							<th class="py-2 pr-3">Attach to all my conversations</th>
							<th class="py-2"></th>
						</tr>
					</thead>
					<tbody>
						{#each collections as c (c.id)}
							<tr class={['border-b border-sand/50 align-top', c.archived ? 'opacity-50' : ''].join(' ')}>
								<td class="py-2.5 pr-3">
									<div class="font-medium text-charcoal">{c.title}</div>
									<div class="text-xs text-muted line-clamp-1">{c.description}</div>
								</td>
								<td class="py-2.5 pr-3">
									<span class={['text-xs px-1.5 py-0.5 rounded font-medium',
										c.visibility === 'shared' ? 'bg-navy/10 text-navy' : 'bg-sand text-charcoal'].join(' ')}>
										{c.visibility}
									</span>
								</td>
								<td class="py-2.5 pr-3">
									<span class={['text-xs px-1.5 py-0.5 rounded font-medium',
										c.status === 'ready' ? 'bg-green-50 text-green-700'
										: c.status === 'failed' ? 'bg-red-50 text-red-600'
										: 'bg-amber-50 text-amber-700'].join(' ')}>
										{c.archived ? 'archived' : c.status}
									</span>
								</td>
								<td class="py-2.5 pr-3 text-xs text-muted tabular-nums">{c.documents} / {c.chunks}</td>
								<td class="py-2.5 pr-3">
									<input type="checkbox"
										checked={userScopeIds.has(c.id)}
										disabled={c.archived || c.status !== 'ready'}
										onchange={() => toggleAccount(c)}
										class="accent-[#1e2f4d] w-4 h-4 cursor-pointer disabled:cursor-not-allowed"
										aria-label="Attach to all my conversations" />
								</td>
								<td class="py-2.5 text-right whitespace-nowrap">
									{#if c.owned_by_me || c.visibility === 'shared'}
										<button onclick={() => toggleArchived(c)}
											class="text-xs text-muted hover:text-navy underline mr-2">
											{c.archived ? 'Unarchive' : 'Archive'}
										</button>
										{#if confirmDelete === c.id}
											<button onclick={() => hardDelete(c)}
												class="text-xs text-white bg-red-600 hover:bg-red-700 rounded px-2 py-0.5 mr-1">Confirm</button>
											<button onclick={() => (confirmDelete = null)}
												class="text-xs text-muted underline">Cancel</button>
										{:else}
											<button onclick={() => (confirmDelete = c.id)}
												class="text-xs text-red-600 hover:text-red-700 underline">Delete</button>
										{/if}
									{/if}
								</td>
							</tr>
						{/each}
					</tbody>
				</table>
			{/if}
		</div>
	</div>
</div>
