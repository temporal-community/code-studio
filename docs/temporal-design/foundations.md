# Holocene/io foundations

These are Code Studio's written design conventions. No component or theme catalog
is bundled, and Code Studio does not currently depend on `@temporalio/ui`. Inspect
the target project's installed components, theme definitions, exports, and package
version before choosing concrete APIs or adding a compatible shared adapter.

For new project foundations, Code Studio adopts Inter Variable for interface text
and Noto Sans Mono for data and identifiers, with system sans/monospace fallbacks.
Reuse the existing font-loading mechanism and inspect shared typography and
component source for sizes and weights. Follow the target's established spacing,
radius, and motion definitions; do not invent values and call them io.

Use verified semantic token values for light and dark themes from the installed
theme or the project's shared adapter. Keep raw values in centralized theme
definitions, and reference semantic roles from components. Explicit theme selection
should take precedence over the OS setting. Preserve the target's theme selector
convention rather than assuming an attribute or provider API.

Token roles include background, content, surface, border, interactive, and action.
Preserve alpha compositing where the verified theme uses it. Reserve
`action-workflow-*` roles for their Temporal domain meaning rather than decoration.
Verify actual foreground/background pairs for contrast instead of assuming all
token combinations are accessible. Record missing references and compatibility
exceptions in the design handoff.
