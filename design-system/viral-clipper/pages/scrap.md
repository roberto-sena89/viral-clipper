# Scrap Workspace Page Override

> **Project:** Viral Clipper  
> **Page type:** Local media workspace  
> **Source of truth:** `design-system/viral-clipper/MASTER.md`

## Purpose

Help a creator find media from a supported platform, inspect the returned items, and choose one to continue into the local cutting workflow.

## Layout

- Keep the global application rail and shared header consistent with Estúdio.
- Use a two-column workspace on wide screens: search and selected-item summary in a compact left column, results in a wide right column.
- At tablet and mobile widths, place search, results, and selected item in one readable vertical flow. Never make a results table require page-level horizontal scrolling.
- Use a concise workspace hero with platform and local-processing context. Avoid promotional language, invented usage metrics, and duplicate brand headers.

## Visual treatment

- Use the shared dark creator-tool palette, Inter typography, pink for primary actions, and cyan/mint only for informative and success states.
- Keep panels solid and quiet; use subtle borders, restrained shadows, and clear spacing instead of nested glass effects.
- Give platform selection, search mode, URL input, results actions, and selected media distinct visual hierarchy.
- Keep session/cookie controls visibly grouped with concise helper text. Keep all existing controls and statuses available.
- Use a consistent icon style; avoid emoji as structural icons.

## Interaction and accessibility

- Preserve existing form IDs, tab roles, keyboard behavior, progress IDs, selection IDs, and live regions used by the local server scripts.
- Retain visible keyboard focus, semantic labels, descriptive empty states, and reduced-motion support.
- Keep the selected-media panel discoverable while browsing results on desktop; make it static on narrow screens.
- Never autoplay downloaded media as a decorative loop.
