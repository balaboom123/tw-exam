import { readdir, readFile } from "node:fs/promises"
import { join } from "node:path"
import { fileURLToPath } from "node:url"
import { gzipSync } from "node:zlib"

const assetDir = fileURLToPath(new URL("../dist/assets/", import.meta.url))
const limit = 100 * 1024
let total = 0
let count = 0

for (const name of await readdir(assetDir)) {
  if (!name.endsWith(".js")) continue
  total += gzipSync(await readFile(join(assetDir, name)), { level: 9 }).length
  count += 1
}

console.log(`Built JavaScript: ${(total / 1024).toFixed(1)} KiB gzip (limit: 100 KiB)`)
if (count === 0 || total > limit) process.exitCode = 1
