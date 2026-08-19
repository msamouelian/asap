import { apiGet, apiPatch } from './client';

export interface User {
	id: string;
	keycloak_id: string;
	email: string;
	full_name: string;
	role: string;    // 'admin' | 'user'
	status: string;  // 'active' | 'inactive'
	system_prompt_id: string | null;
	system_prompt_title: string | null;
}

export const usersApi = {
	me:        ()                                           => apiGet<User>('/users/me'),
	list:      ()                                           => apiGet<User[]>('/users'),
	setStatus: (id: string, status: 'active' | 'inactive') =>
		apiPatch<User>(`/users/${id}/status`, { status }),
	assignSystemPrompt: (id: string, systemPromptId: string | null) =>
		apiPatch<User>(`/users/${id}/system-prompt`, { system_prompt_id: systemPromptId }),
};
