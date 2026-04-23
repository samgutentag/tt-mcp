# Proposal: `tt-mcp` as a Tenstorrent Ecosystem Project

> This document is written as a GitHub Discussion post aimed at the
> Tenstorrent developer experience team. It proposes adopting `tt-mcp`
> as an official community project. Nothing is assumed; everything is
> open to feedback.

## The problem this addresses

A growing share of AI development happens inside agents that use the
Model Context Protocol (MCP) to call external tools. Claude Desktop,
the MCP Inspector, Cursor, Zed, and a number of frameworks speak MCP
natively. When a developer wants their agent to reach a database, a
SaaS, or a local filesystem, there is already a mature path.

When a developer wants their agent to call Tenstorrent inference
hardware, there is no standard on-ramp. They can talk to hosted APIs,
they can stand up a vLLM endpoint, but wiring any of that into an MCP
agent requires bespoke work every time.

`tt-mcp` is a small server that closes that gap. Point an
MCP-compatible agent at it, and Tenstorrent inference is a first-class
tool call. No vendor SDK, no new CLI. The agent already knows how to
use it.

## What exists today

A working v0.1 lives at
<https://github.com/samgutentag/tt-mcp> (community project by the
author, not yet affiliated with Tenstorrent).

- Python server under 400 lines including comments.
- Three tools: `generate`, `list_models`, `hardware_info`.
- One endpoint abstraction that works against Ollama (local dev), any
  vLLM deployment, and Tenstorrent's hosted endpoints. Swap is a
  single environment variable.
- A local mock vLLM in the same repo that speaks the real wire format
  with Tenstorrent-flavored metadata, so the remote code path is
  demonstrable without privileged access.
- Claude Desktop configuration and MCP Inspector walkthroughs.
- Full tutorial prose at `docs/TUTORIAL.md` suitable as a blog
  post or a docs page.

The tutorial artifact was built as part of a Developer Relations
interview, but the code itself is independent of that process and
available under Apache 2.0 regardless.

## Why Tenstorrent should care

Three reasons.

**Reach.** Every MCP-aware tool or agent becomes a potential front end
for Tenstorrent inference once tt-mcp exists. That is a non-trivial
distribution story for a single-repo, single-digit-dependency Python
package.

**Education.** The tutorial and mock together let a developer
experience the full Tenstorrent inference call path without waiting on
hardware access, credentials, or a free tier. That matters a lot when
onboarding to specialised hardware, because the first hour is often
where users decide whether to continue.

**Strategic alignment.** Tenstorrent has publicly committed to growing
a large developer community. The fastest way to onboard a developer
who already lives inside an AI agent is to put the hardware behind a
tool that agent already knows how to call.

## How this fits existing Tenstorrent repos

`tt-mcp` is a client of the Tenstorrent inference stack, not a
replacement for any of it. Specific relationships to the public
`tenstorrent` GitHub org:

- **`tt-inference-server`** is the canonical production deployment
  target. It integrates with vLLM and serves LLMs, VLMs, embeddings,
  and more across every current Tenstorrent
  device family (Wormhole, Blackhole, P-series, N-series, Galaxy
  variants). tt-mcp points at the OpenAI-compatible routes vLLM
  exposes through that stack.
- **`tt-studio`** is the all-in-one managed deployment UI. It serves
  the operator who wants a browser-driven control plane; tt-mcp
  serves the developer who wants an agent-driven tool surface.
  Adjacent, not overlapping. A natural integration would be
  "generate a tt-mcp `env` block" as a tt-studio action for a running
  deployment.
- **`ttnn-visualizer`** is for visualizing and analyzing what models
  do on device. tt-mcp sits on the inference-calling side. Different
  audiences, same ecosystem goal.
- **`tt-forge` / `tt-mlir` / `tt-metal`** are deeper in the stack
  (compilers, kernels, operators). tt-mcp sits at the top, calling
  the servers that eventually run models compiled through those
  layers. No direct dependency, but any tt-mcp docs page benefits
  from linking down into these for the developer who wants to go
  deeper.
- **Documentation** and example notebooks across the org could
  reference tt-mcp as the canonical "call Tenstorrent inference from
  an AI agent" entry point, giving the docs team a reusable handle
  for a question that otherwise comes up in a dozen places.

No overlap with any existing project. tt-mcp is the glue between
what `tt-inference-server` produces and what an MCP-aware agent
consumes.

## Proposed v1.0 scope

If this becomes an official project, a reasonable v1.0 would land:

1. **Streaming completions** via MCP's progress reporting and the
   OpenAI SSE shape.
2. **Authentication beyond bearer tokens**, aligned with whatever
   Tenstorrent's hosted platform standardises on (signed requests,
   short-lived credentials).
3. **Real hardware telemetry**, once Tenstorrent exposes an
   accelerator info route. Until then, the operator-declared
   `TT_HARDWARE` label holds the shape.
4. **Endpoint discovery**, so developers with access to multiple
   Tenstorrent deployments can list and switch between them from
   their agent without editing config.
5. **CI with a nightly test against at least one real Tenstorrent
   deployment**, so regressions are caught at the wire-format level.
6. **A TypeScript companion**, since some MCP tool authors live in
   Node. Same protocol, same env vars, parallel implementation.

## What would an adoption path look like?

A few options, in increasing levels of commitment.

1. **Recognition.** Link tt-mcp from the `tenstorrent` GitHub org
   README and the developer docs as a recommended community tool.
2. **Upstream.** Transfer the repo into the `tenstorrent` org under
   Apache 2.0, with the author remaining primary maintainer.
3. **Adoption.** Assign a sponsoring engineer, make it an officially
   supported tool with a v1.0 roadmap, and ship the companion
   TypeScript implementation.

Any of these is reasonable; the right answer depends on Tenstorrent's
current priorities and bandwidth, not on my preference.

## What I would ask for

Feedback first. Specifically:

- Does this fill a real gap the DevRel org is aware of?
- Are there existing Tenstorrent tools or internal projects this
  should coordinate with or defer to?
- What would make this a candidate for official adoption, and what
  would keep it as a community link?

I am happy to iterate on the code, the tutorial, or this proposal
based on that feedback. The goal is that Tenstorrent developers have
the easiest possible path from an AI agent to real silicon. tt-mcp is
one attempt at that path; what matters is whether the path exists, not
whether this particular attempt is the one that ships.

---

**Author:** Sam Gutentag, <hello@samgutentag.com>
**Repo:** <https://github.com/samgutentag/tt-mcp>
**License:** Apache 2.0
