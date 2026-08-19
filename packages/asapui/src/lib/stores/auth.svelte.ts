import type { User } from '$lib/api/users';
import type { OidcConfig, TokenSet } from '$lib/api/auth';
import {
	buildLogoutUrl,
	decodeJwtPayload,
	fetchOidcConfig,
	initiateLogin,
	refreshAccessToken,
} from '$lib/api/auth';

const K_ACCESS  = 'asap_access_token';
const K_REFRESH = 'asap_refresh_token';
const K_ID      = 'asap_id_token';
const K_EXPIRY  = 'asap_token_expiry'; // epoch ms

class AuthStore {
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

	hydrate(): void {
		this.accessToken  = localStorage.getItem(K_ACCESS);
		this.refreshToken = localStorage.getItem(K_REFRESH);
		this.idToken      = localStorage.getItem(K_ID);
		this.tokenExpiry  = parseInt(localStorage.getItem(K_EXPIRY) ?? '0', 10);
	}

	setTokens(tokens: TokenSet): void {
		this.accessToken  = tokens.access_token;
		this.refreshToken = tokens.refresh_token;
		this.idToken      = tokens.id_token;
		this.tokenExpiry  = Date.now() + tokens.expires_in * 1000;
		localStorage.setItem(K_ACCESS,  tokens.access_token);
		localStorage.setItem(K_REFRESH, tokens.refresh_token);
		localStorage.setItem(K_ID,      tokens.id_token);
		localStorage.setItem(K_EXPIRY,  String(this.tokenExpiry));
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

	// In-flight refresh shared by concurrent callers. Keycloak refresh tokens
	// may be single-use (revokeRefreshToken); parallel refreshes would race
	// and the loser's failure would log the whole session out.
	private refreshInFlight: Promise<boolean> | null = null;

	/**
	 * Refresh the access token if it expires within 30 seconds.
	 * Returns false if refresh fails — caller should redirect to login.
	 */
	async ensureFreshToken(): Promise<boolean> {
		if (!this.accessToken) return false;
		if (this.tokenExpiry - Date.now() > 30_000) return true;
		if (!this.refreshToken) return false;
		this.refreshInFlight ??= (async () => {
			try {
				const config = await this.loadConfig();
				this.setTokens(await refreshAccessToken(this.refreshToken!, config));
				return true;
			} catch {
				this.logout();
				return false;
			} finally {
				this.refreshInFlight = null;
			}
		})();
		return this.refreshInFlight;
	}

	async login(): Promise<void> {
		const config = await this.loadConfig();
		await initiateLogin(config);
	}

	// True once a deliberate logout navigation has started. The chat panel's
	// beforeunload guard checks this so signing out doesn't ALSO trigger the
	// browser's "leave site?" prompt (which, if cancelled, would strand the
	// app with cleared tokens and no way to make API calls).
	leaving = $state(false);

	logout(): void {
		this.leaving = true;
		const logoutUrl = this.oidcConfig && this.idToken
			? buildLogoutUrl(this.oidcConfig, this.idToken)
			: null;

		this.accessToken = this.refreshToken = this.idToken = null;
		this.tokenExpiry = 0;
		this.user = null;

		localStorage.removeItem(K_ACCESS);
		localStorage.removeItem(K_REFRESH);
		localStorage.removeItem(K_ID);
		localStorage.removeItem(K_EXPIRY);

		window.location.href = logoutUrl ?? '/';
	}
}

export const auth = new AuthStore();
