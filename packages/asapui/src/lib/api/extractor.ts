import { apiGet, apiPost } from './client';

export interface JobRun {
	name: string;
	started_at: string | null;
	finished_at: string | null;
	status: 'running' | 'succeeded' | 'failed' | 'pending';
	duration_seconds: number | null;
}

export interface ExtractorStatus {
	is_running: boolean;
	current_job: string | null;
	last_run: JobRun | null;
}

export interface TriggerResponse {
	job_name: string;
	started_at: string;
}

export const extractorApi = {
	trigger: () => apiPost<TriggerResponse>('/extractor/trigger', {}),
	status:  () => apiGet<ExtractorStatus>('/extractor/status'),
	history: () => apiGet<JobRun[]>('/extractor/history'),
};
