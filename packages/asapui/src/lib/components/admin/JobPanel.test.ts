/**
 * JobPanel must notice a finished job without the user closing and
 * reopening the panel — including when the tab was in the background and
 * the browser froze its timers.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, cleanup } from '@testing-library/svelte';
import JobPanel from './JobPanel.svelte';
import type { ExtractorStatus, JobRun } from '$lib/api/extractor';

const running: ExtractorStatus = {
	is_running: true, current_job: 'kggenerator-abc',
	last_run: { name: 'kggenerator-abc', started_at: new Date().toISOString(), finished_at: null, status: 'running', duration_seconds: null },
};
const done: ExtractorStatus = {
	is_running: false, current_job: null,
	last_run: { name: 'kggenerator-abc', started_at: new Date().toISOString(), finished_at: new Date().toISOString(), status: 'succeeded', duration_seconds: 8499 },
};
const historyRunning: JobRun[] = [running.last_run!];
const historyDone: JobRun[] = [done.last_run!];

function fakeApi(sequence: ExtractorStatus[]) {
	let i = 0;
	const next = () => sequence[Math.min(i++, sequence.length - 1)];
	let current: ExtractorStatus = sequence[0];
	return {
		trigger: vi.fn(),
		status: vi.fn(async () => { current = next(); return current; }),
		history: vi.fn(async () => (current.is_running ? historyRunning : historyDone)),
	};
}

const flush = async () => { await Promise.resolve(); await new Promise(r => setTimeout(r, 0)); };

beforeEach(() => { vi.useFakeTimers({ shouldAdvanceTime: true }); });
afterEach(() => { cleanup(); vi.useRealTimers(); });

const props = { title: 'Knowledge Graph Generation', buttonLabel: 'Generate', runningLabel: 'Generation in Progress', runningNote: 'note' };

describe('JobPanel', () => {
	it('shows Running with a last-checked time, then Ready after the next poll', async () => {
		const api = fakeApi([running, running, done]);
		render(JobPanel, { props: { ...props, api } });
		await flush();
		expect(screen.getAllByText('Running').length).toBeGreaterThan(0);
		expect(screen.getByTestId('last-checked').textContent).toMatch(/Last checked \d/);

		await vi.advanceTimersByTimeAsync(30_000); // tick → still running
		await vi.advanceTimersByTimeAsync(30_000); // tick → done
		await flush();
		expect(screen.getByText('Ready')).toBeTruthy();
		expect(screen.getAllByText('Succeeded').length).toBeGreaterThan(0);
		expect(screen.queryAllByText('Running')).toHaveLength(0);
	});

	it('refreshes the moment the tab becomes visible again, without waiting 30 s', async () => {
		const api = fakeApi([running, done]);
		render(JobPanel, { props: { ...props, api } });
		await flush();
		expect(screen.getAllByText('Running').length).toBeGreaterThan(0);
		expect(api.status).toHaveBeenCalledTimes(1);

		// Simulate the tab coming back to the foreground 1 s later.
		await vi.advanceTimersByTimeAsync(1000);
		Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'visible' });
		document.dispatchEvent(new Event('visibilitychange'));
		await flush();

		expect(api.status).toHaveBeenCalledTimes(2);
		expect(screen.getByText('Ready')).toBeTruthy();
		expect(screen.queryAllByText('Running')).toHaveLength(0);
	});

	it('surfaces a failed poll instead of silently showing stale state', async () => {
		const api = fakeApi([running, running]);
		api.status.mockImplementationOnce(async () => running).mockImplementationOnce(async () => { throw new Error('backend unreachable'); });
		render(JobPanel, { props: { ...props, api } });
		await flush();
		await vi.advanceTimersByTimeAsync(30_000);
		await flush();
		expect(screen.getByTestId('last-checked').textContent).toMatch(/could not refresh \(backend unreachable\); retrying/);
	});

	it('stops polling once the job has finished', async () => {
		const api = fakeApi([running, done]);
		render(JobPanel, { props: { ...props, api } });
		await flush();
		await vi.advanceTimersByTimeAsync(30_000);
		await flush();
		const calls = api.status.mock.calls.length;
		await vi.advanceTimersByTimeAsync(120_000);
		expect(api.status.mock.calls.length).toBe(calls);
	});
});
