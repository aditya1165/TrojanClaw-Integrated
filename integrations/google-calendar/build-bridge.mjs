/** Build the exported upstream registry from the published package's source map.
 * The npm CLI bundle does not export ToolRegistry; its unmodified MIT-licensed
 * sources are included in index.js.map. No code is fetched outside the lockfile.
 */
import { readFile, writeFile, mkdir } from "node:fs/promises";
import { dirname, resolve, relative, isAbsolute } from "node:path";
import { fileURLToPath } from "node:url";
import { build } from "esbuild";

const root = dirname(fileURLToPath(import.meta.url));
const packageRoot = resolve(root, "node_modules/@cocal/google-calendar-mcp");
const metadata = JSON.parse(await readFile(resolve(packageRoot, "package.json"), "utf8"));
if (metadata.version !== "2.8.1") throw new Error("The Calendar MCP package version changed; review the adapter before upgrading.");
const sourceMap = JSON.parse(await readFile(resolve(packageRoot, "build/index.js.map"), "utf8"));
const generated = resolve(root, ".generated");
let extracted = 0;
for (let index = 0; index < sourceMap.sources.length; index++) {
  const source = sourceMap.sources[index].replaceAll("\\", "/");
  const match = source.match(/(?:^|\/)src\/(.+\.(?:ts|js))$/);
  if (!match || typeof sourceMap.sourcesContent[index] !== "string") continue;
  const destination = resolve(generated, "src", match[1]);
  const within = relative(generated, destination);
  if (within.startsWith("..") || isAbsolute(within)) throw new Error("Upstream source path escapes generated directory.");
  await mkdir(dirname(destination), { recursive: true });
  await writeFile(destination, sourceMap.sourcesContent[index]);
  extracted++;
}
if (!extracted) throw new Error("Published upstream sources are missing.");
// esbuild omits a re-export-only barrel from source maps. Recreate its sole
// runtime export from the included, unmodified implementation module.
await writeFile(resolve(generated, "src/services/conflict-detection/index.ts"),
  'export { ConflictDetectionService } from "./ConflictDetectionService.js";\n');
await build({ entryPoints: [resolve(generated, "src/tools/registry.ts")], outfile: resolve(generated, "registry.mjs"),
  bundle: true, platform: "node", format: "esm", target: "node20", packages: "external", logLevel: "warning" });
await writeFile(resolve(generated, "UPSTREAM-LICENSE"), await readFile(resolve(packageRoot, "LICENSE")));
console.log(`Built nspady/google-calendar-mcp ${metadata.version} registry from ${extracted} published source modules.`);
