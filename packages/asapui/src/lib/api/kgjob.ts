import { apiGet, apiPost } from './client';
import type { ExtractorStatus, JobRun, TriggerResponse } from './extractor';

/** Knowledge-graph generation job — same shapes as the extractor job. */
export const kgjobApi = {
	trigger: () => apiPost<TriggerResponse>('/kggenerator/trigger', {}),
	status:  () => apiGet<ExtractorStatus>('/kggenerator/status'),
	history: () => apiGet<JobRun[]>('/kggenerator/history'),
};
