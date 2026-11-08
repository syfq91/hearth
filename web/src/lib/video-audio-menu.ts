import videojs from 'video.js';
import type Player from 'video.js/dist/types/player';

/** An audio track the server can remux, plus the "default" choice. */
export type AudioMenuChoice = { index: number | null; label: string };

export interface AudioMenuHandle {
	el(): Element;
	setChoices(choices: AudioMenuChoice[], selected: number | null): void;
}

const COMPONENT_NAME = 'AudioMenuButton';

interface ControlBarHandle {
	el(): Element;
	addChild(child: string, options?: Record<string, unknown>): unknown;
	getChild(name: string): { el(): Element } | undefined;
}

/**
 * Register a Video.js control-bar menu button for server-side audio track
 * selection. The built-in audio button only lists tracks the media element
 * already has; choosing one here re-plans the stream on the server, so the
 * menu is driven by the playback plan instead of the tech.
 */
function registerComponent(): void {
	if (videojs.getComponent(COMPONENT_NAME)) return;

	// eslint-disable-next-line @typescript-eslint/no-explicit-any
	const MenuButtonBase = videojs.getComponent('MenuButton') as any;
	// eslint-disable-next-line @typescript-eslint/no-explicit-any
	const MenuItemBase = videojs.getComponent('MenuItem') as any;

	// eslint-disable-next-line @typescript-eslint/no-explicit-any
	class AudioMenuChoiceItem extends MenuItemBase {
		// eslint-disable-next-line @typescript-eslint/no-explicit-any
		constructor(menuPlayer: any, options: any) {
			super(menuPlayer, options);
		}

		// eslint-disable-next-line @typescript-eslint/no-explicit-any
		handleClick(event: any) {
			super.handleClick(event);
			this.options_.onSelect?.(this.options_.trackIndex);
		}
	}

	// eslint-disable-next-line @typescript-eslint/no-explicit-any
	class AudioMenuButton extends MenuButtonBase {
		constructor(menuPlayer: any, options: any) {
			super(menuPlayer, options);
			this.controlText('Audio track');
			// Reuses the built-in audio icon from video-js.css.
			this.addClass('vjs-audio-button');
		}

		createItems(): unknown[] {
			const choices = (this.options_.choices ?? []) as AudioMenuChoice[];
			if (choices.length <= 1) return [];
			const selected = (this.options_.selectedIndex ?? null) as number | null;
			return choices.map(
				(choice) =>
					new AudioMenuChoiceItem(this.player_, {
						label: choice.label,
						trackIndex: choice.index,
						onSelect: this.options_.onSelect,
						// MenuItem is selectable, but only one at a time.
						selected: choice.index === selected,
						selectable: true,
						multiSelectable: false
					})
			);
		}

		setChoices(choices: AudioMenuChoice[], selected: number | null): void {
			this.options_.choices = choices;
			this.options_.selectedIndex = selected;
			this.update();
		}
	}

	videojs.registerComponent(
		COMPONENT_NAME,
		AudioMenuButton as unknown as Parameters<typeof videojs.registerComponent>[1]
	);
}

/**
 * Add the audio track menu to a player's control bar, just before the
 * captions button, and return a handle for updating its choices.
 */
export function addAudioMenuButton(
	player: Player,
	onSelect: (index: number | null) => void
): AudioMenuHandle {
	registerComponent();
	const controlBar = (player as unknown as { controlBar: ControlBarHandle }).controlBar;
	const button = controlBar.addChild(COMPONENT_NAME, {
		choices: [],
		selectedIndex: null,
		onSelect
	}) as unknown as AudioMenuHandle;

	const subsCaps = controlBar.getChild('subsCapsButton');
	if (subsCaps) {
		controlBar.el().insertBefore(button.el(), subsCaps.el());
	} else {
		const fullscreen = controlBar.getChild('fullscreenToggle');
		if (fullscreen) {
			controlBar.el().insertBefore(button.el(), fullscreen.el());
		}
	}

	return button;
}

export type SubtitleMenuChoice = { id: string; label: string };

export interface SubtitleMenuHandle {
	el(): Element;
	setChoices(choices: SubtitleMenuChoice[], selectedId: string): void;
}

const SUBTITLE_COMPONENT_NAME = 'SubtitleMenuButton';

function registerSubtitleComponent(): void {
	if (videojs.getComponent(SUBTITLE_COMPONENT_NAME)) return;

	// eslint-disable-next-line @typescript-eslint/no-explicit-any
	const MenuButtonBase = videojs.getComponent('MenuButton') as any;
	// eslint-disable-next-line @typescript-eslint/no-explicit-any
	const MenuItemBase = videojs.getComponent('MenuItem') as any;

	// eslint-disable-next-line @typescript-eslint/no-explicit-any
	class SubtitleMenuChoiceItem extends MenuItemBase {
		// eslint-disable-next-line @typescript-eslint/no-explicit-any
		constructor(menuPlayer: any, options: any) {
			super(menuPlayer, options);
		}

		// eslint-disable-next-line @typescript-eslint/no-explicit-any
		handleClick(event: any) {
			super.handleClick(event);
			this.options_.onSelect?.(this.options_.choiceId);
		}
	}

	// eslint-disable-next-line @typescript-eslint/no-explicit-any
	class SubtitleMenuButton extends MenuButtonBase {
		constructor(menuPlayer: any, options: any) {
			super(menuPlayer, options);
			this.controlText('Subtitles');
			// Reuses the built-in subtitles/CC icon from video-js.css
			this.addClass('vjs-subs-caps-button');
		}

		createItems(): unknown[] {
			const choices = (this.options_.choices ?? []) as SubtitleMenuChoice[];
			if (choices.length === 0) return [];
			const selected = (this.options_.selectedId ?? '') as string;
			return choices.map(
				(choice) =>
					new SubtitleMenuChoiceItem(this.player_, {
						label: choice.label,
						choiceId: choice.id,
						onSelect: this.options_.onSelect,
						selected: choice.id === selected,
						selectable: true,
						multiSelectable: false
					})
			);
		}

		setChoices(choices: SubtitleMenuChoice[], selectedId: string): void {
			this.options_.choices = choices;
			this.options_.selectedId = selectedId;
			if (choices.length <= 1) {
				this.hide();
			} else {
				this.show();
			}
			this.update();
		}
	}

	videojs.registerComponent(
		SUBTITLE_COMPONENT_NAME,
		SubtitleMenuButton as unknown as Parameters<typeof videojs.registerComponent>[1]
	);
}

/**
 * Add the subtitle track menu to a player's control bar, just before
 * fullscreen, and return a handle for updating its choices.
 */
export function addSubtitleMenuButton(
	player: Player,
	onSelect: (id: string) => void
): SubtitleMenuHandle {
	registerSubtitleComponent();
	const controlBar = (player as unknown as { controlBar: ControlBarHandle }).controlBar;
	const button = controlBar.addChild(SUBTITLE_COMPONENT_NAME, {
		choices: [],
		selectedId: '',
		onSelect
	}) as unknown as SubtitleMenuHandle;

	const fullscreen = controlBar.getChild('fullscreenToggle');
	if (fullscreen) {
		controlBar.el().insertBefore(button.el(), fullscreen.el());
	}

	return button;
}

const INFO_COMPONENT_NAME = 'InfoButton';

export interface InfoButtonHandle {
	el(): Element;
	setActive(active: boolean): void;
}

function registerInfoComponent(): void {
	if (videojs.getComponent(INFO_COMPONENT_NAME)) return;

	// eslint-disable-next-line @typescript-eslint/no-explicit-any
	const ButtonBase = videojs.getComponent('Button') as any;

	// eslint-disable-next-line @typescript-eslint/no-explicit-any
	class InfoButton extends ButtonBase {
		constructor(menuPlayer: any, options: any) {
			super(menuPlayer, options);
			this.controlText('Playback details');
			this.addClass('vjs-info-control');

			const iconSpan = document.createElement('span');
			iconSpan.className = 'vjs-icon-placeholder';
			iconSpan.innerHTML = `
				<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="vjs-svg-icon">
					<circle cx="12" cy="12" r="10"></circle>
					<path d="M12 16v-4"></path>
					<path d="M12 8h.01"></path>
				</svg>
			`;
			this.el().appendChild(iconSpan);
		}

		handleClick(): void {
			this.options_.onClick?.();
		}

		setActive(active: boolean): void {
			if (active) {
				this.addClass('vjs-info-active');
			} else {
				this.removeClass('vjs-info-active');
			}
		}
	}

	videojs.registerComponent(
		INFO_COMPONENT_NAME,
		InfoButton as unknown as Parameters<typeof videojs.registerComponent>[1]
	);
}

/**
 * Add an info button to a player's control bar just before fullscreen.
 */
export function addInfoButton(
	player: Player,
	onClick: () => void
): InfoButtonHandle {
	registerInfoComponent();
	const controlBar = (player as unknown as { controlBar: ControlBarHandle }).controlBar;
	const button = controlBar.addChild(INFO_COMPONENT_NAME, {
		onClick
	}) as unknown as InfoButtonHandle;

	const fullscreen = controlBar.getChild('fullscreenToggle');
	if (fullscreen) {
		controlBar.el().insertBefore(button.el(), fullscreen.el());
	}

	return button;
}
