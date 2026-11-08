// Serves frontend/build with no backend behind it. The specs stub /api in the
// browser, so what is under test is the panel and nothing else.
import { createServer } from 'node:http';
import { existsSync, readFileSync, statSync } from 'node:fs';
import { dirname, extname, join, normalize } from 'node:path';
import { fileURLToPath } from 'node:url';

const BUILD = join(dirname(fileURLToPath(import.meta.url)), '..', '..', '..', '..', 'frontend', 'build');
const TYPES = {
  '.html': 'text/html',
  '.js': 'text/javascript',
  '.css': 'text/css',
  '.json': 'application/json',
  '.svg': 'image/svg+xml',
  '.png': 'image/png',
  '.woff2': 'font/woff2',
};

if (!existsSync(join(BUILD, 'index.html'))) {
  console.error(`No build at ${BUILD}. Run: npm run build:panel`);
  process.exit(1);
}

createServer((req, res) => {
  const { pathname } = new URL(req.url, 'http://x');
  let path = join(BUILD, normalize(pathname));
  if (!path.startsWith(BUILD) || !existsSync(path) || statSync(path).isDirectory()) {
    path = join(BUILD, 'index.html');
  }
  res.writeHead(200, { 'content-type': TYPES[extname(path)] ?? 'application/octet-stream' });
  res.end(readFileSync(path));
}).listen(Number(process.argv[2]), '127.0.0.1');
