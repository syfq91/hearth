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
		constructor(menuPlayer: any, options: any) {
			super(menuPlayer, options);
			this.on('click', () => {
				options.onSelect?.(options.trackIndex);
			});
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
		constructor(menuPlayer: any, options: any) {
			super(menuPlayer, options);
			this.on('click', () => {
				options.onSelect?.(options.choiceId);
			});
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
