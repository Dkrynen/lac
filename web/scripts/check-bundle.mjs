// Release gate: no CDN/external host in the shipped bundle, and the lazy
// editor chunk stays within a sane ceiling. Run AFTER `npm run build`.
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";

const ASSETS = "dist/assets";
const CDN_HOSTS = [
  "cdn.jsdelivr.net",
  "unpkg.com",
  "cdnjs.cloudflare.com",
  "esm.sh",
  "jspm.dev",
  "cdn.skypack.dev",
  "ga.jspm.io",
];
// Raw-byte ceilings (not gzip). Editor chunk is CM6-heavy but must not balloon;
// index must NOT absorb CM6 (that would mean the lazy split broke).
const CEILINGS = { editorPane: 900_000, index: 700_000 };

let files;
try {
  files = readdirSync(ASSETS).filter((f) => f.endsWith(".js"));
} catch {
  console.error(`check-bundle: ${ASSETS} not found — run \`npm run build\` first.`);
  process.exit(1);
}

const failures = [];
let editorBytes = 0;
let indexBytes = 0;

for (const file of files) {
  const path = join(ASSETS, file);
  const text = readFileSync(path, "utf8");
  for (const host of CDN_HOSTS) {
    if (text.includes(host)) failures.push(`CDN host "${host}" found in ${file}`);
  }
  const bytes = statSync(path).size;
  if (file.startsWith("editor-pane")) editorBytes = bytes;
  if (file.startsWith("index")) indexBytes = bytes;
}

console.log(`check-bundle: editor-pane=${editorBytes}B index=${indexBytes}B`);
if (editorBytes === 0) failures.push("editor-pane chunk missing (lazy split broken?)");
if (indexBytes === 0) failures.push("index chunk missing (renamed entry chunk?)");
if (editorBytes > CEILINGS.editorPane) failures.push(`editor-pane ${editorBytes}B > ceiling ${CEILINGS.editorPane}B`);
if (indexBytes > CEILINGS.index) failures.push(`index ${indexBytes}B > ceiling ${CEILINGS.index}B (CM6 may have leaked into the initial chunk)`);

if (failures.length) {
  console.error("check-bundle FAILED:\n" + failures.map((f) => `  - ${f}`).join("\n"));
  process.exit(1);
}
console.log("check-bundle OK");
