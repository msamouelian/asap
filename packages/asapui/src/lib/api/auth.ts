/**
 * PKCE Authorization Code flow helpers for Keycloak.
 *
 * The SPA fetches /api/auth/config on startup to get the Keycloak URL,
 * then drives the full PKCE exchange without a client secret.
 */

export interface OidcConfig {
	keycloak_url: string;
	realm: string;
	client_id: string;
}

export interface TokenSet {
	access_token: string;
	refresh_token: string;
	id_token: string;
	expires_in: number;
	refresh_expires_in: number;
}

// ---------------------------------------------------------------------------
// Fetch OIDC config from backend (unauthenticated)
// ---------------------------------------------------------------------------

export async function fetchOidcConfig(): Promise<OidcConfig> {
	const res = await fetch('/api/auth/config');
	if (!res.ok) throw new Error('Could not fetch auth configuration from backend.');
	return res.json() as Promise<OidcConfig>;
}

// ---------------------------------------------------------------------------
// PKCE helpers
// ---------------------------------------------------------------------------

function generateRandomString(byteLength: number): string {
	const bytes = new Uint8Array(byteLength);
	crypto.getRandomValues(bytes);
	return btoa(String.fromCharCode(...bytes))
		.replace(/\+/g, '-').replace(/\//g, '_').replace(/=/g, '');
}

async function sha256Base64Url(plain: string): Promise<string> {
	const data = new TextEncoder().encode(plain);
	const digest = await crypto.subtle.digest('SHA-256', data);
	return btoa(String.fromCharCode(...new Uint8Array(digest)))
		.replace(/\+/g, '-').replace(/\//g, '_').replace(/=/g, '');
}

// ---------------------------------------------------------------------------
// Auth flow
// ---------------------------------------------------------------------------

/** Redirect the browser to Keycloak with PKCE parameters. */
export async function initiateLogin(config: OidcConfig): Promise<void> {
	const verifier = generateRandomString(96);
	const challenge = await sha256Base64Url(verifier);
	const state = generateRandomString(16);

	sessionStorage.setItem('pkce_verifier', verifier);
	sessionStorage.setItem('pkce_state', state);

	const params = new URLSearchParams({
		response_type: 'code',
		client_id: config.client_id,
		redirect_uri: `${window.location.origin}/callback`,
		scope: 'openid profile email',
		code_challenge: challenge,
		code_challenge_method: 'S256',
		state,
	});

	window.location.href =
		`${config.keycloak_url}/realms/${config.realm}/protocol/openid-connect/auth?${params}`;
}

/** Exchange the authorization code for tokens. Called from /callback. */
export async function exchangeCode(code: string, config: OidcConfig): Promise<TokenSet> {
	const verifier = sessionStorage.getItem('pkce_verifier');
	if (!verifier) throw new Error('PKCE verifier missing — possible CSRF.');

	sessionStorage.removeItem('pkce_verifier');
	sessionStorage.removeItem('pkce_state');

	const body = new URLSearchParams({
		grant_type: 'authorization_code',
		client_id: config.client_id,
		code,
		redirect_uri: `${window.location.origin}/callback`,
		code_verifier: verifier,
	});

	const res = await fetch(
		`${config.keycloak_url}/realms/${config.realm}/protocol/openid-connect/token`,
		{ method: 'POST', headers: { 'Content-Type': 'application/x-www-form-urlencoded' }, body: body.toString() },
	);
	if (!res.ok) throw new Error(`Token exchange failed: HTTP ${res.status}`);
	return res.json() as Promise<TokenSet>;
}

/** Use the refresh token to obtain a new access token. */
export async function refreshAccessToken(refreshToken: string, config: OidcConfig): Promise<TokenSet> {
	const body = new URLSearchParams({
		grant_type: 'refresh_token',
		client_id: config.client_id,
		refresh_token: refreshToken,
	});

	const res = await fetch(
		`${config.keycloak_url}/realms/${config.realm}/protocol/openid-connect/token`,
		{ method: 'POST', headers: { 'Content-Type': 'application/x-www-form-urlencoded' }, body: body.toString() },
	);
	if (!res.ok) throw new Error(`Token refresh failed: HTTP ${res.status}`);
	return res.json() as Promise<TokenSet>;
}

/** Build the Keycloak logout URL. Redirect the browser here to end the Keycloak session. */
export function buildLogoutUrl(config: OidcConfig, idToken: string): string {
	const params = new URLSearchParams({
		client_id: config.client_id,
		id_token_hint: idToken,
		post_logout_redirect_uri: window.location.origin,
	});
	return `${config.keycloak_url}/realms/${config.realm}/protocol/openid-connect/logout?${params}`;
}

/** Decode a JWT payload without verification (client-side display only). */
export function decodeJwtPayload(token: string): Record<string, unknown> {
	try {
		const [, payload] = token.split('.');
		return JSON.parse(atob(payload.replace(/-/g, '+').replace(/_/g, '/'))) as Record<string, unknown>;
	} catch {
		return {};
	}
}
