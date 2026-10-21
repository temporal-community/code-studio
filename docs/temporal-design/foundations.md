# UI foundations

Start with the target project's shared styles and components. This example does
not prescribe a particular design system, font family, icon set, or UI package.
Verify installed versions and exports before choosing concrete APIs.

## Shared styling

Reuse the project's established typography, spacing, colors, borders, and motion.
Keep reusable values in shared theme definitions. Use semantic roles where the
project provides them, and preserve alpha compositing and contrast relationships.
For Code Studio itself, shared styles and theme values live in `ui/style.css`.

Reuse the existing font-loading mechanism and provide suitable fallbacks. Check
legibility for interface labels, code, and identifiers at the sizes actually used.

## Themes and interaction

Preserve supported themes and the existing theme selector. An explicit user choice
should take precedence over the operating system preference. Verify actual text,
control, and focus contrast in each supported theme.

Use accessible names for icon controls and visible focus for keyboard navigation.
Preserve reduced-motion behavior and make status understandable without relying
on color alone. Check narrow layouts and long content when relevant to the change.

Document deviations from existing conventions and any unavailable validation in
the stage handoff. Do not claim that matching tokens proves accessibility.
