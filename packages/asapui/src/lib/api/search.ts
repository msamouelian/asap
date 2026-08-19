import { apiPost } from './client';

export interface HybridResult {
	rank: number;
	title: string | null;
	record_type: string | null;
	score: number | null;
	match_source: string | null;
	in_collection: string | null;
	in_collection_url: string | null;
	excerpt: string | null;
	aspace_url: string | null;
}

export interface HybridSearchResponse {
	topic: string;
	count: number;
	results: HybridResult[];
}

export const searchApi = {
	hybrid: (topic: string, limit: number) =>
		apiPost<HybridSearchResponse>('/search/hybrid', { topic, limit }),
};
