import { api } from '../../../lib/api/client';
import type {
	ServerTranscodeCapabilities,
	TranscodeSessionResponse,
	TranscodeSettingsResponse
} from '../../../lib/api/types';
import type { PageLoad } from './$types';

export const load: PageLoad = async () => {
	const results = await Promise.allSettled([
		api.get<ServerTranscodeCapabilities>('/admin/transcoding/capabilities'),
		api.get<TranscodeSettingsResponse>('/admin/transcoding/settings'),
		api.get<TranscodeSessionResponse[]>('/admin/transcoding/sessions')
	]);
	const [capabilities, settings, sessions] = results;
	const firstFailure = results.find((r) => r.status === 'rejected') as
		| PromiseRejectedResult
		| undefined;
	return {
		capabilities: capabilities.status === 'fulfilled' ? capabilities.value : null,
		settings: settings.status === 'fulfilled' ? settings.value : null,
		sessions: sessions.status === 'fulfilled' ? sessions.value : [],
		error: firstFailure
			? firstFailure.reason instanceof Error
				? firstFailure.reason.message
				: String(firstFailure.reason)
			: null
	};
};
