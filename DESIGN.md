# Code Studio design guidance

Code Studio is an example app. UI changes should follow the target project's
existing components, styling, and interaction patterns. There is no required
external design system or component package.

Read the [UI guidance](docs/temporal-design/SKILL.md) and
[foundations](docs/temporal-design/foundations.md) for component reuse, accessibility,
themes, and validation. Inspect installed source before choosing concrete APIs.
Keep changes within the approved feature scope.

## How agents receive it

The host freezes the guidance and foundations for each new stage run. The guidance
is included in model instructions alongside the stage playbook. Agents can read the
foundations through the shared-library tools. Design records implementation choices;
Development applies them; Review checks the result against the agreed contract.
Backend-only work does not require a UI deliverable.

Paired experiments capture the guidance once so both attempts use the same inputs.
Existing runs and experiments retain their saved guidance, including any older
catalogs. Restart the host and worker after updating Python code. Untouched default
stage prompts upgrade on restart; customized prompts retain their saved text and
can be edited in Studio.

## Updating the guidance

Edit the written guidance and foundations directly. Prefer the target project's
conventions, and honor explicit user requirements. Document compatibility gaps and
unavailable checks without claiming that unverified behavior works.

Use relevant type/build checks and behavior tests. Optional read-only browser
previews support Code Studio's Vite frontend against the running backend; they do
not verify saving or changed backend behavior. Passing source checks does not prove
rendered appearance or accessibility.
