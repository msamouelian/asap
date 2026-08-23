import { apiPost } from './client';

export interface CypherRunResult {
	columns: string[];
	rows: unknown[][];
	row_count: number;
	truncated: boolean;
}

export const cypherApi = {
	run: (query: string) => apiPost<CypherRunResult>('/cypher/run', { query }),
};
