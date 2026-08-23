import { apiGet, apiPost, apiPut, apiDelete } from './client';

export interface Folder {
	id: string;
	parent_id: string | null; // null = child of the implicit root folder 'All'
	name: string;
	created_ts: string;
}

export const foldersApi = {
	list:   ()                                      => apiGet<Folder[]>('/folders'),
	create: (name: string, parentId: string | null) => apiPost<Folder>('/folders', { name, parent_id: parentId }),
	rename: (id: string, name: string)              => apiPut<Folder>(`/folders/${id}`, { name }),
	remove: (id: string)                            => apiDelete(`/folders/${id}`),
};
