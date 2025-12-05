// plugins/typescript/.eslintrc.js
/**
 * Production-ready ESLint configuration for the OmniFlow TypeScript plugin.
 *
 * - TypeScript-aware via @typescript-eslint/parser
 * - Opinionated, but compatible with Prettier (formatting delegated to Prettier)
 * - Includes import plugin to validate imports and TS resolver
 * - Adds Jest rules for test files
 * - Enables a few pragmatic rules useful for library code (no-unused-vars, explicit function return types optional)
 *
 * Notes:
 *  - Install these devDependencies in your plugin workspace:
 *      eslint, @typescript-eslint/parser, @typescript-eslint/eslint-plugin,
 *      eslint-plugin-import, eslint-import-resolver-typescript,
 *      eslint-plugin-jest, eslint-config-prettier, eslint-plugin-prettier,
 *      eslint-plugin-unicorn (optional / recommended)
 *
 *  - This config assumes `tsconfig.json` is present at plugins/typescript/tsconfig.json.
 *  - Adjust 'project' path in parserOptions if your workspace layout differs.
 */

module.exports = {
  root: true,
  env: {
    node: true,
    es2022: true,
    browser: false,
  },
  parser: '@typescript-eslint/parser',
  parserOptions: {
    ecmaVersion: 2022,
    sourceType: 'module',
    // Point to the project's tsconfig for rules that require type information
    project: ['./tsconfig.json'],
    tsconfigRootDir: __dirname,
  },
  plugins: [
    '@typescript-eslint',
    'import',
    'jest',
    'prettier',
    'unicorn'
  ],
  extends: [
    'eslint:recommended',
    'plugin:@typescript-eslint/recommended',
    // enables rules that need type information (useful but heavier)
    'plugin:@typescript-eslint/recommended-requiring-type-checking',
    'plugin:import/recommended',
    'plugin:import/typescript',
    'plugin:jest/recommended',
    'plugin:unicorn/recommended',
    // Keep prettier last to disable conflicting formatting rules
    'plugin:prettier/recommended'
  ],
  settings: {
    'import/resolver': {
      typescript: {
        // Always try to resolve typescript files using the project's tsconfig
        project: ['./tsconfig.json']
      }
    },
    'import/parsers': {
      '@typescript-eslint/parser': ['.ts', '.tsx']
    }
  },
  ignorePatterns: [
    'dist/',
    'build/',
    'node_modules/',
    '!.eslintrc.js' // still lint config if needed
  ],
  rules: {
    /*********************
     * Core / stylistic *
     *********************/
    // Turn off indent because Prettier controls formatting
    'indent': 'off',
    // Encourage single-purpose files
    'max-lines': ['warn', { max: 400, skipBlankLines: true, skipComments: true }],
    'max-params': ['warn', 5],
    'max-statements': ['warn', 120],

    /*********************
     * TypeScript rules  *
     *********************/
    // Prefer explicit types on exported functions, but not required everywhere
    '@typescript-eslint/explicit-module-boundary-types': ['warn', {
      allowTypedFunctionExpressions: true,
      allowHigherOrderFunctions: true
    }],
    // Discourage the use of 'any', but allow it when necessary with a comment
    '@typescript-eslint/no-explicit-any': ['warn', { fixToUnknown: false }],
    // Allow unused vars if they start with underscore (common pattern)
    '@typescript-eslint/no-unused-vars': ['error', { argsIgnorePattern: '^_', varsIgnorePattern: '^_' }],
    // Prefer readonly for function params when possible
    '@typescript-eslint/prefer-readonly-parameter-types': 'off',
    // Allow non-null assertions sparingly
    '@typescript-eslint/no-non-null-assertion': 'warn',
    // Encourage safer assertions
    '@typescript-eslint/strict-boolean-expressions': 'warn',

    /*********************
     * Import rules      *
     *********************/
    // Ensure imports resolve correctly
    'import/no-unresolved': 'error',
    'import/no-extraneous-dependencies': ['error', {
      devDependencies: [
        '**/tests/**',
        '**/*.test.*',
        '**/*.spec.*',
        'plugins/typescript/tests/**',
        'plugins/**/tests/**'
      ],
      optionalDependencies: false,
      peerDependencies: false
    }],
    // Enforce consistent ordering (you can integrate eslint-plugin-import/order later)
    'import/order': ['warn', {
      groups: [['builtin', 'external'], 'internal', ['parent', 'sibling', 'index']],
      'newlines-between': 'always'
    }],

    /*********************
     * Jest / testing    *
     *********************/
    // Allow dev-only test patterns to import dev deps
    'jest/no-disabled-tests': 'warn',
    'jest/no-focused-tests': 'error',
    'jest/no-identical-title': 'error',

    /*********************
     * Prettier / format *
     *********************/
    'prettier/prettier': ['error', {
      singleQuote: true,
      trailingComma: 'all',
      printWidth: 100,
      semi: true,
    }],

    /*********************
     * Unicorn (optional recommended) *
     *********************/
    // Unicorn rules are a collection of helpful best-practices. Keep some as warnings.
    'unicorn/prefer-module': 'off', // project may still target CommonJS
    'unicorn/filename-case': ['warn', { case: 'kebabCase' }],

    /*********************
     * Relaxations       *
     *********************/
    // Allow console.info/warn in tools and scripts but keep console.log discouraged
    'no-console': ['warn', { allow: ['warn', 'error', 'info'] }],
    // Disable base rules that conflict with TS-aware versions
    'no-unused-vars': 'off',
    'no-shadow': 'off',
    '@typescript-eslint/no-shadow': ['error'],

    // Fine-grained runtime checks
    'no-throw-literal': 'error'
  },

  overrides: [
    // Test files: relax some restrictions and enable jest environment
    {
      files: ['**/*.test.ts', '**/*.spec.ts', 'plugins/**/tests/**', 'tests/**'],
      env: { jest: true },
      rules: {
        // Tests often use devDependencies and allow more flexible patterns
        '@typescript-eslint/explicit-module-boundary-types': 'off',
        'unicorn/filename-case': 'off'
      }
    },
    // Scripts & tooling (bin, scripts) - allow console usage & commonjs
    {
      files: ['scripts/**', 'bin/**'],
      rules: {
        'no-console': 'off',
        '@typescript-eslint/no-var-requires': 'off'
      }
    },
    // Configuration files (may use CommonJS)
    {
      files: ['*.config.js', '.eslintrc.js', 'webpack.config.js'],
      env: { node: true },
      rules: {
        '@typescript-eslint/no-var-requires': 'off',
        'unicorn/prefer-module': 'off'
      }
    }
  ]
};
