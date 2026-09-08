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

const legacyStart = navigation.indexOf("const LEGACY_VIEWS");
const legacyEnd = navigation.indexOf("};", legacyStart);
const legacyBlock = legacyStart >= 0 && legacyEnd > legacyStart
  ? navigation.slice(legacyStart, legacyEnd + 2)
  : "";

if (!legacyBlock) {
  console.error("无法定位 workbench-navigation.ts 的 LEGACY_VIEWS，无法验证路由劫持规则。");
  process.exit(1);
}

const formalRoutes = uniqueRoutes.filter((route) => route !== "/purchase/workbench");
const hijacked = formalRoutes.filter((route) => legacyBlock.includes(`"${route}":`));

if (hijacked.length) {
  console.error("正式业务路由不应被重定向进采购工作台：");
  hijacked.forEach((route) => console.error(`  - ${route}`));
  process.exit(1);
}

console.log(`导航路由检查通过：${uniqueRoutes.length} 个侧栏入口均有页面，正式业务路由未被采购工作台劫持。`);
