# Dev-Team Report Issue Endpoint

## Overview

The `/api/devteam/report` endpoint allows users to submit issue reports directly to the dev-team kanban board.

## Endpoint

**POST** `/api/devteam/report`

## Request Body

```json
{
  "title": "String (required)",
  "body": "String (required)",
  "component": "String (optional, one of: agydashboard, 9router, hermes, system)",
  "urgency": "String (optional, one of: low, medium, high)"
}
```

## Response

### Success (200)

```json
{
  "success": true,
  "output": "Task created successfully"
}
```

### Error (400, 403, 500)

```json
{
  "success": false,
  "error": "Error message"
}
```

## Implementation Details

- The endpoint creates a new task in the `dev-team` kanban board
- The task title is prefixed with `[BUG]`
- The task is assigned to the `coder` assignee
- High urgency tasks get priority 1, others get priority 0
- Additional metadata is added to the task body including component, urgency, user agent, and timestamp

## Example Usage

```bash
curl -X POST http://localhost:5000/api/devteam/report \
  -H "Content-Type: application/json" \
  -d '{
    "title": "Dashboard not loading",
    "body": "The dashboard page is not loading properly",
    "component": "agydashboard",
    "urgency": "high"
  }'
```