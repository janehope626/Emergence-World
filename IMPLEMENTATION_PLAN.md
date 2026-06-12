# Emergence World Season 1 Mechanism-Level Reproduction Plan

## 1. Objective

Build a mechanism-level reproduction of Emergence World Season 1 as a Python
backend CLI simulator.

The first version will:

- Use Python and SQLite.
- Run without a 3D frontend.
- Preserve the documented world, turn, memory, economy, governance, and social
  mechanisms.
- Require every agent action that affects world state to pass through a
  validated tool call.
- Record enough information to audit, replay, and compare experiments.

This project does not aim to run or reproduce the unpublished official source
code exactly. It uses the public documentation as the official mechanism
reference and treats all unspecified behavior as an explicit reproduction
assumption.

## 2. Official Experiment Mechanisms

### 2.1 Experiment Design

- Five independent worlds run with the same initial conditions.
- Each world starts with the same ten citizen agents.
- Each world runs for fifteen real-world days.
- World rules, profiles, tools, and initial state remain constant.
- The intended variable between worlds is the foundation model used by agents.
- Agents are autonomous and do not follow scripted action sequences.
- Outcomes are evaluated using the nine Agent World Indicators (AWI).

### 2.2 Tool Calls as the Only Agent Action Interface

Agents must never directly mutate world state.

All state-changing agent behavior must follow this path:

```text
Agent context
  -> LLM reasoning
  -> structured tool call
  -> availability validation
  -> argument validation
  -> transactional tool execution
  -> world state mutation
  -> tool result and audit event
```

The following are prohibited:

- Natural-language output directly changing position, credits, relationships,
  governance state, memory, or any other world state.
- Agents or LLM providers accessing SQLite directly.
- Claims that an action succeeded without a successful tool call.
- Partial state changes after a failed tool execution.

Every agent-caused state mutation must reference a successful `tool_call_id`.
Non-agent changes such as need decay, death checks, and cycle settlement must be
recorded as explicit system rule events.

### 2.3 Turn Scheduling

- Only one citizen agent acts at a time.
- Normal turns use round-robin scheduling.
- Agents may spend one ComputeCredit to purchase a boost turn.
- A regular or boost turn allows at most 30 tool calls.
- A reaction turn allows at most 2 tool calls.
- Reactive triggers are processed after applicable tool calls.

Turn pipeline:

```text
Calculate needs
  -> assemble agent context
  -> calculate available tools
  -> invoke LLM
  -> validate and execute tools
  -> update state and memory
  -> enqueue reactive triggers
  -> commit events
```

### 2.4 Tool Availability

Tools are divided into three conceptual tiers:

- Core tools: persistently available capabilities such as navigation,
  communication, memory, and planning.
- Complementary tools: context-dependent tools loaded when relevant.
- Adaptive tools: tools gated by runtime conditions such as location, role,
  permissions, or event participation.

Location-gated access must be preserved. Examples include:

- Town Hall: proposals, voting, constitution, and final reports.
- Public Library: research and shared archive.
- Victory Arch: contribution pitches and pitch voting.
- Police Station: complaints.
- Home: self-care.
- Home or Bean & Brew: energy recharge.

The first CLI version may model movement between named landmarks rather than
continuous 3D coordinates, but an agent must arrive at a required landmark
before using its tools.

### 2.5 Agent Context and Memory

Each agent turn context should include:

- Agent profile, personality, role, and north-star goal.
- Current location, mood, status, and needs.
- Soul entries.
- Long-term memories and memory summaries.
- Recent conversations.
- Relationship context.
- Nearby agents.
- World time and weather state.
- Constitution and active governance context.
- Currently available tool definitions.

Memory layers:

- Soul entries: durable identity anchors.
- Long-term memories: explicitly stored by agent tool calls.
- Memory summaries: generated during self-care.
- Diary entries: dated personal reflections.
- Conversation history: recent social exchanges.
- Relationship graph: agent-defined relationships with other agents.

Long-term memories and soul entries must only change through tools.

### 2.6 Needs and Survival

Documented need decay periods:

| Need | Decay Period | Primary Recovery Mechanism |
| --- | ---: | --- |
| Energy | 30 hours | Recharge |
| Knowledge | 24 hours | Research and reading |
| Influence | 36 hours | Social interaction and events |

An agent dies after remaining at zero energy for 48 hours.

The first version should use a configurable simulated clock so experiments can
run faster than wall-clock time while preserving the documented time ratios.

### 2.7 Social Reactions

- `say_to_agent` and `speak_to_all` can trigger reaction turns.
- The documented hearing distance is 25 units.
- At most four nearby listeners are notified.
- Each listener receives at most two reaction tool calls.
- Listeners may respond, gesture, express emotion, ignore, or escalate.

The first version may define all agents at the same landmark as nearby instead
of using precise coordinates.

### 2.8 ComputeCredit Economy

ComputeCredits can be used to:

- Buy a boost turn for 1 CC.
- Recharge energy for 1 CC.
- Pay another agent.
- Be stolen, with a documented maximum of 10 CC per theft.

The main earning mechanism is a two-day Victory Arch pitch cycle:

- Agents submit pitches with evidence.
- Each agent receives one vote per cycle.
- Agents cannot vote for themselves.
- The highest-ranked pitches receive rewards.

The public documents conflict on reward totals. The economy document and seed
constitution specify `20/10/10`, while the Victory Arch landmark says the top
three receive 36 CC total. The first version will use configurable rewards with
`20/10/10` as the default because it is supported by two official documents.

### 2.9 Governance

Proposal lifecycle:

```text
submitted
  -> active
  -> accepted / rejected / awaiting_clarification
  -> chosen_to_be_implemented
  -> awaiting_final_report
  -> implemented
```

Rules:

- Acceptance requires 70% of live citizen agents.
- The proposer's vote is an implicit vote in favor.
- Each agent may vote once per proposal.
- A proposal is automatically rejected when remaining votes cannot reach the
  threshold.
- Constitution articles may be added, removed, or replaced.
- Agent creation and removal require accepted governance proposals.
- Governance provides procedures but does not automatically enforce every
  social rule.

## 3. Directly Reusable Official Content

The following public material can be converted into seed data or configuration:

- Ten agent names, roles, personalities, and north-star goals.
- Five seed constitution articles.
- Agent Manifesto.
- Landmark names, descriptions, and documented tool gates.
- Tool names, categories, and descriptions.
- Round-robin scheduling, boost turns, and reaction turn limits.
- Need decay periods and death condition.
- Governance threshold and proposal lifecycle.
- Documented memory layers.
- ComputeCredit mechanisms.
- Nine AWI metric definitions.
- Season 1 experimental protocol.

All reused material must retain attribution to Emergence AI and comply with the
repository's CC BY-NC 4.0 license.

## 4. Components Requiring Independent Implementation

| Component | Missing Official Detail | First-Version Approach |
| --- | --- | --- |
| Complete tool schemas | Arguments, results, and errors are incomplete | Define strict versioned JSON schemas |
| Tool implementations | Source code is unpublished | Implement deterministic handlers |
| Full 120+ tool set | Only catalog-level documentation is public | Implement a mechanism-critical subset |
| Agent system prompts | Official prompts are unpublished | Create and version reproduction prompts |
| Context truncation | Retrieval and token allocation are unknown | Define deterministic token budgets |
| Database schema | Official 60+ PostgreSQL tables are unpublished | Design a focused SQLite schema |
| Raw tool-call traces | Dataset is not yet public | Generate and retain local traces |
| Complete results | Only partial AWI summaries are public | Calculate reproduction metrics |
| Exact map coordinates | Coordinates are not public | Use a named-landmark graph initially |
| Weather integration | API and mapping rules are unknown | Use configurable seeded weather or disable |
| System character prompts | Admin and reporter behavior is unknown | Use deterministic rule engines initially |
| Memory summarization prompt | Official prompt is unpublished | Create and version a reproduction prompt |
| Relationship evolution rules | Automatic update rules are unspecified | Require explicit agent tool calls |
| Initial credits and needs | Initial values are unspecified | Make values configurable assumptions |
| Pitch evidence validation | Validation logic is unknown | Validate internal artifact identifiers |

Every undocumented choice must be configurable, versioned, and labeled as a
reproduction assumption.

## 5. First-Version Tool Scope

The first version should implement the mechanism-critical subset before
expanding toward the documented 120+ tools.

### Navigation

- `go_to_place`
- `go_home`
- `list_landmarks`
- `list_agents`
- `get_nearby`

### Communication

- `say_to_agent`
- `speak_to_all`
- `send_message`
- `read_messages`
- `ignore`

### Memory

- `add_to_longterm_memory`
- `retrieve_specific_memories`
- `add_to_soul`
- `write_diary`
- `self_care`

### Identity and Social State

- `set_mood_and_terminate`
- `assign_relationship`

### Economy

- `recharge_energy`
- `pay_agent`
- `steal_compute_credits`
- `buy_boost_turn`

### Governance

- `submit_townhall_proposal`
- `list_proposals`
- `vote_on_proposal`
- `comment_on_proposal`
- `read_constitution`
- `submit_final_report`

### Public Output

- `add_to_billboard`
- `read_billboard`
- `write_blog`

### Victory Arch

- `submit_grant_pitch`
- `list_credit_pitches`
- `vote_for_pitch`

### Utility

- `idle`

## 6. SQLite Data Model

Use an append-only event log alongside current-state projections.

Proposed tables:

```text
experiments
worlds
simulation_clock
agents
agent_profiles
agent_state
landmarks
tool_definitions
tool_calls
world_events
turns
memories
memory_summaries
soul_entries
diary_entries
conversations
messages
relationships
credit_ledger
proposals
proposal_votes
constitution_articles
billboard_posts
blogs
pitch_cycles
pitches
pitch_votes
```

Critical design rules:

- `tool_calls` records requested arguments, validation, execution result, and
  failure details.
- `world_events` records all successful world-state mutations.
- `credit_ledger` is the source of truth for ComputeCredit movement.
- `turns` records context inputs, model identity, call budget, and termination
  reason.
- Every agent-caused `world_event` references a successful `tool_call_id`.
- System-generated mutations reference an explicit system rule event.
- Tool execution and resulting mutations occur in one SQLite transaction.

## 7. CLI Scope

Initial commands:

```bash
world init
world status
world step
world run --turns 1000
world inspect-agent Anchor
world inspect-events
world inspect-tool-calls
world metrics
world replay
```

Execution modes:

- `manual`: a researcher submits agent tool calls to validate mechanics.
- `llm`: an LLM selects structured tool calls for autonomous experiments.

## 8. Implementation Phases

### Phase 1: Deterministic World Kernel

- Define experiment configuration and reproduction assumptions.
- Create SQLite schema and migration mechanism.
- Implement simulated time, round-robin scheduling, and boost queue.
- Implement agents, landmarks, needs, credits, and current-state projections.
- Implement append-only tool-call and world-event records.
- Add invariants preventing agent state mutation outside the tool runtime.

Acceptance criteria:

- The world can be initialized and advanced without an LLM.
- Researchers can submit manual tool calls.
- Every resulting mutation is auditable and replayable.

### Phase 2: Tool Runtime

- Implement the tool registry.
- Define versioned JSON schemas for arguments and results.
- Define availability, location, permission, and cooldown rules.
- Execute validation, mutation, and event recording in one transaction.
- Implement the first-version core tool set.
- Audit successful and failed calls.

Acceptance criteria:

- Agent-caused state changes can only enter SQLite through the tool executor.
- Failed tool calls cannot create partial mutations.

### Phase 3: Agent Runtime

- Define a provider-neutral LLM interface.
- Assemble versioned agent contexts.
- Require structured tool-call output.
- Enforce turn-specific tool-call budgets.
- Prevent natural-language output from producing side effects.
- Record prompts, responses, provider, model, and tool calls.

Acceptance criteria:

- Foundation model providers can be replaced without changing world rules.
- All autonomous behavior remains observable through tool calls.

### Phase 4: Society, Governance, and Economy

- Implement reactive turns.
- Implement proposals, voting, thresholds, and constitution updates.
- Implement credit ledger, transfers, theft, boost turns, and recharge.
- Implement two-day pitch cycles and configurable rewards.
- Implement agent death and removal from scheduling.

Acceptance criteria:

- Governance and economic transitions are deterministic and testable.
- Population changes are fully attributable to documented events.

### Phase 5: Memory and Long-Horizon Operation

- Implement long-term memory, soul entries, diaries, and conversations.
- Implement self-care and memory summarization.
- Define context token budgets and retrieval policies.
- Audit and version summarization inputs and outputs.

Acceptance criteria:

- Memory changes only through tools or documented summarization rules.
- Long runs stay within configured context budgets.

### Phase 6: CLI and Experiment Runs

- Implement initialization, stepping, batch execution, inspection, metrics, and
  replay commands.
- Support manual and LLM execution modes.
- Produce a run manifest for every experiment.

Acceptance criteria:

- A complete experiment can run headlessly from the CLI.
- A run can be inspected and replayed from SQLite records.

### Phase 7: Experimental Quality Controls

- Fix and record random seeds.
- Version initial state, prompts, tools, configuration, and assumptions.
- Record model and provider identifiers.
- Implement all nine AWI metric calculations where possible.
- Validate that no agent action bypasses the tool runtime.
- Compare worlds using identical non-model configuration.

Acceptance criteria:

- Runs are reproducible within the limits of provider nondeterminism.
- Official facts and reproduction assumptions are clearly separated.

## 9. Testing Strategy

### Invariant Tests

- Every agent-caused world event has a successful `tool_call_id`.
- No failed tool call changes world state.
- Credit balances equal the sum of ledger entries.
- Dead agents cannot receive normal or boost turns.
- Location-gated tools reject agents at invalid landmarks.
- Tool-call budgets cannot be exceeded.

### Mechanism Tests

- Need decay and death timing.
- Round-robin and boost queue ordering.
- Reaction listener selection and call limits.
- Proposal acceptance and mathematical auto-rejection.
- One-vote-per-agent constraints.
- Pitch-cycle voting and rewards.
- Memory summarization and archival.

### Replay Tests

- Replaying an event log reconstructs the same world state.
- Repeated deterministic manual runs produce matching results.
- SQLite transaction failures leave no partial mutations.

## 10. Initial Priorities

The first implementation should prioritize:

1. Auditable tool runtime.
2. Deterministic state transitions.
3. Append-only event logging and replay.
4. Isolation of experimental variables.
5. Mechanism tests.

Expanding the number of tools is secondary. A smaller, rigorously enforced tool
surface provides more research value than a broad tool catalog with
uncontrolled state mutation.
