import assert from "node:assert/strict"
import test from "node:test"

import { grantSocialAccess, hasSocialAccess, withSocialAccess, SOCIAL_CHANNELS } from "../src/lib/social-gate.ts"

test("download gate access is global and honors legacy per-channel keys", () => {
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

test("download gate falls back to session storage when local storage is blocked", () => {
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

test("a URL grant preserves access when both stores are denied", () => {
  const previousWindow = globalThis.window
  const blocked = {
    getItem: () => { throw new Error("blocked") },
    setItem: () => { throw new Error("blocked") },
  }
  globalThis.window = {
    localStorage: blocked,
    sessionStorage: blocked,
    location: {
      search: "?unlocked=1", href: "https://example.test/tw-exam/?unlocked=1",
      origin: "https://example.test",
    },
  }

  try {
    assert.equal(grantSocialAccess(), false)
    assert.equal(hasSocialAccess(), true)
    assert.equal(withSocialAccess("/tw-exam/?q=math#results"), "/tw-exam/?q=math&unlocked=1#results")
    assert.equal(withSocialAccess("https://github.com/example/repo"), "https://github.com/example/repo")
    window.location.search = "?unlocked=0"
    assert.equal(hasSocialAccess(), false)
    assert.equal(withSocialAccess("/tw-exam/"), "/tw-exam/")
    assert.equal(withSocialAccess("/tw-exam/", true), "/tw-exam/?unlocked=1")
  } finally {
    if (previousWindow === undefined) delete globalThis.window
    else globalThis.window = previousWindow
  }
})
