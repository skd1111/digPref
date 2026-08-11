// ESLint 8 legacy 配置 —— shared-protocol 仅检查 .ts 源文件（.js 为生成产物）
module.exports = {
  root: true,
  env: { es2022: true, node: true },
  parser: "@typescript-eslint/parser",
  parserOptions: {
    ecmaVersion: "latest",
    sourceType: "module",
  },
  plugins: ["@typescript-eslint"],
  extends: ["eslint:recommended", "plugin:@typescript-eslint/recommended"],
  ignorePatterns: ["node_modules", "*.js", "*.cjs"],
  rules: {
    "@typescript-eslint/no-unused-vars": [
      "error",
      { argsIgnorePattern: "^_", varsIgnorePattern: "^_", caughtErrors: "none" },
    ],
    "@typescript-eslint/no-explicit-any": "off",
    // `string & {}` 是保留字面量提示同时允许任意字符串的惯用写法（协议类型在用）
    "@typescript-eslint/ban-types": [
      "error",
      { types: { "{}": false }, extendDefaults: true },
    ],
  },
};
