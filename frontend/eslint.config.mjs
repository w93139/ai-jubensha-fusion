import { defineConfig, globalIgnores } from "eslint/config";
import nextVitals from "eslint-config-next/core-web-vitals";
import nextTypeScript from "eslint-config-next/typescript";

const migrationCompatibleNextVitals = nextVitals.map((config) => {
  if (config.name !== "next") {
    return config;
  }

  return {
    ...config,
    rules: {
      ...config.rules,
      // These compiler-oriented checks are new in the Next 16 preset. Keep
      // legacy pages visible as warnings until they can be refactored without
      // changing runtime behaviour as part of this configuration migration.
      "react-hooks/immutability": "warn",
      "react-hooks/set-state-in-effect": "warn",
      "react-hooks/static-components": "warn",
    },
  };
});

export default defineConfig([
  ...migrationCompatibleNextVitals,
  ...nextTypeScript,
  {
    rules: {
      "@typescript-eslint/no-explicit-any": "off",
    },
  },
  {
    files: ["tests/**/*.test.cjs"],
    rules: {
      // Node runs .cjs tests as CommonJS; their module loader is require().
      "@typescript-eslint/no-require-imports": "off",
    },
  },
  globalIgnores([
    ".next/**",
    "out/**",
    "build/**",
    "next-env.d.ts",
    "src/client/**",
  ]),
]);
