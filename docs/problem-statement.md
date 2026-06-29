# Problem Statement

## Context

Acme Commerce runs a mid-market e-commerce platform with thousands of daily
support tickets. Each ticket may involve orders, refunds, shipping delays,
account tiers, and policy questions. Human support agents are the primary
operators; customers submit questions through email, chat, and a help portal.

Today, answering a single ticket requires agents to query multiple backends
independently. There is no unified surface that exposes customer data, search,
documentation, and object storage to an AI assistant in a controlled way.

## The Pain

Support agents routinely jump across four separate systems for every ticket:

| System | What agents look up |
|--------|---------------------|
| PostgreSQL | Customer profile, tier, order history, ticket records |
| Elasticsearch | Full-text search over tickets and operational logs |
| Object storage (S3) | Invoices, shipping labels, attachment evidence |
| Vector store | Semantic search over help-centre articles and policy docs |

That is four connections, four query languages, and four mental models per
ticket. Agents context-switch constantly, miss cross-system links (e.g. an
order id buried in a ticket thread), and resolve tickets slowly. New hires
take weeks to learn which system holds which fact.

When teams bolt a generic chatbot on top, the failure modes are predictable:

- The model hallucinates order status, refund eligibility, or policy wording
  because it never retrieved authoritative data.
- The model commits to refunds or exceptions without a human approval gate.
- A single `@tool` demo that returns `"hello world"` does not survive contact
  with production auth, tenancy, rate limits, or downstream outages.

## What We Are Solving

We need a **production-grade MCP server** that exposes Acme's heterogeneous
data layer as one secure, governed tool surface, plus an **agentic copilot**
that helps human agents draft grounded replies faster.

The server must treat every tool call as untrusted input (including
agent-generated payloads), enforce identity and tenant scope, and survive
real-world failure modes: rate spikes, cache misses, circuit trips, and
partial backend outages.

The copilot must:

1. Pull the right data on its own across Postgres, search, storage, and vectors.
2. Draft a reply the human can send or edit with minimal rework.
3. Cite sources so the human can verify claims before sending.
4. Never invent facts, policies, prices, or dates.
5. Never commit to a refund or policy exception without explicit human approval.
6. Stay bounded in time, tokens, and tool calls so cost per ticket is predictable.

## Why Now

Model Context Protocol (MCP) gives a standard wire format for tools, but most
tutorials stop at a local demo. Teams that skip transport hardening, auth,
policy, observability, and governance discover the gaps on the 3 AM pager.

This project exists to capture the **full stack** between "agent wants data"
and "production can answer who called what, for which tenant, with what
outcome" — before writing application code in later milestones.

## Stakeholders Affected

| Stakeholder | How they feel the pain today |
|-------------|------------------------------|
| Tier-1 support agents | Slow lookups, repeated questions to seniors, inconsistent answers |
| Tier-2 / escalation agents | Re-explaining context because prior notes lack linked order data |
| Support managers | No visibility into AI spend, error rates, or approval bypass attempts |
| Platform / SRE | Fear of unconstrained agent HTTP calls, missing audit trails, tenant leaks |
| End customers | Longer resolution times and contradictory answers across channels |

## Problem in One Sentence

**Support agents waste time and risk wrong answers because customer truth is
fragmented across Postgres, Elasticsearch, S3, and a vector store, and no
existing AI integration enforces security, grounding, and cost bounds at
production scale.**
