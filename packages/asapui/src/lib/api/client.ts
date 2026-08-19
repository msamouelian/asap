import { auth } from '$lib/stores/auth.svelte';

const BASE = '/api';

async function apiFetch(path: string, options: RequestInit = {}): Promise<Response> {
	if (auth.isAuthenticated) await auth.ensureFreshToken();

	const headers: Record<string, string> = {
		'Content-Type': 'application/json',
		...(options.headers as Record<string, string>),
	};
	if (auth.accessToken) headers['Authorization'] = `Bearer ${auth.accessToken}`;

	const res = await fetch(`${BASE}${path}`, { ...options, headers });

	if (res.status === 401 || res.status === 403) {
		// 401 = token invalid/expired after refresh; 403 = account deactivated.
		// Logout clears state and redirects to Keycloak.
		auth.logout();
		throw new Error('Session expired or account deactivated.');
	}
	return res;
}

export async function apiGet<T>(path: string): Promise<T> {
	const res = await apiFetch(path);
	if (!res.ok) {
		const err = await res.json().catch(() => ({ detail: `HTTP ${res.status}` }));
		throw new Error(err.detail ?? `HTTP ${res.status}`);
	}
	return res.json() as Promise<T>;
}

export async function apiPost<T>(path: string, data?: unknown): Promise<T> {
	const res = await apiFetch(path, {
		method: 'POST',
		body: data !== undefined ? JSON.stringify(data) : undefined,
	});
	if (!res.ok) {
		const err = await res.json().catch(() => ({ detail: `HTTP ${res.status}` }));
		throw new Error(err.detail ?? `HTTP ${res.status}`);
	}
	return res.json() as Promise<T>;
}

export async function apiPut<T>(path: string, data: unknown): Promise<T> {
	const res = await apiFetch(path, { method: 'PUT', body: JSON.stringify(data) });
	if (!res.ok) {
		const err = await res.json().catch(() => ({ detail: `HTTP ${res.status}` }));
		throw new Error(err.detail ?? `HTTP ${res.status}`);
	}
	// 204 No Content (e.g. scope toggles) has no body to parse.
	if (res.status === 204) return undefined as T;
	return res.json() as Promise<T>;
}

export async function apiPatch<T>(path: string, data: unknown): Promise<T> {
	const res = await apiFetch(path, { method: 'PATCH', body: JSON.stringify(data) });
	if (!res.ok) {
		const err = await res.json().catch(() => ({ detail: `HTTP ${res.status}` }));
		throw new Error(err.detail ?? `HTTP ${res.status}`);
	}
	// 204 No Content (e.g. scope toggles) has no body to parse.
	if (res.status === 204) return undefined as T;
	return res.json() as Promise<T>;
}

export async function apiDelete(path: string): Promise<void> {
	const res = await apiFetch(path, { method: 'DELETE' });
	if (!res.ok) {
		const err = await res.json().catch(() => ({ detail: `HTTP ${res.status}` }));
		throw new Error(err.detail ?? `HTTP ${res.status}`);
	}
}

/** Returns the raw Response for SSE streaming. */
export async function apiStream(path: string, data: unknown, signal?: AbortSignal): Promise<Response> {
	if (auth.isAuthenticated) await auth.ensureFreshToken();

	const headers: Record<string, string> = { 'Content-Type': 'application/json' };
	if (auth.accessToken) headers['Authorization'] = `Bearer ${auth.accessToken}`;

	const res = await fetch(`${BASE}${path}`, {
		method: 'POST',
		headers,
		body: JSON.stringify(data),
		signal,
	});

	if (res.status === 401 || res.status === 403) {
		auth.logout();
		throw new Error('Session expired.');
	}
	if (!res.ok) throw new Error(`HTTP ${res.status}`);
	return res;
}
