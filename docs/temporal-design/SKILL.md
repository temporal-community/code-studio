---
name: ui-guidance
description: General UI guidance for this example's Design, Development, and Review agents. Follow the target project's conventions and skip UI work for backend-only changes.
---

# UI guidance

Follow the target project's existing components, styling, and interaction patterns.
Honor explicit user requirements and preserve approved scope. This example does not
require an external design system, component package, or migration of unrelated UI.
Backend-only changes need no UI deliverable.

## Inspect the project first

- Read the target's package and lock files, shared styles, and existing components.
  Verify exports, props, tokens, and behavior against installed source before use.
- Reuse existing components and wrappers where they fit. Propose a new dependency
  or design system only when the task calls for it; explain the tradeoff.
- Use available project design guidance. Record gaps rather than inventing APIs or
  treating draft guidance as verified implementation behavior.

This guidance is included in model instructions. Frozen copies are available via
`stage_read_shared_file` at `/skills/temporal-design/SKILL.md` and
`/skills/temporal-design/foundations.md`. These virtual paths remain stable for
saved runs. Read [foundations.md](foundations.md) locally for theme and typography
conventions. No component or token catalog is bundled.

## Interaction and accessibility

- Reuse the project's shared color roles, typography, spacing, and icon conventions.
  Keep reusable styling in shared definitions rather than duplicating values.
- Define relevant loading, empty, error, success, disabled, hover, pressed, and focus
  states. Prevent duplicate submissions during asynchronous work.
- Support keyboard navigation, accessible names, focus management, responsive layouts,
  and reduced motion. Communicate status through more than color alone.
- Check text and focus contrast in the supported themes. Preserve theme selection
  and system preference behavior; token reuse alone does not establish contrast.
- Keep implementation details out of user-facing copy unless they help the user act.

## Stage responsibilities

- **Ideation / Planner:** identify UI impact and include relevant acceptance coverage.
- **Design:** provide a concise implementation contract: affected files, verified
  components and APIs, interaction states, accessibility, and validation. Prefer
  existing patterns for small changes rather than manufacturing a redesign.
- **Development:** implement the agreed contract and record material deviations.
- **Review:** inspect the actual changes against the contract and available evidence.
  Report concrete defects, not personal visual preferences.
- **Deployment:** carry forward relevant limitations and recovery instructions.

Use checks configured by the target project. Source/type checks do not prove
rendered appearance. Report unavailable browser checks honestly; they do not alone
require blocking approval. Claim only checks actually run.
