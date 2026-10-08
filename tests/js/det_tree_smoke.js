/* TICKET-014 sel_tree smoke: executes the DOM-free block of app.js.
 * Usage: node det_tree_smoke.js <path-to-app.js>
 * Extracts the TICKET-014-PURE-START..END block, evals it in isolation
 * (no $, t, document, api) and checks the C1-class behaviour:
 * DTO unwrapping, preorder collection, branch ops incl. rel == "".
 */
"use strict";

const fs = require("fs");

const appJsPath = process.argv[2];
if (!appJsPath) {
  console.error("usage: node det_tree_smoke.js <app.js>");
  process.exit(2);
}
const src = fs.readFileSync(appJsPath, "utf-8");
const START = "TICKET-014-PURE-START";
const END = "TICKET-014-PURE-END";
const a = src.indexOf(START);
const b = src.indexOf(END);
if (a < 0 || b < 0 || b <= a) {
  console.error("pure block markers not found");
  process.exit(2);
}
const block = src.slice(src.indexOf("\n", a) + 1, b);
// The block only declares functions; load them via new Function (strict-safe,
// unlike direct eval which would trap declarations in its own scope).
const factory = new Function(
  block + "\nreturn { detUnwrap, detCollectRels, detBranchApply };"
);
const { detUnwrap, detCollectRels, detBranchApply } = factory();

let failures = 0;
function check(name, cond) {
  if (cond) {
    console.log("ok: " + name);
  } else {
    console.log("FAIL: " + name);
    failures += 1;
  }
}

const node = {
  rel: "", kind: "folder", children: [
    { rel: "a.md", kind: "file", children: [] },
    {
      rel: "sub", kind: "folder", children: [
        { rel: "sub/b.py", kind: "file", children: [] },
        { rel: "sub/deep", kind: "folder", children: [
          { rel: "sub/deep/c.md", kind: "file", children: [] },
        ] },
      ],
    },
  ],
};

// 1. unwrap the backend wrapper {root, tree: node} (C1 regression)
check("unwrap wrapper", detUnwrap({ root: "R", tree: node }) === node);
// 2. tolerate a bare node
check("unwrap bare node", detUnwrap({ tree: node }) === node);
// 3. garbage -> null
check("unwrap null", detUnwrap(null) === null);
check("unwrap garbage", detUnwrap({ tree: {} }) === null);

// 4. preorder collection with dedupe
const rels = detCollectRels(node);
check(
  "collect preorder",
  JSON.stringify(rels) === JSON.stringify(["", "a.md", "sub", "sub/b.py", "sub/deep", "sub/deep/c.md"])
);
check("collect empty", JSON.stringify(detCollectRels(null)) === "[]");

// 5. branch ops
function fresh() {
  return { "": true, "a.md": true, "sub": true, "sub/b.py": true, "sub/deep": true, "sub/deep/c.md": true };
}
let c = detBranchApply(rels.slice(), fresh(), "", false);
check("root branch deselects all", Object.values(c).every((v) => v === false));
c = detBranchApply(rels.slice(), fresh(), "sub", false);
check(
  "subtree deselect",
  c["sub"] === false && c["sub/b.py"] === false && c["sub/deep/c.md"] === false &&
  c[""] === true && c["a.md"] === true
);
c = detBranchApply(rels.slice(), fresh(), "a.md", false);
check("exact file", c["a.md"] === false && c["sub"] === true);
c = detBranchApply(rels.slice(), fresh(), "sub", "invert");
check(
  "branch invert",
  c["sub"] === false && c["sub/b.py"] === false && c[""] === true && c["a.md"] === true
);
c = detBranchApply(rels.slice(), fresh(), "nope", false);
check("foreign prefix untouched", Object.values(c).every((v) => v === true));

if (failures > 0) {
  console.error(failures + " failure(s)");
  process.exit(1);
}
console.log("smoke passed");
