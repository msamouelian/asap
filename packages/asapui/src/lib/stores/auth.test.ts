/**
 * Auth store: tokens must never touch browser storage.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';

// Network-touching helpers are mocked; the store is tested in isolation.
vi.mock('$lib/api/auth', () => ({
	fetchOidcConfig: vi.fn(async () => ({ keycloak_url: 'https://kc.test', realm: 'asap', client_id: 'asapui' })),
	initiateLogin: vi.fn(async () => {}),
	refreshAccessToken: vi.fn(),
	buildLogoutUrl: vi.fn(() => 'https://kc.test/logout'),
	decodeJwtPayload: vi.fn(() => ({})),
}));

import { initiateLogin, refreshAccessToken } from '$lib/api/auth';
import { AuthStore, LEGACY_STORAGE_KEYS, purgeLegacyStorage } from './auth.svelte';

const tokens = (n = 1, expiresIn = 1200) => ({
	access_token: `access-${n}`,
	refresh_token: `refresh-${n}`,
	id_token: `id-${n}`,
	expires_in: expiresIn,
	refresh_expires_in: 3600,
});

function storageDump(): Record<string, string> {
	const out: Record<string, string> = {};
	for (let i = 0; i < localStorage.length; i++) {
		const k = localStorage.key(i)!;
		out[k] = localStorage.getItem(k)!;
	}
	return out;
}

beforeEach(() => {
	localStorage.clear();
	sessionStorage.clear();
	vi.mocked(refreshAccessToken).mockReset();
	vi.mocked(initiateLogin).mockClear();
});

// ── No persistence ──────────────────────────────────────────────────────────

describe('tokens are never persisted', () => {
	it('login leaves localStorage and sessionStorage empty', () => {
		const store = new AuthStore();
		store.hydrate();
		store.setTokens(tokens());
		expect(store.isAuthenticated).toBe(true);
		expect(storageDump()).toEqual({});
		expect(sessionStorage.length).toBe(0);
	});

	it('refresh leaves storage empty', async () => {
		const store = new AuthStore();
		store.setTokens(tokens(1, 10)); // expires in 10s → inside the refresh window
		vi.mocked(refreshAccessToken).mockResolvedValue(tokens(2));
		expect(await store.ensureFreshToken()).toBe(true);
		expect(store.accessToken).toBe('access-2');
		expect(storageDump()).toEqual({});
	});

	it('does not read tokens back from storage on startup', () => {
		localStorage.setItem('asap_access_token', 'planted');
		localStorage.setItem('asap_refresh_token', 'planted');
		const store = new AuthStore();
		store.hydrate();
		expect(store.isAuthenticated).toBe(false);
		expect(store.accessToken).toBeNull();
	});
});

describe('legacy on-disk tokens are purged', () => {
	it('hydrate removes every legacy key and nothing else', () => {
		for (const k of LEGACY_STORAGE_KEYS) localStorage.setItem(k, 'old');
		localStorage.setItem('asap_left_width', '320'); // a UI preference, must survive
		new AuthStore().hydrate();
		for (const k of LEGACY_STORAGE_KEYS) expect(localStorage.getItem(k)).toBeNull();
		expect(localStorage.getItem('asap_left_width')).toBe('320');
	});

	it('purgeLegacyStorage tolerates unavailable storage', () => {
		const spy = vi.spyOn(Storage.prototype, 'removeItem').mockImplementation(() => { throw new Error('blocked'); });
		expect(() => purgeLegacyStorage()).not.toThrow();
		spy.mockRestore();
	});

	it('logout clears memory and purges legacy keys', () => {
		const store = new AuthStore();
		store.setTokens(tokens());
		localStorage.setItem('asap_refresh_token', 'old');
		const nav = vi.spyOn(window, 'location', 'get').mockReturnValue({ href: '' } as Location);
		store.logout();
		nav.mockRestore();
		expect(store.accessToken).toBeNull();
		expect(store.refreshToken).toBeNull();
		expect(store.idToken).toBeNull();
		expect(store.user).toBeNull();
		expect(localStorage.getItem('asap_refresh_token')).toBeNull();
	});
});

// ── Refresh behaviour ───────────────────────────────────────────────────────

describe('ensureFreshToken', () => {
	it('does nothing when the token is still fresh', async () => {
		const store = new AuthStore();
		store.setTokens(tokens());
		expect(await store.ensureFreshToken()).toBe(true);
		expect(refreshAccessToken).not.toHaveBeenCalled();
	});

	it('refreshes once for concurrent callers (rotated tokens are single-use)', async () => {
		const store = new AuthStore();
		store.setTokens(tokens(1, 10));
		vi.mocked(refreshAccessToken).mockResolvedValue(tokens(2));
		const results = await Promise.all([store.ensureFreshToken(), store.ensureFreshToken(), store.ensureFreshToken()]);
		expect(results).toEqual([true, true, true]);
		expect(refreshAccessToken).toHaveBeenCalledTimes(1);
	});

	it('re-authenticates via Keycloak (no Keycloak-wide logout) when refresh fails', async () => {
		const store = new AuthStore();
		store.oidcConfig = { keycloak_url: 'https://kc.test', realm: 'asap', client_id: 'asapui' };
		store.setTokens(tokens(1, 10));
		vi.mocked(refreshAccessToken).mockRejectedValue(new Error('invalid_grant'));
		expect(await store.ensureFreshToken()).toBe(false);
		expect(store.accessToken).toBeNull();
		expect(store.refreshToken).toBeNull();
		expect(initiateLogin).toHaveBeenCalledTimes(1);
		expect(store.leaving).toBe(true);
	});
});
