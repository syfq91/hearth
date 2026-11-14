<script lang="ts">
	import { X, RotateCcw } from '@lucide/svelte';

	interface Props {
		itemId: string;
		currentOffset?: number;
		onOffsetChange: (offsetSeconds: number) => void;
		onClose: () => void;
	}

	let { itemId, currentOffset = 0, onOffsetChange, onClose }: Props = $props();

	let offset = $state(0);

	// Sync when prop changes
	$effect(() => {
		offset = currentOffset;
	});

	function updateOffset(newOffset: number) {
		// Clamp between -5.0s and +5.0s, round to 3 decimals
		const clamped = Math.round(Math.max(-5.0, Math.min(5.0, newOffset)) * 1000) / 1000;
		offset = clamped;
		onOffsetChange(clamped);
		try {
			if (typeof window !== 'undefined' && window.localStorage) {
				window.localStorage.setItem(`hearth_sub_offset_${itemId}`, String(clamped));
			}
		} catch {
			/* localStorage quota or privacy restrictions */
		}
	}

	function step(deltaSeconds: number) {
		updateOffset(offset + deltaSeconds);
	}

	function reset() {
		updateOffset(0);
	}

	function formatOffset(seconds: number): string {
		const ms = Math.round(seconds * 1000);
		const sign = ms > 0 ? '+' : '';
		return `${sign}${seconds.toFixed(2)}s (${sign}${ms}ms)`;
	}
</script>

<div class="sub-sync-panel" role="dialog" aria-label="Subtitle Synchronization" tabindex="-1">
	<div class="header">
		<h2 class="title">Subtitle Timing Offset</h2>
		<button type="button" class="close-btn" onclick={onClose} aria-label="Close subtitle sync">
			<X size={18} />
		</button>
	</div>

	<p class="description">
		Calibrate subtitle synchronization. Negative values display subtitles earlier; positive values delay them.
	</p>

	<div class="current-value" aria-live="polite">
		{formatOffset(offset)}
	</div>

	<div class="slider-row">
		<span class="limit">-5.0s</span>
		<input
			type="range"
			min="-5"
			max="5"
			step="0.05"
			value={offset}
			oninput={(e) => updateOffset(Number.parseFloat(e.currentTarget.value))}
			aria-label="Subtitle offset slider in seconds"
		/>
		<span class="limit">+5.0s</span>
	</div>

	<div class="steppers-grid">
		<div class="stepper-col">
			<span class="col-label">Earlier</span>
			<div class="stepper-buttons">
				<button type="button" class="step-btn" onclick={() => step(-0.05)} title="Earlier by 50ms">
					-50ms
				</button>
				<button type="button" class="step-btn" onclick={() => step(-0.25)} title="Earlier by 250ms">
					-250ms
				</button>
				<button type="button" class="step-btn" onclick={() => step(-1.0)} title="Earlier by 1s">
					-1000ms
				</button>
			</div>
		</div>

		<div class="stepper-col">
			<span class="col-label">Later</span>
			<div class="stepper-buttons">
				<button type="button" class="step-btn" onclick={() => step(0.05)} title="Later by 50ms">
					+50ms
				</button>
				<button type="button" class="step-btn" onclick={() => step(0.25)} title="Later by 250ms">
					+250ms
				</button>
				<button type="button" class="step-btn" onclick={() => step(1.0)} title="Later by 1s">
					+1000ms
				</button>
			</div>
		</div>
	</div>

	<div class="footer">
		<button type="button" class="reset-btn" onclick={reset} disabled={offset === 0}>
			<RotateCcw size={15} />
			<span>Reset to 0ms</span>
		</button>
	</div>
</div>

<style>
	.sub-sync-panel {
		position: absolute;
		bottom: 6.5rem;
		right: 1.5rem;
		z-index: 30;
		width: min(90vw, 22rem);
		background: rgba(14, 17, 22, 0.95);
		border: 1px solid var(--border);
		border-radius: var(--radius);
		padding: 1rem 1.25rem;
		backdrop-filter: blur(12px);
		box-shadow: 0 8px 32px rgba(0, 0, 0, 0.6);
		color: var(--text);
		display: flex;
		flex-direction: column;
		gap: 0.85rem;
	}

	.header {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 0.5rem;
	}

	.title {
		margin: 0;
		font-size: 1rem;
		font-weight: 600;
		color: #fff;
	}

	.close-btn {
		background: transparent;
		border: none;
		color: var(--muted);
		cursor: pointer;
		padding: 0.25rem;
		border-radius: 4px;
		display: flex;
		align-items: center;
		justify-content: center;
		transition: color 0.15s ease, background 0.15s ease;
	}

	.close-btn:hover {
		color: #fff;
		background: var(--surface-2);
	}

	.description {
		margin: 0;
		font-size: 0.8rem;
		color: var(--muted);
		line-height: 1.4;
	}

	.current-value {
		text-align: center;
		font-size: 1.15rem;
		font-weight: 700;
		font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
		color: var(--accent);
		background: rgba(255, 138, 61, 0.1);
		border: 1px solid rgba(255, 138, 61, 0.3);
		padding: 0.4rem;
		border-radius: 6px;
	}

	.slider-row {
		display: flex;
		align-items: center;
		gap: 0.5rem;
	}

	.slider-row input[type='range'] {
		flex: 1;
		accent-color: var(--accent);
		cursor: pointer;
	}

	.limit {
		font-size: 0.75rem;
		color: var(--muted);
		font-family: ui-monospace, monospace;
	}

	.steppers-grid {
		display: grid;
		grid-template-columns: 1fr 1fr;
		gap: 0.75rem;
	}

	.stepper-col {
		display: flex;
		flex-direction: column;
		gap: 0.35rem;
	}

	.col-label {
		font-size: 0.75rem;
		font-weight: 600;
		color: var(--muted);
		text-transform: uppercase;
		letter-spacing: 0.05em;
	}

	.stepper-buttons {
		display: flex;
		flex-direction: column;
		gap: 0.3rem;
	}

	.step-btn {
		background: var(--surface);
		border: 1px solid var(--border);
		border-radius: 4px;
		color: var(--text);
		font-size: 0.8rem;
		padding: 0.35rem 0.5rem;
		cursor: pointer;
		transition: background 0.15s ease, border-color 0.15s ease, color 0.15s ease;
	}

	.step-btn:hover {
		background: var(--surface-2);
		border-color: var(--accent);
		color: #fff;
	}

	.step-btn:focus-visible {
		outline: 2px solid var(--accent);
		outline-offset: 1px;
	}

	.footer {
		display: flex;
		justify-content: flex-end;
		margin-top: 0.25rem;
	}

	.reset-btn {
		display: inline-flex;
		align-items: center;
		gap: 0.35rem;
		background: transparent;
		border: 1px solid var(--border);
		border-radius: 4px;
		color: var(--muted);
		font-size: 0.75rem;
		padding: 0.3rem 0.6rem;
		cursor: pointer;
		transition: color 0.15s ease, border-color 0.15s ease;
	}

	.reset-btn:not(:disabled):hover {
		color: #fff;
		border-color: var(--muted);
	}

	.reset-btn:disabled {
		opacity: 0.4;
		cursor: not-allowed;
	}
</style>
