import type { Folder } from '$lib/api/folders';
import { foldersApi } from '$lib/api/folders';

class FolderStore {
	folders = $state<Folder[]>([]);
	loaded  = $state(false);

	async load() {
		this.folders = await foldersApi.list();
		this.loaded = true;
	}

	childrenOf(parentId: string | null): Folder[] {
		return this.folders
			.filter(f => f.parent_id === parentId)
			.sort((a, b) => a.name.localeCompare(b.name, undefined, { sensitivity: 'base' }));
	}

	async create(name: string, parentId: string | null): Promise<Folder> {
		const folder = await foldersApi.create(name, parentId);
		this.folders = [...this.folders, folder];
		return folder;
	}

	async rename(id: string, name: string) {
		const updated = await foldersApi.rename(id, name);
		const f = this.folders.find(x => x.id === id);
		if (f) f.name = updated.name;
	}

	async remove(id: string) {
		await foldersApi.remove(id);
		this.folders = this.folders.filter(f => f.id !== id);
	}
}

export const folders = new FolderStore();
