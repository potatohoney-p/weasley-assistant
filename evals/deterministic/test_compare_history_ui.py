"""Execute the real arena handlers with a synthetic HTTP response in Node.

No browser, model provider or npm package is needed. Node is optional for
Python-only development installs and is available on the hosted CI runner.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

NODE = shutil.which("node")
JS = Path(__file__).resolve().parents[2] / "weasley_assistant/ops/static/js/compare.js"


@pytest.mark.skipif(NODE is None, reason="Node is needed to execute dashboard JavaScript")
@pytest.mark.parametrize("handler", ["regradeCompare()", "gradeCard('test:a')"])
@pytest.mark.parametrize("case", ["newer_race", "removed_race", "switched_view"])
def test_grade_response_updates_only_selected_cards(handler, case):
    probe = r'''
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const context = vm.createContext({
  localStorage: {getItem: () => null}, VIEWS: {}, render: () => {},
  editing: false,
});
vm.runInContext(fs.readFileSync(process.argv[1], "utf8"), context);
vm.runInContext(`
  compareState.results = {"test:a": {spec: "test:a", quality: {score: 1}}};
  globalThis.original = compareState.results;
  globalThis.postJSON = () => new Promise(resolve => { globalThis.finish = resolve; });
  globalThis.pending = ${process.argv[2]};
`, context);
if (process.argv[3] === "switched_view") {
  vm.runInContext('compareState.results = {"test:a": {quality: {score: 4}}};', context);
}
vm.runInContext(`finish({
  runs: [{message: "newer race", results: [{spec: "test:a", quality: {score: 9}}]}],
  aggregate: [],
  graded_results: ${process.argv[3] === "removed_race" ? "[]" :
    '[{spec: "test:a", quality: {score: 7}}]'}
});`, context);
context.pending.then(() => {
  const expected = process.argv[3] === "switched_view" ? 4 :
    process.argv[3] === "removed_race" ? 1 : 7;
  assert.equal(vm.runInContext('compareState.results["test:a"].quality.score', context), expected);
  assert.equal(vm.runInContext('compareState.history[0].message', context), "newer race");
}).catch(error => { console.error(error); process.exitCode = 1; });
'''
    result = subprocess.run([NODE, "-e", probe, str(JS), handler, case],
                            capture_output=True, text=True, timeout=10, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
