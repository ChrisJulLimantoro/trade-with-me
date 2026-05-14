# Graph Report - .  (2026-05-13)

## Corpus Check
- Corpus is ~19,095 words - fits in a single context window. You may not need a graph.

## Summary
- 25 nodes · 20 edges · 9 communities (6 shown, 3 thin omitted)
- Extraction: 90% EXTRACTED · 10% INFERRED · 0% AMBIGUOUS · INFERRED: 2 edges (avg confidence: 0.9)
- Token cost: 36,877 input · 2,190 output

## Community Hubs (Navigation)
- [[_COMMUNITY_CLI Entrypoint Internals|CLI Entrypoint Internals]]
- [[_COMMUNITY_Pipeline Phases 1-5|Pipeline Phases 1-5]]
- [[_COMMUNITY_Package + Analysis Skills|Package + Analysis Skills]]
- [[_COMMUNITY_Top-Level Docs|Top-Level Docs]]
- [[_COMMUNITY_MCP Server & UISkills Phases|MCP Server & UI/Skills Phases]]
- [[_COMMUNITY_System Rationale|System Rationale]]
- [[_COMMUNITY_Post-Mortem Skill|Post-Mortem Skill]]
- [[_COMMUNITY_Weekly Reflection Skill|Weekly Reflection Skill]]

## God Nodes (most connected - your core abstractions)
1. `Specs Overview` - 3 edges
2. `Phase 1: Data Collection` - 3 edges
3. `Phase 5: Learning` - 3 edges
4. `Phase 6: UI` - 3 edges
5. `ATS CLI Entrypoint` - 3 edges
6. `main_callback()` - 2 edges
7. `Architecture Document` - 2 edges
8. `README Document` - 2 edges
9. `Phase 2: Data Processing` - 2 edges
10. `Phase 3: Orchestration` - 2 edges

## Surprising Connections (you probably didn't know these)
- `/cycle-now Skill` --calls--> `ATS CLI Entrypoint`  [INFERRED]
  specs/03-orchestration.md → src/ats/cli.py
- `/analyze-symbol Skill` --calls--> `ATS CLI Entrypoint`  [INFERRED]
  specs/04-deep-analysis.md → src/ats/cli.py
- `README Document` --references--> `Architecture Document`  [EXTRACTED]
  README.md → architecture.md
- `Architecture Document` --references--> `Specs Overview`  [EXTRACTED]
  architecture.md → specs/00-overview.md
- `README Document` --references--> `Specs Overview`  [EXTRACTED]
  README.md → specs/00-overview.md

## Communities (9 total, 3 thin omitted)

### Community 0 - "CLI Entrypoint Internals"
Cohesion: 0.4
Nodes (3): main_callback(), ATS CLI — baseline entrypoint.  Each phase extends this module with its own comm, No-op root callback; phase commands are registered here.

### Community 1 - "Pipeline Phases 1-5"
Cohesion: 0.5
Nodes (5): Phase 1: Data Collection, Phase 2: Data Processing, Phase 3: Orchestration, Phase 4: Deep Analysis, Phase 5: Learning

### Community 2 - "Package + Analysis Skills"
Cohesion: 0.5
Nodes (4): ATS CLI Entrypoint, ATS Package, /analyze-symbol Skill, /cycle-now Skill

### Community 3 - "Top-Level Docs"
Cohesion: 1.0
Nodes (3): Architecture Document, README Document, Specs Overview

### Community 4 - "MCP Server & UI/Skills Phases"
Cohesion: 0.67
Nodes (3): MCP Server, Phase 6: UI, Phase 7: Skills & MCP

## Knowledge Gaps
- **10 isolated node(s):** `Agentic Trading System.  See `architecture.md` at the repo root for the high-lev`, `ATS CLI — baseline entrypoint.  Each phase extends this module with its own comm`, `No-op root callback; phase commands are registered here.`, `Phase 7: Skills & MCP`, `ATS Package` (+5 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **3 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `Phase 1: Data Collection` connect `Pipeline Phases 1-5` to `Top-Level Docs`?**
  _High betweenness centrality (0.091) - this node is a cross-community bridge._
- **Why does `Phase 5: Learning` connect `Pipeline Phases 1-5` to `MCP Server & UI/Skills Phases`?**
  _High betweenness centrality (0.091) - this node is a cross-community bridge._
- **Why does `Phase 6: UI` connect `MCP Server & UI/Skills Phases` to `Pipeline Phases 1-5`?**
  _High betweenness centrality (0.062) - this node is a cross-community bridge._
- **Are the 2 inferred relationships involving `ATS CLI Entrypoint` (e.g. with `/cycle-now Skill` and `/analyze-symbol Skill`) actually correct?**
  _`ATS CLI Entrypoint` has 2 INFERRED edges - model-reasoned connections that need verification._
- **What connects `Agentic Trading System.  See `architecture.md` at the repo root for the high-lev`, `ATS CLI — baseline entrypoint.  Each phase extends this module with its own comm`, `No-op root callback; phase commands are registered here.` to the rest of the system?**
  _10 weakly-connected nodes found - possible documentation gaps or missing edges._