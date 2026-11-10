<script lang="ts">
	import { api } from '../../../lib/api/client';
	import type { LogEntryResponse } from '../../../lib/api/types';
	import type { PageProps } from './$types';
	import {
		RefreshCw,
		Search,
		X,
		Copy,
		Check,
		ChevronRight,
		ChevronDown,
		Download,
		Pause,
		Play,
		AlertCircle,
		AlertTriangle,
		Info,
		ScrollText
	} from '@lucide/svelte';

	let { data }: PageProps = $props();

	let logs = $state<LogEntryResponse[]>([]);
	let selectedCategory = $state<string>('all');
	let selectedLevel = $state<string>('all');
	let searchQuery = $state<string>('');
	let limit = $state<number>(100);
	let autoRefresh = $state<boolean>(true);
	let loading = $state<boolean>(false);
	let error = $state<string | null>(null);

	// Set of expanded log indexes
	let expandedIndices = $state<Set<number>>(new Set());
	let copiedIndex = $state<number | null>(null);
	let copiedAll = $state<boolean>(false);

	$effect(() => {
		logs = data.logs ?? [];
		if (data.error) {
			error = data.error;
		}
	});

	const categories = [
		{ id: 'all', label: 'All' },
		{ id: 'error', label: 'Errors' },
		{ id: 'scanning', label: 'Scanning' },
		{ id: 'activity', label: 'Activity' },
		{ id: 'transcoding', label: 'Transcoding' },
		{ id: 'jobs', label: 'Jobs' },
		{ id: 'metadata', label: 'Metadata' }
	];

	const levels = ['all', 'DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'];

	async function fetchLogs(silent = false): Promise<void> {
		if (!silent) {
			loading = true;
		}
		error = null;
		try {
			const query: Record<string, string | number> = {
				limit
			};
			if (selectedCategory !== 'all') {
				query.category = selectedCategory;
			}
			if (selectedLevel !== 'all') {
				query.level = selectedLevel;
			}
			if (searchQuery.trim()) {
				query.search = searchQuery.trim();
			}

			const result = await api.get<LogEntryResponse[]>('/admin/logs', { query });
			logs = result;
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
		} finally {
			if (!silent) {
				loading = false;
			}
		}
	}

	function handleCategoryChange(catId: string): void {
		selectedCategory = catId;
		expandedIndices = new Set();
		fetchLogs();
	}

	function handleLevelChange(event: Event): void {
		selectedLevel = (event.target as HTMLSelectElement).value;
		expandedIndices = new Set();
		fetchLogs();
	}

	function handleLimitChange(event: Event): void {
		limit = Number((event.target as HTMLSelectElement).value);
		expandedIndices = new Set();
		fetchLogs();
	}

	let searchDebounceTimer: ReturnType<typeof setTimeout> | null = null;
	function handleSearchInput(): void {
		if (searchDebounceTimer) {
			clearTimeout(searchDebounceTimer);
		}
		searchDebounceTimer = setTimeout(() => {
			expandedIndices = new Set();
			fetchLogs();
		}, 300);
	}

	function clearSearch(): void {
		searchQuery = '';
		expandedIndices = new Set();
		fetchLogs();
	}

	function toggleExpand(index: number): void {
		const next = new Set(expandedIndices);
		if (next.has(index)) {
			next.delete(index);
		} else {
			next.add(index);
		}
		expandedIndices = next;
	}

	function expandAll(): void {
		expandedIndices = new Set(logs.map((_, i) => i));
	}

	function collapseAll(): void {
		expandedIndices = new Set();
	}

	async function copyLog(index: number, entry: LogEntryResponse): Promise<void> {
		try {
			await navigator.clipboard.writeText(JSON.stringify(entry, null, 2));
			copiedIndex = index;
			setTimeout(() => {
				if (copiedIndex === index) copiedIndex = null;
			}, 2000);
		} catch {
			// fallback if clipboard API unavailable
		}
	}

	async function copyAllLogs(): Promise<void> {
		try {
			await navigator.clipboard.writeText(JSON.stringify(logs, null, 2));
			copiedAll = true;
			setTimeout(() => {
				copiedAll = false;
			}, 2000);
		} catch {
			// clipboard failed
		}
	}

	function downloadLogs(): void {
		const blob = new Blob([JSON.stringify(logs, null, 2)], { type: 'application/json' });
		const url = URL.createObjectURL(blob);
		const a = document.createElement('a');
		a.href = url;
		a.download = `hearth-logs-${new Date().toISOString().replace(/[:.]/g, '-')}.json`;
		a.click();
		URL.revokeObjectURL(url);
	}

	// Auto-refresh interval
	$effect(() => {
		if (!autoRefresh) return;
		const interval = setInterval(() => {
			fetchLogs(true);
		}, 3000);
		return () => clearInterval(interval);
	});

	function formatTime(iso: string): string {
		try {
			const d = new Date(iso);
			return d.toLocaleTimeString([], { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' }) +
				'.' +
				String(d.getMilliseconds()).padStart(3, '0');
		} catch {
			return iso;
		}
	}

	function formatDate(iso: string): string {
		try {
			const d = new Date(iso);
			return d.toLocaleDateString([], { month: 'short', day: 'numeric' });
		} catch {
			return '';
		}
	}

	function getCategoryForLogger(logger: string): string {
		if (logger.startsWith('app.services.scanner')) return 'scanner';
		if (logger.startsWith('app.services.transcode')) return 'transcode';
		if (logger.startsWith('app.core.middleware') || logger.startsWith('app.api')) return 'activity';
		if (logger.startsWith('app.services.jobs')) return 'jobs';
		if (logger.startsWith('app.services.metadata')) return 'metadata';
		if (logger.startsWith('app.services.media')) return 'media';
		if (logger.startsWith('app.services.backup')) return 'backup';
		const parts = logger.split('.');
		return parts[parts.length - 1] ?? logger;
	}

	let errorCount = $derived(
		logs.filter((l) => l.level === 'ERROR' || l.level === 'CRITICAL' || l.exception).length
	);
	let warnCount = $derived(logs.filter((l) => l.level === 'WARNING').length);
</script>

<svelte:head>
	<title>Server Logs · Admin · Hearth</title>
</svelte:head>

<div class="logs-header">
	<div>
		<h2>Server Logs</h2>
		<p class="muted">
			Real-time in-memory server logs across scanner, transcode, jobs, and HTTP requests.
		</p>
	</div>
	<div class="header-actions">
		<button
			type="button"
			class="ghost toggle-btn"
			class:active={autoRefresh}
			onclick={() => (autoRefresh = !autoRefresh)}
			aria-pressed={autoRefresh}
			title={autoRefresh ? 'Pause live updates' : 'Resume live updates (every 3s)'}
		>
			{#if autoRefresh}
				<span class="live-pulse"></span>
				<Pause size={14} /> <span>Live</span>
			{:else}
				<Play size={14} /> <span>Paused</span>
			{/if}
		</button>
		<button
			type="button"
			class="ghost"
			onclick={() => fetchLogs()}
			disabled={loading}
			title="Refresh logs now"
		>
			<RefreshCw size={14} class={loading ? 'spin' : ''} />
			<span>Refresh</span>
		</button>
		<button
			type="button"
			class="ghost"
			onclick={copyAllLogs}
			disabled={logs.length === 0}
			title="Copy all visible logs to clipboard"
		>
			{#if copiedAll}
				<Check size={14} /> <span>Copied</span>
			{:else}
				<Copy size={14} /> <span>Copy JSON</span>
			{/if}
		</button>
		<button
			type="button"
			class="ghost"
			onclick={downloadLogs}
			disabled={logs.length === 0}
			title="Download logs as JSON file"
		>
			<Download size={14} />
			<span>Export</span>
		</button>
	</div>
</div>

{#if error}
	<p class="banner" role="alert">
		<AlertCircle size={16} />
		<span>Failed to load logs: {error}</span>
	</p>
{/if}

<!-- Category Pills Navigation -->
<div class="category-pills" role="tablist" aria-label="Log categories">
	{#each categories as cat (cat.id)}
		<button
			type="button"
			role="tab"
			aria-selected={selectedCategory === cat.id}
			class="pill"
			class:active={selectedCategory === cat.id}
			onclick={() => handleCategoryChange(cat.id)}
		>
			{#if cat.id === 'error' && errorCount > 0}
				<AlertCircle size={13} class="icon-err" />
			{/if}
			<span>{cat.label}</span>
			{#if cat.id === 'error' && errorCount > 0}
				<span class="counter-badge error">{errorCount}</span>
			{/if}
		</button>
	{/each}
</div>

<!-- Secondary Filter Bar -->
<div class="filters-bar">
	<div class="search-box">
		<Search size={14} class="search-icon" />
		<input
			type="text"
			placeholder="Search message, logger, request ID..."
			bind:value={searchQuery}
			oninput={handleSearchInput}
			aria-label="Search server logs"
		/>
		{#if searchQuery}
			<button type="button" class="clear-search" onclick={clearSearch} aria-label="Clear search">
				<X size={13} />
			</button>
		{/if}
	</div>

	<div class="filter-controls">
		<label class="select-label">
			<span class="muted">Level:</span>
			<select value={selectedLevel} onchange={handleLevelChange} aria-label="Filter by level">
				{#each levels as lvl}
					<option value={lvl}>{lvl === 'all' ? 'All Levels' : lvl}</option>
				{/each}
			</select>
		</label>

		<label class="select-label">
			<span class="muted">Limit:</span>
			<select value={limit} onchange={handleLimitChange} aria-label="Limit log count">
				<option value={50}>50</option>
				<option value={100}>100</option>
				<option value={250}>250</option>
				<option value={500}>500</option>
			</select>
		</label>

		<div class="bulk-expand">
			<button
				type="button"
				class="link-button"
				onclick={expandAll}
				disabled={logs.length === 0}
			>
				Expand all
			</button>
			<span class="muted">/</span>
			<button
				type="button"
				class="link-button"
				onclick={collapseAll}
				disabled={expandedIndices.size === 0}
			>
				Collapse
			</button>
		</div>
	</div>
</div>

<!-- Stats and Active Filters -->
<div class="stats-row">
	<div class="stats-left">
		<span class="muted">Showing {logs.length} entries</span>
		{#if errorCount > 0}
			<span class="chip-count chip-err">
				<AlertCircle size={12} /> {errorCount} {errorCount === 1 ? 'error' : 'errors'}
			</span>
		{/if}
		{#if warnCount > 0}
			<span class="chip-count chip-warn">
				<AlertTriangle size={12} /> {warnCount} {warnCount === 1 ? 'warning' : 'warnings'}
			</span>
		{/if}
	</div>
	{#if selectedCategory !== 'all' || selectedLevel !== 'all' || searchQuery.trim()}
		<button
			type="button"
			class="link-button reset-btn"
			onclick={() => {
				selectedCategory = 'all';
				selectedLevel = 'all';
				searchQuery = '';
				fetchLogs();
			}}
		>
			Reset filters
		</button>
	{/if}
</div>

<!-- Log Entries Viewer -->
<div class="log-container" role="region" aria-label="Server log records">
	{#if logs.length === 0}
		<div class="empty-state">
			<ScrollText size={32} class="empty-icon" />
			<p>No log records found matching the active filters.</p>
			{#if selectedCategory !== 'all' || selectedLevel !== 'all' || searchQuery.trim()}
				<button
					type="button"
					class="ghost"
					onclick={() => {
						selectedCategory = 'all';
						selectedLevel = 'all';
						searchQuery = '';
						fetchLogs();
					}}
				>
					Clear filters
				</button>
			{/if}
		</div>
	{:else}
		<div class="log-table">
			{#each logs as entry, index (index)}
				{@const isExpanded = expandedIndices.has(index)}
				{@const lvl = entry.level.toUpperCase()}
				{@const hasDetails = entry.exception || entry.request_id || (entry.extra && Object.keys(entry.extra).length > 0)}
				<div
					class="log-row"
					class:expanded={isExpanded}
					class:lvl-error={lvl === 'ERROR' || lvl === 'CRITICAL'}
					class:lvl-warn={lvl === 'WARNING'}
					class:lvl-info={lvl === 'INFO'}
					class:lvl-debug={lvl === 'DEBUG'}
				>
					<div
						class="log-row-header"
						role="button"
						tabindex="0"
						onclick={() => toggleExpand(index)}
						onkeydown={(e) => {
							if (e.key === 'Enter' || e.key === ' ') {
								e.preventDefault();
								toggleExpand(index);
							}
						}}
						aria-expanded={isExpanded}
					>
						<span class="expander" aria-hidden="true">
							{#if isExpanded}
								<ChevronDown size={14} />
							{:else}
								<ChevronRight size={14} />
							{/if}
						</span>

						<span class="col-time" title={entry.timestamp}>
							<span class="time-date">{formatDate(entry.timestamp)}</span>
							<span class="time-clock">{formatTime(entry.timestamp)}</span>
						</span>

						<span class="col-level">
							<span class="level-chip {lvl.toLowerCase()}">{lvl}</span>
						</span>

						<span class="col-source" title={entry.logger}>
							<span class="source-tag">{getCategoryForLogger(entry.logger)}</span>
						</span>

						<span class="col-msg">
							<span class="msg-text">{entry.message}</span>
							{#if entry.extra && Object.keys(entry.extra).length > 0}
								<span class="extra-preview">
									{#if entry.extra.path}
										<span class="extra-item">{String(entry.extra.method ?? '')} {String(entry.extra.path)}</span>
									{/if}
									{#if entry.extra.status_code}
										<span class="extra-item status-{String(entry.extra.status_code).charAt(0)}xx">
											{String(entry.extra.status_code)}
										</span>
									{/if}
									{#if entry.extra.duration_ms}
										<span class="extra-item">{String(entry.extra.duration_ms)}ms</span>
									{/if}
									{#if entry.extra.session_id}
										<span class="extra-item">sess:{String(entry.extra.session_id).slice(0, 8)}</span>
									{/if}
									{#if entry.extra.job_id}
										<span class="extra-item">job:{String(entry.extra.job_id).slice(0, 8)}</span>
									{/if}
								</span>
							{/if}
						</span>

						<div class="row-actions" onclick={(e) => e.stopPropagation()} role="none">
							<button
								type="button"
								class="icon-btn"
								title="Copy log entry JSON"
								onclick={() => copyLog(index, entry)}
								aria-label="Copy log entry"
							>
								{#if copiedIndex === index}
									<Check size={13} class="copied-check" />
								{:else}
									<Copy size={13} />
								{/if}
							</button>
						</div>
					</div>

					{#if isExpanded}
						<div class="log-details" role="region" aria-label="Log entry details">
							<div class="detail-grid">
								<div class="detail-item">
									<span class="detail-label">Logger:</span>
									<span class="detail-val mono">{entry.logger}</span>
								</div>
								{#if entry.request_id}
									<div class="detail-item">
										<span class="detail-label">Request ID:</span>
										<span class="detail-val mono">{entry.request_id}</span>
									</div>
								{/if}
								<div class="detail-item">
									<span class="detail-label">Timestamp:</span>
									<span class="detail-val mono">{entry.timestamp}</span>
								</div>
							</div>

							{#if entry.extra && Object.keys(entry.extra).length > 0}
								<div class="extra-section">
									<span class="extra-heading">Context / Extra Data:</span>
									<pre class="code-box">{JSON.stringify(entry.extra, null, 2)}</pre>
								</div>
							{/if}

							{#if entry.exception}
								<div class="exception-section">
									<div class="exception-header">
										<span class="extra-heading err-heading">Exception / Traceback:</span>
										<button
											type="button"
											class="link-button"
											onclick={async () => {
												if (entry.exception) {
													await navigator.clipboard.writeText(entry.exception);
												}
											}}
										>
											Copy Trace
										</button>
									</div>
									<pre class="code-box exc-box">{entry.exception}</pre>
								</div>
							{/if}
						</div>
					{/if}
				</div>
			{/each}
		</div>
	{/if}
</div>

<style>
	.logs-header {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		justify-content: space-between;
		gap: 1rem;
		margin-bottom: 1.25rem;
	}

	.logs-header h2 {
		font-size: 1.3rem;
		margin: 0 0 0.25rem;
	}

	.header-actions {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 0.5rem;
	}

	.toggle-btn {
		display: inline-flex;
		align-items: center;
		gap: 0.4rem;
	}

	.toggle-btn.active {
		border-color: var(--accent);
		color: var(--accent);
	}

	.live-pulse {
		width: 8px;
		height: 8px;
		background: #22c55e;
		border-radius: 50%;
		box-shadow: 0 0 8px #22c55e;
		display: inline-block;
	}

	.ghost {
		display: inline-flex;
		align-items: center;
		gap: 0.4rem;
		background: var(--surface-2);
		border: 1px solid var(--border);
		border-radius: var(--radius);
		padding: 0.4rem 0.8rem;
		color: var(--text);
		font-size: 0.85rem;
		cursor: pointer;
		transition: background 0.15s, border-color 0.15s;
	}

	.ghost:hover:not(:disabled) {
		background: var(--surface);
		border-color: var(--accent);
	}

	.ghost:disabled {
		opacity: 0.5;
		cursor: not-allowed;
	}

	.spin {
		animation: spin 1s linear infinite;
	}

	@keyframes spin {
		from {
			transform: rotate(0deg);
		}
		to {
			transform: rotate(360deg);
		}
	}

	/* Categories Pills */
	.category-pills {
		display: flex;
		flex-wrap: wrap;
		gap: 0.4rem;
		margin-bottom: 1rem;
	}

	.pill {
		display: inline-flex;
		align-items: center;
		gap: 0.4rem;
		background: var(--surface);
		border: 1px solid var(--border);
		border-radius: 999px;
		padding: 0.35rem 0.85rem;
		font-size: 0.85rem;
		color: var(--muted);
		cursor: pointer;
		transition: all 0.15s ease;
	}

	.pill:hover {
		color: var(--text);
		border-color: var(--muted);
	}

	.pill.active {
		background: var(--surface-2);
		border-color: var(--accent);
		color: var(--text);
		font-weight: 600;
	}

	.counter-badge {
		font-size: 0.72rem;
		font-weight: 700;
		padding: 0.05rem 0.4rem;
		border-radius: 999px;
		line-height: 1.2;
	}

	.counter-badge.error {
		background: rgba(255, 107, 107, 0.2);
		color: var(--danger);
		border: 1px solid var(--danger);
	}

	.icon-err {
		color: var(--danger);
	}

	/* Filter Controls Bar */
	.filters-bar {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		justify-content: space-between;
		gap: 0.75rem;
		background: var(--surface);
		border: 1px solid var(--border);
		border-radius: var(--radius);
		padding: 0.65rem 0.9rem;
		margin-bottom: 0.75rem;
	}

	.search-box {
		position: relative;
		flex: 1 1 240px;
		display: flex;
		align-items: center;
	}

	.search-box :global(.search-icon) {
		position: absolute;
		left: 0.65rem;
		color: var(--muted);
		pointer-events: none;
	}

	.search-box input {
		width: 100%;
		background: var(--surface-2);
		border: 1px solid var(--border);
		border-radius: 6px;
		padding: 0.35rem 1.8rem 0.35rem 2rem;
		color: var(--text);
		font-size: 0.85rem;
	}

	.search-box input:focus {
		border-color: var(--accent);
		outline: none;
	}

	.clear-search {
		position: absolute;
		right: 0.5rem;
		background: transparent;
		border: none;
		color: var(--muted);
		cursor: pointer;
		display: flex;
		align-items: center;
		padding: 0.15rem;
	}

	.clear-search:hover {
		color: var(--text);
	}

	.filter-controls {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 0.75rem;
	}

	.select-label {
		display: inline-flex;
		align-items: center;
		gap: 0.4rem;
		font-size: 0.85rem;
	}

	.select-label select {
		background: var(--surface-2);
		border: 1px solid var(--border);
		border-radius: 6px;
		padding: 0.3rem 0.55rem;
		color: var(--text);
		font-size: 0.85rem;
	}

	.select-label select:focus {
		border-color: var(--accent);
		outline: none;
	}

	.bulk-expand {
		display: inline-flex;
		align-items: center;
		gap: 0.35rem;
		font-size: 0.82rem;
	}

	.link-button {
		background: none;
		border: none;
		padding: 0;
		color: var(--muted);
		cursor: pointer;
		font-size: 0.82rem;
		text-decoration: underline;
	}

	.link-button:hover:not(:disabled) {
		color: var(--text);
	}

	.link-button:disabled {
		opacity: 0.4;
		cursor: not-allowed;
		text-decoration: none;
	}

	/* Stats Row */
	.stats-row {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 0.5rem;
		font-size: 0.82rem;
		margin-bottom: 0.75rem;
		padding: 0 0.25rem;
	}

	.stats-left {
		display: inline-flex;
		align-items: center;
		gap: 0.6rem;
		flex-wrap: wrap;
	}

	.chip-count {
		display: inline-flex;
		align-items: center;
		gap: 0.3rem;
		font-size: 0.75rem;
		font-weight: 600;
		padding: 0.1rem 0.5rem;
		border-radius: 999px;
	}

	.chip-err {
		background: rgba(255, 107, 107, 0.15);
		color: var(--danger);
		border: 1px solid rgba(255, 107, 107, 0.35);
	}

	.chip-warn {
		background: rgba(245, 158, 11, 0.15);
		color: #f59e0b;
		border: 1px solid rgba(245, 158, 11, 0.35);
	}

	.reset-btn {
		color: var(--accent);
	}

	/* Main Log Container */
	.log-container {
		background: #090c10;
		border: 1px solid var(--border);
		border-radius: var(--radius);
		overflow: hidden;
		font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono',
			'Courier New', monospace;
	}

	.empty-state {
		display: flex;
		flex-direction: column;
		align-items: center;
		justify-content: center;
		padding: 3rem 1.5rem;
		color: var(--muted);
		gap: 0.75rem;
		text-align: center;
	}

	.empty-state :global(.empty-icon) {
		opacity: 0.4;
	}

	.log-table {
		display: flex;
		flex-direction: column;
	}

	.log-row {
		border-bottom: 1px solid rgba(255, 255, 255, 0.05);
		transition: background 0.1s ease;
	}

	.log-row:hover {
		background: rgba(255, 255, 255, 0.02);
	}

	.log-row.expanded {
		background: rgba(255, 255, 255, 0.03);
	}

	.log-row.lvl-error {
		border-left: 3px solid var(--danger);
		background: rgba(255, 107, 107, 0.04);
	}

	.log-row.lvl-warn {
		border-left: 3px solid #f59e0b;
		background: rgba(245, 158, 11, 0.03);
	}

	.log-row.lvl-info {
		border-left: 3px solid transparent;
	}

	.log-row.lvl-debug {
		border-left: 3px solid transparent;
		opacity: 0.8;
	}

	.log-row-header {
		display: flex;
		align-items: center;
		gap: 0.6rem;
		padding: 0.45rem 0.75rem;
		cursor: pointer;
		font-size: 0.82rem;
		line-height: 1.4;
		user-select: none;
	}

	.expander {
		display: inline-flex;
		align-items: center;
		color: var(--muted);
		width: 14px;
		flex-shrink: 0;
	}

	.col-time {
		display: inline-flex;
		flex-direction: row;
		gap: 0.35rem;
		color: var(--muted);
		flex-shrink: 0;
		font-size: 0.76rem;
		white-space: nowrap;
	}

	.time-date {
		opacity: 0.7;
	}

	.col-level {
		flex-shrink: 0;
	}

	.level-chip {
		display: inline-block;
		font-size: 0.68rem;
		font-weight: 700;
		padding: 0.08rem 0.4rem;
		border-radius: 4px;
		text-transform: uppercase;
		letter-spacing: 0.03em;
	}

	.level-chip.error,
	.level-chip.critical {
		background: rgba(255, 107, 107, 0.2);
		color: var(--danger);
		border: 1px solid var(--danger);
	}

	.level-chip.warning {
		background: rgba(245, 158, 11, 0.18);
		color: #f59e0b;
		border: 1px solid #d97706;
	}

	.level-chip.info {
		background: rgba(56, 189, 248, 0.15);
		color: #38bdf8;
		border: 1px solid rgba(56, 189, 248, 0.35);
	}

	.level-chip.debug {
		background: var(--surface-2);
		color: var(--muted);
		border: 1px solid var(--border);
	}

	.col-source {
		flex-shrink: 0;
	}

	.source-tag {
		display: inline-block;
		background: var(--surface-2);
		border: 1px solid rgba(255, 255, 255, 0.08);
		color: var(--muted);
		border-radius: 4px;
		padding: 0.08rem 0.45rem;
		font-size: 0.72rem;
	}

	.col-msg {
		flex: 1 1 auto;
		display: flex;
		align-items: center;
		gap: 0.5rem;
		overflow: hidden;
		min-width: 0;
	}

	.msg-text {
		color: var(--text);
		white-space: nowrap;
		overflow: hidden;
		text-overflow: ellipsis;
	}

	.extra-preview {
		display: inline-flex;
		align-items: center;
		gap: 0.35rem;
		flex-shrink: 0;
	}

	.extra-item {
		font-size: 0.72rem;
		background: rgba(255, 255, 255, 0.06);
		padding: 0.05rem 0.35rem;
		border-radius: 3px;
		color: var(--muted);
	}

	.status-2xx {
		color: #4ade80;
	}

	.status-3xx {
		color: #38bdf8;
	}

	.status-4xx {
		color: #f59e0b;
	}

	.status-5xx {
		color: var(--danger);
	}

	.row-actions {
		margin-left: auto;
		flex-shrink: 0;
		display: flex;
		align-items: center;
		gap: 0.25rem;
	}

	.icon-btn {
		background: transparent;
		border: none;
		color: var(--muted);
		cursor: pointer;
		padding: 0.2rem;
		border-radius: 4px;
		display: inline-flex;
		align-items: center;
		justify-content: center;
	}

	.icon-btn:hover {
		color: var(--text);
		background: rgba(255, 255, 255, 0.08);
	}

	.copied-check {
		color: #22c55e;
	}

	/* Expanded Details Area */
	.log-details {
		padding: 0.75rem 1rem 1rem 2.2rem;
		border-top: 1px dashed rgba(255, 255, 255, 0.08);
		background: rgba(0, 0, 0, 0.2);
		font-size: 0.8rem;
		display: flex;
		flex-direction: column;
		gap: 0.6rem;
	}

	.detail-grid {
		display: grid;
		grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
		gap: 0.4rem 1rem;
	}

	.detail-item {
		display: flex;
		align-items: center;
		gap: 0.5rem;
	}

	.detail-label {
		color: var(--muted);
		font-size: 0.75rem;
	}

	.detail-val {
		color: var(--text);
		font-size: 0.78rem;
	}

	.mono {
		font-family: inherit;
	}

	.extra-section,
	.exception-section {
		display: flex;
		flex-direction: column;
		gap: 0.3rem;
		margin-top: 0.2rem;
	}

	.exception-header {
		display: flex;
		align-items: center;
		justify-content: space-between;
	}

	.extra-heading {
		color: var(--muted);
		font-size: 0.74rem;
		text-transform: uppercase;
		letter-spacing: 0.04em;
	}

	.err-heading {
		color: var(--danger);
	}

	.code-box {
		margin: 0;
		padding: 0.6rem 0.8rem;
		background: #0d1117;
		border: 1px solid rgba(255, 255, 255, 0.08);
		border-radius: 6px;
		color: #c9d1d9;
		font-size: 0.76rem;
		line-height: 1.45;
		overflow-x: auto;
		white-space: pre-wrap;
		word-break: break-all;
	}

	.exc-box {
		color: #ff9494;
		background: #180d0d;
		border-color: rgba(255, 107, 107, 0.2);
	}

	.banner {
		display: flex;
		align-items: center;
		gap: 0.5rem;
		background: var(--surface);
		border: 1px solid var(--border);
		border-left: 3px solid var(--danger);
		border-radius: var(--radius);
		padding: 0.6rem 1rem;
		color: var(--danger);
		margin-bottom: 1rem;
		font-size: 0.9rem;
	}

	.muted {
		color: var(--muted);
	}
</style>
