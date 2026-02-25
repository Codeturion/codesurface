# codesurface

**MCP server that indexes a C# codebase's public API at startup and serves it via compact tool responses — saving tokens vs reading source files.**

Parses all `.cs` files, extracts public classes/methods/properties/fields/events, and serves them through 4 MCP tools. Works with Claude Code, Cursor, Windsurf, or any MCP-compatible AI tool.

## Quick Start

```bash
pip install codesurface
```

Then add to your `.mcp.json`:

```json
{
  "mcpServers": {
    "codesurface": {
      "command": "codesurface",
      "args": ["--project", "/path/to/your/src"]
    }
  }
}
```

Point `--project` at any directory containing `.cs` files — a Unity `Assets/Scripts` folder, a .NET `src/` tree, a Godot C# project, etc.

Restart your AI tool and ask: *"What methods does MyService have?"*

## Tools

| Tool | Purpose | Example |
|------|---------|---------|
| `search` | Find APIs by keyword | "MergeService", "BlastBoard", "GridCoord" |
| `get_signature` | Exact signature by name or FQN | "TryMerge", "CampGame.Services.IMergeService.TryMerge" |
| `get_class` | Full class reference card — all public members | "BlastBoardModel" → all methods/fields/properties |
| `get_stats` | Overview of indexed codebase | File count, record counts, namespace breakdown |

## Benchmarks

Measured against a real Unity game project (129 files, 1,018 API records) across a 10-step cross-cutting research workflow.

| Strategy | Total Tokens | vs MCP |
|----------|-------------|--------|
| **MCP (codesurface)** | **1,021** | — |
| Skilled Agent (Grep + partial Read) | 4,453 | 4.4x more |
| Naive Agent (Grep + full Read) | 11,825 | 11.6x more |

Even with follow-up reads for implementation detail, the hybrid MCP + targeted Read approach uses **54% fewer tokens** than a skilled Grep+Read agent.

See [workflow-benchmark.md](workflow-benchmark.md) for the full step-by-step analysis.

## Setup Details

<details>
<summary>Claude Code configuration</summary>

Add to `<project>/.mcp.json`:

**Using uv (recommended):**
```json
{
  "mcpServers": {
    "codesurface": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/codesurface", "codesurface", "--project", "/path/to/your/src"]
    }
  }
}
```

**Using pip install:**
```json
{
  "mcpServers": {
    "codesurface": {
      "command": "codesurface",
      "args": ["--project", "/path/to/your/src"]
    }
  }
}
```

</details>

<details>
<summary>CLAUDE.md snippet (recommended)</summary>

Add to your project's `CLAUDE.md` so the AI knows when to use the tools:

```markdown
## Codebase API Lookup (codesurface MCP)

Use the `codesurface` MCP tools to look up your project's classes, methods, properties, and fields instead of reading source files.

| When | Tool | Example |
|------|------|---------|
| Searching for an API by keyword | `search` | `search("MergeService")` |
| Need exact method signature | `get_signature` | `get_signature("TryMerge")` |
| Want all members on a class | `get_class` | `get_class("BlastBoardModel")` |
| Overview of indexed codebase | `get_stats` | `get_stats()` |
```

</details>

<details>
<summary>Project structure</summary>

```
codesurface/
├── src/codesurface/
│   ├── server.py        # MCP server — 4 tools
│   ├── db.py            # SQLite + FTS5 database layer
│   └── cs_parser.py     # C# public API parser
├── pyproject.toml
└── README.md
```

</details>

<details>
<summary>Troubleshooting</summary>

**"No codebase indexed"**
- Ensure `--project` points to a directory containing `.cs` files
- The server indexes at startup — check stderr for the "Indexed N records" message

**Server won't start**
- Check Python version: `python --version` (needs 3.10+)
- Check `mcp[cli]` is installed: `pip install mcp[cli]`

**Stale results**
- The index is built at server startup. Restart the MCP server to pick up new/changed files

</details>

---

## Contact

fuatcankoseoglu@gmail.com

## License

MIT
