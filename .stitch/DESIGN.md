---
name: Hearth
colors:
  # Primary Foundation (Canvas, Surfaces & Containers)
  background: '#0e1116'
  on-background: '#e6edf3'
  surface: '#161b22'
  surface-dim: '#0e1116'
  surface-bright: '#1f2630'
  surface-container-lowest: '#090c10'
  surface-container-low: '#0e1116'
  surface-container: '#161b22'
  surface-container-high: '#1b212b'
  surface-container-highest: '#1f2630'
  on-surface: '#e6edf3'
  on-surface-variant: '#9aa7b4'
  inverse-surface: '#e6edf3'
  inverse-on-surface: '#0e1116'
  outline: '#2b3441'
  outline-variant: '#1f2630'
  surface-tint: '#ff8a3d'

  # Accent & Interactive (Fireplace Ember Orange)
  primary: '#ff8a3d'
  on-primary: '#1a1205'
  primary-container: '#3a1f0d'
  on-primary-container: '#ffc299'
  inverse-primary: '#c95b10'

  # Secondary (Sky Blue - Accessible Focus & Navigation)
  secondary: '#7cc4ff'
  on-secondary: '#003354'
  secondary-container: '#12324e'
  on-secondary-container: '#cbe6ff'

  # Tertiary (Golden Amber - Star Ratings & Favorites)
  tertiary: '#eab308'
  on-tertiary: '#3f2e00'
  tertiary-container: '#423303'
  on-tertiary-container: '#fef08a'

  # Functional States (Danger & Alerts)
  error: '#ff6b6b'
  on-error: '#49000a'
  error-container: '#3d1416'
  on-error-container: '#ffdada'

  # Project-Specific Tokens (Hearth Core)
  bg: '#0e1116'
  surface-2: '#1f2630'
  border: '#2b3441'
  text: '#e6edf3'
  muted: '#9aa7b4'
  accent: '#ff8a3d'
  accent-contrast: '#1a1205'
  focus-ring: '#7cc4ff'
  danger: '#ff6b6b'
  warning: '#e6a23c'
  rating: '#eab308'
  slider-track: 'rgba(255, 255, 255, 0.2)'
  slider-buffered: 'rgba(255, 255, 255, 0.3)'

typography:
  display-lg:
    fontFamily: "system-ui, -apple-system, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif"
    fontSize: 28.8px
    fontWeight: '700'
    lineHeight: 36px
    letterSpacing: '-0.02em'
  headline-md:
    fontFamily: "system-ui, -apple-system, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif"
    fontSize: 17.6px
    fontWeight: '600'
    lineHeight: 22px
    letterSpacing: '-0.01em'
  title-sm:
    fontFamily: "system-ui, -apple-system, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif"
    fontSize: 15.2px
    fontWeight: '600'
    lineHeight: 20px
    letterSpacing: '0'
  body-base:
    fontFamily: "system-ui, -apple-system, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif"
    fontSize: 16px
    fontWeight: '400'
    lineHeight: 24px
    letterSpacing: '0'
  body-medium:
    fontFamily: "system-ui, -apple-system, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif"
    fontSize: 14.4px
    fontWeight: '500'
    lineHeight: 20px
    letterSpacing: '0'
  body-muted:
    fontFamily: "system-ui, -apple-system, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif"
    fontSize: 13.6px
    fontWeight: '400'
    lineHeight: 18px
    letterSpacing: '0'
  label-caps:
    fontFamily: "system-ui, -apple-system, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif"
    fontSize: 12px
    fontWeight: '600'
    lineHeight: 16px
    letterSpacing: '0.04em'
  code-mono:
    fontFamily: "ui-monospace, SFMono-Regular, Consolas, 'Liberation Mono', Menlo, monospace"
    fontSize: 13px
    fontWeight: '400'
    lineHeight: 18px
    letterSpacing: '0'

rounded:
  sm: 0.25rem
  DEFAULT: 0.625rem
  md: 0.375rem
  lg: 0.625rem
  xl: 0.875rem
  full: 9999px

spacing:
  unit: 4px
  xs: 4px
  sm: 8px
  md: 16px
  lg: 20px
  xl: 32px
  2xl: 48px
  gutter: 16px
  margin-mobile: 12px
  margin-desktop: 20px
  shelf-column: 'clamp(140px, 18vw, 200px)'
  max-width: 1400px

elevation:
  dropdown: '0 4px 20px rgba(0, 0, 0, 0.25)'
  modal: '0 12px 32px rgba(0, 0, 0, 0.45)'
  player-panel: '0 8px 30px rgba(0, 0, 0, 0.5)'
---

# Design System: Hearth

## 1. Visual Theme & Atmosphere

Hearth embodies the warmth, dependability, and intimacy of a home hearth. Built as a single-node, self-hosted media server for movies, TV series, music, books, and comics, its visual identity is strictly media-first and dark-only. The interface steps back into the shadows with deep obsidian and slate surfaces (`#0e1116`, `#161b22`), allowing vibrant cover art, photographic film stills, and album sleeves to command the screen. Accented with the radiant glow of fireplace ember orange (`#ff8a3d`), interactive elements feel lively and welcoming without fatiguing eyes during late-night home theater sessions.

The design philosophy prioritizes zero-friction resumption and living room ergonomics. Unfinished media dominates the home view through kinetic, horizontal scroll-snapping shelves (`Continue watching`, `Continue reading`, `Next up`), giving household members instant one-click access back into their content. Designed from the ground up for both modern web browsers and 10-foot smart TVs, all geometry and typography are strictly proportional and rem-based. A 2D spatial navigation engine and high-visibility sky-blue focus rings (`#7cc4ff`) ensure fluid, tactile control using TV remotes, arrow keys, or touchscreens.

---

## 2. Color Palette & Roles

### Primary Foundation
* **Obsidian Canvas (`#0e1116` / `var(--bg)`):** The foundational dark backdrop applied to `html`, `body`, and full-screen video wrappers. Calibrated to prevent OLED backlight bleed and maximize poster contrast.
* **Charcoal Slate Surface (`#161b22` / `var(--surface)`):** Elevated container layer for cards, the sticky top navigation bar, the bottom persistent audio dock, modals, and dropdowns.
* **Interactive Slate Highlight (`#1f2630` / `var(--surface-2)`):** Secondary surface used for hover fills, ghost action buttons, active navigation states, form inputs, and chip badges.
* **Muted Steel Structural Border (`#2b3441` / `var(--border)`):** Hairline structural dividing line used for subtle boundaries, table rows, and card perimeters without visual harshness.

### Accent & Interactive
* **Fireplace Ember Orange (`#ff8a3d` / `var(--accent)`):** The signature Hearth brand accent. Drives primary calls-to-action (`Play`, `Sign in`), playback scrub progress indicators, and active sliders.
* **Ember Contrast Dark (`#1a1205` / `var(--accent-contrast)`):** Ultra-dark warm tone for high-contrast legible text inside solid ember buttons (`.primary`, `.play`).
* **Sky Blue Focus Ring (`#7cc4ff` / `var(--focus-ring)`):** High-visibility, accessible focus outline (`3px` desktop, `4px` TV) ensuring unmissable D-pad spatial orientation on living room displays.

### Typography & Text Hierarchy
* **Icy Silver White (`#e6edf3` / `var(--text)`):** Primary text color for page titles, media names, section headings, and active links.
* **Cool Slate Gray (`#9aa7b4` / `var(--muted)`):** Secondary muted text for release years, seasons/episodes, artists, publishers, metadata tags, and timestamps.

### Functional States
* **Golden Amber Rating (`#eab308`):** Star rating iconography and highlighted favorite states.
* **Warning Amber (`#e6a23c`):** Status chip warnings and storage limit indicators in admin screens.
* **Coral Red Danger (`#ff6b6b` / `var(--danger)`):** Error notifications, destructive actions (delete, cancel job, sign out), and failure banners.
* **Subtle Red Tint (`rgba(229, 115, 115, 0.12)` / `rgba(220, 38, 38, 0.15)`): Background tint for dismissable warning banners and destructive hover states.

---

## 3. Typography Rules

### Hierarchy & Weights
The typography system relies on clean, high-performance system font stacks (`system-ui, -apple-system, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif`) with monospace fallbacks for timestamps and system identifiers.

* **Display Heading 1 (`1.8rem` / `700 bold` / `line-height: 1.25`):** Reserved for media detail titles (`ItemDetail.svelte`), sign-in screens, and top-level admin dashboards.
* **Section Heading 2 (`1.1rem` / `600 semibold` / `line-height: 1.25`):** Used for horizontal shelf titles (`Continue watching`, `Next up`, `Recently added`) and catalog section headers.
* **Card Heading 3 (`0.95rem` / `600 semibold` / `line-height: 1.3`):** Media card titles on shelves and grids. Single-line truncation with CSS ellipsis.
* **Body Base (`1rem` / `16px` / `400 regular` / `line-height: 1.5`):** General copy, synopses, and dialog body text.
* **Subtitles & Metadata (`0.85rem` / `400 regular`):** Secondary metadata strings (`{year} · {type}`, `{series} · S{season}E{episode}`).
* **Status Badges & Chips (`0.75rem` / `500 medium`):** Compact metadata badges, transcode tags (`direct_play`, `hw`), and job statuses.
* **Tabular Numbers:** Tabular numeric variants (`font-variant-numeric: tabular-nums`) applied to elapsed/total playback clocks in `AudioBar` and `VideoPlayer`.

### Spacing Principles
All typography and spatial metrics use `rem` units. When 10-foot TV mode activates (`html.tv`), the root font size scales from `16px` to `20px` (a 1.25× uniform scale). This magnifies all text, controls, margins, and hit targets proportionally, guaranteeing effortless legibility from across the living room.

---

## 4. Component Stylings

### Buttons
* **Primary / Play Button (`.primary`, `.play`):**
  - Background: `var(--accent)` (`#ff8a3d`)
  - Foreground: `var(--accent-contrast)` (`#1a1205`)
  - Typography: `font-weight: 600`
  - Padding: `0.6rem 1.6rem`, `border-radius: var(--radius)` (`10px`)
  - Hover: `filter: brightness(1.05)`
* **Ghost Action Button (`.ghost`):**
  - Background: `var(--surface-2)` (`#1f2630`)
  - Border: `1px solid var(--border)` (`#2b3441`)
  - Foreground: `var(--text)` (`#e6edf3`)
  - Padding: `0.35rem 0.85rem` (toolbar) or `0.6rem 1.1rem` (detail view)
  - Hover: `background: var(--surface)` (`#161b22`)
* **Destructive Button (`.danger`):**
  - Foreground: `#ff6b6b` / `#e57373`
  - Hover: `background: rgba(229, 115, 115, 0.12)`

### Cards & Media Shelves
* **Media Shelf (`Shelf.svelte`):**
  - Horizontal scroll-snap grid: `display: grid; grid-auto-flow: column; grid-auto-columns: clamp(140px, 18vw, 200px); gap: 1rem;`
  - Proximity snap: `scroll-snap-type: x proximity;`
* **Media Card (`MediaCard.svelte`):**
  - Card container: `display: flex; flex-direction: column; gap: 0.4rem;`
  - Poster link: `border-radius: var(--radius); overflow: hidden;`
  - Hover state: `filter: brightness(1.08)`
  - Media aspect ratios:
    - Movies & Books: `2 / 3` portrait poster ratio
    - TV Next Up & Episodes: `16 / 9` widescreen still ratio
    - Music Albums: `1 / 1` square cover ratio
  - Playback progress bar: Pinned to the bottom edge of the poster (`height: 4px; background: rgba(255, 255, 255, 0.2); fill: var(--accent)`).

### Navigation & Topbar
* **Sticky Topbar (`+layout.svelte`):**
  - Docked to top: `position: sticky; top: 0; z-index: 20; background: var(--surface); border-bottom: 1px solid var(--border);`
  - Brand logo: Ember accent color (`#ff8a3d`), `font-weight: 700; font-size: 1.1rem;`
  - Navigation links: Inline flex with Lucide icons (`16px`), muted text defaulting to `#9aa7b4`, transitioning to `#e6edf3` on hover and `font-weight: 600` on active route (`aria-current="page"`).
  - User Dropdown: Trigger button with avatar icon and chevron, popping an elevated container (`box-shadow: 0 4px 20px rgba(0, 0, 0, 0.25)`).

### Inputs & Forms
* **Form Inputs (`input`, `select`, `textarea`):**
  - Background: `var(--bg)` (`#0e1116`)
  - Border: `1px solid var(--border)` (`#2b3441`)
  - Radius: `var(--radius)` (`10px`)
  - Padding: `0.55rem 0.7rem`
  - Focus state: `:focus-visible { outline: 3px solid var(--focus-ring); outline-offset: 2px; }`

### Dedicated Media Players & Readers
* **Persistent Audio Dock (`AudioBar.svelte`):**
  - Docked to viewport bottom (`position: sticky; bottom: 0; z-index: 20; background: var(--surface); border-top: 1px solid var(--border);`).
  - Contains track title, artist/album subtitle, transport controls (`SkipBack`, `Play/Pause`, `SkipForward`, `Square`), volume scrubber with custom thumb, and tabular timestamp counters.
* **Video Theater (`VideoPlayer.svelte`):**
  - Full-screen immersion with Video.js and WebAssembly ASS/SSA canvas renderer (`jassub`).
  - Overlay gradient controls (`rgba(0, 0, 0, 0.85)` to transparent).
  - Stream mode badge: Pill chip indicating playback engine (`direct_play`, `remux`, `transcode`, `hw`).
* **Book & Comic Readers (`EpubReader`, `PdfReader`, `ComicReader`):**
  - Full-bleed viewer with minimal floating pagination controls.
  - Comic reader supports single-page fit-width/fit-height modes against a pure `#000` canvas.

### Admin & System Lists
* **System Fact Lists (`.facts`):** Two-column definition list (`grid-template-columns: auto 1fr`) pairing muted labels with crisp data values.
* **Status Chips (`.chip`):** Pill-shaped indicators (`background: var(--surface-2); border: 1px solid var(--border); border-radius: 999px; font-size: 0.75rem`).
* **Banners (`.banner`):** Surface containers with colored accent borders (`border-left: 3px solid var(--danger)` or `var(--accent)`).

---

## 5. Layout Principles

### Grid & Structure
* **Maximum Content Width:** `1400px` centered with auto margins (`margin: 0 auto;`).
* **Mobile / Desktop Padding:** `0.75rem` (`12px`) on compact viewports (<720px), `1.25rem` (`20px`) on desktop.
* **Item Detail Grid:** Asymmetric two-column split (`grid-template-columns: minmax(200px, 280px) 1fr; gap: 2rem;`), collapsing to single-column on mobile (<720px).

### Whitespace Strategy
* **Base Gap (`--gap`):** `1rem` on desktop, expanding to `1.25rem` in TV mode.
* **Section Margins:** `2rem` bottom margin on shelves (`Shelf.svelte`), creating clear spatial demarcation between media categories without rigid border rules.

### Alignment & Visual Balance
* Left-aligned text hierarchy with right-aligned utilities and status counters.
* Artwork-driven hierarchy: Vibrant posters provide visual variety while uniform card bounds maintain clean architectural cadence.

### Responsive Behavior & 10-Foot TV Mode
* **Proportional Rem Scaling:** Setting `html.tv` increases root font size from `16px` to `20px`, scaling typography, card clamps, buttons, and gaps in unison.
* **2D Spatial Navigation:** Keyboard/remote arrow keys calculate 2D Euclidean proximity between focusable elements (`MIN_TRAVEL_PX = 4`, `PERPENDICULAR_WEIGHT = 2`).
* **Scroll Margins for Sticky Chrome:** In TV mode, all interactive elements declare:
  ```css
  html.tv a, html.tv button, html.tv input, html.tv select, html.tv [tabindex] {
    scroll-margin-top: 4.5rem;
    scroll-margin-bottom: 4.5rem;
  }
  ```
  This guarantees that focused cards and controls never get obscured behind the sticky topbar or the sticky audio bar.
* **Accessibility & Reduced Motion:** Respects `prefers-reduced-motion` by clamping transition and animation durations to `0.01ms`.

---

## 6. Design System Notes for Stitch Generation

### Language to Use
When prompting Stitch to generate new screens or components for Hearth, use evocative, sensory adjectives:
* *"Deep obsidian media canvas with subtle charcoal slate containers and hairline steel borders."*
* *"Warm fireplace ember orange accents on primary action buttons and playback progress meters."*
* *"High-contrast icy silver typography paired with cool slate gray metadata subtitles."*
* *"Proportional 10-foot living room interface with sky-blue focus outlines and horizontal scroll-snap shelves."*
* *"Clean, uncluttered, dark-mode-only dashboard designed for nighttime viewing."*

### Color References
* Canvas background: `Deep Obsidian (#0e1116)`
* Card / Topbar surface: `Charcoal Slate (#161b22)`
* Elevated hover surface: `Interactive Slate (#1f2630)`
* Border dividers: `Muted Steel (#2b3441)`
* Primary text: `Icy Silver White (#e6edf3)`
* Secondary text: `Cool Slate Gray (#9aa7b4)`
* Brand accent: `Fireplace Ember Orange (#ff8a3d)`
* Accent button text: `Warm Dark Brown (#1a1205)`
* Focus outline: `Sky Blue (#7cc4ff)`
* Danger / Error: `Coral Red (#ff6b6b)`
* Star ratings: `Golden Amber (#eab308)`

### Component Prompts
* **Media Shelf Screen:**
  > "Create a dark-mode media shelf section for a self-hosted media server. Background #0e1116 with container cards #161b22. Display an H2 section title 'Continue watching' in #e6edf3. Add a horizontal row of portrait 2:3 ratio movie posters with 10px rounded corners. Each poster has an orange (#ff8a3d) 4px progress bar along the bottom edge, a bold title, and release year in muted gray (#9aa7b4). Include a high-visibility sky blue (#7cc4ff) focus ring around the currently focused card."
* **Now Playing Audio Dock:**
  > "Design a sticky bottom music player bar for Hearth. Background #161b22 with a top border in #2b3441. Show square album art (48x48px), track title in #e6edf3, and artist in #9aa7b4. Include ghost transport buttons (Previous, Play/Pause in orange, Next, Stop), tabular elapsed and total timestamps (03:42 / 05:10), and a thin scrub slider with an orange #ff8a3d active fill and rounded thumb."
* **Media Detail Page:**
  > "Design an asymmetric media detail layout. Left column has a prominent 2:3 portrait poster with 10px rounded corners and a golden amber (#eab308) star rating badge. Right column features an H1 title in icy white #e6edf3, release year and runtime in muted slate #9aa7b4, a warm fireplace ember orange #ff8a3d primary 'Play' button, and a ghost 'Add to Favourites' button with a heart icon. Underneath, show a brief plot synopsis and a two-column cast definition list."

### Incremental Iteration
1. Maintain strict dark-mode-only aesthetics; never generate light-mode backgrounds or white canvas panels.
2. Ensure all interactive controls have explicit `:focus-visible` states using `#7cc4ff`.
3. Use exact domain terminology: `Continue watching`, `Continue reading`, `Next up`, `direct_play`, `remux`, `transcode`.
4. Keep cards responsive with horizontal scroll-snap on shelves rather than multi-row wraps for unfinished media.
