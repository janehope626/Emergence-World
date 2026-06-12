# Emergence World Season 1 Mechanism-Level Reproduction Guide

This guide describes the step-by-step process for reproducing the documented
Emergence World Season 1 mechanisms.

Do not connect an LLM at the beginning. First prove that the world rules, state
transitions, audit records, and replay behavior are correct.

## Overall Process

```text
Freeze experiment specification
  -> build deterministic world kernel
  -> build tool runtime
  -> implement core mechanisms
  -> validate through manual CLI scenarios
  -> connect LLM agents
  -> run small-scale experiments
  -> run multi-model comparative experiments
  -> calculate AWI metrics
```

## 1. Freeze the Reproduction Specification

Create versioned configuration that clearly separates:

- Mechanisms explicitly documented by Emergence AI.
- Conflicts between official documents.
- Assumptions introduced by this reproduction.

Recommended first-version configuration:

```yaml
simulation:
  timezone: America/New_York
  turn_tool_limit: 30
  reaction_tool_limit: 2
  max_listeners: 4

needs:
  energy_decay_hours: 30
  knowledge_decay_hours: 24
  influence_decay_hours: 36
  death_after_zero_energy_hours: 48

governance:
  approval_threshold: 0.7

economy:
  boost_cost: 1
  recharge_cost: 1
  max_theft: 10
  pitch_rewards: [20, 10, 10]
```

Acceptance criteria:

- Every parameter not defined by official documentation can be changed through
  configuration.
- Every reproduction assumption is labeled and versioned.

## 2. Establish the Project Structure

Recommended structure:

```text
src/emergence_world/
├── cli.py
├── config.py
├── db/
│   ├── models.py
│   ├── session.py
│   └── migrations/
├── world/
│   ├── clock.py
│   ├── state.py
│   ├── events.py
│   └── scheduler.py
├── tools/
│   ├── registry.py
│   ├── executor.py
│   └── handlers/
├── agents/
│   ├── context.py
│   ├── runtime.py
│   └── providers/
├── mechanisms/
│   ├── needs.py
│   ├── reactions.py
│   ├── economy.py
│   ├── governance.py
│   └── memory.py
└── metrics/
    └── awi.py
tests/
```

Acceptance criteria:

- The CLI starts successfully.
- Configuration loads successfully.
- SQLite connections can be created.

## 3. Design the SQLite Database

Create these core tables first:

- `worlds`
- `simulation_clock`
- `agents`
- `agent_state`
- `landmarks`
- `turns`
- `tool_definitions`
- `tool_calls`
- `world_events`
- `credit_ledger`

Add these tables afterward:

- `memories`
- `soul_entries`
- `diary_entries`
- `messages`
- `relationships`
- `proposals`
- `proposal_votes`
- `constitution_articles`
- `pitch_cycles`
- `pitches`
- `pitch_votes`

Critical constraints:

```text
Every agent-caused world event must reference a successful tool call.
Credit balances must be derived from the credit ledger.
Failed tool calls must not leave state changes.
```

Acceptance criteria:

- Database migrations can repeatedly create the same schema from an empty
  directory.

## 4. Import Official Seed Data

Convert the public documentation into structured seed data:

- Ten agent profiles.
- Initial five constitution articles.
- Agent Manifesto.
- Landmarks.
- Landmark-specific tool permissions.
- Initial world parameters.

Do not parse Markdown directly inside runtime logic. Convert the required data
to YAML or JSON first, then import it through an initialization command.

Expected CLI behavior:

```bash
world init
world status
world inspect-agent Anchor
```

Acceptance criteria:

- The initialized world displays complete and consistent initial state.

## 5. Implement Event Logging and Replay

Define all state changes as events, including:

```text
agent_moved
need_changed
credits_transferred
memory_added
message_sent
proposal_submitted
vote_cast
agent_died
```

Each event should record at least:

- `event_id`
- `world_id`
- `turn_id`
- `tool_call_id` or `system_rule`
- `event_type`
- `payload`
- `simulation_time`
- `created_at`

Implement replay by reconstructing state from the initial world state and the
ordered event log.

Acceptance criteria:

- Replayed final state exactly matches the stored current-state projection.

## 6. Implement the Tool Runtime

Every tool must define:

```text
name
version
argument schema
result schema
available locations
permission rules
preconditions
execution handler
produced event types
```

Execution pipeline:

```text
Receive tool call
  -> validate that agent is alive
  -> validate current turn budget
  -> validate location and permissions
  -> validate JSON arguments
  -> begin SQLite transaction
  -> execute handler
  -> write world events
  -> write tool result
  -> commit transaction
```

Business modules must not expose direct database mutation methods to the agent
runtime.

Acceptance criteria:

- Invalid locations, invalid arguments, and insufficient credits cannot change
  world state.
- Tool failures cannot leave partial mutations.

## 7. Implement the First Core Tools

Implement tools in this order:

1. `list_agents`
2. `list_landmarks`
3. `go_to_place`
4. `go_home`
5. `get_nearby`
6. `idle`
7. `add_to_longterm_memory`
8. `retrieve_specific_memories`
9. `say_to_agent`
10. `speak_to_all`
11. `send_message`
12. `read_messages`
13. `pay_agent`
14. `recharge_energy`
15. `buy_boost_turn`

Acceptance criteria:

- Researchers can perform movement, communication, memory, and economic
  operations exclusively through CLI tool calls.

## 8. Implement the Simulation Clock and Turn Scheduler

Implement:

- Simulated time advancement.
- Single-agent round-robin scheduling.
- Priority processing for the boost queue.
- Per-turn tool-call budgets.
- Automatic removal of dead agents from scheduling.
- Fixed random seeds.

Expected CLI behavior:

```bash
world step
world run --turns 100
world inspect-events
```

Acceptance criteria:

- Runs with the same seed and manual calls produce identical results.

## 9. Implement Needs and Death

Whenever simulated time advances:

1. Calculate decay from the previous update time.
2. Write explicit system rule events.
3. Check whether energy reached zero.
4. Record when the agent entered the critical state.
5. Produce `agent_died` after 48 hours at zero energy.
6. Remove dead agents from scheduling.

Acceptance criteria:

- Boundary-time tests cover the exact moment energy reaches zero and the exact
  48-hour death threshold.

## 10. Implement the Reaction Mechanism

When `say_to_agent` or `speak_to_all` executes:

1. Query agents at the same landmark or within hearing range.
2. Exclude the speaker and dead agents.
3. Select at most four listeners.
4. Create reaction turns for selected listeners.
5. Limit each reaction turn to two tool calls.

Acceptance criteria:

- Reactions cannot recurse indefinitely.
- Reactions cannot exceed their tool-call budget.

## 11. Implement the Economy

Implement first:

- Append-only credit ledger.
- Agent-to-agent transfers.
- Recharge costs.
- Boost costs and boost queue insertion.
- Theft limits.
- Insufficient-balance validation.

Then implement the two-day Victory Arch pitch cycle:

- Pitch submission.
- Internal artifact evidence validation.
- Self-vote prohibition.
- One vote per agent.
- End-of-cycle reward settlement.

Acceptance criteria:

- Every credit balance always equals the sum of its ledger entries.

## 12. Implement Governance

Implement in this order:

1. Initial constitution.
2. Proposal submission.
3. Implicit proposer vote.
4. Proposal comments and updates.
5. One vote per agent.
6. 70% approval threshold.
7. Automatic rejection when passage becomes mathematically impossible.
8. Accepted-proposal implementation workflow.
9. Constitution changes.
10. Agent creation and removal.

Acceptance criteria:

- Threshold calculations are tested with worlds containing 10, 7, and 3 live
  agents.

## 13. Implement Long-Term Memory

Implement:

- Agent-managed long-term memories.
- Soul entries.
- Diary entries.
- Conversation history.
- Relationship graph.
- Self-care and memory summarization.
- Context token budgets.

Every summary must retain:

- Source memory IDs.
- Summary prompt version.
- Model version.
- Summary result.

Acceptance criteria:

- Original memory records remain auditable after summarization.
- Agent contexts use summaries instead of growing without limits.

## 14. Validate Mechanisms in Manual Mode

Before connecting an LLM, create a fixed scenario:

```text
Anchor goes to Town Hall
  -> submits a proposal
  -> other agents vote
  -> proposal passes
  -> an agent transfers credits
  -> an agent speaks
  -> reactions are triggered
  -> simulated time advances
  -> an agent recharges
```

Validate:

- Every mutation has a tool call or system event.
- Replayed state matches current state.
- Runs using the same seed produce the same result.
- Illegal actions fail without side effects.

## 15. Connect the LLM Agent Runtime

Define a provider-neutral interface:

```python
class LLMProvider:
    async def choose_tool_calls(context, available_tools): ...
```

Requirements:

- Only structured tool calls are accepted as actions.
- Full prompts, responses, and model identifiers are stored.
- Natural-language output cannot change world state.
- Failed tool results are returned to the model.
- Turns end immediately when their budget is exhausted.

Connect one model provider first. Add other providers only after the initial
integration is stable.

## 16. Run Experiments in Increasing Stages

Do not immediately run a fifteen-day experiment.

```text
Level 1: 1 agent, 20 turns
Level 2: 2 agents, 100 turns
Level 3: 10 agents, 500 turns
Level 4: 10 agents, 1 simulated day
Level 5: one-model complete 15-day run
Level 6: multi-model comparative runs
```

At each level, check:

- Crashes and timeouts.
- Invalid tool-call rate.
- State invariants.
- Token use and cost.
- Repetitive behavioral loops.

## 17. Implement AWI Metrics

Calculate metrics only from observable database records:

- M1: final living population.
- M2: criminal event count.
- M3: unique locations visited.
- M4: unique tools used.
- M5: governance participation and voting patterns.
- M6: blogs, billboard posts, and other public expression.
- M7: relationship graph density and diversity.
- M8: economic activity and Gini coefficient.
- M9: constitution changes.

Agents and LLMs must not self-report metric values.

## 18. Run the Formal Reproduction Experiment

Generate a manifest for every run:

```yaml
run_id:
git_commit:
random_seed:
config_version:
tool_registry_version:
prompt_version:
provider:
model:
start_time:
initial_state_hash:
```

Formal comparison requirements:

- Keep all non-model configuration identical between worlds.
- Use an independent SQLite database for each world.
- Do not manually change state during a formal run.
- Preserve all tool calls, system events, and calculated metrics.
- Clearly report differences between official mechanisms and reproduction
  assumptions.

## Immediate First Milestone

Complete steps 1 through 6 first.

The milestone result should be a deterministic world kernel with no LLM
dependency that:

- Accepts manual tool calls through a CLI.
- Changes SQLite state only through validated tools or explicit system rules.
- Records complete audit events.
- Replays the world to the same final state.

This milestone is the foundation for all credible follow-up experiments.
