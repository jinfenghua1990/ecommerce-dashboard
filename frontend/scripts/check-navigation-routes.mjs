import fs from "node:fs";
import path from "node:path";
import process from "node:process";

const root = process.cwd();
const appDir = path.join(root, "src", "app");
const sidebarPath = path.join(root, "src", "components", "sidebar.tsx");
const navigationPath = path.join(root, "src", "lib", "workbench-navigation.ts");

const sidebar = fs.readFileSync(sidebarPath, "utf8");
const navigation = fs.readFileSync(navigationPath, "utf8");

const sidebarRoutes = [...sidebar.matchAll(/href:\s*"([^"?#]+)(?:[?#][^"]*)?"/g)]
  .map((match) => match[1])
  .filter((href) => href.startsWith("/"));

const uniqueRoutes = [...new Set(sidebarRoutes)];
const missing = [];

for (const route of uniqueRoutes) {
  const pagePath = route === "/"
    ? path.join(appDir, "page.tsx")
    : path.join(appDir, ...route.split("/").filter(Boolean), "page.tsx");
  if (!fs.existsSync(pagePath)) missing.push(`${route} -> ${path.relative(root, pagePath)}`);
}

if (missing.length) {
  console.error("侧栏存在没有页面实现的路由：");
  missing.forEach((row) => console.error(`  - ${row}`));
  process.exit(1);
}

const legacyStart = navigation.indexOf("const LEGACY_PURCHASE_VIEWS");
const legacyEnd = navigation.indexOf("};", legacyStart);
const legacyBlock = legacyStart >= 0 && legacyEnd > legacyStart
  ? navigation.slice(legacyStart, legacyEnd + 2)
  : "";

if (!legacyBlock) {
  console.error("无法定位 workbench-navigation.ts 的 LEGACY_PURCHASE_VIEWS，无法验证路由劫持规则。");
  process.exit(1);
}

const formalRoutes = uniqueRoutes.filter((route) => route !== "/purchase/workbench");
const hijacked = formalRoutes.filter((route) => legacyBlock.includes(`"${route}":`));

if (hijacked.length) {
  console.error("正式业务路由不应被重定向进采购工作台：");
  hijacked.forEach((route) => console.error(`  - ${route}`));
  process.exit(1);
}

// V1.6.1：旧 UI 页面只能保留兼容跳转，不允许再次恢复成第二套工作台。
const legacyRedirectPages = [
  "src/app/purchase/workbench-v2/page.tsx",
  "src/app/procurement-chain/detail/page.tsx",
];

const legacyUiErrors = [];
for (const relativePath of legacyRedirectPages) {
  const filePath = path.join(root, relativePath);
  if (!fs.existsSync(filePath)) continue;
  const content = fs.readFileSync(filePath, "utf8");
  const bytes = Buffer.byteLength(content, "utf8");
  if (bytes > 3000 || !content.includes("/purchase/workbench") || !content.includes("router.replace")) {
    legacyUiErrors.push(`${relativePath} 必须仅保留新版采购工作台兼容跳转（当前 ${bytes} bytes）`);
  }
}

// 采购工作台只能承载采购域视图，禁止重新嵌入正式业务页面。
const workspacePath = path.join(root, "src/app/purchase/workbench/workspace-modules.tsx");
const workspace = fs.readFileSync(workspacePath, "utf8");
const forbiddenEmbeddedModules = [
  "../../sales/page",
  "../../products/page",
  "../../payments/page",
  "../../profit/page",
  "../../finance/page",
  "../../exceptions/page",
  "../../automation/page",
  "../../settings/page",
  "../../data-center-import/page",
];
for (const modulePath of forbiddenEmbeddedModules) {
  if (workspace.includes(modulePath)) legacyUiErrors.push(`workspace-modules.tsx 禁止嵌入正式业务页：${modulePath}`);
}

const allowedWorkbenchViews = ["orders", "suppliers", "chain", "matching", "tax"];
const viewBlockEnd = navigation.indexOf("} as const;");
const viewBlock = viewBlockEnd > 0 ? navigation.slice(0, viewBlockEnd) : navigation;
const forbiddenViews = ["dashboard", "sales", "products", "inventory_goods", "inventory_consumables", "payments", "profit", "finance", "exceptions", "automation", "settings", "imports"];
for (const view of forbiddenViews) {
  if (viewBlock.includes(`${view}:`)) legacyUiErrors.push(`采购工作台 WORKBENCH_VIEWS 不应再包含旧全局模块：${view}`);
}
for (const view of allowedWorkbenchViews) {
  if (!viewBlock.includes(`${view}:`)) legacyUiErrors.push(`采购工作台缺少正式采购域视图：${view}`);
}

if (legacyUiErrors.length) {
  console.error("检测到旧版 UI / 模块嵌套回归：");
  legacyUiErrors.forEach((row) => console.error(`  - ${row}`));
  process.exit(1);
}

console.log(`导航与新版 UI 检查通过：${uniqueRoutes.length} 个侧栏入口均有页面；正式业务路由独立；旧 UI 仅保留兼容跳转。`);
