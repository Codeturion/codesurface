# Evaluating API-Indexed Retrieval vs Textual Search for LLM Agent Codebase Navigation

## Overview

Five real-world codebases. Five languages. One question: **how much does a pre-indexed API surface save an LLM agent compared to Grep+Read?**

Each benchmark follows a realistic cross-cutting research workflow — the kind of investigation an agent performs before implementing a feature. Every MCP token count comes from actual tool responses measured against the indexed project.

## Cross-Language Results

| Language | Project | Files | Records | MCP | Skilled | Naive | MCP vs Skilled | MCP vs Naive |
|----------|---------|------:|--------:|----:|--------:|------:|---------------:|-------------:|
| C# | Unity game | 129 | 1,034 | **1,021** | 4,453 | 11,825 | 77% fewer | 91% fewer |
| TypeScript | [immich](https://github.com/immich-app/immich) | 694 | 8,344 | **1,451** | 4,500 | 14,550 | 68% fewer | 90% fewer |
| Java | [guava](https://github.com/google/guava) | 891 | 8,377 | **1,851** | 4,200 | 26,700 | 56% fewer | 93% fewer |
| Go | [gin](https://github.com/gin-gonic/gin) | 38 | 534 | **1,791** | 2,770 | 15,300 | 35% fewer | 88% fewer |
| Python | codesurface | 9 | 40 | **753** | 2,000 | 10,400 | 62% fewer | 93% fewer |
| | **TOTAL** | **1,761** | **18,329** | **6,867** | **17,923** | **78,775** | **62% fewer** | **91% fewer** |

## Methodology

Three agent strategies are compared for each workflow:

| Strategy | Description |
|---|---|
| **MCP** | Pre-indexed API server. Returns only public signatures, pre-ranked. 1 tool call per lookup. |
| **Skilled Agent** | Targeted Grep + partial Read. Uses `Grep -C 3` for signatures, `Read` with offset/limit (~40 lines) for classes. Assumes the agent already knows file paths or finds them efficiently. |
| **Naive Agent** | Grep to find file → Read the entire file. When multiple steps target the same file, the cost is counted on first access only (the agent retains file content in context). |

**Token estimate**: `len(text) / 4` (standard approximation for code).

**Important caveats**:
- The Skilled Agent numbers assume optimal tool usage — real agents vary between Skilled and Naive.
- Grep simulations use case-insensitive substring matching (similar to ripgrep). In practice, an agent might need multiple grep attempts.
- MCP returns are pure signal (public API only). Grep+Read returns include implementation noise the LLM must mentally filter.
- For small files (< 30 lines), all three approaches converge — the advantage grows with file size and codebase complexity.

---

## C# — Unity Game (129 files, 1,034 records)

**Feature**: When a player wins a blast level, spawn reward items in their camp.

**Why this workflow?** It crosses both game modes (Blast and Camp), touches shared events, requires understanding DI wiring, and involves multiple services. This is a realistic cross-cutting feature that an LLM agent would need to research before implementing.

### Results

| # | Developer Question | MCP | Skilled | Naive | MCP vs Skilled |
|---|---|---:|---:|---:|---:|
| 1 | What data comes back from a completed blast level? | 57 | 119 | 120 | 52% |
| 2 | How does the game mode context bridge work? | 93 | 100 | 332 | 7% |
| 3 | What orchestrates game mode transitions? | 122 | 461 | 1,950 | 74% |
| 4 | What events fire when a blast level ends? | 62 | 163 | 390 | 62% |
| 5 | How do I spawn items into the camp grid? | 153 | 1,614 | 2,746 | 91% |
| 6 | How do I find empty cells near a location? | 221 | 353 | 836 | 37% |
| 7 | Where do I wire new camp logic? (entry point) | 54 | 571 | 3,803 | 91% |
| 8 | What shared events bridge camp and blast? | 34 | 196 | 407 | 83% |
| 9 | What's the EventBus API? | 60 | 402 | 476 | 85% |
| 10 | Does a reward-related event or model already exist? | 165 | 474 | 765 | 65% |
| | **TOTAL** | **1,021** | **4,453** | **11,825** | **77%** |

### Highlighted step: Where do I wire new camp logic?

MCP returns 54 tokens — just the class declaration and 2 public methods:

```
Class: CampEntryPoint
Namespace: CampGame.Scopes
Declaration: class CampEntryPoint : IStartable, IDisposable
File: CampGame/Scopes/CampEntryPoint.cs

-- METHODS (2) --
  void Dispose()
  void Start()

Total: 2 members
```

The Skilled Agent reads ~40 lines (571 tokens). The Naive Agent reads the full file — 3,803 tokens of constructor injection, Start() body, Dispose() body, and private fields the agent doesn't need for discovery.

### Key observation

The biggest gaps appear on **class lookups for large files** (steps 3, 5, 7). A 200-line file with 9 public methods contains ~190 lines of implementation the LLM doesn't need. MCP returns only the public surface.

---

## TypeScript — immich (694 files, 8,344 records)

**Feature**: Add user notification when an album is shared via link.

**Why this workflow?** Immich is a self-hosted photo management app. Adding share notifications touches controllers, DTOs, repositories, the notification system, shared link infrastructure, and background jobs. This crosses multiple architectural layers in a real production codebase.

### Results

| # | Developer Question | Tool | MCP | Skilled | Naive |
|---|---|---|---:|---:|---:|
| 1 | How does album sharing work? | `search("album share")` | 170 | 380 | 1,800 |
| 2 | What's the album controller API? | `get_class("AlbumController")` | 300 | 750 | 2,100 |
| 3 | What notification system exists? | `search("notification")` | 145 | 480 | 2,800 |
| 4 | What's the notification controller API? | `get_class("NotificationController")` | 160 | 550 | 1,300 |
| 5 | What DTO carries notification data? | `get_class("NotificationDto")` | 83 | 260 | 900 |
| 6 | How does authentication work? | `search("AuthDto")` | 85 | 380 | 700 |
| 7 | How are shared links created? | `search("SharedLink create")` | 170 | 450 | 2,000 |
| 8 | What's the notification storage layer? | `get_class("NotificationRepository")` | 120 | 550 | 1,000 |
| 9 | What background job system exists? | `search("job queue")` | 133 | 320 | 1,200 |
| 10 | What's the download/retrieval pattern? | `get_class("DownloadRepository")` | 85 | 380 | 750 |
| | **TOTAL** | | **1,451** | **4,500** | **14,550** |

### Highlighted step: What's the album controller API?

MCP returns 300 tokens — 13 method signatures with their decorator patterns:

```
Class: AlbumController
Namespace: server.src.controllers.album.controller
Declaration: class AlbumController
File: server/src/controllers/album.controller.ts:25

-- METHODS (13) --
  addAssetsToAlbum(@Auth() auth: AuthDto, @Param() { id }: UUIDParamDto, @Body() dto: BulkIdsDto,)
  addUsersToAlbum(@Auth() auth: AuthDto, @Param() { id }: UUIDParamDto, @Body() dto: AddUsersDto,)
  constructor(private service: AlbumService)
  createAlbum(@Auth() auth: AuthDto, @Body() dto: CreateAlbumDto): Promise<AlbumResponseDto>
  deleteAlbum(@Auth() auth: AuthDto, @Param() { id }: UUIDParamDto)
  getAlbumInfo(@Auth() auth: AuthDto, @Param() { id }: UUIDParamDto, @Query() dto: AlbumInfoDto,)
  getAlbumStatistics(@Auth() auth: AuthDto): Promise<AlbumStatisticsResponseDto>
  getAllAlbums(@Auth() auth: AuthDto, @Query() query: GetAlbumsDto): Promise<AlbumResponseDto[]>
  removeAssetFromAlbum(...)
  removeUserFromAlbum(...)
  updateAlbumInfo(...)
  updateAlbumUser(...)

Total: 13 members
```

The agent immediately sees: AlbumController injects AlbumService, every endpoint uses `@Auth()`, users are added via `addUsersToAlbum`, and the sharing DTO is `AddUsersDto`. No method bodies, no import statements, no route decorators — pure API surface.

### Key observation

TypeScript controllers with decorators (`@Auth()`, `@Body()`, `@Param()`) are particularly noisy to Grep+Read. Each method is 5-10 lines in the source but 1 line in MCP. The **decorator-heavy** patterns in NestJS-style apps amplify MCP's advantage.

---

## Java — Guava (891 files, 8,377 records)

**Feature**: Build a cache-backed user profile lookup service using Guava's caching library.

**Why this workflow?** Guava is a foundational Java utility library (8,377 indexed records across 891 files). Its Javadoc-heavy source files make Grep+Read particularly expensive — a 500-line file might contain 300 lines of Javadoc. This workflow researches the full caching stack: interfaces, builders, loaders, stats, eviction, and async patterns.

### Results

| # | Developer Question | Tool | MCP | Skilled | Naive |
|---|---|---|---:|---:|---:|
| 1 | What cache types exist? | `search("Cache")` | 200 | 350 | 2,400 |
| 2 | What's the Cache interface? | `get_class("Cache")` | 213 | 600 | 2,400 |
| 3 | How does LoadingCache extend it? | `get_class("LoadingCache")` | 138 | 450 | 1,200 |
| 4 | How do I implement a loader? | `get_class("CacheLoader")` | 175 | 600 | 3,500 |
| 5 | How do I handle eviction callbacks? | `get_class("RemovalListener")` | 75 | 250 | 700 |
| 6 | How do I configure async removal? | `get_signature("asynchronous")` | 75 | 200 | 500 |
| 7 | How do I monitor cache performance? | `get_class("CacheStats")` | 450 | 600 | 2,500 |
| 8 | How do I set cache size limits? | `get_signature("maximumSize")` | 175 | 350 | 5,500 |
| 9 | What input validation exists? | `search("Preconditions check")` | 175 | 400 | 6,000 |
| 10 | What async patterns complement caching? | `search("ListenableFuture")` | 175 | 400 | 2,000 |
| | **TOTAL** | | **1,851** | **4,200** | **26,700** |

### Highlighted step: What input validation exists?

MCP returns 175 tokens — the top 5 Preconditions methods by relevance:

```
Found 5 result(s) for 'Preconditions check':

[METHOD] com.google.common.base.Preconditions.checkArgument(boolean)
  Signature: static void checkArgument(boolean expression)
  File: Preconditions.java:125

[METHOD] com.google.common.base.Preconditions.checkNotNull(T)
  Signature: static T checkNotNull(T reference)
  File: Preconditions.java:954

[METHOD] com.google.common.base.Preconditions.checkArgument(boolean,Object)
  Signature: static void checkArgument(boolean expression, Object errorMessage)
  File: Preconditions.java:139

...
```

The Naive Agent reads the full Preconditions.java — a 1,000+ line file with **80 method overloads** (checkArgument × 26 parameter combos, checkNotNull × 26, checkState × 26, plus index checks). That's 6,000 tokens of nearly identical signatures the LLM must wade through. MCP's BM25 ranking surfaces the 5 most relevant methods.

### Key observation

Java's Javadoc convention makes Naive reads extremely expensive. A 150-line interface can occupy 500 lines in source (3:1 doc-to-code ratio). Guava is worst-case for this — `CacheBuilder.java` has a 190-line class-level Javadoc before the first method. MCP strips all of this, returning only signatures and truncated summaries. The **Javadoc-heavy** pattern amplifies MCP's advantage: MCP vs Naive = **93% fewer tokens**.

---

## Go — gin (38 files, 534 records)

**Feature**: Add JWT authentication middleware to a gin REST API.

**Why this workflow?** gin is a compact but dense framework — 534 records in 38 files. The `Context` struct alone has 128 methods in a single 1,200-line file. This workflow shows how MCP handles "god files" through targeted `get_signature` lookups instead of reading 1,200 lines to find a 2-line method.

### Results

| # | Developer Question | Tool | MCP | Skilled | Naive |
|---|---|---|---:|---:|---:|
| 1 | How is middleware structured? | `search("HandlerFunc middleware")` | 150 | 300 | 3,200 |
| 2 | How do I read request headers? | `get_signature("GetHeader")` | 40 | 120 | 9,600 |
| 3 | How do I reject unauthorized requests? | `get_signature("AbortWithStatusJSON")` | 50 | 150 | — † |
| 4 | How do I store auth data in context? | `get_signature("Context.Set")` | 45 | 200 | — † |
| 5 | How do I chain to the next handler? | `get_signature("Next")` | 38 | 80 | — † |
| 6 | How does route grouping work? | `get_class("RouterGroup")` | 450 | 600 | 1,600 |
| 7 | What error types exist? | `search("Error")` | 130 | 350 | 900 |
| 8 | What built-in middleware exists? | `search("Logger Recovery")` | 68 | 200 | — ‡ |
| 9 | How does the engine start? | `get_class("Engine")` | 780 | 650 | — ‡ |
| 10 | How do I send JSON responses? | `get_signature("Context.JSON")` | 40 | 120 | — † |
| | **TOTAL** | | **1,791** | **2,770** | **15,300** |

† `context.go` (1,200 lines) already loaded in step 2.
‡ `gin.go` (400 lines) already loaded in step 1.

**Naive file reads**: context.go (9,600) + gin.go (3,200) + routergroup.go (1,600) + errors.go (900) = **15,300 tokens** from 4 unique files.

### Highlighted step: How do I read request headers?

MCP returns 40 tokens — a single targeted signature:

```
[METHOD] gin.Context.GetHeader
  Signature: GetHeader(key string) string
  Summary: GetHeader returns value from request headers.
  File: context.go:1088
```

The Skilled Agent greps for `GetHeader` with 3 lines of context — 120 tokens.
The Naive Agent reads all of `context.go` — **9,600 tokens** (128 methods, binding helpers, cookie utilities, query parsing, multipart handling, template rendering, and serialization methods) to find a 2-line function.

This is where MCP's advantage is most dramatic: **40 tokens vs 9,600 tokens** — a **240x reduction**.

### Key observation

gin's `Context` is a **god struct** — 128 methods in one file. This pattern is common in Go (net/http.Request, testing.T, etc.). MCP turns 1,200 lines into targeted 1-line lookups. However, for `get_class("Engine")` (38 members, 780 tokens), MCP returns MORE tokens than a Skilled Agent's targeted 50-line Read (650 tokens). MCP's advantage is strongest on **search and signature lookups**, not on full class dumps of large types.

---

## Python — codesurface (9 files, 40 records)

**Feature**: Add a Rust parser to the codesurface project.

**Why this workflow?** This is a small codebase (9 files, 40 records) — deliberately included to show how MCP scales at the lower end. The scenario is realistic: a contributor needs to understand the parser plugin system, the base class contract, reference implementations, and the registration mechanism.

### Results

| # | Developer Question | Tool | MCP | Skilled | Naive |
|---|---|---|---:|---:|---:|
| 1 | What's the parser base class? | `get_class("BaseParser")` | 75 | 200 | 200 |
| 2 | How does the Go parser work? | `get_class("GoParser")` | 63 | 300 | 1,200 |
| 3 | What parsers already exist? | `search("parse")` | 225 | 200 | 6,200 |
| 4 | How do I register file extensions? | `search("extension")` | 113 | 250 | 400 |
| 5 | How does the TypeScript parser differ? | `get_class("TypeScriptParser")` | 63 | 300 | 1,200 |
| 6 | How is parser selection handled? | `get_signature("get_parser")` | 63 | 150 | — † |
| 7 | How does the Java parser work? | `get_class("JavaParser")` | 63 | 300 | 1,200 |
| 8 | What's the overall architecture? | `get_stats()` | 88 | 300 | — ‡ |
| | **TOTAL** | | **753** | **2,000** | **10,400** |

† `__init__.py` already loaded in step 4. ‡ No file equivalent.

### Highlighted step: What parsers already exist?

MCP returns 225 tokens — a ranked overview of every parser in the project:

```
Found 10 result(s) for 'parse':

[TYPE] codesurface.parsers.python_parser.PythonParser
  Signature: class PythonParser(BaseParser)
  File: codesurface/parsers/python_parser.py:69

[TYPE] codesurface.parsers.go.GoParser
  Signature: class GoParser(BaseParser)
  File: codesurface/parsers/go.py:137

[TYPE] codesurface.parsers.java.JavaParser
  Signature: class JavaParser(BaseParser)
  File: codesurface/parsers/java.py:127

[TYPE] codesurface.parsers.csharp.CSharpParser
  Signature: class CSharpParser(BaseParser)
  File: codesurface/parsers/csharp.py:101

[TYPE] codesurface.parsers.typescript.TypeScriptParser
  Signature: class TypeScriptParser(BaseParser)
  File: codesurface/parsers/typescript.py:145

[METHOD] codesurface.parsers.get_parser(str)
  Signature: get_parser(lang: str) -> BaseParser
  File: codesurface/parsers/__init__.py:22

...
```

One tool call gives the contributor a complete map: all 5 parser classes, their file locations, and the `get_parser()` factory function. A Naive Agent would grep for "Parser" and read each of the 5 parser files (~200 lines each) = 6,200 tokens.

### Key observation

For step 1 (`BaseParser`), both MCP and Skilled return ~200 tokens — the file is only 30 lines, so reading it whole is effectively free. This confirms that **MCP's advantage scales with file size**. The biggest savings come from step 3 (cross-file discovery) and steps 2/5/7 (parser reference files at ~200 lines each).

---

## Cross-Language Analysis

### Token efficiency by language

| Language | MCP Total | Skilled Total | Naive Total | MCP vs Skilled | MCP vs Naive |
|----------|----------:|--------------:|------------:|---------------:|-------------:|
| C# | 1,021 | 4,453 | 11,825 | 4.4x | 11.6x |
| TypeScript | 1,451 | 4,500 | 14,550 | 3.1x | 10.0x |
| Java | 1,851 | 4,200 | 26,700 | 2.3x | 14.4x |
| Go | 1,791 | 2,770 | 15,300 | 1.5x | 8.5x |
| Python | 753 | 2,000 | 10,400 | 2.7x | 13.8x |

### What drives the ratio?

| Factor | Increases MCP advantage | Decreases MCP advantage |
|--------|------------------------|------------------------|
| File size | Large files (100+ lines) → more noise to skip | Small files (< 30 lines) → reading whole file is cheap |
| Javadoc/comments | Heavy documentation in source → inflates Grep+Read | Minimal comments → source is compact |
| Decorator patterns | NestJS/Spring decorators add 3-5x line overhead | Plain function definitions → 1 line per function |
| God files | 100+ members in one file → targeted lookup saves most | 1 class per file → little waste in full read |
| Codebase size | More files → harder for agent to find the right one | Few files → agent already knows where to look |
| Overloaded methods | Java's 80-overload Preconditions → MCP filters by relevance | Unique method names → grep finds exactly 1 match |

### Where MCP wins most

1. **Cross-file discovery** (search tool): Finding which classes/events exist across a large codebase. The agent doesn't know file paths — MCP's FTS5 index finds them in 1 call.
2. **God file navigation** (get_signature tool): Extracting 1 method from a 1,200-line file. MCP returns 40 tokens; Grep+Read returns 120-9,600 tokens.
3. **Large class reference** (get_class tool): Getting 13 method signatures from a 160-line controller. MCP returns the public surface; Grep+Read includes method bodies.

### Where MCP wins least

1. **Small files** (< 30 lines): Reading the whole file is as cheap as the MCP response. Python's `BaseParser` (30 lines) shows 0% advantage.
2. **Large classes with huge docs**: `get_class("Engine")` in gin returns 780 tokens (38 members). A Skilled Agent's targeted 50-line Read returns 650 tokens. MCP loses on this step.
3. **Single well-named classes**: When a grep for `className` returns exactly 1 file and the file is small, the Skilled Agent nearly matches MCP.

---

## Honest Assessment: Where MCP Is Insufficient

MCP returns **public API surface only**. There are cases in every workflow where that's not enough:

### C# — CampEntryPoint (step 7)

MCP returns `Start()` and `Dispose()`. To wire a new controller, the agent needs the constructor (DI parameters) and the body of `Start()` (initialization sequence). **Verdict**: MCP saves discovery, but a follow-up `Read` is required.

### TypeScript — AlbumController (step 2)

MCP shows all 13 methods, but the agent doesn't see the `@Controller('albums')` decorator, route path structure, or HTTP method annotations from the source. To understand URL routing, the agent needs the source. **Verdict**: MCP shows WHAT exists; the source shows HOW it's wired.

### Java — CacheBuilder (not shown individually)

`get_class("CacheBuilder")` returns 23 methods + a massive Javadoc summary (~1,450 tokens). This is actually LARGER than a Skilled Agent's targeted Read. For classes with enormous Javadoc, MCP's class-level tool returns more than necessary. **Verdict**: Use `get_signature` for specific builder methods instead of `get_class` for the whole builder.

### Go — Context (step 2)

If the agent uses `get_class("Context")` instead of targeted `get_signature` calls, the response is 2,500 tokens (128 methods). The Skilled Agent reading 60 lines gets 900 tokens. **Verdict**: For god structs, `get_signature` is better than `get_class`. MCP's advantage depends on using the right tool.

### Python — BaseParser (step 1)

MCP returns 75 tokens. Reading the full 30-line file returns 200 tokens. The gap is 125 tokens — meaningful in aggregate but negligible for a single lookup. **Verdict**: Small codebases benefit less from MCP.

---

## The Realistic Workflow: MCP + Targeted Read

MCP does not eliminate file reading. Across all 5 benchmarks, **~30% of steps require a follow-up Read** after MCP discovery:

| Language | Total Steps | Steps Needing Follow-up Read | Follow-up Token Cost |
|----------|:-----------:|:---------------------------:|--------------------:|
| C# | 10 | 3 | ~1,032 |
| TypeScript | 10 | 2 | ~800 |
| Java | 10 | 2 | ~600 |
| Go | 10 | 2 | ~500 |
| Python | 8 | 1 | ~200 |
| **Total** | **48** | **10** | **~3,132** |

### Hybrid totals

| Workflow | MCP Discovery | Follow-up Reads | Total | vs Skilled | vs Naive |
|---|---:|---:|---:|---:|---:|
| **MCP + Targeted Read** | 6,867 | 3,132 | **9,999** | 44% fewer | 87% fewer |
| Skilled Agent | — | — | **17,923** | — | 77% fewer |
| Naive Agent | — | — | **78,775** | — | — |

Even with follow-up reads, the hybrid approach uses **44% fewer tokens** than the Skilled Agent and **87% fewer** than the Naive Agent.

The key insight: MCP eliminates the **38 exploratory lookups** that don't need implementation detail, and narrows the 10 that do to **targeted reads of known files at known line numbers**.

---

## Context Window Impact

The token savings become critical at smaller context windows:

| Window | MCP+Read % | Skilled % | Naive % | Impact |
|-------:|----------:|---------:|--------:|--------|
| 8K | 125% | 224% | 985% | Only MCP completes the workflow (barely) |
| 32K | 31% | 56% | 246% | Naive agent exhausts context on research alone |
| 128K | 8% | 14% | 62% | Moderate advantage — more room for implementation |
| 200K | 5% | 9% | 39% | Marginal — optimization rather than necessity |

At **8K context** (common for smaller models and tool-use scenarios), only MCP leaves enough headroom for the agent to actually write code after researching. At 200K, all three fit comfortably.

---

## Operational Cost

MCP requires a pre-indexing step that Grep+Read does not:

| Project | Files | Records | Parse Time | Index Time | Total |
|---------|------:|--------:|-----------:|-----------:|------:|
| Unity game (C#) | 129 | 1,034 | 0.024s | 0.019s | **0.043s** |
| gin (Go) | 38 | 534 | <0.1s | <0.1s | **<0.1s** |
| immich (TypeScript) | 694 | 8,344 | 0.6s | <0.1s | **0.6s** |
| guava (Java) | 891 | 8,377 | 2.4s | <0.1s | **2.4s** |
| codesurface (Python) | 9 | 40 | <0.1s | <0.1s | **<0.1s** |

Storage is in-memory SQLite (no disk I/O). The index rebuilds on server restart and updates incrementally via `reindex()` — only changed files are re-parsed.

---

## Beyond Token Counting: Reducing Entropy in Agent Reasoning

The deeper value of a pre-indexed API is not token savings. It is **determinism**.

| Property | MCP | Grep+Read |
|---|---|---|
| **What the LLM sees** | Canonical API surface — every public member, nothing else | Variable — depends on grep pattern, context lines, file structure |
| **Hallucination surface** | Low — signatures are authoritative | Higher — LLM may infer behavior from partial context |
| **Consistency** | Same query always returns same result | Grep results vary with pattern and file ordering |
| **Discovery** | Semantic search across all types (FTS5 + BM25) | Pattern matching on text — misses abbreviations, PascalCase splits |

### Concrete example: inferring behavior from partial context

In the C# benchmark (step 2), the agent needs to know how `ReportLevelCompleted` works.

**Grep+Read agent** reads `GameModeManager.cs` and sees:
```csharp
public void ReportLevelCompleted(LevelResult result)
{
    _currentMode?.OnLevelCompleted(result);
    UnloadGameMode();
}
```
The agent sees `_currentMode?.OnLevelCompleted(result)` and may infer that `OnLevelCompleted` publishes a `LevelCompletedEvent` — which does not exist. The actual event is `LevelWonEvent`, published elsewhere.

**MCP agent** sees only the signature:
```
void ReportLevelCompleted(LevelResult result)
```
No implementation to over-interpret. When the agent needs level-end events, it runs `search("LevelWon")` and gets the correct answer directly.

The critical difference is not tokens. It is **inference chain reliability**. The Grep+Read agent made a plausible wrong inference from true context. The MCP agent never had that gap — the tool's constrained output forced explicit disambiguation.

### Measurable outcomes (not captured here)

- Fewer hallucinated method signatures
- Fewer incorrect parameter types
- Fewer "let me check that file again" round-trips
- Faster convergence to correct implementation

These require A/B testing with real agent task completion, which is a meaningful next step.
