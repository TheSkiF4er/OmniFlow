# 📦 OmniFlow JavaScript Plugin

**OmniFlow JavaScript Plugin** is the official template for creating high-performance, secure, and fully interoperable plugins for the **OmniFlow Workflow Automation Engine**.

This template provides everything required to build, test, lint, containerize, and distribute JavaScript-based extensions following OmniFlow’s best architectural practices.

---

## 🚀 Features

* **Modern JavaScript (ESM)** with optional TypeScript support
* **Automatic plugin discovery**
* **Rollup-based production build** (fast + optimized)
* **ESLint + Prettier** for consistent code quality
* **Jest** for unit tests
* **Dockerfile included** for containerized deployment
* **Zero runtime dependencies unless required by the plugin**

---

## 📁 Directory Structure

```
plugins/javascript/
│
├── tests/
│   └── sample_plugin.test.js
│
├── Dockerfile
├── package.json
├── package-lock.json
├── .eslintrc.json
├── .prettierrc
├── sample_plugin.js
└── README.md
```

---

## 🛠 Installation

Clone the repository and install dependencies:

```bash
npm install
```

Build the plugin:

```bash
npm run build
```

Run tests:

```bash
npm test
```

Format your code:

```bash
npm run format
```

Lint your code:

```bash
npm run lint
```

---

## 🧩 Creating a Plugin

Every JavaScript plugin must:

1. Export a **default object** containing at least:

   * `name` — plugin name
   * `version` — semver
   * `init()` — code executed on plugin load
   * `handlers` — event/job/action handlers

2. Optionally export additional utilities.

### Example: Minimal Plugin

```js
export default {
  name: "sample-js-plugin",
  version: "1.0.0",

  async init({ logger }) {
    logger.info("JavaScript plugin initialized!");
  },

  handlers: {
    onTaskStart: async ({ task, logger }) => {
      logger.info(`Task started: ${task.id}`);
    }
  }
};
```

---

## 🧪 Testing Plugins

All plugins include Jest by default.

Example test:

```js
import plugin from "../src/index.js";

test("plugin loads correctly", () => {
  expect(plugin.name).toBe("sample-js-plugin");
});
```

Run all tests:

```bash
npm test
```

---

## 🐳 Docker Usage

The included Dockerfile builds an optimized production image:

```bash
docker build -t omniflow-js-plugin .
```

Run:

```bash
docker run --rm omniflow-js-plugin
```

---

## 📦 Publishing the Plugin

Once the plugin is ready:

```bash
npm run build
npm publish
```

Make sure your `package.json` contains a unique package name before publishing to npm.

---

## 🤝 Contributing

Contributions, improvements, and bug reports are welcome.

Please follow:

* Conventional Commits (`feat:`, `fix:`)
* Prettier formatting
* ESLint rules

---

## 📜 License

Licensed under the **Apache License 2.0**
© TheSkiF4er
