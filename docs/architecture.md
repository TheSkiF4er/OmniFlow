# OmniFlow — Architecture Overview

## Table of Contents

1. [Introduction](#introduction)
2. [System Components](#system-components)
3. [Core Workflow Engine](#core-workflow-engine)
4. [Plugins & Language Modules](#plugins--language-modules)
5. [Connectors](#connectors)
6. [UI & Frontend](#ui--frontend)
7. [Data Storage](#data-storage)
8. [Messaging & Events](#messaging--events)
9. [Deployment & Scaling](#deployment--scaling)

---

## Introduction

OmniFlow is a **modular, cross-platform automation engine** designed to support workflows, integrations, and automation tasks.
It is **language-agnostic**, allowing plugins to be implemented in any popular programming language (Go, Python, Java, C#, PHP, JavaScript, Ruby, TypeScript, Kotlin, etc.).

The architecture follows **event-driven design** with a focus on scalability, flexibility, and real-time workflow execution.

---

## System Components

```
/omniflow
 ├─ /core            – Workflow engine (Go / C#)
 ├─ /ui              – Web UI (React + TypeScript)
 ├─ /plugins         – Language-specific plugin modules
 ├─ /connectors      – External service integrations
 ├─ /examples        – Example workflows
 ├─ /docs            – Documentation
 ├─ docker-compose.yml
 └─ README.md
```

---

## Core Workflow Engine

The **Core Engine** is responsible for:

* Parsing workflow definitions (YAML / JSON)
* Scheduling tasks & cron jobs
* Executing plugins and connectors
* Event handling & notifications
* Workflow state management

**Key Features:**

* **Language-agnostic plugin execution** via gRPC / REST
* **Concurrent workflow execution** using goroutines / async tasks
* **Error handling & retries**
* **Audit logs** for all workflow actions

---

## Plugins & Language Modules

Plugins extend OmniFlow functionality. Each plugin is **isolated** and communicates with the core engine through standard interfaces:

* **JavaScript** → Node.js modules
* **Python** → FastAPI or standalone scripts
* **PHP** → Web scripts or CLI commands
* **Go / Ruby / Kotlin / Java** → Compiled binaries or REST endpoints

**Plugin Example (JavaScript):**

```js
module.exports = async (input) => {
    return { result: input.value * 2 };
};
```

---

## Connectors

Connectors provide pre-built integrations with external services:

* GitHub / GitLab
* Telegram / Discord
* MySQL / PostgreSQL / Redis
* OpenAI / AWS / Cloud services

**Connector Architecture:**

* REST API trigger or webhook listener
* Asynchronous event processing
* Secure credentials management

---

## UI & Frontend

* **React + TypeScript** frontend
* **Drag-and-drop workflow editor**
* **Pages:** Home, Workflows, Settings, Login
* **Redux + Ant Design** for state management and UI components

**Frontend communicates with the Core Engine via:**

* REST API for workflow & plugin management
* WebSocket for real-time updates

---

## Data Storage

OmniFlow uses **PostgreSQL** or **SQLite** for lightweight deployments:

* `workflows` — workflow definitions & state
* `tasks` — scheduled / running tasks
* `logs` — execution logs & events
* `users` — authentication and access control

---

## Messaging & Events

Event-driven architecture:

* **Redis / RabbitMQ / Kafka** for queues
* Plugins publish events → Core Engine consumes
* WebSocket pushes updates to frontend in real-time

---

## Deployment & Scaling

* Dockerized deployment (`docker-compose.yml`)
* Modular services for Core, UI, Plugins
* Horizontal scaling for high-load environments
* Cloud-ready: can run on Kubernetes or cloud VMs

---

OmniFlow architecture emphasizes **modularity, language interoperability, and real-time automation**.
This design allows developers to add new languages, plugins, and integrations without modifying the core engine.
