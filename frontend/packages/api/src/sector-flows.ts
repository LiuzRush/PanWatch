import { fetchAPI } from './client'

type QueryValue = string | number | boolean | null | undefined

function withQuery(path: string, params: Record<string, QueryValue>): string {
  const q = new URLSearchParams()
  Object.entries(params || {}).forEach(([k, v]) => {
    if (v === undefined || v === null) return
    const sv = String(v).trim()
    if (!sv) return
    q.set(k, sv)
  })
  const s = q.toString()
  return s ? `${path}?${s}` : path
}

export interface SectorFlowContributor {
  code: string
  name: string
  type: 'industry' | 'concept'
  main_net_inflow: number | null
  main_net_inflow_pct: number | null
  turnover: number | null
  change_pct: number | null
}

export interface SectorFlowGroupSummary {
  id?: string
  name: string
  enabled?: boolean
  aliases: string[]
  order: number
  weight: number
  color?: string
  sector_count: number
  main_net_inflow: number
  raw_main_net_inflow?: number
  adjusted_main_net_inflow?: number
  turnover: number
  main_net_inflow_pct: number | null
  raw_main_net_inflow_pct?: number | null
  adjusted_main_net_inflow_pct?: number | null
  contributors: SectorFlowContributor[]
}

export interface SectorFlowGroupSummaryResponse {
  date: string
  source: 'live' | 'snapshot'
  groups: SectorFlowGroupSummary[]
}

export interface SectorFlowSnapshotResponse {
  date: string
  market: string
  created_at: number
  items: unknown[]
}

export interface SectorFlowGroupSelector {
  type: 'any' | 'industry' | 'concept'
  code: string
  name: string
}

export interface SectorFlowGroupRule {
  id?: string
  name: string
  enabled?: boolean
  includes: string[]
  excludes: string[]
  include_items?: SectorFlowGroupSelector[]
  exclude_items?: SectorFlowGroupSelector[]
  aliases: string[]
  weight: number
  order: number
  color?: string
}

export interface SectorFlowGroupsResponse {
  groups: SectorFlowGroupRule[]
}

export interface SectorMetaItem {
  code: string
  name: string
  type: 'industry' | 'concept'
  market: string
  source: string
}

export interface SectorMetaResponse {
  market: string
  type: 'industry' | 'concept'
  keyword: string
  items: SectorMetaItem[]
}

export interface SectorFlowGroupDiagnosticSelector {
  type: 'any' | 'industry' | 'concept'
  code: string
  name: string
}

export interface SectorFlowGroupDiagnostics {
  total_flows: number
  matched_flows: number
  duplicate_members: Array<{
    type: string
    code: string
    name: string
    groups: string[]
  }>
  empty_groups: Array<{
    id?: string
    name: string
    reason: string
  }>
  invalid_selectors: Array<{
    group_id?: string
    group_name: string
    direction: 'include' | 'exclude'
    selector: SectorFlowGroupDiagnosticSelector
    label: string
    reason: string
  }>
}

export interface SectorFlowGroupPreviewResponse extends SectorFlowGroupSummaryResponse {
  diagnostics: SectorFlowGroupDiagnostics
}

export const sectorFlowApi = {
  groupSummary: (params?: { date?: string }) =>
    fetchAPI<SectorFlowGroupSummaryResponse>(
      withQuery('/sector-flow-groups/summary', { date: params?.date })
    ),

  refreshSnapshot: (params?: { date?: string }) =>
    fetchAPI<SectorFlowSnapshotResponse>(
      withQuery('/sector-flows/snapshots/refresh', { date: params?.date }),
      { method: 'POST', timeoutMs: 40000 }
    ),

  getGroups: () => fetchAPI<SectorFlowGroupsResponse>('/sector-flow-groups'),

  getDefaultGroups: () => fetchAPI<SectorFlowGroupsResponse>('/sector-flow-groups/defaults'),

  updateGroups: (groups: SectorFlowGroupRule[]) =>
    fetchAPI<SectorFlowGroupsResponse>('/sector-flow-groups', {
      method: 'PUT',
      body: JSON.stringify({ groups }),
    }),

  previewGroups: (groups: SectorFlowGroupRule[], params?: { date?: string }) =>
    fetchAPI<SectorFlowGroupPreviewResponse>(
      withQuery('/sector-flow-groups/preview', { date: params?.date }),
      {
        method: 'POST',
        body: JSON.stringify({ groups }),
        timeoutMs: 40000,
      }
    ),

  listSectors: (params: { type: 'industry' | 'concept'; keyword?: string; limit?: number }) =>
    fetchAPI<SectorMetaResponse>(
      withQuery('/sectors', { type: params.type, keyword: params.keyword || '', limit: params.limit || 50 })
    ),
}
