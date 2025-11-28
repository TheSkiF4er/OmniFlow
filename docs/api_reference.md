# OmniFlow — API Reference

### Table of Contents
1. Authentication
2. Workflows
3. Plugins
4. Connectors
5. Real-time Events (WebSocket)

---

## Authentication

### POST /api/login
Authenticate a user and obtain a JWT token.

**Request:**
```json
POST /api/login
Content-Type: application/json

{
    "email": "user@example.com",
    "password": "your_password"
}
```

**Response:**
```json
{
    "token": "eyJhbGciOiJIUzI1NiIsInR..."
}
```

---

### POST /api/logout
Invalidate the current token.

**Response:**
```json
{
    "message": "Logged out successfully"
}
```

---

## Workflows

### GET /api/workflows
Retrieve a list of all workflows.

**Response:**
```json
[
    {
        "id": "wf_123",
        "name": "Example Workflow",
        "status": "active",
        "created_at": "2025-11-28T12:00:00Z"
    }
]
```

### POST /api/workflows
Create a new workflow.

**Request:**
```json
{
    "name": "New Workflow",
    "nodes": [],
    "edges": []
}
```

**Response:**
```json
{
    "id": "wf_456",
    "name": "New Workflow",
    "status": "draft"
}
```

### GET /api/workflows/{id}
Retrieve a single workflow by ID.

---

## Plugins

### GET /api/plugins
List all installed plugins.

### POST /api/plugins/{plugin_id}/execute
Execute a plugin with input data.

**Request:**
```json
{
    "input": { "value": 42 }
}
```

**Response:**
```json
{
    "output": { "result": 84 }
}
```

---

## Connectors

### POST /api/connectors/{connector_name}/trigger
Trigger a connector event.

**Request:**
```json
{
    "event": "message",
    "payload": { "text": "Hello, OmniFlow!" }
}
```

**Response:**
```json
{
    "status": "ok"
}
```

---

## Real-time Events (WebSocket)

OmniFlow supports WebSocket connections for real-time workflow updates.

* **Endpoint:** `ws://localhost:8080/ws`
* **Events:**
  * `workflow_update`
  * `plugin_output`
  * `connector_event`

**Example:**
```json
{
    "event": "workflow_update",
    "workflow_id": "wf_123",
    "changes": { "nodes_added": 1 }
}
```

---

## Error Codes

| Code | Description           |
| ---- | --------------------- |
| 400  | Bad Request           |
| 401  | Unauthorized          |
| 403  | Forbidden             |
| 404  | Not Found             |
| 500  | Internal Server Error |

--- 

## Notes

* All endpoints require `Authorization: Bearer <token>` header except `/api/login`
* JSON is used as default content type
* Plugins and connectors run asynchronously; responses may be delayed
