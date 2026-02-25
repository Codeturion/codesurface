# Evaluating API-Indexed Retrieval vs Textual Search for LLM Agent Codebase Navigation

## Scenario

**Feature**: When a player wins a blast level, spawn reward items in their camp.

**Why this workflow?** It crosses both game modes (Blast and Camp), touches shared events,
requires understanding DI wiring, and involves multiple services. This is a realistic
cross-cutting feature that an LLM agent would need to research before implementing.

**Codebase**: 1018 API records from 129 files

## Methodology

Three agent strategies are compared for the same 10-step research workflow:

| Strategy | Description |
|---|---|
| **MCP** | Pre-indexed API server. Returns only public signatures, pre-ranked. 1 tool call per lookup. |
| **Skilled Agent** | Targeted Grep + partial Read. Uses `Grep -C 3` for signatures, `Read` with offset/limit (~40 lines) for classes, `Grep -C 0` for searches. Assumes the agent already knows file paths or finds them efficiently. |
| **Naive Agent** | Grep to find file → Read the entire file. Includes all imports, private fields, method bodies, comments. This is worst-case but not uncommon for agents unfamiliar with a codebase. |

**Token estimate**: `len(text) / 4` (standard approximation for code).

**Important caveats**:
- The Skilled Agent numbers assume optimal tool usage — real agents vary between Skilled and Naive depending on context and codebase familiarity.
- Grep simulations use case-insensitive substring matching (similar to ripgrep). In practice, an agent might need multiple grep attempts to find the right file.
- MCP returns are pure signal (public API only). Grep+Read returns include noise the LLM must mentally filter, which has a cognitive cost beyond raw token count.
- For small files (< 30 lines), all three approaches converge — the advantage grows with file size and codebase complexity.

## Results Summary

| # | Developer Question | MCP | Skilled | Naive | MCP vs Skilled | MCP vs Naive |
|---|---|---:|---:|---:|---:|---:|
| 1 | What data comes back from a completed blast level? | 57 | 119 | 120 | 52% | 52% |
| 2 | How does the game mode context bridge work? | 93 | 100 | 332 | 7% | 72% |
| 3 | What orchestrates game mode transitions? | 122 | 461 | 1,950 | 74% | 94% |
| 4 | What events fire when a blast level ends? | 62 | 163 | 390 | 62% | 84% |
| 5 | How do I spawn items into the camp grid? | 153 | 1,614 | 2,746 | 91% | 94% |
| 6 | How do I find empty cells near a location? | 221 | 353 | 836 | 37% | 74% |
| 7 | Where do I wire new camp logic? (entry point) | 54 | 571 | 3,803 | 91% | 99% |
| 8 | What shared events bridge camp and blast? | 34 | 196 | 407 | 83% | 92% |
| 9 | What's the EventBus API? | 60 | 402 | 476 | 85% | 87% |
| 10 | Does a reward-related event or model already exist? | 165 | 474 | 765 | 65% | 78% |
| | **TOTAL** | **1,021** | **4,453** | **11,825** | **77%** | **91%** |

## Cumulative Token Usage

Tokens accumulated across the 10-step research workflow (lower is better):

```
Step       MCP  Skilled   Naive
────── ─────── ──────── ───────   ────────────────────────────────────────────────────────────
  1         57      119     120   █ MCP
                                  ▓ Skilled
                                  ░ Naive
  2        150      219     452   █ MCP
                                  ▓ Skilled
                                  ░░ Naive
  3        272      680   2,402   █ MCP
                                  ▓▓▓ Skilled
                                  ░░░░░░░░░░░░ Naive
  4        334      843   2,792   █ MCP
                                  ▓▓▓▓ Skilled
                                  ░░░░░░░░░░░░░░ Naive
  5        487    2,457   5,538   ██ MCP
                                  ▓▓▓▓▓▓▓▓▓▓▓▓ Skilled
                                  ░░░░░░░░░░░░░░░░░░░░░░░░░░░░ Naive
  6        708    2,810   6,374   ███ MCP
                                  ▓▓▓▓▓▓▓▓▓▓▓▓▓▓ Skilled
                                  ░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░ Naive
  7        762    3,381  10,177   ███ MCP
                                  ▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓ Skilled
                                  ░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░ Naive
  8        796    3,577  10,584   ████ MCP
                                  ▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓ Skilled
                                  ░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░ Naive
  9        856    3,979  11,060   ████ MCP
                                  ▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓ Skilled
                                  ░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░ Naive
  10     1,021    4,453  11,825   █████ MCP
                                  ▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓ Skilled
                                  ░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░ Naive
```

## Step-by-Step Detail

### Step 1: What data comes back from a completed blast level?

| Approach | Tokens | Method |
|---|---:|---|
| MCP | **57** | `get_class` |
| Skilled | 119 | Grep → Read ~40 lines |
| Naive | 120 | Grep → Read full file |

**MCP vs Skilled: 52% fewer tokens**

MCP response:
```
Class: LevelResult
Namespace: GenericMerge.GameModes
Declaration: struct LevelResult
File: GameModes/Core/LevelResult.cs

-- FIELDS (4) --
  bool Completed
  List<RewardItem> Rewards
  int Score
  int StarsEarned

Total: 4 members
```

### Step 2: How does the game mode context bridge work?

| Approach | Tokens | Method |
|---|---:|---|
| MCP | **93** | `get_class` |
| Skilled | 100 | Grep → Read ~40 lines |
| Naive | 332 | Grep → Read full file |

**MCP vs Skilled: 7% fewer tokens**

MCP response:
```
Class: IGameModeContext
Namespace: GenericMerge.GameModes
Declaration: interface IGameModeContext
File: GameModes/Core/IGameModeContext.cs

-- METHODS (5) --
  bool ConsumePowerup(string powerupId)
  IReadOnlyList<PowerupModel> GetAvailablePowerups()
  void ReportLevelAbandoned()
  void ReportLevelCompleted(LevelResult result)
  void ReportLevelFailed()

Total: 5 members
```

### Step 3: What orchestrates game mode transitions?

| Approach | Tokens | Method |
|---|---:|---|
| MCP | **122** | `get_class` |
| Skilled | 461 | Grep → Read ~40 lines |
| Naive | 1,950 | Grep → Read full file |

**MCP vs Skilled: 74% fewer tokens**

MCP response:
```
Class: GameModeManager
Namespace: GenericMerge.Scopes
Declaration: class GameModeManager : IDisposable
File: Scopes/GameModeManager.cs

-- METHODS (8) --
  bool ConsumePowerup(string powerupId)
  void Dispose()
  IReadOnlyList<PowerupModel> GetAvailablePowerups()
  void LoadGameMode(LevelConfig config)
  void ReportLevelAbandoned()
  void ReportLevelCompleted(LevelResult result)
  void ReportLevelFailed()
  void UnloadGameMode()

-- FIELDS (1) --
  bool IsGameModeActive

Total: 9 members
```

### Step 4: What events fire when a blast level ends?

| Approach | Tokens | Method |
|---|---:|---|
| MCP | **62** | `search` |
| Skilled | 163 | Grep project-wide (ctx 0) |
| Naive | 390 | Grep project-wide (ctx 2) |

**MCP vs Skilled: 62% fewer tokens**

MCP response:
```
Found 3 result(s):

[TYPE] BlastGame.Events.LevelWonEvent
  Signature: struct LevelWonEvent : IEvent

[FIELD] BlastGame.Events.LevelWonEvent.Score
  Signature: int Score

[FIELD] BlastGame.Events.LevelWonEvent.StarsEarned
  Signature: int StarsEarned
```

### Step 5: How do I spawn items into the camp grid?

| Approach | Tokens | Method |
|---|---:|---|
| MCP | **153** | `get_signature` |
| Skilled | 1,614 | Grep (ctx 3) only |
| Naive | 2,746 | Grep (ctx 5) + Read chunk |

**MCP vs Skilled: 91% fewer tokens**

MCP response:
```
[FIELD] CampGame.Config.TierEntry.SpawnItem
  Signature: string SpawnItem
  Namespace: CampGame.Config
  File: CampGame/Config/MergeChainConfig.cs

[METHOD] CampGame.Services.CampGridService.SpawnItem(string,int,GridCoord)
  Signature: CampItemModel SpawnItem(string chainId, int tier, GridCoord coord)
  Namespace: CampGame.Services
  File: CampGame/Services/CampGridService.cs

[METHOD] CampGame.Services.ICampGridService.SpawnItem(string,int,GridCoord)
  Signature: CampItemModel SpawnItem(string chainId, int tier, GridCoord coord)
  Namespace: CampGame.Services
  File: CampGame/Services/ICampGridService.cs
```

### Step 6: How do I find empty cells near a location?

| Approach | Tokens | Method |
|---|---:|---|
| MCP | **221** | `get_signature` |
| Skilled | 353 | Grep (ctx 3) only |
| Naive | 836 | Grep (ctx 5) + Read chunk |

**MCP vs Skilled: 37% fewer tokens**

MCP response:
```
[METHOD] CampGame.Models.CampGridModel.FindEmptyNeighbors(GridCoord,int,HashSet<GridCoord> exclude =)
  Signature: List<GridCoord> FindEmptyNeighbors(GridCoord center, int maxCount, HashSet<GridCoord> exclude = null)
  Namespace: CampGame.Models
  File: CampGame/Models/CampGridModel.cs

[METHOD] CampGame.Services.CampGridService.FindEmptyNeighbors(GridCoord,int,HashSet<GridCoord> exclude =)
  Signature: List<GridCoord> FindEmptyNeighbors(GridCoord center, int maxCount, HashSet<GridCoord> exclude = null)
  Namespace: CampGame.Services
  File: CampGame/Services/CampGridService.cs

[METHOD] CampGame.Services.ICampGridService.FindEmptyNeighbors(GridCoord,int,HashSet<GridCoord> exclude =)
  Signature: List<GridCoord> FindEmptyNeighbors(GridCoord center, int maxCount, HashSet<GridCoord> exclude = null)
  Namespace: CampGame.Services
  File: CampGame/Services/ICampGridService.cs
```

### Step 7: Where do I wire new camp logic? (entry point)

| Approach | Tokens | Method |
|---|---:|---|
| MCP | **54** | `get_class` |
| Skilled | 571 | Grep → Read ~40 lines |
| Naive | 3,803 | Grep → Read full file |

**MCP vs Skilled: 91% fewer tokens**

MCP response:
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

### Step 8: What shared events bridge camp and blast?

| Approach | Tokens | Method |
|---|---:|---|
| MCP | **34** | `search` |
| Skilled | 196 | Grep project-wide (ctx 0) |
| Naive | 407 | Grep project-wide (ctx 2) |

**MCP vs Skilled: 83% fewer tokens**

MCP response:
```
Found 1 result(s):

[TYPE] GenericMerge.Shared.Events.ReturnToCampRequestedEvent
  Signature: struct ReturnToCampRequestedEvent : IEvent
```

### Step 9: What's the EventBus API?

| Approach | Tokens | Method |
|---|---:|---|
| MCP | **60** | `get_class` |
| Skilled | 402 | Grep → Read ~40 lines |
| Naive | 476 | Grep → Read full file |

**MCP vs Skilled: 85% fewer tokens**

MCP response:
```
Class: EventBus
Namespace: GenericMerge.Core.Events
Declaration: class EventBus
File: Core/Events/EventBus.cs

-- METHODS (3) --
  void Publish(T evt)
  void Subscribe(Action<T> handler)
  void Unsubscribe(Action<T> handler)

Total: 3 members
```

### Step 10: Does a reward-related event or model already exist?

| Approach | Tokens | Method |
|---|---:|---|
| MCP | **165** | `search` |
| Skilled | 474 | Grep project-wide (ctx 0) |
| Naive | 765 | Grep project-wide (ctx 1) |

**MCP vs Skilled: 65% fewer tokens**

MCP response:
```
Found 8 result(s):

[TYPE] BlastGame.Models.RewardData
  Signature: struct RewardData

[TYPE] GenericMerge.GameModes.RewardItem
  Signature: struct RewardItem

[FIELD] BlastGame.Models.BlastLevelData.Rewards
  Signature: RewardData[] Rewards

[FIELD] GenericMerge.GameModes.LevelResult.Rewards
  Signature: List<RewardItem> Rewards

[FIELD] BlastGame.Services.LevelDataParser.rewards
  Signature: RawReward[] rewards

[TYPE] GenericMerge.Shared.Models.OrderReward
  Signature: class OrderReward

... (5 more lines)
```

## Analysis

### Token totals

| Metric | MCP | Skilled Agent | Naive Agent |
|---|---:|---:|---:|
| Total tokens | 1,021 | 4,453 | 11,825 |
| vs MCP | — | 4.4x | 11.6x |
| Tool calls | 10 | ~20 | ~20 |

### Where MCP wins most

The biggest gaps appear on **class lookups for large files** (steps 3, 5, 7).
A 200-line file with 9 public methods contains ~190 lines of implementation the LLM doesn't need.
Even a skilled agent reading ~40 lines still includes private fields, attributes, and partial method bodies.
MCP returns only the public method signatures.

### Where MCP wins least

The gap narrows on **small files** (step 2: IGameModeContext is a short interface, MCP vs Skilled = 7%) and
**tight signature lookups** (step 6: 37%) where a skilled agent's `Grep -C 3` already captures the signature.
For a 5-field struct or a single-method interface, reading the whole file is nearly as cheap as the MCP response.

## Where MCP Is Insufficient

MCP returns **public API surface only**. There are cases in this workflow where that is not enough:

### Step 7: CampEntryPoint — signature hides the wiring pattern

MCP returns `Start()` and `Dispose()`. But to wire a new controller, the agent needs to see:
- Constructor parameters (what dependencies are injected)
- The body of `Start()` (the initialization sequence: which controllers are initialized in what order)
- The body of `Dispose()` (every controller must be disposed here or EventBus leaks)

**Verdict**: MCP saves the discovery step ("what class do I need?"), but the agent will still need
to `Read` the file before writing code. The realistic flow is MCP → Read, not MCP alone.

### Step 3: GameModeManager — transition logic is in the implementation

MCP shows `void LoadGameMode(LevelConfig config)`. But understanding the transition flow
(scene loading, scope creation, event wiring) requires the method body.
An agent that only sees the signature might not know that `LoadGameMode` triggers an async scene load.

### Step 9: EventBus — generic signatures lack behavioral context

MCP shows `Subscribe<T>(Action<T>)`, `Publish<T>(T)`, `Unsubscribe<T>(Action<T>)`.
This is correct and sufficient for usage. But if the agent needs to know execution order,
thread safety, or re-entrancy behavior, the implementation is required.

### Honest assessment

For this 10-step workflow, **~3 steps require a follow-up Read** after MCP discovery.
MCP does not eliminate file reading — it reduces it to targeted reads of files you already know you need,
rather than exploratory reads to figure out what exists.

## The Realistic Workflow: MCP + Targeted Read

Given the limitations above, the honest comparison is not MCP vs Read, but **MCP+Read vs Read-only**:

### Follow-up read breakdown

| Step | File | Why MCP is insufficient | Lines read | Tokens |
|---:|---|---|---:|---:|
| 3 | `GameModeManager.cs` | Transition logic in method bodies | ~40 | 461 |
| 7 | `CampEntryPoint.cs` | Constructor DI + Start()/Dispose() bodies | ~40 | 571 |
| | | **Total follow-up cost** | | **1,032** |

### Hybrid totals

| Workflow | MCP Discovery | Follow-up Reads | Total |
|---|---:|---:|---:|
| MCP + Targeted Read | 1,021 | 1,032 | **2,053** |
| Skilled Agent (Read-only) | — | — | **4,453** |
| Naive Agent (Read-only) | — | — | **11,825** |

Even with follow-up reads, the hybrid approach uses **54% fewer tokens** than the Skilled Agent.
The key insight: MCP eliminates the **8 exploratory lookups** that don't need implementation detail,
and narrows the 2 that do to **targeted reads of known files**.

## Operational Cost of MCP

MCP is not free. It requires a pre-indexing step that Grep+Read does not:

| Metric | Value |
|---|---|
| C# files scanned | 129 |
| API records indexed | 1,019 |
| Parse time | 0.024s |
| DB build time | 0.019s |
| **Total index time** | **0.043s** |
| Storage | In-memory SQLite (no disk I/O) |
| Rebuild trigger | MCP server restart (session start) |

**Staleness risk**: The index is built at MCP server startup and is not updated mid-session.
If the agent creates new classes or renames methods during a session, the index is stale.
Mitigation: the agent can always fall back to Grep+Read for code it just wrote.

**Scaling estimate**: Indexing is ~O(n) in file count. At this codebase size (129 files),
it takes 0.043s. A 10x larger codebase (~1,300 files) would take ~0.4s.

## Context Window Scaling

The token savings become more or less critical depending on context window size.
This table shows the same 10-step workflow at different window sizes:

| Window | MCP % used | Skilled % used | Naive % used | MCP headroom advantage |
|---:|---:|---:|---:|---|
| 8K | 12.8% | 55.7% | 147.8% | Naive agent risks exhausting context |
| 32K | 3.2% | 13.9% | 37.0% | Moderate advantage |
| 128K | 0.8% | 3.5% | 9.2% | Marginal at this window size |
| 200K | 0.5% | 2.2% | 5.9% | Marginal at this window size |

At **8K context** (common for smaller models), the Naive Agent spends almost **150% of its budget** on research alone —
it cannot complete this workflow. The Skilled Agent uses 56%. MCP uses 13%.
At **200K context**, all three fit comfortably, and the advantage is optimization rather than necessity.

## Beyond Token Counting: Reducing Entropy in Agent Reasoning

The deeper value of a pre-indexed API is not token savings. It is **determinism**.

| Property | MCP | Grep+Read |
|---|---|---|
| **What the LLM sees** | Canonical API surface — every public member, nothing else | Variable — depends on grep pattern, context window, file structure |
| **Hallucination surface** | Low — signatures are authoritative | Higher — LLM may infer from partial context, wrong file, or stale grep match |
| **Consistency** | Same query always returns same result | Grep results vary with pattern, context lines, file ordering |
| **Discovery capability** | Semantic search across all types | Pattern matching on text — misses PascalCase components, abbreviations |
| **Parallelism** | All 10 lookups can run in parallel | Sequential: grep result informs which file to read |

When an LLM reads 40 lines of source that include `private readonly EventBus _eventBus;`,
`[Inject]` attributes, `#region` blocks, and helper methods, it must decide what is API surface
and what is implementation noise. That decision is a source of reasoning error.

### Concrete example: inferring behavior from partial context

Consider step 2: the agent needs to know how `IGameModeContext.ReportLevelCompleted` works.

**Grep+Read agent** reads `GameModeManager.cs` (the implementation) and sees:
```csharp
public void ReportLevelCompleted(LevelResult result)
{
    _currentMode?.OnLevelCompleted(result);
    UnloadGameMode();
}
```
The agent sees `_currentMode?.OnLevelCompleted(result)` and may reasonably infer that
`OnLevelCompleted` publishes an event internally (since the codebase is event-driven).
It might then write code that subscribes to a `LevelCompletedEvent` — which does not exist.
The actual event is `LevelWonEvent`, published by `BlastGameController`, not by the game mode context.
The agent hallucinated the event name and its source from plausible-looking implementation detail.

**MCP agent** sees the same method as a signature only:
```
void ReportLevelCompleted(LevelResult result)
```
No implementation body to over-interpret. The agent knows the method exists and what it accepts,
but it cannot infer internal event publishing from a signature. When it needs to find level-end events,
it runs `search("LevelWon")` and gets the authoritative answer: `LevelWonEvent` in `BlastGame.Events`.
The disambiguation is forced by the tool's limited output, not by the agent's reasoning.

### Reasoning trace comparison

The following traces reconstruct how each agent strategy reasons through the same
sub-task: *"How do blast level results reach the camp?"* (spans steps 2-4).
Each row shows what the agent sees, what it infers, what it does next, and where the
inference chain diverges.

#### Phase 1: Discover the bridge API

| | Grep+Read Agent | MCP Agent |
|---|---|---|
| **Action** | `Grep "IGameModeContext"` → Read `GameModeManager.cs` (~40 lines from class decl) | `get_class("IGameModeContext")` |
| **Sees** | `class GameModeManager : IDisposable` with fields: `private readonly EventBus _eventBus;`, `private IGameMode _currentMode;`, `private LifetimeScope _modeScope;`. Method body: `ReportLevelCompleted` calls `_currentMode?.OnLevelCompleted(result)` then `UnloadGameMode()` | `interface IGameModeContext` with 5 method signatures. No fields, no bodies, no implementation |
| **Infers** | "GameModeManager owns the EventBus. It delegates to `_currentMode.OnLevelCompleted`. The mode probably publishes a completion event internally. I should look for `LevelCompletedEvent`." | "IGameModeContext has `ReportLevelCompleted(LevelResult)`. I don't know what happens inside. I need to find what events fire on level end." |
| **Risk** | Over-interprets implementation — infers event publishing that doesn't exist | No implementation to over-interpret — forced to search explicitly |

#### Phase 2: Find level-end events

| | Grep+Read Agent | MCP Agent |
|---|---|---|
| **Action** | `Grep "LevelCompletedEvent"` — 0 results. Tries `Grep "LevelCompleted"` — finds method definitions (not events). Tries `Grep "LevelWon"` — finds it. | `search("LevelWon")` — 3 results |
| **Sees** | After 2-3 grep attempts: `struct LevelWonEvent : IEvent` in BlastEvents.cs | Immediately: `LevelWonEvent` in `BlastGame.Events` with fields `Score`, `StarsEarned` |
| **Cost** | 3 grep calls + reading through false positives. ~500 tokens of noise before finding the answer | 1 tool call. 62 tokens. Direct hit |
| **Risk** | May abandon search after first failed grep and *assume* the event exists with a guessed name | Search is exhaustive — if it's not in the index, it doesn't exist |

#### Phase 3: Wire the subscription

| | Grep+Read Agent | MCP Agent |
|---|---|---|
| **Action** | `Grep "class EventBus"` → Read full file (476 tokens) | `get_class("EventBus")` — 60 tokens |
| **Sees** | Full EventBus.cs: generic `Subscribe<T>`, `Publish<T>`, `Unsubscribe<T>` + private `Dictionary<Type, List<Delegate>>` + locking logic + helper methods | 3 method signatures: `Subscribe<T>(Action<T>)`, `Publish<T>(T)`, `Unsubscribe<T>(Action<T>)` |
| **Writes** | `_eventBus.Subscribe<LevelWonEvent>(OnLevelWon)` — correct, but arrived here after 2-3 false starts on event name | `_eventBus.Subscribe<LevelWonEvent>(OnLevelWon)` — correct, arrived directly |
| **Risk** | May copy internal locking pattern or private field naming into new code (cargo-culting from implementation) | Cannot cargo-cult — only sees API contract |

#### Divergence summary

| Metric | Grep+Read Agent | MCP Agent |
|---|---|---|
| Tool calls for this sub-task | 5-7 (grep attempts + reads) | 3 (one per phase) |
| Tokens consumed | ~1,100-1,500 | ~215 |
| Wrong inferences made | 1 (hallucinated `LevelCompletedEvent`) | 0 |
| Recovery cost | 2 extra grep calls to find correct event name | None — correct on first lookup |
| Implementation noise absorbed | Private fields, method bodies, locking internals | Zero — signatures only |

The critical difference is not tokens. It is **inference chain reliability**.
The Grep+Read agent made a *plausible* wrong inference from *true* context.
That is the hardest class of error to detect and correct — the reasoning looks sound,
but the conclusion is wrong because the agent filled a gap with pattern-matching
instead of lookup.

The MCP agent never had that gap. The tool's constrained output forced explicit
disambiguation at every step. The agent could not *infer* event names — it had to *find* them.

This is not measurable in tokens. It is measurable in:
- Fewer hallucinated method signatures
- Fewer incorrect parameter types
- Fewer "let me check that file again" round-trips
- Faster convergence to correct implementation

These outcome metrics are not captured by this benchmark. They require A/B testing
with real agent task completion, which is a meaningful next step.

