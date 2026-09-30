/**
 * OIDC callback validation: the state check must be strict.
 */

import { describe, it, expect } from 'vitest';
import { readCallbackParams } from './auth';

describe('readCallbackParams', () => {
	it('accepts a code whose state matches the saved state', () => {
		expect(readCallbackParams('?code=abc&state=s1', 's1')).toEqual({ code: 'abc' });
	});

	it('rejects when the saved state is missing (was previously skipped)', () => {
		expect(() => readCallbackParams('?code=abc&state=s1', null)).toThrow(/State mismatch/);
	});

	it('rejects when the returned state is missing', () => {
		expect(() => readCallbackParams('?code=abc', 's1')).toThrow(/State mismatch/);
	});

	it('rejects a mismatched state', () => {
		expect(() => readCallbackParams('?code=abc&state=other', 's1')).toThrow(/State mismatch/);
	});

	it('rejects a missing code', () => {
		expect(() => readCallbackParams('?state=s1', 's1')).toThrow(/No authorization code/);
	});

	it('surfaces a Keycloak error response', () => {
		expect(() => readCallbackParams('?error=access_denied&error_description=User+cancelled', 's1'))
			.toThrow('User cancelled');
	});
});
