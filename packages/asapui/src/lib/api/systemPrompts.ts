import { apiDelete, apiGet, apiPost, apiPut } from './client';

export interface SystemPrompt {
    id:               string;
    creating_user_id: string;
    title:            string;
    description:      string | null;
    prompt_text:      string;
    is_default:       boolean;
    create_ts:        string;
    modified_ts:      string | null;
    expiration_ts:    string | null;
    tag_ids:          string[];
}

export interface SystemPromptTag {
    association_id: string;
    tag_id:         string;
    name:           string;
    description:    string | null;
}

export const systemPromptsApi = {
    list: (activeOnly = true) =>
        apiGet<SystemPrompt[]>(`/system-prompts?active_only=${activeOnly}`),

    get: (id: string) =>
        apiGet<SystemPrompt>(`/system-prompts/${id}`),

    create: (body: { title: string; description?: string | null; prompt_text: string; is_default?: boolean }) =>
        apiPost<SystemPrompt>('/system-prompts', body),

    update: (id: string, body: { title?: string; description?: string | null; prompt_text?: string; is_default?: boolean }) =>
        apiPut<SystemPrompt>(`/system-prompts/${id}`, body),

    expire: (id: string) =>
        apiDelete(`/system-prompts/${id}`),

    unexpire: (id: string) =>
        apiPost<SystemPrompt>(`/system-prompts/${id}/unexpire`, {}),

    listTags: (id: string) =>
        apiGet<SystemPromptTag[]>(`/system-prompts/${id}/tags`),

    addTag: (id: string, tagId: string) =>
        apiPost<SystemPromptTag>(`/system-prompts/${id}/tags`, { tag_id: tagId }),

    removeTag: (id: string, tagId: string) =>
        apiDelete(`/system-prompts/${id}/tags/${tagId}`),
};
