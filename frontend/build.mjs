import {
  copyFile,
  mkdir,
  rm,
  stat,
} from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const SOURCE_DIR = path.dirname(fileURLToPath(import.meta.url));

export const STATIC_ASSETS = Object.freeze([
  "index.html",
  "styles.css",
  "app.js",
  "config.js",
  "favicon.svg",
]);

function assertSafeOutput(outputDir) {
  const resolved = path.resolve(outputDir);
  if (resolved === SOURCE_DIR) {
    throw new Error("Refusing to replace the frontend source directory");
  }
  const allowed = [
    path.resolve(SOURCE_DIR, "..", "dist"),
    path.resolve(SOURCE_DIR, "build"),
  ];
  if (allowed.indexOf(resolved) < 0) {
    throw new Error(
      "Build output must be repository dist or frontend build"
    );
  }
  return resolved;
}

export async function buildStatic(outputPath) {
  if (!outputPath || typeof outputPath !== "string") {
    throw new Error("An output directory is required");
  }
  const outputDir = assertSafeOutput(outputPath);
  for (const asset of STATIC_ASSETS) {
    const source = path.join(SOURCE_DIR, asset);
    const info = await stat(source);
    if (!info.isFile()) {
      throw new Error(`Required static asset is not a file: ${asset}`);
    }
  }

  await rm(outputDir, { recursive: true, force: true });
  await mkdir(outputDir, { recursive: true });
  for (const asset of STATIC_ASSETS) {
    await copyFile(path.join(SOURCE_DIR, asset), path.join(outputDir, asset));
  }
  return { outputDir, copied: STATIC_ASSETS.length };
}

const invokedPath = process.argv[1] ? path.resolve(process.argv[1]) : "";
if (invokedPath === fileURLToPath(import.meta.url)) {
  const result = await buildStatic(process.argv[2]);
  console.log(`Built ${result.copied} static assets into ${result.outputDir}`);
}
