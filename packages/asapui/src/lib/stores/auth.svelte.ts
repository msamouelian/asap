/**
 * Auth store — OIDC tokens held in memory only.
 *
 * Tokens are never written to localStorage/sessionStorage/IndexedDB. Any
 * script running on this origin (an XSS, a rogue extension) could read
 * on-disk tokens long after the tab closed and keep the session alive with
 * the refresh token; in-memory tokens die with the tab. On reload the app has
 * no token and redirects to Keycloak, whose own HttpOnly session cookie
 * completes the round trip without a password prompt.
 *
 * One tab at a time: tokens are not shared between tabs (see tabLock.ts).
 * Keycloak rotates refresh tokens (revokeRefreshToken), so two tabs with
 * independent token chains would invalidate each other on every refresh.
 *
 * A failed refresh re-authenticates via Keycloak (silent while the Keycloak
 * session lives) rather than ending the Keycloak session outright.
 */

import type { User } from '$lib/api/users';
import type { OidcConfig, TokenSet } from '$lib/api/auth';
import {
	buildLogoutUrl,
	decodeJwtPayload,
	fetchOidcConfig,
	initiateLogin,
	refreshAccessToken,
} from '$lib/api/auth';

/** Keys an earlier version wrote to localStorage. Purged on every startup. */
export const LEGACY_STORAGE_KEYS = [
	'asap_access_token',
	'asap_refresh_token',
	'asap_id_token',
	'asap_token_expiry',
] as const;

/** Refresh when the access token has less than this long to live. */
const REFRESH_SKEW_MS = 30_000;

/** Remove tokens an older build stored on disk. Safe to call repeatedly. */
export function purgeLegacyStorage(): void {
	try {
		for (const k of LEGACY_STORAGE_KEYS) localStorage.removeItem(k);
	} catch {
		/* storage unavailable — nothing to purge */
	}
}

export class AuthStore {
	accessToken  = $state<string | null>(null);
	refreshToken = $state<string | null>(null);
	idToken      = $state<string | null>(null);
	tokenExpiry  = $state<number>(0);
	user         = $state<User | null>(null);
	oidcConfig   = $state<OidcConfig | null>(null);

	get isAuthenticated(): boolean { return !!this.accessToken; }
	get isAdmin():         boolean { return this.user?.role === 'admin'; }

	/** First name for the greeting banner, decoded from id_token or user profile. */
	get displayName(): string {
		if (this.user) {
			return this.user.full_name.split(' ')[0] || this.user.email;
		}
		if (this.idToken) {
			const p = decodeJwtPayload(this.idToken);
			return (p['given_name'] as string) || (p['name'] as string) || '';
		}
		return '';
	}

	async loadConfig(): Promise<OidcConfig> {
		if (!this.oidcConfig) this.oidcConfig = await fetchOidcConfig();
		return this.oidcConfig;
	}

	/**
	 * Startup. Purges any tokens an older build left on disk. Tokens
	 * themselves are never read from storage, so a fresh tab starts signed out.
	 */
	hydrate(): void {
		purgeLegacyStorage();
	}

	/** Store a token set from login or refresh (memory only). */
	setTokens(tokens: TokenSet): void {
		this.accessToken  = tokens.access_token;
		this.refreshToken = tokens.refresh_token;
		this.idToken      = tokens.id_token;
		this.tokenExpiry  = Date.now() + tokens.expires_in * 1000;
	}

	private clear(): void {
		this.accessToken = this.refreshToken = this.idToken = null;
		this.tokenExpiry = 0;
		this.user = null;
	}

	setUser(u: User): void { this.user = u; }

	async loadUser(): Promise<void> {
		if (!this.accessToken) return;
		try {
			const { usersApi } = await import('$lib/api/users');
			this.user = await usersApi.me();
		} catch {
			// 401/403 handled by client.ts
		}
	}

	// In-flight refresh shared by concurrent callers. Refresh tokens are
	// single-use (Keycloak revokeRefreshToken); parallel refreshes would race
	// and the loser's failure would bounce the tab through Keycloak.
	private refreshInFlight: Promise<boolean> | null = null;

	/**
	 * Refresh the access token if it expires within REFRESH_SKEW_MS.
	 * Returns false (after starting re-authentication) if refresh fails.
	 */
	async ensureFreshToken(): Promise<boolean> {
		if (!this.accessToken) return false;
		if (this.tokenExpiry - Date.now() > REFRESH_SKEW_MS) return true;
		if (!this.refreshToken) return false;
		this.refreshInFlight ??= (async () => {
			try {
				const config = await this.loadConfig();
				this.setTokens(await refreshAccessToken(this.refreshToken!, config));
				return true;
			} catch {
				await this.reauthenticate();
				return false;
			} finally {
				this.refreshInFlight = null;
			}
		})();
		return this.refreshInFlight;
	}

	async login(): Promise<void> {
		const config = await this.loadConfig();
		this.leaving = true;
		await initiateLogin(config);
	}

	/**
	 * Drop the tokens and go back through Keycloak. While the Keycloak session
	 * is alive this completes without a password prompt.
	 */
	private async reauthenticate(): Promise<void> {
		this.clear();
		try {
			await this.login();
		} catch {
			window.location.href = '/';
		}
	}

	// True once a deliberate logout/re-auth navigation has started. The chat
	// panel's beforeunload guard checks this so signing out doesn't ALSO
	// trigger the browser's "leave site?" prompt.
	leaving = $state(false);

	/** Explicit sign-out: ends the Keycloak session. */
	logout(): void {
		this.leaving = true;
		const logoutUrl = this.oidcConfig && this.idToken
			? buildLogoutUrl(this.oidcConfig, this.idToken)
			: null;

		this.clear();
		purgeLegacyStorage();

		window.location.href = logoutUrl ?? '/';
	}
}

export const auth = new AuthStore();
