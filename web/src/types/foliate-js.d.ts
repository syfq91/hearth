/**
 * Minimal ambient types for `foliate-js`, which ships pure ESM with no
 * declarations. Only the surface Hearth uses is described here; the element's
 * full API lives in node_modules/foliate-js/README.md.
 */
declare module 'foliate-js/view.js' {
	// Importing the module registers the <foliate-view> custom element as a
	// side effect; there is nothing to export that Hearth consumes directly.
	export {};
}
