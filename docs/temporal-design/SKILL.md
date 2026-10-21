---
name: temporal-design
description: Apply Holocene/io to UI design, implementation, and review for every Code Studio project, including Code Studio itself. Skip component references for work with no UI impact.
---

# Holocene/io design policy

Holocene/io governs every UI project run through Code Studio, including Code Studio
itself. Apply it to the UI being changed. Preserve approved feature scope and behavior;
adopting this policy does not authorize an unrelated application-wide migration.
Explicit user decisions can override the policy; name the affected rule and rationale
in the handoff so Development and Review use the same exception. Backend-only work
needs no UI deliverable or component inspection.

## Use the reference efficiently

This repository bundles written policy and foundations only. It does not provide a
component, token, icon, or draft-contract catalog. Verify concrete APIs, token values,
and icon exports against the target project's installed source. If that source is
unavailable, record the limitation rather than inventing a contract.

In a stage run, the host provides this policy in the instructions and exposes frozen
guidance through `stage_read_shared_file`:

- `/skills/temporal-design/SKILL.md`: this policy, also included in model instructions.
- `/skills/temporal-design/foundations.md`: typography, theming, and dependency setup.

The foundations are not already loaded just because the policy is. In a local
checkout outside the stage tools, read [foundations.md](foundations.md) for setup
and typography, then inspect relevant installed components and theme definitions.

## Verify compatibility, then choose components

- Inspect the target's package/lock files and actual exports or component source.
  Record the inspected dependency and reference versions; flag mismatches. Installed
  source defines what an API can do. Authored guidance defines intended usage. Resolve
  conflicts explicitly rather than inventing props, variants, import paths, or behavior.
- For new work, prefer an available io equivalent; otherwise use Holocene. This is
  Code Studio's adoption rule. Reuse existing wrappers when they preserve the required
  contract. Migrate touched components only when compatible and within scope.
- If the target lacks `@temporalio/ui`, identify the smallest compatible adoption
  path in the plan. For Svelte, prefer verified package components when available.
  For another framework, use a shared local adapter implementing the same semantic
  tokens and interaction contracts; Svelte imports are not portable. Document the
  adapter and missing equivalents. Do not silently install dependencies, switch
  frameworks, or fall back to an unrelated visual system. Ask only for a material
  unresolved scope/compatibility decision; routine mappings belong in the handoff.
- Read applicable design contracts when available, including their draft status and
  known gaps. Account for caller responsibilities such as accessible names, toggle state,
  async duplicate-action prevention, and status-chip filter semantics. Verify them
  against current source. Do not turn unverified reference gaps into unrelated repair work.

## Visual and interaction rules

- Use semantic io color tokens in components. Raw color values belong only in the
  shared theme/token definitions; avoid primitive ramps and arbitrary Tailwind colors
  such as `bg-slate-100`. Map existing aliases centrally to io roles if needed.
- Page ground uses `background-primary`; content surfaces use `surface-primary` and
  the documented hierarchy. Preserve alpha overlays. Use the reserved
  `action-workflow-*` colors for their Temporal domain objects.
- With the Temporal library, use io icons as imported components. With an adapter,
  use verified matching assets with accessible names as appropriate; do not invent
  exports or return to string-keyed Holocene icons.
- Use the shared typography, sizing, spacing, and component variants. Check current
  source for exact values. Do not override a component's semantic color or size
  contract with utility classes.
- Define applicable loading, empty, success, error, disabled, hover, pressed, and focus
  states. Specify keyboard behavior, labels, focus management, narrow layouts, and
  reduced-motion behavior. Status meaning must remain understandable without color.
- Assess foreground/background and focus contrast in light and dark themes, including
  disabled, overlay, and error states. Token membership alone does not prove contrast.
  Maintain explicit theme selection and system preference behavior supported by the app.

## Stage responsibilities

- **Ideation / Planner:** recognize UI impact, retain this policy in acceptance coverage,
  and account for component/dependency or adapter work within the approved scope.
- **Design:** produce a concise UI implementation contract for each affected surface:
  existing/proposed file paths; verified components/imports/variants or adapter mapping;
  semantic token roles; applicable interaction/theme/responsive states; accessibility;
  target and reference versions; verification plan; and explicit exceptions. Reuse
  approved patterns for small changes rather than manufacturing a full redesign.
- **Development:** implement that contract using the relevant frozen references and
  current source. Preserve the contract in repair passes; record deviations and actual
  checks. Written guidance does not substitute for checking installed APIs.
- **Review:** inspect the actual diff against Design's UI contract (read its canonical
  artifact if only the summary is supplied). Check component/API compatibility,
  semantic colors, reserved colors, states, accessibility, and exceptions. Identify
  concrete violations by file and correction. Do not block on personal preferences.
- **Deployment:** carry forward relevant exceptions and verification limitations.

Use the run's available checks and validation policy. Derive commands from project
configuration instead of assuming `pnpm check:fast` exists. Source/type checks do not
prove rendered appearance. Report unavailable browser checks as limitations; they do
not alone require a blocked verdict or another approval. Claim only checks actually run.
