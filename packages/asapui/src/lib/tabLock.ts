/**
 * One ASAP tab per browser profile.
 *
 * Tokens live only in memory and are not shared between tabs, and Keycloak
 * rotates refresh tokens, so a second tab with its own token chain would
 * invalidate the first tab's refresh token on every refresh (and vice versa).
 * Rather than let two tabs bounce each other through Keycloak, the second
 * tab is told the app is already open.
 *
 * Implemented with the Web Locks API: the first tab takes a named lock and
 * holds it for its lifetime; the browser releases it when the tab closes or
 * navigates away (including the redirect to Keycloak). Browsers without the
 * API (none current) simply allow the tab.
 */

const LOCK_NAME = 'asap-single-tab';

/**
 * Try to become the app's only tab. Resolves true if this tab now holds the
 * lock (or the browser cannot enforce one), false if another tab holds it.
 * Never rejects.
 */
export function claimTab(locks: LockManager | undefined = globalThis.navigator?.locks): Promise<boolean> {
	if (!locks?.request) return Promise.resolve(true);
	return new Promise<boolean>(resolve => {
		locks.request(LOCK_NAME, { ifAvailable: true }, lock => {
			if (!lock) {
				resolve(false);
				return;
			}
			resolve(true);
			// Hold the lock until the tab goes away.
			return new Promise<void>(() => {});
		}).catch(() => resolve(true));
	});
}
