// ESLint 8 legacy 配置 —— 与 devDependencies 中的 @typescript-eslint v7 配套
module.exports = {
  root: true,
  env: { browser: true, es2022: true, node: true },
  parser: "@typescript-eslint/parser",
  parserOptions: {
    ecmaVersion: "latest",
    sourceType: "module",
    ecmaFeatures: { jsx: true },
  },
  plugins: ["@typescript-eslint", "react-hooks"],
  extends: [
    "eslint:recommended",
    "plugin:@typescript-eslint/recommended",
    "plugin:react-hooks/recommended",
  ],
  ignorePatterns: ["dist", "node_modules", "src-tauri", "coverage", "*.js", "*.cjs"],
  rules: {
    // 下划线前缀变量视为有意忽略
    "@typescript-eslint/no-unused-vars": [
      "error",
      { argsIgnorePattern: "^_", varsIgnorePattern: "^_", caughtErrors: "none" },
    ],
    // 协议/工具层大量使用 any 承载 LLM 自由结构，暂不强制
    "@typescript-eslint/no-explicit-any": "off",
    // `string & {}` 是保留字面量提示同时允许任意字符串的惯用写法；
    // `Symbol` 为 shared-protocol codenav 协议类型名（与 Python Symbol 镜像），非 JS 内置
    "@typescript-eslint/ban-types": [
      "error",
      { types: { "{}": false, Symbol: false }, extendDefaults: true },
    ],
    // exhaustive-deps 存量待逐项审计（盲改依赖数组有行为回归风险），暂关
    "react-hooks/exhaustive-deps": "off",
    "no-empty": ["error", { allowEmptyCatch: true }],
  },
};
