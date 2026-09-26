import assert from "node:assert/strict"
import test from "node:test"

import { resolvePagesBase } from "./site-config.ts"

test("Pages base follows the repository name", () => {
  assert.equal(resolvePagesBase({ githubRepository: "balaboom123/tw-exam" }), "/tw-exam/")
})

test("explicit root or subpath overrides the repository name", () => {
  assert.equal(resolvePagesBase({ githubRepository: "owner/repo", explicitBase: "/" }), "/")
  assert.equal(resolvePagesBase({ githubRepository: "owner/repo", explicitBase: "other" }), "/other/")
})
