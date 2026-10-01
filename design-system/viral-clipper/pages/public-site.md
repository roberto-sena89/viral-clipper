# Public-site page override

This page is the public presentation site for a local, open-source short-video editing tool. It is not the operational studio dashboard or a marketing page for a hosted subscription product.

## Visual direction

- Use the Soft UI Evolution restraint from `MASTER.md`, adapted to the existing dark creative-tool identity.
- Background: `#0B1020`; surfaces: `#121A2B` and `#192338`; text: `#F5F7FC`; secondary text: `#C2CAD9`.
- Primary action: short-video-editor pink `#EC4899` / `#DB2777`; blue `#70A6FF` is reserved for timeline and information states; mint `#52D6B0` marks local/success details.
- Keep contrast high, borders quiet, shadows broad and subtle, and corners consistent.
- Use the repository's self-hosted Inter fonts. Use system monospace for commands; do not make the site depend on a font CDN.

## Page content

- Lead with the practical outcome, followed by a labeled product illustration, three workflow capabilities, a three-step explanation, a qualified local-processing note, and direct documentation/repository links.
- The illustration is explicitly a preview of the workflow, not a live or measured product capture.
- Do not invent customer counts, testimonials, performance promises, or guarantees of virality.
- Describe local rendering accurately. Optional models, downloads, and configured services may use the network.
- Keep the user's editorial review visible in the story: recommendations identify candidate moments; the creator chooses what to publish.

## Interaction and responsive behavior

- Keep the static site JavaScript-free. Use semantic headings, native links, a visible keyboard focus ring, and a documentation contents list.
- Use subtle hover states only; honor `prefers-reduced-motion`.
- Collapse the hero and feature grids cleanly on narrow screens, keep code samples horizontally scrollable, and avoid horizontal page overflow.
