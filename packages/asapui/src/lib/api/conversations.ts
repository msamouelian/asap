import { apiGet, apiPost, apiPut, apiDelete } from './client';

export interface Conversation {
	id: string;
	user_id: string;
	title: string | null;
	created_ts: string;
	message_count: number;
	context_pct: number | null;
	folder_id: string | null; // null = the implicit root folder 'All'
}

export interface ConversationMessage {
	id: string;
	conversation_id: string;
	role: string;
	message: string;
	reasoning: string | null;
	retrieved_chunks?: {
		id: string; text: string; page_no: number | null;
		heading: string | null; file_name: string; collection: string;
	}[] | null;
	retrieval_query?: string | null;
	created_ts: string;
}

export const conversationsApi = {
	list:     ()                           => apiGet<Conversation[]>('/conversations'),
	create:   (title?: string)             => apiPost<Conversation>('/conversations', { title }),
	get:      (id: string)                 => apiGet<Conversation>(`/conversations/${id}`),
	rename:   (id: string, title: string)  => apiPut<Conversation>(`/conversations/${id}`, { title }),
	move:     (id: string, folderId: string | null) =>
		apiPut<Conversation>(`/conversations/${id}/folder`, { folder_id: folderId }),
	remove:   (id: string)                 => apiDelete(`/conversations/${id}`),
	messages: (id: string)                 => apiGet<ConversationMessage[]>(`/conversations/${id}/messages`),
};
