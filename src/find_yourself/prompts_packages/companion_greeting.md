---
name: companion-greeting
description: Default warm greeting prompt for private companion mode
scope: platform
variables_schema:
  username:
    type: str
    required: true
---
Hello {{username}}, welcome back to your personal sanctuary. What shall we focus on today?
