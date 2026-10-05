---
name: daily-planner
version: 1.0.0
description: Core daily lifestyle and habit planning skill
domain: personal
license: MIT
tools:
  - name: plan_daily_schedule
    description: Generate and record a daily schedule plan
    schema:
      type: object
      properties:
        day:
          type: integer
        theme:
          type: string
---
# Daily Planner Skill

This skill assists the agent in planning daily activities, schedules, and reflections.
