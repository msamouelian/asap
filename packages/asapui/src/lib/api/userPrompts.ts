import { apiGet, apiPost, apiPut, apiDelete } from './client';

export interface UserPrompt {
	id: string;
	creating_user_id: string;
	title: string;
	prompt_text: string;
	create_ts: string;
	modified_ts: string | null;
	is_global: boolean;
}

export interface PromptTag {
	association_id: string;
	tag_id: string;
	name: string;
	description: string | null;
}

export interface UserPromptCreate { title: string; prompt_text: string; is_global?: boolean; }
export interface UserPromptUpdate { title?: string; prompt_text?: string; is_global?: boolean; }

export const userPromptsApi = {
	list:      ()                              => apiGet<UserPrompt[]>('/user-prompts'),
	create:    (b: UserPromptCreate)           => apiPost<UserPrompt>('/user-prompts', b),
	get:       (id: string)                    => apiGet<UserPrompt>(`/user-prompts/${id}`),
	update:    (id: string, b: UserPromptUpdate) => apiPut<UserPrompt>(`/user-prompts/${id}`, b),
	remove:    (id: string)                    => apiDelete(`/user-prompts/${id}`),
	listTags:  (id: string)                    => apiGet<PromptTag[]>(`/user-prompts/${id}/tags`),
	addTag:    (id: string, tagId: string)     => apiPost<PromptTag>(`/user-prompts/${id}/tags`, { tag_id: tagId }),
	removeTag: (id: string, tagId: string)     => apiDelete(`/user-prompts/${id}/tags/${tagId}`),
};
