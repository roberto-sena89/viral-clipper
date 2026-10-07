# Design System Master File

> **LOGIC:** When building a specific page, first check `design-system/pages/[page-name].md`.
> If that file exists, its rules **override** this Master file.
> If not, strictly follow the rules below.

---

**Project:** Viral Clipper
**Generated:** 2026-10-01 07:21:37
**Category:** Video Editing / Short-form Creator Tool
**Design Dials:** Variance 6/10 (Balanced / Modern) | Motion 3/10 (Subtle) | Density 7/10 (Standard)

---

## Global Rules

### Color Palette

**One palette for the whole product.** These values are the tokens declared in
`web/shared.css`, which is the single source of truth: the Estúdio, the Biblioteca
and Ajustes all inherit it, and `public-site/assets/site.css` mirrors it. A page
must not re-declare them.

There used to be two. `shared.css` carried an indigo base (`#6366F1`) and
`index.css` redefined the whole `:root` in magenta — so the Biblioteca, the only
page that does not load `index.css`, silently rendered itself indigo while the
other two rendered magenta. `SharedStyleSheetTests` and
`test_the_panel_palette_lives_in_the_shared_sheet` now hold the line.

| Role | Hex | CSS Variable |
|------|-----|--------------|
| Primary | `#FF4FAE` | `--accent-primary` (studio) / `--pink` (site) |
| On Primary | `#1C1024` | `--grad-ink` |
| Primary tint | `#FF8BC8` | `--accent-soft` |
| Primary glow | `rgba(255,79,174,.22)` | `--accent-glow` |
| Accent/CTA | `#70C9EE` | `--blue` (site only) |
| On Accent/CTA | `#090B10` | — |
| Background | `#090B10` | `--bg-deep` (studio) / `--bg` (site) |
| Surface | `#11151E` | `--bg-surface` (studio) / `--surface` (site) |
| Card | `#131923` | `--bg-card` (studio) / `--surface-raised` (site) |
| Foreground | `#F3F4F6` | `--text-primary` / `--ink` |
| Muted Foreground | `#9CA3AF` | `--text-muted` / `--ink-muted` |
| Subtle Foreground | `#818896` | `--text-ink-subtle` |
| Border | `rgba(218,226,243,.09)` | `--border-subtle` / `--line` |
| Success | `#54D6A6` | `--success` / `--mint` |
| Warning | `#FBBF24` | `--warn` |
| Destructive | `#FF7184` | `--danger` |
| Active tint | `rgba(255,79,174,.11)` | `--active-tint` |

**Color Notes:** Dark neutral surfaces (not navy), magenta for creator actions,
cyan for information and timeline states (site only), green for local/success,
amber for anything that means *attend to this*: pending, skipped, session,
mid-potential, and the `ln-warn` lines in both logs.

**Naming warning, resolved:** `--accent-warm` held `#70C9EE` and was used for
seven attention states, two of them literally `.ln-warn` — each sitting beside a
`rgba(251,191,36,…)` frame, so cyan text on an amber wash. It is now `--warn`
(`#FBBF24`), which also reads better on dark: 11.79:1 on `--bg-deep` against
10.57:1 for the cyan.

### Typography

- **Heading Font:** Inter (self-hosted)
- **Body Font:** Inter (self-hosted)
- **Utility Font:** system monospace for commands and technical values
- **Mood:** modern, creative-tool, precise, readable
- **Font source:** repository-local WOFF2; public pages do not require a font CDN

**CSS Import:**
```css
Use the locally hosted Inter WOFF2 assets and a system monospace stack for code.
```

### Spacing Variables

*Density: 7/10 — Standard*

| Token | Value | Usage |
|-------|-------|-------|
| `--space-xs` | `4px` / `0.25rem` | Tight gaps |
| `--space-sm` | `8px` / `0.5rem` | Icon gaps, inline spacing |
| `--space-md` | `16px` / `1rem` | Standard padding |
| `--space-lg` | `24px` / `1.5rem` | Section padding |
| `--space-xl` | `32px` / `2rem` | Large gaps |
| `--space-2xl` | `48px` / `3rem` | Section margins |
| `--space-3xl` | `64px` / `4rem` | Hero padding |

### Shadow Depths

| Level | Value | Usage |
|-------|-------|-------|
| `--shadow-sm` | `0 1px 2px rgba(0,0,0,0.05)` | Subtle lift |
| `--shadow-md` | `0 4px 6px rgba(0,0,0,0.1)` | Cards, buttons |
| `--shadow-lg` | `0 10px 15px rgba(0,0,0,0.1)` | Modals, dropdowns |
| `--shadow-xl` | `0 20px 25px rgba(0,0,0,0.15)` | Hero images, featured cards |

---

## Component Specs

### Buttons

```css
/* Primary Button */
.btn-primary {
  background: #FF4FAE;
  color: #1C1024;
  padding: 12px 24px;
  border-radius: 10px;
  font-weight: 650;
  transition: transform 200ms ease, background-color 200ms ease, box-shadow 200ms ease;
  cursor: pointer;
}

.btn-primary:hover {
  background: #FFB9DF;
  transform: translateY(-2px);
}

/* Secondary Button */
.btn-secondary {
  background: transparent;
  color: #FF8BC8;
  border: 2px solid rgba(255, 79, 174, .5);
  padding: 12px 24px;
  border-radius: 8px;
  font-weight: 600;
  transition: all 200ms ease;
  cursor: pointer;
}
```

### Cards

```css
.card {
  background: #131923;
  border: 1px solid rgba(218,226,243,.09);
  border-radius: 18px;
  padding: 24px;
  box-shadow: 0 16px 48px rgba(0,0,0,.24);
  transition: border-color 200ms ease, background-color 200ms ease;
}

.card:hover {
  border-color: rgba(218,226,243,.16);
}
```

### Inputs

```css
.input {
  padding: 12px 16px;
  border: 1px solid rgba(218,226,243,.16);
  border-radius: 8px;
  font-size: 16px;
  transition: border-color 200ms ease;
}

.input:focus {
  border-color: #FF4FAE;
  outline: none;
  box-shadow: 0 0 0 3px rgba(255, 79, 174, .20);
}
```

### Modals

```css
.modal-overlay {
  background: rgba(0, 0, 0, 0.5);
  backdrop-filter: blur(4px);
}

.modal {
  background: white;
  border-radius: 16px;
  padding: 32px;
  box-shadow: var(--shadow-xl);
  max-width: 500px;
  width: 90%;
}
```

---

## Style Guidelines

**Style:** Soft UI Evolution

**Keywords:** Evolved soft UI, better contrast, modern aesthetics, subtle depth, accessibility-focused, improved shadows, hybrid

**Best For:** Modern enterprise apps, SaaS platforms, health/wellness, modern business tools, professional, hybrid

**Key Effects:** Improved shadows (softer than flat, clearer than neumorphism), modern (200-300ms), focus visible, measured contrast targets

### Page Pattern

**Pattern Name:** Real-Time / Operations Landing

- **Conversion Strategy:** Offer a demo or sandbox and show trust signals. Label telemetry as live only when backed by a current source, with update time and stale state. Provide pause/hide or update-frequency controls for tickers and previews, stop offscreen/hidden work, support keyboard controls, and render a static final snapshot under reduced motion.
- **CTA Placement:** Primary CTA in nav + After metrics
- **Section Order:** Hero (product + live preview or status) > Key metrics/indicators > How it works > CTA (Start trial / Contact)

---

## Motion

**Scroll Reveal** (Subtle) — Trigger: scroll (viewport enter) | Duration: 300-400ms | Easing: `power1.out`

```js
gsap.from(el, { opacity: 0, y: 12, duration: 0.35, ease: 'power1.out', scrollTrigger: { trigger: el, start: 'top 90%', toggleActions: 'play none none reverse' } });
```

**Framework notes:** Requires the ScrollTrigger plugin registered once via gsap.registerPlugin(ScrollTrigger); Use matchMedia('(prefers-reduced-motion: reduce)') to skip non-essential motion and render the final state immediately

- ✅ Keep the y offset small (8-16px) so it reads as a fade, not a slide
- ❌ Don't reveal below-the-fold content needed for SEO/crawlers as invisible-by-default without a no-JS fallback
- ⚡ toggleActions 'play none none reverse' avoids re-triggering on every scroll direction change

---

## Anti-Patterns (Do NOT Use)

- ❌ Cluttered interface
- ❌ No presence

### Additional Forbidden Patterns

- ❌ **Emojis as icons** — Use SVG icons (Heroicons, Lucide, Simple Icons)
- ❌ **Missing cursor:pointer** — All clickable elements must have cursor:pointer
- ❌ **Layout-shifting hovers** — Avoid scale transforms that shift layout
- ❌ **Low contrast text** — Maintain 4.5:1 minimum contrast ratio
- ❌ **Instant state changes** — Always use transitions (150-300ms)
- ❌ **Invisible focus states** — Focus states must be visible for a11y

---

## Pre-Delivery Checklist

Before delivering any UI code, verify:

- [ ] No emojis used as icons (use SVG instead)
- [ ] All icons from consistent icon set (Heroicons/Lucide)
- [ ] `cursor-pointer` on all clickable elements
- [ ] Hover states with smooth transitions (150-300ms)
- [ ] Light mode: text contrast 4.5:1 minimum
- [ ] Focus states visible for keyboard navigation
- [ ] `prefers-reduced-motion` respected
- [ ] Responsive: 375px, 768px, 1024px, 1440px
- [ ] No content hidden behind fixed navbars
- [ ] No horizontal scroll on mobile
