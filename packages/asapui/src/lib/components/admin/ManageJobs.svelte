<script lang="ts">
	import JobPanel from './JobPanel.svelte';
	import { extractorApi } from '$lib/api/extractor';
	import { kgjobApi } from '$lib/api/kgjob';
</script>

<div class="p-6 flex flex-col gap-8 overflow-y-auto">
	<JobPanel
		title="Data Extraction"
		buttonLabel="Run Extraction"
		runningLabel="Extraction in Progress"
		runningNote="Extraction typically takes about 1.5 hours, and the graph is rebuilt from scratch during the run. Status refreshes automatically every 30 seconds."
		api={extractorApi}
	/>

	<JobPanel
		title="Knowledge Graph Generation"
		buttonLabel="Generate Knowledge Graph"
		runningLabel="Generation in Progress"
		runningNote="Deletes the existing knowledge graph and regenerates it from the extracted graph (LLM extraction is incremental — only new collections are processed). Chat responses may be slower while the job shares the inference server. Status refreshes automatically every 30 seconds."
		api={kgjobApi}
	/>
</div>
