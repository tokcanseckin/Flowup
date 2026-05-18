# Project Documentation

Centralized location for all project-level documentation, planning notes, and reference materials.

## Contents

### Core Project Docs
- **[overview.md](overview.md)** — Product intent, architecture, requirements, and file map
- **[state-of-project.md](state-of-project.md)** — Current status, what's shipped, coverage stats, known gaps
- **[ideas.md](ideas.md)** — Backlog of ideas and feature proposals
- **[blog.md](blog.md)** — Weekly narrative updates (public-facing changelog)

### Integration & Technical Reference
- **[openrussian-integration-complete.md](openrussian-integration-complete.md)** — OpenRussian dictionary integration completion notes
- **[openrussian-alternatives.md](openrussian-alternatives.md)** — Research on alternative dictionary sources
- **[graduation-runbook.md](graduation-runbook.md)** — Process for graduating eval pipelines to production
- **[integration-plan.md](integration-plan.md)** — Data model migration and integration strategy
- **[analytics-events.md](analytics-events.md)** — Complete list of Plausible analytics events and metrics

## Usage

These docs are the **source of truth** for:
- Architecture decisions and product intent (overview.md)
- What's working today vs what's planned (state-of-project.md)
- Feature backlog and prioritization (ideas.md)
- Public release notes (blog.md)
- Analytics tracking reference (analytics-events.md)

When starting work on a new feature or investigating a question, start here before diving into code.

## Naming Convention

All documentation files use **lowercase-with-dashes** (kebab-case) for consistency and readability, except for `README.md` which follows the universal convention.
