# Local Studio Page Overrides

> Dashboard for the local short-video editing app. These decisions override the marketing-oriented page pattern in `MASTER.md` where they differ.

## Visual direction

- Use the existing dark creative-tool palette, Inter font, and magenta creator action from `MASTER.md`; use cyan only for process information and mint for ready/local states.
- Keep the canvas near-black, panels solid charcoal, borders visible but restrained, and controls high-contrast. Avoid translucent glass panels and large decorative gradients.
- The app is a working studio, not a public landing page. Do not use a large promotional hero, fabricated metrics, or decorative autoplay clips.

## Layout and hierarchy

- Keep the left page rail on desktop and sticky action header. Hide the duplicated header brand while the rail is present; restore it on narrow screens when the rail is hidden.
- Use a compact workspace introduction with the local processing disclosure, then show project configuration and the active execution card together on desktop.
- Make the source URL the first and most visually prominent control. Keep sensible defaults visible, and retain every advanced setting and its existing control ID.
- Keep execution status, named processing phases, actions, and logs grouped in one card. Queue, completed clips, and editorial report remain full-width sections after setup.
- Use an 8px spacing rhythm, 12–14px card radii, readable labels, and 40px or larger controls. On small screens, collapse to one column and remove sticky side panels.

## Interaction and accessibility

- Keep existing IDs and native form controls so current JavaScript behaviors and form submission continue to work.
- Use visible keyboard focus, explicit field hints, semantic headings and status labels. Status meaning must not rely on color alone.
- Use subtle 150–200ms control transitions. Honor `prefers-reduced-motion`; do not autoplay decorative video in the workspace introduction.
- Preserve usable widths at 375px, 768px, 1024px, and 1440px without horizontal page overflow.
