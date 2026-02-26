<!-- mcp-name: io.github.Codeturion/codesurface -->

# codesurface

[![PyPI Version](https://img.shields.io/pypi/v/codesurface.svg)](https://pypi.org/project/codesurface/)
[![PyPI Downloads](https://img.shields.io/pypi/dm/codesurface.svg)](https://pypi.org/project/codesurface/)
[![MCP Registry](https://img.shields.io/badge/MCP-Registry-green)](https://registry.modelcontextprotocol.io/servers/io.github.Codeturion/codesurface)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![Blog Post](https://img.shields.io/badge/Blog-Benchmark%20Write--up-blue)](https://www.codeturion.me/blog/reducing-llm-agent-hallucinations-through-constrained-api-retrieval)

**MCP server that indexes your codebase's public API at startup and serves it via compact tool responses — saving tokens vs reading source files.**

Parses source files, extracts public classes/methods/properties/fields/events, and serves them through 5 MCP tools. Works with Claude Code, Cursor, Windsurf, or any MCP-compatible AI tool.

**Supported languages:** C# (`.cs`), Go (`.go`), Java (`.java`), Python (`.py`), TypeScript/TSX (`.ts`, `.tsx`)

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

Point `--project` at any directory containing supported source files — a Unity `Assets/Scripts` folder, a Spring Boot project, a .NET `src/` tree, a Node.js/React project, a Python package, etc. Languages are auto-detected.

Restart your AI tool and ask: *"What methods does MyService have?"*

## Tools

| Tool | Purpose | Example |
|------|---------|---------|
| `search` | Find APIs by keyword | "MergeService", "BlastBoard", "GridCoord" |
| `get_signature` | Exact signature by name or FQN | "TryMerge", "CampGame.Services.IMergeService.TryMerge" |
| `get_class` | Full class reference card — all public members | "BlastBoardModel" → all methods/fields/properties |
| `get_stats` | Overview of indexed codebase | File count, record counts, namespace breakdown |
| `reindex` | Incremental index update (mtime-based) | Only re-parses changed/new/deleted files |

## Tested On

| Project | Language | Files | Records | Time |
|---------|----------|-------|---------|------|
| [vscode](https://github.com/microsoft/vscode) | TypeScript | 6,611 | 88,293 | 9.3s |
| [Paper](https://github.com/PaperMC/Paper) | Java | 2,909 | 33,973 | 2.3s |
| [client-go](https://github.com/kubernetes/client-go) | Go | 219 | 2,760 | 0.4s |
| [langchain](https://github.com/langchain-ai/langchain) | Python | 1,880 | 12,418 | 1.1s |
| [pydantic](https://github.com/pydantic/pydantic) | Python | 365 | 9,648 | 0.3s |
| [guava](https://github.com/google/guava) | Java | 891 | 8,377 | 2.4s |
| [immich](https://github.com/immich-app/immich) | TypeScript | 919 | 7,957 | 0.6s |
| [fastapi](https://github.com/tiangolo/fastapi) | Python | 881 | 5,713 | 0.5s |
| [ant-design](https://github.com/ant-design/ant-design) | TypeScript | 2,947 | 5,452 | 0.9s |
| [dify](https://github.com/langgenius/dify) | TypeScript | 4,903 | 5,038 | 1.9s |
| [crawlee-python](https://github.com/apify/crawlee-python) | Python | 386 | 2,473 | 0.3s |
| [flask](https://github.com/pallets/flask) | Python | 63 | 872 | <0.1s |
| [cobra](https://github.com/spf13/cobra) | Go | 15 | 249 | <0.1s |
| [gin](https://github.com/gin-gonic/gin) | Go | 41 | 574 | <0.1s |
| Unity game (private) | C# | 129 | 1,018 | 0.1s |

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
| After creating/deleting source files | `reindex` | `reindex()` |
```

</details>

<details>
<summary>Project structure</summary>

```
codesurface/
├── src/codesurface/
│   ├── server.py           # MCP server — 5 tools
│   ├── db.py               # SQLite + FTS5 database layer
│   └── parsers/
│       ├── base.py         # BaseParser ABC
│       ├── csharp.py       # C# parser
│       ├── go.py           # Go parser
│       ├── java.py         # Java parser
│       ├── python_parser.py # Python parser
│       └── typescript.py   # TypeScript/TSX parser
├── pyproject.toml
└── README.md
```

</details>

<details>
<summary>Troubleshooting</summary>

**"No codebase indexed"**
- Ensure `--project` points to a directory containing supported source files (`.cs`, `.go`, `.java`, `.py`, `.ts`, `.tsx`)
- The server indexes at startup — check stderr for the "Indexed N records" message

**Server won't start**
- Check Python version: `python --version` (needs 3.10+)
- Check `mcp[cli]` is installed: `pip install mcp[cli]`

**Stale results after editing source files**
- Call `reindex()` — only re-parses files whose modification time changed, fast even on large codebases

</details>

---

## Contact

fuatcankoseoglu@gmail.com

## License

MIT
