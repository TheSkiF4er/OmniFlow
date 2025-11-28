# OmniFlow — User Guide

## Table of Contents

1. [Introduction](#introduction)
2. [Installation](#installation)
3. [Running OmniFlow](#running-omniflow)
4. [Workflow Editor](#workflow-editor)
5. [Creating Plugins](#creating-plugins)
6. [Using Connectors](#using-connectors)
7. [Managing Workflows](#managing-workflows)
8. [API Access](#api-access)
9. [Troubleshooting](#troubleshooting)

---

## Introduction

OmniFlow is a **cross-platform, modular automation engine** that allows users to:

* Automate workflows and tasks
* Integrate multiple services
* Use plugins written in any programming language
* Create event-driven automation pipelines

This guide will help you get started quickly.

---

## Installation

### Prerequisites

* Docker & Docker Compose
* Node.js >= 18 (for UI)
* Go / Python / PHP / Java / C# runtimes depending on plugins

### Clone Repository

```bash
git clone https://github.com/YOUR_NAME/omniflow.git
cd omniflow
```

### Start with Docker Compose

```bash
docker-compose up -d
```

**Frontend URL:** [http://localhost:3000](http://localhost:3000)

Backend API: `http://localhost:8080/api`

---

## Running OmniFlow

1. **Core Engine**: Handles workflow execution and plugin orchestration
2. **UI**: React-based visual editor
3. **Plugins**: Optional, executed as separate processes
4. **Connectors**: Optional, for external services integration

All components can run via Docker or natively on supported platforms.

---

## Workflow Editor

* Drag & drop nodes to create workflows
* Nodes can represent **tasks, plugins, connectors, conditions, or loops**
* Connect nodes with arrows to define execution order
* Each node has **configurable parameters**
* Save workflows as JSON/YAML for portability

### Example Workflow Node Types

* **Task** — simple function execution
* **Plugin** — call an external module
* **Connector** — trigger events or send messages
* **Conditional** — if/else branching
* **Loop** — iterate over lists or data

---

## Creating Plugins

Plugins allow extending OmniFlow functionality.

### JavaScript Example:

```js
module.exports = async (input) => {
    return { result: input.value * 2 };
};
```

### Python Example:

```python
def handler(event):
    return {"result": event["x"] * 2}
```

Plugins can be written in **any supported language**. Place them in the `/plugins/<language>` folder.

---

## Using Connectors

Connectors integrate external services.

Supported connectors include:

* GitHub / GitLab
* Telegram / Discord
* MySQL / PostgreSQL / Redis
* OpenAI / AWS services

### Trigger a Connector

Send events via REST API or WebSocket:

```json
POST /api/connectors/telegram/trigger
{
    "event": "message",
    "payload": {"text": "Hello from OmniFlow"}
}
```

---

## Managing Workflows

* **Activate / Deactivate** workflows
* **View execution logs**
* **Schedule cron jobs** for automation
* **Clone workflows** for testing variations

Frontend provides intuitive controls for all operations.

---

## API Access

OmniFlow exposes REST and WebSocket endpoints:

* **REST API** for managing workflows, plugins, and connectors
* **WebSocket** for real-time updates and events

Authentication via JWT is required for most endpoints. See [`api_reference.md`](./api_reference.md) for full details.

---

## Troubleshooting

* **Frontend not loading:** Ensure Node.js and Docker are running
* **Plugin fails:** Check plugin runtime environment and logs
* **Connector not triggering:** Verify API key / credentials and network access
* **Workflow stuck:** Check Core Engine logs and Redis/Kafka queues

Logs are available in Docker containers:

```bash
docker-compose logs -f
```

---

OmniFlow is designed for **flexible, cross-platform automation**, allowing users to build complex workflows without writing extensive glue code.
