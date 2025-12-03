# OmniFlow Go Plugin

**Production-Ready Plugin for OmniFlow**

The **OmniFlow Go Plugin** provides a clean, robust, and developer-friendly environment for building high-performance OmniFlow plugins using the Go programming language.
This module implements the unified OmniFlow Plugin Protocol and includes tooling, examples, tests, and containerization files required for reliable production usage.

---

## ✨ Features

* **Full plugin lifecycle support** (initialization, execution, termination).
* **Strict compliance with OmniFlow Plugin Protocol** (JSON-RPC-like messaging).
* **High-performance runtime** powered by Go’s concurrency primitives.
* **Built-in JSON utilities** and cross-plugin data schemas.
* **Integration test suite** validating protocol correctness.
* **Docker-ready environment** for sandboxed execution.
* **Makefile automation** for building, testing, linting, and packaging.
* **Clear and extendable project structure** suitable for enterprise environments.

---

## 📁 Directory Structure

```
OmniFlow/plugins/go/
│
├── test/
│   ├── integration_test.sh  # End-to-end protocol integration test
│   └── plugin_test.go       # Unit tests for plugin logic
│
├── go.mod                   # Go module definition
├── go.sum                   # Dependency checksums
├── Dockerfile               # Production-ready build and runtime
├── Makefile                 # Build, test, lint, and release automation
├── sample_plugin.go         # main plugin source (example name)
└── README.md                # You are here
```

---

## 🚀 Getting Started

### 1. Install Go

The SDK requires:

```
Go 1.21+ (recommended 1.22 or later)
```

### 2. Clone the OmniFlow repository

```bash
git clone https://github.com/omniflow/omniflow.git
cd omniflow/plugins/go
```

### 3. Install dependencies

```bash
go mod tidy
```

---

## 🧪 Testing

### Run unit tests:

```bash
make test
```

### Run integration protocol validation:

```bash
test/integration_test.sh
```

This ensures your plugin fully complies with OmniFlow's messaging protocol.

---

## 🐳 Docker Support

Build a production container:

```bash
docker build -t omniflow-go-plugin .
```

Run the plugin inside the OmniFlow orchestration environment:

```bash
docker run --rm omniflow-go-plugin
```

The provided `Dockerfile` uses:

* Multi-stage builds
* Minimalistic runtime environment
* Non-root execution
* Optimized Go binary (`CGO_DISABLED=1`, `-ldflags "-s -w"`)

---

## 🛠 Makefile Commands

| Command       | Description            |
| ------------- | ---------------------- |
| `make build`  | Build plugin binary    |
| `make test`   | Run unit tests         |
| `make lint`   | Run static analysis    |
| `make clean`  | Remove build artifacts |
| `make docker` | Build Docker image     |

---

## 🔌 OmniFlow Protocol Compatibility

This SDK fully implements:

* **Message envelopes** (`type`, `timestamp`, `payload`)
* **Plugin lifecycle events**
* **Request/response semantics**
* **Error propagation**
* **Streaming output support** (when enabled)

To learn more, see:
`OmniFlow/plugins/common/protocol.md`

---

## 📚 References

* **OmniFlow Plugin API:** `plugins/common/plugin-api.md`
* **JSON Schemas:** `plugins/common/schemas/`
* **Cross-language examples:** `plugins/examples/integration/`

---

## 📝 License

This module is licensed under **Apache 2.0**, the same as the rest of OmniFlow.

---

## 🧩 Contributing

Contributions are welcome!
Create a pull request or open an issue if you want to propose improvements.
