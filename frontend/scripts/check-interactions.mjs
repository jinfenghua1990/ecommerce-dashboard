import fs from "node:fs";
import path from "node:path";
import ts from "typescript";

const roots = ["src/app", "src/components"].filter((p) => fs.existsSync(p));
const files = [];

function walk(dir) {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) walk(full);
    else if (/\.(tsx|jsx)$/.test(entry.name)) files.push(full);
  }
}
for (const root of roots) walk(root);

const problems = [];

function tagName(node) {
  return node.tagName?.getText?.() ?? "";
}

function attrsOf(node) {
  const attrs = new Map();
  let hasSpread = false;
  for (const prop of node.attributes?.properties ?? []) {
    if (ts.isJsxSpreadAttribute(prop)) {
      hasSpread = true;
      continue;
    }
    if (ts.isJsxAttribute(prop)) attrs.set(prop.name.getText(), prop);
  }
  return { attrs, hasSpread };
}

function literalAttr(attr) {
  if (!attr?.initializer) return null;
  if (ts.isStringLiteral(attr.initializer)) return attr.initializer.text;
  if (ts.isJsxExpression(attr.initializer) && attr.initializer.expression && ts.isStringLiteral(attr.initializer.expression)) {
    return attr.initializer.expression.text;
  }
  return null;
}

function isEmptyHandler(attr) {
  if (!attr?.initializer || !ts.isJsxExpression(attr.initializer)) return false;
  const expr = attr.initializer.expression;
  if (!expr) return true;
  if (expr.kind === ts.SyntaxKind.NullKeyword) return true;
  if (ts.isIdentifier(expr) && expr.text === "undefined") return true;
  if (ts.isArrowFunction(expr) && ts.isBlock(expr.body) && expr.body.statements.length === 0) return true;
  return false;
}

function insideForm(node) {
  let current = node.parent;
  while (current) {
    if (ts.isJsxElement(current) && tagName(current.openingElement) === "form") return true;
    if (ts.isJsxSelfClosingElement(current) && tagName(current) === "form") return true;
    current = current.parent;
  }
  return false;
}

for (const file of files) {
  const text = fs.readFileSync(file, "utf8");
  const source = ts.createSourceFile(file, text, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);

  function visit(node) {
    if (ts.isJsxOpeningElement(node) || ts.isJsxSelfClosingElement(node)) {
      const tag = tagName(node);
      if (tag === "button" || tag === "a" || tag === "Link") {
        const { attrs, hasSpread } = attrsOf(node);
        const pos = source.getLineAndCharacterOfPosition(node.getStart(source));
        const location = `${file}:${pos.line + 1}`;

        const click = attrs.get("onClick");
        if (click && isEmptyHandler(click)) problems.push(`${location} ${tag} 使用空 onClick`);

        if (tag === "button") {
          const eventNames = ["onClick", "onPointerDown", "onMouseDown", "onKeyDown", "formAction"];
          const hasAction = hasSpread || eventNames.some((name) => attrs.has(name));
          const typeValue = literalAttr(attrs.get("type"));
          const submits = typeValue === "submit" || (typeValue === null && insideForm(node));
          if (!hasAction && !submits) problems.push(`${location} button 没有事件处理或提交行为`);
        } else {
          const href = literalAttr(attrs.get("href"));
          if (href !== null) {
            const value = href.trim();
            if (!value || value === "#" || value.startsWith("javascript:")) {
              problems.push(`${location} ${tag} 使用空/占位 href=${JSON.stringify(href)}`);
            }
          }
        }
      }
    }
    ts.forEachChild(node, visit);
  }
  visit(source);

  if (/alert\s*\(\s*["'](?:TODO|开发中|暂未开放|敬请期待)/.test(text)) {
    problems.push(`${file}: 存在占位式按钮 alert`);
  }
}

if (problems.length) {
  console.error("Interaction integrity failed:\n" + problems.map((p) => `- ${p}`).join("\n"));
  process.exit(1);
}

console.log(`Interaction integrity OK: checked ${files.length} TSX/JSX files with TypeScript AST.`);
