import assert from "node:assert/strict";
import { mkdir, readFile, stat, writeFile } from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import { STATIC_ASSETS, buildStatic } from "../../frontend/build.mjs";

const REPO_ROOT = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "..",
  ".."
);

test("buildStatic copies every declared asset and removes stale output", async () => {
  const output = path.join(REPO_ROOT, "dist");
  await mkdir(output, { recursive: true });
  await writeFile(path.join(output, "stale.txt"), "stale", "utf8");

  const result = await buildStatic(output);

  assert.equal(result.outputDir, path.resolve(output));
  assert.equal(result.copied, STATIC_ASSETS.length);
  await assert.rejects(stat(path.join(output, "stale.txt")));
  for (const asset of STATIC_ASSETS) {
    const body = await readFile(path.join(output, asset));
    assert.ok(body.length > 0, asset + " should not be empty");
  }
});

test("buildStatic refuses to replace the frontend source directory", async () => {
  const source = path.join(REPO_ROOT, "frontend");
  await assert.rejects(
    buildStatic(source),
    /refusing to replace the frontend source directory/i
  );
});

test("buildStatic refuses every output except repo dist or frontend build", async () => {
  await assert.rejects(
    buildStatic(REPO_ROOT),
    /output must be repository dist or frontend build/i
  );
});
