import { describe, it, expect } from 'vitest';
import { claimTab } from './tabLock';

/** Minimal in-memory LockManager: one holder per name, ifAvailable semantics. */
function fakeLocks() {
	const held = new Set<string>();
	const release = new Map<string, () => void>();
	const manager = {
		request: async (name: string, opts: LockOptions, cb: (lock: Lock | null) => unknown) => {
			if (held.has(name)) return cb(null);
			held.add(name);
			const p = Promise.resolve(cb({ name, mode: 'exclusive' } as Lock));
			// The lock is released when the callback's promise settles.
			void p.finally(() => held.delete(name));
			release.set(name, () => held.delete(name));
			return p;
		},
	} as unknown as LockManager;
	return { manager, held, release };
}

describe('claimTab', () => {
	it('first tab gets the lock', async () => {
		const { manager, held } = fakeLocks();
		expect(await claimTab(manager)).toBe(true);
		expect(held.size).toBe(1);
	});

	it('second tab is refused while the first holds the lock', async () => {
		const { manager } = fakeLocks();
		expect(await claimTab(manager)).toBe(true);
		expect(await claimTab(manager)).toBe(false);
	});

	it('a tab can claim again once the holder is gone', async () => {
		const { manager, release } = fakeLocks();
		expect(await claimTab(manager)).toBe(true);
		release.get('asap-single-tab')!();
		expect(await claimTab(manager)).toBe(true);
	});

	it('allows the tab when the browser has no Web Locks API', async () => {
		expect(await claimTab(undefined)).toBe(true);
	});

	it('allows the tab if the lock request itself fails', async () => {
		const broken = { request: () => Promise.reject(new Error('nope')) } as unknown as LockManager;
		expect(await claimTab(broken)).toBe(true);
	});
});
