/* A minimal static file server for the end-to-end tests: serves a directory
 * the way a static host (Vercel, Render Static Site, Dappling) serves
 * frontend/ — files only, no rewrites, no backend. Dependency-free. */
"use strict";

const fs = require("node:fs");
const http = require("node:http");
const path = require("node:path");

const TYPES = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".svg": "image/svg+xml",
  ".json": "application/json",
};

function startStaticServer(root, port) {
  const base = path.resolve(root);
  const server = http.createServer((request, response) => {
    const pathname = decodeURIComponent(new URL(request.url, "http://x").pathname);
    const file = path.resolve(base, "." + (pathname.endsWith("/") ? pathname + "index.html" : pathname));
    if (file !== base && !file.startsWith(base + path.sep)) {
      response.writeHead(403).end();
      return;
    }
    fs.readFile(file, (error, body) => {
      if (error) {
        response.writeHead(404, { "Content-Type": "text/plain" }).end("not found");
        return;
      }
      response.writeHead(200, {
        "Content-Type": TYPES[path.extname(file)] || "application/octet-stream",
        "Cache-Control": "no-store",
      });
      response.end(body);
    });
  });
  return new Promise((resolve) => {
    server.listen(port || 0, "127.0.0.1", () => {
      const { port: bound } = server.address();
      resolve({
        url: "http://127.0.0.1:" + bound + "/",
        close: () => new Promise((done) => server.close(done)),
      });
    });
  });
}

module.exports = { startStaticServer };

if (require.main === module) {
  const root = process.argv[2] || path.join(__dirname, "..", "..", "frontend");
  startStaticServer(root, Number(process.argv[3]) || 5173).then((s) =>
    console.log("Serving " + root + " at " + s.url)
  );
}
