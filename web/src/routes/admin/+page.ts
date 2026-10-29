import { api } from '../../lib/api/client';
import type {
	AdminSystemInfoResponse,
	AuditEntry,
	JobResponse,
	TranscodeSessionResponse
} from '../../lib/api/types';
import type { PageLoad } from './$types';

export const load: PageLoad = async () => {
	const results = await Promise.allSettled([
		api.get<AdminSystemInfoResponse>('/admin/system/info'),
		api.get<TranscodeSessionResponse[]>('/admin/transcoding/sessions'),
		api.get<JobResponse[]>('/admin/jobs', { query: { limit: 10 } }),
		api.get<AuditEntry[]>('/admin/audit', { query: { limit: 10 } })
	]);
	const [system, sessions, jobs, audit] = results;
	const firstFailure = results.find((r) => r.status === 'rejected') as
		| PromiseRejectedResult
		| undefined;
	return {
		system: system.status === 'fulfilled' ? system.value : null,
		sessions: sessions.status === 'fulfilled' ? sessions.value : [],
		jobs: jobs.status === 'fulfilled' ? jobs.value : [],
		audit: audit.status === 'fulfilled' ? audit.value : [],
		error: firstFailure
			? firstFailure.reason instanceof Error
				? firstFailure.reason.message
				: String(firstFailure.reason)
			: null
	};
};
