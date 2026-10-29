/**
 * Reading-progress sync for the EPUB/PDF/comic readers.
 *
 * Contract (docs/READER_INTEGRATION.md §5): debounce `PUT /books/{id}/progress`
 * by 1500ms, and flush immediately on `visibilitychange`/`pagehide` using a
 * keepalive request so a closed tab still lands its last locator.
 */
import { api } from '../api/client';

const DEBOUNCE_MS = 1500;

export interface ProgressState {
	locator: string;
	percentage: number;
	completed?: boolean;
}

export class ProgressSync {
	#bookId: string;
	#timer: ReturnType<typeof setTimeout> | null = null;
	#pending: ProgressState | null = null;
	#flushHandler = () => this.#flush(true);
	#deviceName: string;

	constructor(bookId: string) {
		this.#bookId = bookId;
		this.#deviceName = detectDevice();
		window.addEventListener('pagehide', this.#flushHandler);
		document.addEventListener('visibilitychange', this.#flushHandler);
	}

	/** Queue a locator; the request fires after the debounce window. */
	update(state: ProgressState): void {
		this.#pending = state;
		if (this.#timer) clearTimeout(this.#timer);
		this.#timer = setTimeout(() => this.#flush(false), DEBOUNCE_MS);
	}

	#flush(immediate: boolean): void {
		const state = this.#pending;
		if (!state) return;
		this.#pending = null;
		if (this.#timer) {
			clearTimeout(this.#timer);
			this.#timer = null;
		}
		const body = JSON.stringify({
			locator: state.locator,
			percentage: state.percentage,
			completed: state.completed ?? state.percentage >= 99,
			device_name: this.#deviceName
		});
		if (immediate) {
			// A closing page cannot await a request: use keepalive fetch so the
			// final locator still lands (sendBeacon cannot attach headers).
			void fetch(`/api/v1/books/${this.#bookId}/progress`, {
				method: 'PUT',
				headers: {
					'Content-Type': 'application/json',
					Authorization: `Bearer ${localStorage.getItem('hearth.access') ?? ''}`
				},
				body,
				keepalive: true
			}).catch(() => {});
			return;
		}
		void api
			.put(`/books/${this.#bookId}/progress`, {
				body: JSON.parse(body) as Record<string, unknown>
			})
			.catch(() => {
				/* progress is best-effort */
			});
	}

	dispose(): void {
		this.#flush(true);
		window.removeEventListener('pagehide', this.#flushHandler);
		document.removeEventListener('visibilitychange', this.#flushHandler);
	}
}

function detectDevice(): string {
	const ua = navigator.userAgent;
	if (/Tizen|SmartTV|NetCast|Web0S|WebOS/i.test(ua)) return 'TV';
	if (/iPad|Tablet/i.test(ua)) return 'Tablet';
	if (/Android/i.test(ua)) return 'Mobile';
	return 'Web';
}
