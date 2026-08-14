// ESLint 9 flat config. package.json has declared a `lint` script and the eslint/react
// plugins since the project started, but there was no config file, so `npm run lint`
// always exited with "couldn't find an eslint.config.js" -- the frontend has never
// actually been linted.
import js from "@eslint/js";
import react from "eslint-plugin-react";
import reactHooks from "eslint-plugin-react-hooks";

export default [
  { ignores: ["dist/**", "node_modules/**"] },
  js.configs.recommended,
  {
    files: ["src/**/*.{js,jsx}"],
    languageOptions: {
      ecmaVersion: 2022,
      sourceType: "module",
      parserOptions: { ecmaFeatures: { jsx: true } },
      globals: {
        window: "readonly",
        document: "readonly",
        localStorage: "readonly",
        fetch: "readonly",
        setTimeout: "readonly",
        console: "readonly",
        FormData: "readonly",
        URL: "readonly",
        Blob: "readonly",
      },
    },
    settings: { react: { version: "detect" } },
    plugins: { react, "react-hooks": reactHooks },
    rules: {
      ...react.configs.recommended.rules,
      ...reactHooks.configs.recommended.rules,
      // This project uses the modern JSX transform, so React need not be in scope.
      "react/react-in-jsx-scope": "off",
      // Props are not type-checked in this codebase; enabling this would flag every
      // component without adding real safety.
      "react/prop-types": "off",
    },
  },
];
