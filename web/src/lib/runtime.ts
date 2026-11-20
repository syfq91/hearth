/** Human-readable runtime for detail pages: "24m", "2h", "2h 4m". */
export function formatRuntime(seconds: number | null | undefined): string | null {
	if (!seconds || seconds <= 0) return null;
	const minutes = Math.round(seconds / 60);
	const hours = Math.floor(minutes / 60);
	const remainder = minutes % 60;
	if (hours === 0) return `${remainder}m`;
	if (remainder === 0) return `${hours}h`;
	return `${hours}h ${remainder}m`;
}
