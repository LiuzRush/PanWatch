---
name: panwatch-query-interfaces
description: PanWatch project guide for choosing and using backend query interfaces and external data chains. Use when developing or reviewing PanWatch agents, pages, dashboards, opportunity-pool features, paper-trading features, chat/AI features, or code that needs stock quotes, K-lines, news, announcements, capital flow, discovery, portfolio exchange rates, notifications, model checks, or other external query services.
---

# PanWatch Query Interfaces

## Overview

Use this skill inside the PanWatch repository before adding or changing any feature that queries external market data, news, AI services, notifications, or version/update services. It keeps UI pages, backend agents, and strategy code aligned with the existing API routes, collectors, provider orchestrators, caches, and authentication assumptions.

The complete interface inventory lives in `references/query-interface-guide.md`.

## Required Workflow

1. Read `references/query-interface-guide.md` before choosing a route, collector, or provider.
2. Classify the requested data as direct UI query, business-triggered query, internal aggregation, AI/model query, notification query, or local-only read.
3. Prefer existing FastAPI routes for frontend/page work, existing service classes for backend workflows, and existing collectors/providers for data-source extensions.
4. Check whether the target path already has cache, negative-cache, provider fallback, proxy, market-hours, authentication, or persistence behavior before adding another external call.
5. If a feature needs new data, extend the closest existing collector/provider or route pattern first; only add a new external source when the reference guide shows there is no suitable path.
6. When an interface changes, update both `cognition/查询接口说明文档.md` and `references/query-interface-guide.md` so the skill stays in sync.

## Route Selection Shortcuts

- Market index widgets: use `GET /api/market/indices`.
- Stock search and watchlist quotes: use `GET /api/stocks/search`, `POST /api/stocks/refresh-list`, or `GET /api/stocks/quotes`.
- Realtime quote features: use `GET /api/quotes/{symbol}` or `POST /api/quotes/batch`; backend logic should usually go through `QuoteOrchestrator`.
- K-line and technical summary features: use `/api/klines/*` routes or `KlineCollector`.
- Individual stock capital-flow history: use `GET /api/capital-flow/{symbol}/history` or `CapitalFlowCollector.get_capital_flow_history()`.
- Sector/concept capital-flow features: use `/api/sector-flows/*`, `/api/sectors`, and `/api/sector-flow-groups/*`; backend code should reuse `EastMoneySectorFlowCollector`.
- Sector-flow group defaults live in `config/sector_flow_groups.json`; runtime overrides are stored in `DATA_DIR/sector_flow_groups.json`.
- For sector-flow group editors, use `/api/sector-flow-groups/defaults` to restore templates and `/api/sector-flow-groups/preview` to validate draft rules before saving.
- News, announcements, events, and AI announcement interpretation: use `/api/news`, `NewsCollector`, `EventsCollector`, or the existing `/api/insights/*` flows.
- Discovery and opportunity-pool pages: use `/api/discovery/*` and `/api/recommendations/*`; refreshing entry candidates can also refresh strategy signals.
- Paper-trading operations: use `/api/paper-trading/*` and the existing paper-trading engine; do not bypass its trading calendar, T+1, and market-session checks.
- Agent development: use `SignalPackBuilder` for stock context packs and the existing agent trigger routes under `/api/agents/*`.
- Portfolio features: use `/api/portfolio/*`; these routes can combine quotes, K-lines, AI review, and Sina FX conversion.
- AI/provider checks: use `/api/providers/*` and the configured OpenAI-compatible client flow.
- Notification tests: use `/api/channels/{channel_id}/test` or feature-specific notification routes.
- Update checks: use `GET /api/settings/update-check`.
- Local-only screens: verify the "容易误判但不主动出网的接口" section before assuming an endpoint queries the network.

## Implementation Rules

- Frontend code should call backend routes instead of external market/news domains directly.
- Backend page APIs should return typed, stable payloads that match existing route conventions in `src/web/`.
- Agent and strategy code should reuse collectors and `SignalPackBuilder` instead of duplicating quote, K-line, news, event, or capital-flow fetch logic.
- Quote-sensitive flows should prefer `QuoteOrchestrator` when provider fallback or short TTL cache matters.
- Long-running refreshes should follow existing background-task patterns and avoid blocking page reads when the reference guide has separate read and refresh endpoints.
- New tests should mock collectors, provider clients, AI clients, and notification clients; do not make live network calls in tests.
- Respect authentication notes from the reference guide: most business APIs require login except explicitly public endpoints such as `/api/market/indices`, `/api/health`, and `/api/version`.

## When Reviewing Code

Check for these issues first:

- A frontend component queries Tencent, Eastmoney, Xueqiu, Sina, Docker Hub, or an AI endpoint directly.
- A backend route duplicates an existing collector/provider instead of reusing it.
- A refresh endpoint is used where a local read endpoint would be sufficient.
- A local-only endpoint is described as a live external query.
- Market-hours, T+1, cache, proxy, SSL, or datasource enable/priority behavior is skipped in a trading-related path.
- New interfaces are undocumented in `cognition/查询接口说明文档.md` and this skill's reference copy.

## Reference

Load `references/query-interface-guide.md` for the detailed endpoint list, call chains, external sources, caches, and non-network endpoints.
