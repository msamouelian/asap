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

	/** Re-parent a folder (parentId null = root). The subtree moves with it. */
	async move(id: string, parentId: string | null) {
		const f = this.folders.find(x => x.id === id);
		if (!f || f.parent_id === parentId) return;
		const updated = await foldersApi.move(id, parentId);
		f.parent_id = updated.parent_id;
	}

	/** True when `candidateId` is `folderId` itself or lies anywhere inside its subtree. */
	isSelfOrDescendant(candidateId: string | null, folderId: string): boolean {
		let cur: string | null = candidateId;
		const seen = new Set<string>();
		while (cur !== null && !seen.has(cur)) {
			if (cur === folderId) return true;
			seen.add(cur);
			cur = this.folders.find(x => x.id === cur)?.parent_id ?? null;
		}
		return false;
	}

	/** Whether a folder may be moved to destination key ('root' or a folder id). */
	canMoveFolderTo(folderId: string, destKey: string): boolean {
		const dest = destKey === 'root' ? null : destKey;
		const f = this.folders.find(x => x.id === folderId);
		if (!f || f.parent_id === dest) return false;
		return dest === null || !this.isSelfOrDescendant(dest, folderId);
	}
}

export const folders = new FolderStore();
