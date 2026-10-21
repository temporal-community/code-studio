// Compile the browser protocol with the already-installed TypeScript compiler.
import ts from 'typescript';
import { readFile, writeFile, mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { spawnSync } from 'node:child_process';

const directory = await mkdtemp(join(tmpdir(), 'code-studio-tests-'));
try {
  for (const name of ['document', 'python', 'theme', 'harnessSession', 'featureDiff', 'refresh', 'identity', 'copyOutput', 'featureDraft', 'approvalInbox', 'stageRunComparison', 'workspaceConnection']) {
    const source = await readFile(new URL(`../ui/shared/${name}.ts`, import.meta.url), 'utf8');
    const compiled = ts.transpileModule(source, {
      compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2023 },
    }).outputText;
    await writeFile(join(directory, `${name}.mjs`), compiled);
  }
  const files = process.argv.length > 2 ? process.argv.slice(2) : ['tests/document.test.mjs', 'tests/editor.test.mjs', 'tests/theme.test.mjs', 'tests/harness_session.test.mjs', 'tests/refresh.test.mjs', 'tests/identity.test.mjs', 'tests/copy_output.test.mjs', 'tests/feature_draft.test.mjs', 'tests/approval_inbox.test.mjs', 'tests/stage_run_comparison.test.mjs', 'tests/workspace_connection.test.mjs'];
  const result = spawnSync(process.execPath, ['--test', ...files], {
    stdio: 'inherit', env: { ...process.env, DOCUMENT_MODULE: join(directory, 'document.mjs'), PYTHON_MODULE: join(directory, 'python.mjs'), THEME_MODULE: join(directory, 'theme.mjs'), HARNESS_SESSION_MODULE: join(directory, 'harnessSession.mjs'), FEATURE_DIFF_MODULE: join(directory, 'featureDiff.mjs'), REFRESH_MODULE: join(directory, 'refresh.mjs'), IDENTITY_MODULE: join(directory, 'identity.mjs'), COPY_OUTPUT_MODULE: join(directory, 'copyOutput.mjs'), FEATURE_DRAFT_MODULE: join(directory, 'featureDraft.mjs'), APPROVAL_INBOX_MODULE: join(directory, 'approvalInbox.mjs'), STAGE_RUN_COMPARISON_MODULE: join(directory, 'stageRunComparison.mjs'), WORKSPACE_CONNECTION_MODULE: join(directory, 'workspaceConnection.mjs') },
  });
  if (result.error) throw result.error;
  process.exitCode = result.status ?? 1;
} finally {
  await rm(directory, { recursive: true, force: true });
}
