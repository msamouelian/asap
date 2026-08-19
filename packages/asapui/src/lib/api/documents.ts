import { apiDelete, apiGet, apiPatch, apiPut } from './client';
import { auth } from '$lib/stores/auth.svelte';

export interface CollectionInfo {
	id: string;
	title: string;
	description: string | null;
	visibility: 'private' | 'shared';
	archived: boolean;
	status: 'processing' | 'ready' | 'failed';
	uploaded_by: string;
	owned_by_me: boolean;
	source_location: string | null;
	created_at: string | null;
	documents: number;
	chunks: number;
}

export interface JobProgress {
	id: string;
	status: 'pending' | 'running' | 'completed' | 'failed';
	collection_title: string;
	document_collection_id: string;
	total_documents: number;
	processed_documents: number;
	total_chunks: number;
	current_document: string | null;
	error: string | null;
	created_ts: string;
	completed_ts: string | null;
}

export interface ScopedCollection {
	id: string;
	title: string;
	visibility: 'private' | 'shared';
	scope: 'user' | 'conversation';
}

export const documentsApi = {
	list: () => apiGet<CollectionInfo[]>('/documents/collections'),

	setArchived: (id: string, archived: boolean) =>
		apiPatch<CollectionInfo>(`/documents/collections/${id}`, { archived }),

	remove: (id: string) => apiDelete(`/documents/collections/${id}`),

	currentJob: () => apiGet<JobProgress | null>('/documents/jobs/current'),

	userScope: () => apiGet<ScopedCollection[]>('/documents/scopes/user'),
	enableUserScope: (id: string) => apiPut<void>(`/documents/scopes/user/${id}`, {}),
	disableUserScope: (id: string) => apiDelete(`/documents/scopes/user/${id}`),

	effectiveScope: (conversationId: string) =>
		apiGet<ScopedCollection[]>(`/documents/scopes/conversation/${conversationId}`),
	enableConversationScope: (conversationId: string, id: string) =>
		apiPut<void>(`/documents/scopes/conversation/${conversationId}/${id}`, {}),
	disableConversationScope: (conversationId: string, id: string) =>
		apiDelete(`/documents/scopes/conversation/${conversationId}/${id}`),

	/**
	 * Multipart upload. Folder uploads pass each file's webkitRelativePath as
	 * the filename so the backend can preserve the structure.
	 */
	async upload(
		meta: { title: string; description: string; visibility: 'private' | 'shared' },
		files: File[],
	): Promise<{ job_id: string; document_collection_id: string; staged_files: number }> {
		if (auth.isAuthenticated) await auth.ensureFreshToken();
		const form = new FormData();
		form.append('title', meta.title);
		form.append('description', meta.description);
		form.append('visibility', meta.visibility);
		const first = files[0] as File & { webkitRelativePath?: string };
		form.append(
			'source_location',
			first?.webkitRelativePath ? first.webkitRelativePath.split('/')[0] : (first?.name ?? ''),
		);
		for (const f of files) {
			const rel = (f as File & { webkitRelativePath?: string }).webkitRelativePath;
			form.append('files', f, rel && rel.length > 0 ? rel : f.name);
		}
		const headers: Record<string, string> = {};
		if (auth.accessToken) headers['Authorization'] = `Bearer ${auth.accessToken}`;
		const res = await fetch('/api/documents/collections', {
			method: 'POST',
			headers, // no Content-Type: the browser sets the multipart boundary
			body: form,
		});
		if (!res.ok) {
			const err = await res.json().catch(() => ({ detail: `HTTP ${res.status}` }));
			throw new Error(err.detail ?? `HTTP ${res.status}`);
		}
		return res.json();
	},
};
