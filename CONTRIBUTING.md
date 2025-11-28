# Contributing to OmniFlow

First of all, thank you for your interest in contributing! 🙌
We welcome contributions of all kinds: bug fixes, new features, documentation improvements, examples, or plugins in different languages.

This guide explains how to get started and contribute effectively.

---

## Table of Contents

1. [How to Contribute](#how-to-contribute)
2. [Code of Conduct](#code-of-conduct)
3. [Development Setup](#development-setup)
4. [Workflow for Contributions](#workflow-for-contributions)
5. [Submitting Pull Requests](#submitting-pull-requests)
6. [Reporting Issues](#reporting-issues)
7. [Coding Guidelines](#coding-guidelines)
8. [License](#license)

---

## How to Contribute

You can contribute in many ways:

* Fixing bugs and improving the core engine
* Creating or improving plugins in any supported language
* Writing examples and workflows
* Improving documentation and guides
* Suggesting or implementing new features

---

## Code of Conduct

All contributors are expected to follow the [OmniFlow Code of Conduct](./CODE_OF_CONDUCT.md) to ensure a welcoming, respectful, and safe community.

---

## Development Setup

1. **Clone the repository**

```bash
git clone https://github.com/YOUR_NAME/omniflow.git
cd omniflow
```

2. **Install dependencies for UI**

```bash
cd ui
npm install
```

3. **Run Docker containers for core & database**

```bash
docker-compose up -d
```

4. **Run the frontend**

```bash
npm run dev
```

---

## Workflow for Contributions

We use a standard Git workflow:

1. **Fork the repository**
2. **Create a new branch** for your changes

```bash
git checkout -b feature/my-new-feature
```

3. **Make changes**
4. **Commit your changes** with descriptive messages

```bash
git commit -m "Add feature X to the workflow engine"
```

5. **Push your branch** to your fork

```bash
git push origin feature/my-new-feature
```

6. **Open a Pull Request** against the `main` branch of the original repository

---

## Submitting Pull Requests

* Ensure your code passes all tests and linter checks
* Provide a clear description of what your PR does
* Reference any related issues with `#issue_number`
* Add examples or documentation if applicable

---

## Reporting Issues

* Use the [GitHub Issues](https://github.com/TheSkiF4er/omniflow/issues) tracker
* Provide clear steps to reproduce bugs
* Include logs or screenshots when possible

---

## Coding Guidelines

* Follow consistent **naming conventions**
* Write **readable, maintainable code**
* Include **comments for complex logic**
* Ensure **cross-language compatibility** for plugins
* Test your changes locally

---

## License

All contributions are under the **Apache License 2.0**, consistent with the main OmniFlow repository. By contributing, you agree to license your work under this license.
