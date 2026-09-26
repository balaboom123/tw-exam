import assert from "node:assert/strict"
import { readFile } from "node:fs/promises"
import test from "node:test"

import ts from "typescript"

async function loadSocialGate() {
  const source = await readFile(new URL("../src/lib/social-gate.ts", import.meta.url), "utf8")
  const { outputText } = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.ES2022, target: ts.ScriptTarget.ES2022 },
  })
  return import(`data:text/javascript;base64,${Buffer.from(outputText).toString("base64")}`)
}

test("download gate access is global and honors legacy per-channel keys", async () => {
  const { grantSocialAccess, hasSocialAccess, SOCIAL_CHANNELS } = await loadSocialGate()
  const previousWindow = globalThis.window
  const storage = new Map()

  globalThis.window = {
    localStorage: {
      getItem: (key) => storage.get(key) ?? null,
      setItem: (key, value) => storage.set(key, value),
    },
  }

  try {
    assert.equal(SOCIAL_CHANNELS.length, 3)
    assert.equal(hasSocialAccess(), false)

    grantSocialAccess()
    assert.equal(hasSocialAccess(), true)

    storage.clear()
    storage.set("taiwan-exam-download-access:cap", "1")
    assert.equal(hasSocialAccess(), true)
  } finally {
    if (previousWindow === undefined) {
      delete globalThis.window
    } else {
      globalThis.window = previousWindow
    }
  }
})

test("download gate falls back to session storage when local storage is blocked", async () => {
  const { grantSocialAccess, hasSocialAccess } = await loadSocialGate()
  const previousWindow = globalThis.window
  const session = new Map()
  globalThis.window = {
    localStorage: {
      getItem: () => { throw new Error("blocked") },
      setItem: () => { throw new Error("blocked") },
    },
    sessionStorage: {
      getItem: (key) => session.get(key) ?? null,
      setItem: (key, value) => session.set(key, value),
    },
  }

  try {
    assert.equal(hasSocialAccess(), false)
    assert.equal(grantSocialAccess(), true)
    assert.equal(hasSocialAccess(), true)
  } finally {
    if (previousWindow === undefined) delete globalThis.window
    else globalThis.window = previousWindow
  }
})

test("a URL flag cannot unlock downloads when browser storage is denied", async () => {
  const { grantSocialAccess, hasSocialAccess } = await loadSocialGate()
  const previousWindow = globalThis.window
  const blocked = {
    getItem: () => { throw new Error("blocked") },
    setItem: () => { throw new Error("blocked") },
  }
  globalThis.window = {
    localStorage: blocked,
    sessionStorage: blocked,
    location: { search: "?unlocked=1" },
  }

  try {
    assert.equal(grantSocialAccess(), false)
    assert.equal(hasSocialAccess(), false)
  } finally {
    if (previousWindow === undefined) delete globalThis.window
    else globalThis.window = previousWindow
  }
})
