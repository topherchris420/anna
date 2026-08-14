import assert from "node:assert/strict";
import {
  chmod,
  mkdtemp,
  readFile,
  rm,
  writeFile,
} from "node:fs/promises";
import { existsSync } from "node:fs";
import path from "node:path";
import { spawnSync } from "node:child_process";
import test from "node:test";
import { fileURLToPath } from "node:url";

const REPO_ROOT = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "..",
  ".."
);

function bashExecutable() {
  if (process.platform !== "win32") return "bash";
  const candidates = [
    "C:\\Program Files\\Git\\bin\\bash.exe",
    "C:\\Program Files\\Git\\usr\\bin\\bash.exe",
  ];
  return candidates.find(existsSync) || "bash";
}

async function writeExecutable(file, lines) {
  await writeFile(file, lines.join("\n"), "utf8");
  await chmod(file, 0o755);
}

function runEntrypoint(tempName, assignments = [], timeout = 5000) {
  const command = [
    `ENTRYPOINT_TEST_LOG="$PWD/${tempName}/events.log"`,
    `PATH="$PWD/${tempName}:$PATH"`,
    "SEED_CORPUS=false",
    ...assignments,
    "./deploy/entrypoint-free.sh",
  ].join(" ");
  return spawnSync(bashExecutable(), ["-c", command], {
    cwd: REPO_ROOT,
    encoding: "utf8",
    timeout,
  });
}

test("the free image includes a Neon-compatible libpq client", async () => {
  const dockerfile = await readFile(
    path.join(REPO_ROOT, "deploy", "Dockerfile.free"),
    "utf8"
  );
  assert.match(dockerfile, /^FROM python:3\.10(?:\.\d+)?-slim-bookworm$/m);
});

test("the free-tier web server starts before database bootstrap finishes", async () => {
  const tempRoot = await mkdtemp(path.join(REPO_ROOT, ".entrypoint-test-"));
  const tempName = path.basename(tempRoot);
  const logPath = path.join(tempRoot, "events.log");
  const fakeFlask = path.join(tempRoot, "flask");
  const fakeGunicorn = path.join(tempRoot, "gunicorn");

  try {
    await writeExecutable(
      fakeFlask,
      [
        "#!/bin/sh",
        "printf 'bootstrap-start:%s\\n' \"$*\" >> \"$ENTRYPOINT_TEST_LOG\"",
        "sleep 0.05",
        "printf 'bootstrap-finish:%s\\n' \"$*\" >> \"$ENTRYPOINT_TEST_LOG\"",
      ]
    );
    await writeExecutable(
      fakeGunicorn,
      [
        "#!/bin/sh",
        "printf 'server-start\\n' >> \"$ENTRYPOINT_TEST_LOG\"",
        "sleep 1",
      ]
    );
    const result = runEntrypoint(tempName);
    assert.equal(
      result.status,
      0,
      `entrypoint failed:\n${result.stdout}\n${result.stderr}`
    );

    const events = (await readFile(logPath, "utf8")).trim().split(/\r?\n/);
    const serverStart = events.indexOf("server-start");
    const firstBootstrapFinish = events.findIndex((event) =>
      event.startsWith("bootstrap-finish:")
    );
    assert.ok(serverStart >= 0, "the web server should start");
    assert.ok(firstBootstrapFinish >= 0, "database bootstrap should run");
    assert.ok(
      serverStart < firstBootstrapFinish,
      `server started too late: ${events.join(", ")}`
    );
  } finally {
    await rm(tempRoot, { recursive: true, force: true });
  }
});

test("database bootstrap keeps retrying until a long outage recovers", async () => {
  const tempRoot = await mkdtemp(path.join(REPO_ROOT, ".entrypoint-test-"));
  const tempName = path.basename(tempRoot);
  const logPath = path.join(tempRoot, "events.log");

  try {
    await writeExecutable(path.join(tempRoot, "flask"), [
      "#!/bin/sh",
      "if [ \"$*\" = \"engine index-init\" ]; then",
      "  count_file=\"$ENTRYPOINT_TEST_LOG.count\"",
      "  count=0",
      "  [ ! -f \"$count_file\" ] || count=$(cat \"$count_file\")",
      "  count=$((count + 1))",
      "  printf '%s\\n' \"$count\" > \"$count_file\"",
      "  if [ \"$count\" -le 20 ]; then exit 1; fi",
      "  printf 'bootstrap-recovered\\n' >> \"$ENTRYPOINT_TEST_LOG\"",
      "  : > \"$ENTRYPOINT_TEST_LOG.recovered\"",
      "fi",
      "exit 0",
    ]);
    await writeExecutable(path.join(tempRoot, "sleep"), ["#!/bin/sh", "exit 0"]);
    await writeExecutable(path.join(tempRoot, "gunicorn"), [
      "#!/bin/sh",
      "printf 'server-start\\n' >> \"$ENTRYPOINT_TEST_LOG\"",
      "n=0",
      "while [ ! -f \"$ENTRYPOINT_TEST_LOG.recovered\" ] && [ \"$n\" -lt 200 ]; do",
      "  /bin/sleep 0.05",
      "  n=$((n + 1))",
      "done",
    ]);

    const result = runEntrypoint(
      tempName,
      ["BOOTSTRAP_RETRY_DELAY=0", "BOOTSTRAP_RETRY_MAX_DELAY=0"],
      20000
    );
    assert.equal(
      result.status,
      0,
      `entrypoint failed:\n${result.stdout}\n${result.stderr}`
    );

    const events = (await readFile(logPath, "utf8")).trim().split(/\r?\n/);
    assert.ok(events.includes("bootstrap-recovered"), events.join(", "));
    assert.ok(
      events.indexOf("server-start") < events.indexOf("bootstrap-recovered"),
      `server should remain available during retries: ${events.join(", ")}`
    );
  } finally {
    await rm(tempRoot, { recursive: true, force: true });
  }
});
