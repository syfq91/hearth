---
name: "Hearth"
version: "1.0.0"
description: "Design system specification and visual identity for Hearth — a fast, single-node, self-hosted media server for movies, TV shows, music, books, and comics."
colors:
  bg: "#0e1116"
  surface: "#161b22"
  surface-2: "#1f2630"
  border: "#2b3441"
  text: "#e6edf3"
  muted: "#9aa7b4"
  accent: "#ff8a3d"
  accent-contrast: "#1a1205"
  focus-ring: "#7cc4ff"
  danger: "#ff6b6b"
  danger-subtle: "rgba(229, 115, 115, 0.12)"
  rating: "#eab308"
  slider-track: "rgba(255, 255, 255, 0.2)"
  slider-buffered: "rgba(255, 255, 255, 0.3)"
typography:
  fontFamily:
    sans: "system-ui, -apple-system, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif"
    mono: "ui-monospace, SFMono-Regular, Consolas, 'Liberation Mono', Menlo, monospace"
  fontSize:
    xs: "0.75rem"
    sm: "0.85rem"
    base: "1rem"
    lg: "1.1rem"
    xl: "1.2rem"
    "2xl": "1.8rem"
    root-desktop: "16px"
    root-tv: "20px"
  fontWeight:
    regular: 400
    medium: 500
    semibold: 600
    bold: 700
  lineHeight:
    tight: 1.25
    normal: 1.5
spacing:
  xs: "0.25rem"
  sm: "0.5rem"
  base: "1rem"
  lg: "1.25rem"
  xl: "2rem"
  gap-desktop: "1rem"
  gap-tv: "1.25rem"
  shelf-column: "clamp(140px, 18vw, 200px)"
  max-width: "1400px"
rounded:
  sm: "4px"
  md: "6px"
  base: "10px"
  full: "999px"
elevation:
  dropdown: "0 4px 20px rgba(0, 0, 0, 0.25)"
---

# Hearth Design System (`DESIGN.md`)

> **"Your media, your server."**  
> Hearth turns the folders of movies, TV shows, music, books, and comics on your own hardware into a fast, good-looking web app for the whole household. Your files stay on your hardware, and there is no subscription.

This document is the normative visual design specification for Hearth. It defines the design tokens, layout geometry, component rules, 10-foot TV interactions, and styling guardrails based on the codebase.

---

## 1. Product Purpose & Architectural Scope

Hearth is a single-node, self-hosted multi-media application running in a single Docker container. The interface is served as a static SvelteKit 5 SPA directly from FastAPI (`/`) talking to the backend at `/api/v1`.

### Core Goals
1. **Unfinished Media Front-and-Center:** Immediate one-click resumption of unfinished movies, series, books, and music right from the home screen.
2. **First-Class Household Living Room Experience:** Seamless operation on both desktop/mobile browsers and 10-foot smart TVs using remote controls and arrow keys.
3. **Hardware-Resilient Media Playback:** Zero-friction direct play with automatic fallback to CPU transcoding (`libx264`) or hardware VA-API (Intel QuickSync / AMD Radeon), high-fidelity SSA/ASS subtitle rendering via WebAssembly (`jassub`), and gapless audio.
4. **Dark, Cozy, Media-First Aesthetic:** Deep dark slate backgrounds (`#0e1116`) that recede behind colorful artwork posters, albums, and book covers, highlighted by a warm fireplace ember accent (`#ff8a3d`).

---

## 2. Lead Experience & Visual Weight

The README leads with: **"Your media, your server."** and **"The homepage keeps your unfinished media one click away with Continue watching and Continue reading rows."**

In Hearth, unfinished media dominates the visual hierarchy:
- **Primary Visual Weight:** The top fold of the home screen prioritizes active consumption queues:
  1. `Continue watching` (Movies and TV episodes in progress with visible percentage progress bars).
  2. `Continue reading` (EPUB, PDF, and Comic CBZ/CBR books with percentage reading meters).
  3. `Next up` (Next sequential TV episodes for in-progress series).
  4. `Recently added albums` (Square 1:1 artwork music row).
  5. `Recently added` (Newly scanned catalog items).
- **Secondary Weight:** Library catalogs, global search, administrative monitoring (transcode sessions, scanning jobs, audit logs).

---

## 3. Native Shape: The Media Shelf & Playback Canvas

Hearth does not use generic dashboard widgets. Its native layout is driven by three media paradigms:

```
┌────────────────────────────────────────────────────────────────────────┐
│ Topbar (Sticky): Hearth [Home] [Library] [Music] [Books] [Favourites] [Search] │
├────────────────────────────────────────────────────────────────────────┤
│ Continue watching (Horizontal Snap Shelf)                              │
│ ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐            │
│ │ Movie 1 │ │ Ep 102  │ │ Movie 2 │ │ Ep 204  │ │ Movie 3 │  [--->]    │
│ │ [==== ] │ │ [======]│ │ [=    ] │ │ [===  ] │ │ [======]│            │
│ └─────────┘ └─────────┘ └─────────┘ └─────────┘ └─────────┘            │
│ Continue reading (Horizontal Snap Shelf)                               │
│ ┌───────┐   ┌───────┐   ┌───────┐                                      │
│ │Book 1 │   │Book 2 │   │Comic 1│                                      │
│ └───────┘   └───────┘   └───────┘                                      │
├────────────────────────────────────────────────────────────────────────┤
│ AudioBar (Sticky Bottom): [Art] Track Title - Artist  [|<] [ > ] [>|]  │
└────────────────────────────────────────────────────────────────────────┘
```

1. **The Horizontal Scroll-Snap Shelf (`Shelf.svelte`):**
   - Media cards align in an auto-flowing horizontal grid (`grid-auto-flow: column`).
   - Column widths dynamically scale: `clamp(140px, 18vw, 200px)` with proximity scroll snap (`scroll-snap-type: x proximity`).
   - Card media displays with strict aspect ratios:
     - Movies & Books: `2 / 3` portrait poster ratio.
     - TV Next Up / Episodes: `16 / 9` widescreen still ratio.
     - Music Albums: `1 / 1` square ratio.
2. **Persistent Audio Dock (`AudioBar.svelte`):**
   - Anchored to the bottom (`position: sticky; bottom: 0; z-index: 20`).
   - Compact metadata (Title, Artist, Album), tabular playback timestamps (`font-variant-numeric: tabular-nums`), scrubbing slider, and ghost playback controls (`Play`, `Pause`, `SkipBack`, `SkipForward`, `Square` stop).
3. **Dedicated Immersion Environments:**
   - **Video Theater (`VideoPlayer.svelte`):** Full-bleed video player with Video.js, WebAssembly ASS/SSA canvas layer (`jassub`), audio track switcher, subtitle selector, and stream mode badge (`direct_play`, `remux`, `transcode`).
   - **Book & Comic Readers:** Specialized readers for EPUB (`foliate-js`), PDF (`pdfjs-dist`), and comics (`ComicReader.svelte` for `.cbz` / `.cbr` archives).

---

## 4. Design Tokens & Visual Hierarchy

### Color Palette

| Token Name | Hex / Value | Code Usage | Semantic Role |
| :--- | :--- | :--- | :--- |
| `--bg` | `#0e1116` | Background of `html`, `body`, inputs | Base deep slate canvas |
| `--surface` | `#161b22` | Cards, topbar, audio bar, modals | Elevated container surface |
| `--surface-2` | `#1f2630` | Hover states, buttons, dropdowns, inputs | Secondary interactive surface |
| `--border` | `#2b3441` | Borders, dividers, subtle outlines | Component structural boundaries |
| `--text` | `#e6edf3` | Headings, titles, active link text | Primary high-contrast typography |
| `--muted` | `#9aa7b4` | Subtitles, labels, metadata, inactive links | Secondary muted typography |
| `--accent` | `#ff8a3d` | Brand logo, primary buttons, progress bars | Hearth ember orange accent |
| `--accent-contrast`| `#1a1205` | Text inside `.primary` / `.play` buttons | High-contrast dark button label |
| `--focus-ring` | `#7cc4ff` | `:focus-visible` outline | Sky-blue accessible focus ring |
| `--danger` | `#ff6b6b` | Alerts, error banners, sign-out actions | Critical failure & destruction |
| Rating Star | `#eab308` | `<Star>` icon fill and text | Media star rating |

### Typography

- **Font Family:** `system-ui, -apple-system, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif`
- **Root Sizing:**
  - Desktop / Default: `16px` base font size.
  - TV Mode: `20px` base font size (scaling all `rem`-based measurements by 1.25×).
- **Scale Hierarchy:**
  - `h1` (Item detail, Auth title): `1.8rem`, `line-height: 1.25`, `font-weight: 700`
  - `h2` (Shelf headings, section titles): `1.1rem`, `line-height: 1.25`, `font-weight: 600`
  - `h3` / Card Title: `0.95rem`, `font-weight: 600`, single-line truncated
  - Body & Form Controls: `1rem` (`16px`), `line-height: 1.5`
  - Subtitles, Meta & Taglines: `0.85rem` - `0.9rem`, `color: var(--muted)`
  - Chips & Status Badges: `0.75rem`, `border-radius: 999px`

### Spatial Grid & Radii

- **Base Radius (`--radius`):** `10px` (applied to media cards, modals, posters, audio controls).
- **Inner Controls Radius:** `calc(var(--radius) - 2px)` (8px) or `6px` for dropdown items and select elements.
- **Pill Radius:** `999px` (status chips, transcode badges, TV mode indicators).
- **Base Gap (`--gap`):** `1rem` on desktop, expanding to `1.25rem` in TV mode.
- **Max Container Width:** `1400px` centered with auto margins.

---

## 5. 10-Foot Living-Room TV Mode

Hearth features a first-class 10-foot TV interface designed for smart TVs (Samsung Tizen, LG webOS, Android TV, Apple TV) and media PCs:

1. **Proportional Rem Scaling:**
   All sizing in the web client is strictly `rem`-based. Setting `html.tv` increases the root font size from `16px` to `20px`, which scales all text, padding, buttons, and card geometry proportionally for comfortable viewing from 10 feet away.
2. **Spatial Navigation Engine (`spatial-focus.ts`):**
   - Intercepts `ArrowUp`, `ArrowDown`, `ArrowLeft`, `ArrowRight` when TV mode is active.
   - Calculates 2D Euclidean proximity between focusable elements:
     - `MIN_TRAVEL_PX = 4` (minimum movement threshold).
     - `PERPENDICULAR_WEIGHT = 2` (prefers aligned elements over diagonal jumps).
   - Automatically scrolls the active element into view.
3. **Scroll Margins for Sticky Chrome:**
   To prevent focused elements from being occluded by the sticky topbar or the sticky audio bar, all interactive elements in TV mode declare:
   ```css
   html.tv a, html.tv button, html.tv input, html.tv select, html.tv [tabindex] {
       scroll-margin-top: 4.5rem;
       scroll-margin-bottom: 4.5rem;
   }
   ```
4. **Enhanced High-Visibility Focus Ring:**
   - Desktop: `outline: 3px solid var(--focus-ring); outline-offset: 2px;`
   - TV Mode: `outline: 4px solid var(--focus-ring); outline-offset: 3px;`

---

## 6. Core Component Patterns

### 1. MediaCard (`MediaCard.svelte`)
- Encapsulates poster artwork with rounded corners (`10px`).
- Shows interactive hover brightening: `filter: brightness(1.08)`.
- Displays playback progress bar pinned to the bottom of the poster:
  - Background: `rgba(255, 255, 255, 0.2)`
  - Active fill: `var(--accent)` (`#ff8a3d`)
- Includes structured subtitle:
  - Episodes: `{series_title} · S{season}E{episode}`
  - Movies / Media: `{year} · {type}`

### 2. Buttons & Actions
- **Primary / Play Button (`.primary`, `.play`):**
  - Background: `var(--accent)` (`#ff8a3d`)
  - Foreground: `var(--accent-contrast)` (`#1a1205`)
  - Typography: `font-weight: 600`
  - Padding: `0.6rem 1.6rem`, `border-radius: var(--radius)`
- **Ghost Action Button (`.ghost`):**
  - Background: `var(--surface-2)` (`#1f2630`)
  - Border: `1px solid var(--border)` (`#2b3441`)
  - Foreground: `var(--text)` (`#e6edf3`)
  - Hover: `background: var(--surface)`
- **Danger Action Button (`.danger`):**
  - Color: `#e57373`
  - Hover: `background: rgba(229, 115, 115, 0.12)`

### 3. Admin & Fact Lists
- **System Grid (`.facts`):** Two-column definition list (`grid-template-columns: auto 1fr`) pairing muted labels (`<dt>`) with crisp values (`<dd>`).
- **Status Chips (`.chip`):** Pill-shaped badges (`background: var(--surface-2); border: 1px solid var(--border); border-radius: 999px; font-size: 0.75rem`).
- **Error & Status Banners (`.banner`):**
  - Background: `var(--surface)`
  - Border: `1px solid var(--border)`
  - Accent Callout: `border-left: 3px solid var(--danger)` (`#ff6b6b`)

---

## 7. Canonical Labels, Copy & Terminology

Every label and message in the interface must match the real codebase terminology:

- **Navigation Items:** `Home`, `Library`, `Music`, `Books`, `Favourites`, `Search`, `Admin`, `Profile`, `Sign out`
- **Shelves:** `Continue watching`, `Continue reading`, `Next up`, `Recently added albums`, `Recently added`
- **Item Actions:** `Play`, `Resume`, `Favorite`, `In favorites`, `Watched`, `Mark watched`, `Read EPUB`, `Read PDF`, `Read CBZ`
- **Library Types:** `Movies`, `TV shows`, `Music`, `Books`
- **Stream Modes:** `direct_play`, `remux`, `transcode`, `hw` (hardware accelerated)
- **Job Statuses:** `pending`, `running`, `completed`, `failed`, `cancelled`
- **Error Envelope Format:**
  ```json
  {
    "error": {
      "code": "not_found",
      "message": "Item not found",
      "details": {}
    }
  }
  ```

---

## 8. Critical Files & Source of Truth

| File Path | Role & Purpose |
| :--- | :--- |
| `web/src/app.css` | Global CSS custom properties, resets, focus-visible styles, TV mode overrides, reduced-motion rules |
| `web/src/routes/+layout.svelte` | App shell, sticky topbar, navigation routes, user dropdown menu, skip link, spatial navigation mount |
| `web/src/routes/+page.svelte` | Homepage implementation containing the unfinished media shelves and empty states |
| `web/src/lib/components/Shelf.svelte` | Horizontal scroll-snap shelf container |
| `web/src/lib/components/MediaCard.svelte` | Individual media cards with artwork aspect ratios, progress bars, and metadata subtitles |
| `web/src/lib/components/AudioBar.svelte` | Persistent sticky audio player with gapless preloader and MediaSession integration |
| `web/src/lib/components/VideoPlayer.svelte` | Video.js HLS player, stream mode badge, and JASSUB WebAssembly subtitle overlay |
| `web/src/lib/components/ItemDetail.svelte` | Rich media detail layout (backdrop, poster, rating, cast grid, play/favorite controls) |
| `web/src/lib/tv.svelte.ts` | TV mode detection rune store and persistent storage handling |
| `web/src/lib/actions/spatial-focus.ts` | 2D geometric D-pad keyboard navigation action |
| `packages/client/src/schema.d.ts` | Generated TypeScript types matching the backend OpenAPI schema |

---

## 9. Design System Do's and Don'ts

### Do's
- **DO use `rem` units for all dimensions and typography.** This ensures TV mode scaling works automatically across all views.
- **DO keep the dark aesthetic.** Hearth is strictly dark-mode (`color-scheme: dark; --bg: #0e1116`). There is no light mode.
- **DO maintain visible focus outlines.** Keyboard and D-pad navigation depend on `--focus-ring` (`#7cc4ff`) with offset.
- **DO maintain media aspect ratios:** `2/3` for movies/books, `16/9` for episodes/stills, and `1/1` for music albums.
- **DO preserve scroll margins.** In TV mode, all focusable elements must have `scroll-margin-top: 4.5rem` and `scroll-margin-bottom: 4.5rem` to avoid being hidden under the sticky header or audio bar.
- **DO honor `prefers-reduced-motion`.** Disable transitions and animations when requested by the OS.

### Don'ts
- **DON'T introduce light mode palettes or high-key white backgrounds.**
- **DON'T hardcode fixed `px` sizes for containers, cards, or fonts.** Fixed pixel values break 10-foot TV mode scaling.
- **DON'T hide or suppress focus rings.** Never use `outline: none` without providing an explicit `:focus-visible` replacement.
- **DON'T add arbitrary third-party CSS utility frameworks (Tailwind, Bootstrap).** Hearth uses clean vanilla CSS with native CSS variables and Svelte scoped styles.
- **DON'T invent fake labels or status terms.** Use the exact terminology defined in the database models and OpenAPI contract.
