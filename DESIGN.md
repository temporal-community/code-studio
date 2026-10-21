# Code Studio design system

Holocene/io governs all UI work in this repository and all UI projects run through
Code Studio. Read [the design policy](docs/temporal-design/SKILL.md) before designing,
implementing, or reviewing UI changes. It defines component selection, semantic
colors, compatibility/adapters, interaction states, exceptions, and stage handoffs.

Read [foundations](docs/temporal-design/foundations.md) for theme and typography
guidance. No component, token, icon, or draft-contract catalog is bundled. Verify
implementation details against the target project's installed source and document
any unavailable references or compatibility gaps.

Code Studio does not currently depend on `@temporalio/ui`. For UI implementation,
plan a compatible package adoption or a shared adapter as described in the policy.
Apply the system to touched surfaces while keeping broader migration work within
explicitly approved scope. This policy does not claim existing UI already conforms.

## How agents receive it

The host freezes the policy and written foundations for each new stage run. The policy
is included in model instructions even when the team has customized a stage prompt.
Agents read the foundations through the shared-library tools.
Design records exact implementation choices; Development applies them; Review checks
the diff against Design's complete artifact. Backend-only work skips UI references.

Existing runs retain their saved inputs. Paired experiments capture the reference
library once so both arms use the same version. Existing experiments created before
this feature retain their original absence of design references. Restart the host and
worker after updating Python code; new stage runs then receive the policy.

Older saved runs and experiments may retain their previously frozen catalogs in
local storage. Removing the bundled catalog does not rewrite those saved inputs.

## Updating the guidance

Edit the policy and foundations directly, and verify implementation guidance against
the target dependency before adopting it. Record the inspected version and any
verification limitations. Updated guidance applies to newly captured inputs.

Verification combines type/build checks, applicable behavior tests, and independent
review. The host supports optional read-only browser previews for Code Studio's
Vite frontend. Screenshots and navigation use the running backend; they do not
verify saving or new backend behavior. Other projects require a preview adapter.
Record those limitations; passing type checks do not guarantee visual conformity.
