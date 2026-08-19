import { apiGet, apiPost, apiPut, apiDelete } from './client';

export interface Tag {
	id: string;
	name: string;
	description: string | null;
}

export const tagsApi = {
	list:   ()                                    => apiGet<Tag[]>('/tags'),
	create: (name: string, description?: string)  => apiPost<Tag>('/tags', { name, description }),
	update: (id: string, name?: string, desc?: string) => apiPut<Tag>(`/tags/${id}`, { name, description: desc }),
	remove: (id: string)                          => apiDelete(`/tags/${id}`),
};
