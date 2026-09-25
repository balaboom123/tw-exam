import assert from "node:assert/strict"
import { readFile } from "node:fs/promises"
import test from "node:test"

import ts from "typescript"

async function loadCategories() {
  const source = await readFile(new URL("../src/lib/exam-categories.ts", import.meta.url), "utf8")
  const { outputText } = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.ES2022, target: ts.ScriptTarget.ES2022 },
  })
  return import(`data:text/javascript;base64,${Buffer.from(outputText).toString("base64")}`)
}

test("every published class and subclass remains reachable through the filters", async () => {
  const { CLASS_ORDER, orderExamClasses, orderExamSubclasses } = await loadCategories()
  const source = JSON.parse(await readFile(new URL("../../data/sites/default/frontend-bundles.json", import.meta.url), "utf8"))
  const bundles = source.bundles
  assert.ok(bundles.length > 0)

  const publishedClasses = new Set(bundles.map((bundle) => bundle.examClass))
  assert.deepEqual(new Set(CLASS_ORDER), publishedClasses)
  assert.deepEqual(new Set(orderExamClasses(publishedClasses)), publishedClasses)

  for (const examClass of publishedClasses) {
    const matching = bundles.filter((bundle) => bundle.examClass === examClass)
    const subclasses = new Set(matching.map((bundle) => bundle.examSubclass))
    assert.ok(subclasses.size > 0, examClass)
    assert.ok([...subclasses].every(Boolean), examClass)
    assert.deepEqual(new Set(orderExamSubclasses(subclasses)), subclasses)
  }

  assert.ok(publishedClasses.has("證照／檢定"))
  assert.ok(publishedClasses.has("國營／就業甄試"))
  assert.ok(bundles.some((bundle) => bundle.examClass === "公職考試" && bundle.examSubclass === "公職／公務人員"))
})

test("new feed classes are visible after the known display order", async () => {
  const { CLASS_ORDER, orderExamClasses } = await loadCategories()
  assert.deepEqual(orderExamClasses(["新類別", "證照／檢定"]), ["證照／檢定", "新類別"])
  assert.ok(!CLASS_ORDER.includes("新類別"))
})
