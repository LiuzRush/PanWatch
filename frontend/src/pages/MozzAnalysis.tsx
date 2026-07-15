import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  AlertTriangle,
  ArrowDownRight,
  ArrowUpRight,
  BarChart3,
  Clock3,
  Database,
  Eye,
  GitBranch,
  Plus,
  RefreshCw,
  RotateCcw,
  Save,
  Search,
  Settings2,
  Trash2,
  X,
} from 'lucide-react'
import {
  sectorFlowApi,
  type SectorFlowGroupDiagnostics,
  type SectorFlowGroupRule,
  type SectorFlowGroupSelector,
  type SectorFlowGroupSummaryResponse,
  type SectorMetaItem,
} from '@panwatch/api'
import { Button } from '@panwatch/base-ui/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@panwatch/base-ui/components/ui/dialog'
import { Input } from '@panwatch/base-ui/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@panwatch/base-ui/components/ui/select'
import { Switch } from '@panwatch/base-ui/components/ui/switch'

const REFRESH_INTERVAL_OPTIONS = [1, 3, 5, 10] as const
type RefreshIntervalMinute = typeof REFRESH_INTERVAL_OPTIONS[number]
type SelectorDirection = 'include' | 'exclude'
type SelectorKind = 'industry' | 'concept'

const GROUP_COLORS = ['#4f46e5', '#0891b2', '#dc2626', '#16a34a', '#ca8a04', '#9333ea', '#0f766e']

function readRefreshInterval(): RefreshIntervalMinute {
  const raw = Number(localStorage.getItem('panwatch_mozz_flow_refresh_min') || 5)
  return REFRESH_INTERVAL_OPTIONS.includes(raw as RefreshIntervalMinute)
    ? raw as RefreshIntervalMinute
    : 5
}

function todayString(): string {
  const d = new Date()
  const mm = String(d.getMonth() + 1).padStart(2, '0')
  const dd = String(d.getDate()).padStart(2, '0')
  return `${d.getFullYear()}-${mm}-${dd}`
}

function money(v?: number | null): string {
  if (v == null || !Number.isFinite(v)) return '--'
  const abs = Math.abs(v)
  const sign = v > 0 ? '+' : v < 0 ? '-' : ''
  if (abs >= 1e8) return `${sign}${(abs / 1e8).toFixed(2)}亿`
  if (abs >= 1e4) return `${sign}${(abs / 1e4).toFixed(0)}万`
  return `${sign}${abs.toFixed(0)}`
}

function pct(v?: number | null): string {
  if (v == null || !Number.isFinite(v)) return '--'
  return `${v > 0 ? '+' : ''}${v.toFixed(2)}%`
}

function flowColor(v?: number | null): string {
  if (v == null || v === 0) return 'text-muted-foreground'
  return v > 0 ? 'text-rose-500' : 'text-emerald-500'
}

function flowTone(v?: number | null): string {
  if (v == null || v === 0) return 'bg-muted-foreground/40'
  return v > 0 ? 'bg-rose-500' : 'bg-emerald-500'
}

function sourceText(source?: string): string {
  return source === 'snapshot' ? '快照' : '实时'
}

function selectorToken(item: SectorFlowGroupSelector): string {
  return (item.code || item.name || '').trim()
}

function selectorText(item: SectorFlowGroupSelector): string {
  const code = item.code?.trim()
  const name = item.name?.trim()
  if (code && name) return `${name} · ${code}`
  return name || code || '--'
}

function normalizeSelector(raw: unknown): SectorFlowGroupSelector | null {
  if (typeof raw === 'string') {
    const token = raw.trim()
    return token ? { type: 'any', code: '', name: token } : null
  }
  if (!raw || typeof raw !== 'object') return null
  const source = raw as Partial<SectorFlowGroupSelector>
  const type = source.type === 'industry' || source.type === 'concept' ? source.type : 'any'
  const code = String(source.code || '').trim()
  const name = String(source.name || '').trim()
  if (!code && !name) return null
  return { type, code, name }
}

function normalizeSelectors(items?: unknown[], fallback?: string[]): SectorFlowGroupSelector[] {
  const source = items && items.length ? items : fallback
  const out: SectorFlowGroupSelector[] = []
  const seen = new Set<string>()
  ;(source || []).forEach((raw) => {
    const item = normalizeSelector(raw)
    if (!item) return
    const key = `${item.type}:${item.code.toLowerCase()}:${item.name.toLowerCase()}`
    if (seen.has(key)) return
    seen.add(key)
    out.push(item)
  })
  return out
}

function normalizeRule(rule: SectorFlowGroupRule, idx: number): SectorFlowGroupRule {
  const includeItems = normalizeSelectors(rule.include_items, rule.includes)
  const excludeItems = normalizeSelectors(rule.exclude_items, rule.excludes)
  return {
    id: rule.id || `group-${Date.now().toString(36)}-${idx}`,
    name: rule.name || `未命名聚类${idx + 1}`,
    enabled: rule.enabled !== false,
    includes: includeItems.map(selectorToken).filter(Boolean),
    excludes: excludeItems.map(selectorToken).filter(Boolean),
    include_items: includeItems,
    exclude_items: excludeItems,
    aliases: rule.aliases || [],
    weight: Number.isFinite(Number(rule.weight)) ? Number(rule.weight) : 1,
    order: Number.isFinite(Number(rule.order)) ? Number(rule.order) : (idx + 1) * 10,
    color: rule.color || GROUP_COLORS[idx % GROUP_COLORS.length],
  }
}

function normalizeRules(rules: SectorFlowGroupRule[]): SectorFlowGroupRule[] {
  return (rules || []).map((rule, idx) => normalizeRule(rule, idx))
}

function rebuildRule(rule: SectorFlowGroupRule): SectorFlowGroupRule {
  const includeItems = normalizeSelectors(rule.include_items, rule.includes)
  const excludeItems = normalizeSelectors(rule.exclude_items, rule.excludes)
  return {
    ...rule,
    include_items: includeItems,
    exclude_items: excludeItems,
    includes: includeItems.map(selectorToken).filter(Boolean),
    excludes: excludeItems.map(selectorToken).filter(Boolean),
  }
}

function splitAliases(value: string): string[] {
  return value.split(/[，,]/).map((x) => x.trim()).filter(Boolean)
}

function sectorToSelector(item: SectorMetaItem): SectorFlowGroupSelector {
  return { type: item.type, code: item.code, name: item.name }
}

function StatTile({
  label,
  value,
  tone,
  icon: Icon,
}: {
  label: string
  value: string
  tone?: string
  icon: typeof BarChart3
}) {
  return (
    <div className="card p-4">
      <div className="flex items-center justify-between gap-3">
        <div className="min-w-0">
          <div className="text-[12px] text-muted-foreground">{label}</div>
          <div className={`mt-1 truncate font-mono text-[19px] font-semibold ${tone || 'text-foreground'}`}>{value}</div>
        </div>
        <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-primary/10 text-primary">
          <Icon className="h-4 w-4" />
        </div>
      </div>
    </div>
  )
}

export default function MozzAnalysisPage() {
  const [summary, setSummary] = useState<SectorFlowGroupSummaryResponse | null>(null)
  const [rules, setRules] = useState<SectorFlowGroupRule[]>([])
  const [selectedIndex, setSelectedIndex] = useState(0)
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [error, setError] = useState('')
  const [refreshIntervalMin, setRefreshIntervalMin] = useState<RefreshIntervalMinute>(() => readRefreshInterval())
  const [lastLoadedAt, setLastLoadedAt] = useState<Date | null>(null)
  const [configOpen, setConfigOpen] = useState(false)
  const [draftRules, setDraftRules] = useState<SectorFlowGroupRule[]>([])
  const [draftIndex, setDraftIndex] = useState(0)
  const [configError, setConfigError] = useState('')
  const [configSaving, setConfigSaving] = useState(false)
  const [configPreviewing, setConfigPreviewing] = useState(false)
  const [previewGroups, setPreviewGroups] = useState<SectorFlowGroupSummaryResponse['groups']>([])
  const [previewDiagnostics, setPreviewDiagnostics] = useState<SectorFlowGroupDiagnostics | null>(null)
  const [selectorKind, setSelectorKind] = useState<SelectorKind>('industry')
  const [selectorQuery, setSelectorQuery] = useState('')
  const [selectorResults, setSelectorResults] = useState<SectorMetaItem[]>([])
  const [selectorLoading, setSelectorLoading] = useState(false)

  const load = useCallback(async (date?: string) => {
    setLoading(true)
    setError('')
    try {
      const [summaryRes, groupRes] = await Promise.all([
        sectorFlowApi.groupSummary(date ? { date } : undefined),
        sectorFlowApi.getGroups(),
      ])
      setSummary(summaryRes)
      setRules(groupRes.groups || [])
      setSelectedIndex(0)
      setLastLoadedAt(new Date())
    } catch (e) {
      setError(e instanceof Error ? e.message : '加载失败')
      setSummary(null)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    load()
  }, [load])

  useEffect(() => {
    localStorage.setItem('panwatch_mozz_flow_refresh_min', String(refreshIntervalMin))
    const id = window.setInterval(() => {
      if (document.visibilityState === 'visible') load()
    }, refreshIntervalMin * 60 * 1000)
    return () => window.clearInterval(id)
  }, [load, refreshIntervalMin])

  useEffect(() => {
    if (!configOpen) return
    const keyword = selectorQuery.trim()
    if (!keyword) {
      setSelectorResults([])
      setSelectorLoading(false)
      return
    }
    setSelectorLoading(true)
    const id = window.setTimeout(async () => {
      try {
        const res = await sectorFlowApi.listSectors({ type: selectorKind, keyword, limit: 30 })
        setSelectorResults(res.items || [])
      } catch {
        setSelectorResults([])
      } finally {
        setSelectorLoading(false)
      }
    }, 250)
    return () => window.clearTimeout(id)
  }, [configOpen, selectorKind, selectorQuery])

  const groups = useMemo(
    () => [...(summary?.groups || [])].sort((a, b) => Math.abs(b.main_net_inflow || 0) - Math.abs(a.main_net_inflow || 0)),
    [summary],
  )
  const selected = groups[Math.min(selectedIndex, Math.max(groups.length - 1, 0))]
  const maxAbs = useMemo(() => Math.max(1, ...groups.map((g) => Math.abs(g.main_net_inflow || 0))), [groups])
  const totals = useMemo(() => {
    const totalNet = groups.reduce((sum, g) => sum + (g.main_net_inflow || 0), 0)
    const inflowCount = groups.filter((g) => (g.main_net_inflow || 0) > 0).length
    const outflowCount = groups.filter((g) => (g.main_net_inflow || 0) < 0).length
    const totalTurnover = groups.reduce((sum, g) => sum + (g.turnover || 0), 0)
    return { totalNet, inflowCount, outflowCount, totalTurnover }
  }, [groups])

  const refreshSnapshot = async () => {
    setRefreshing(true)
    setError('')
    try {
      const snapshot = await sectorFlowApi.refreshSnapshot({ date: todayString() })
      await load(snapshot.date)
    } catch (e) {
      setError(e instanceof Error ? e.message : '刷新失败')
    } finally {
      setRefreshing(false)
    }
  }

  const activeDraft = draftRules[Math.min(draftIndex, Math.max(draftRules.length - 1, 0))]
  const includeItems = activeDraft ? normalizeSelectors(activeDraft.include_items, activeDraft.includes) : []
  const excludeItems = activeDraft ? normalizeSelectors(activeDraft.exclude_items, activeDraft.excludes) : []
  const diagnosticCount = (previewDiagnostics?.duplicate_members.length || 0)
    + (previewDiagnostics?.empty_groups.length || 0)
    + (previewDiagnostics?.invalid_selectors.length || 0)

  const openConfig = () => {
    setDraftRules(normalizeRules(rules))
    setDraftIndex(0)
    setConfigError('')
    setPreviewGroups([])
    setPreviewDiagnostics(null)
    setSelectorQuery('')
    setSelectorResults([])
    setConfigOpen(true)
  }

  const updateDraft = (patch: Partial<SectorFlowGroupRule>) => {
    setDraftRules((prev) => prev.map((rule, idx) => (
      idx === draftIndex ? rebuildRule({ ...rule, ...patch }) : rule
    )))
  }

  const addDraftRule = () => {
    setDraftRules((prev) => {
      const nextRule = normalizeRule({
        id: `group-${Date.now().toString(36)}`,
        name: `新聚类${prev.length + 1}`,
        enabled: true,
        includes: [],
        excludes: [],
        include_items: [],
        exclude_items: [],
        aliases: [],
        weight: 1,
        order: (prev.length + 1) * 10,
        color: GROUP_COLORS[prev.length % GROUP_COLORS.length],
      }, prev.length)
      setDraftIndex(prev.length)
      return [...prev, nextRule]
    })
  }

  const removeDraftRule = (idx: number) => {
    setDraftRules((prev) => {
      const next = prev.filter((_, i) => i !== idx)
      setDraftIndex(Math.max(0, Math.min(idx, next.length - 1)))
      return next
    })
  }

  const addSelector = (direction: SelectorDirection, item: SectorFlowGroupSelector) => {
    if (!activeDraft) return
    const key = `${item.type}:${item.code.toLowerCase()}:${item.name.toLowerCase()}`
    const field = direction === 'include' ? 'include_items' : 'exclude_items'
    const current = normalizeSelectors(activeDraft[field], direction === 'include' ? activeDraft.includes : activeDraft.excludes)
    if (current.some((x) => `${x.type}:${x.code.toLowerCase()}:${x.name.toLowerCase()}` === key)) return
    updateDraft({ [field]: [...current, item] } as Partial<SectorFlowGroupRule>)
  }

  const removeSelector = (direction: SelectorDirection, idx: number) => {
    if (!activeDraft) return
    const field = direction === 'include' ? 'include_items' : 'exclude_items'
    const fallback = direction === 'include' ? activeDraft.includes : activeDraft.excludes
    const current = normalizeSelectors(activeDraft[field], fallback)
    updateDraft({ [field]: current.filter((_, i) => i !== idx) } as Partial<SectorFlowGroupRule>)
  }

  const previewDraftRules = async () => {
    const payload = normalizeRules(draftRules)
    setConfigPreviewing(true)
    setConfigError('')
    try {
      const res = await sectorFlowApi.previewGroups(payload)
      setPreviewGroups(res.groups || [])
      setPreviewDiagnostics(res.diagnostics)
    } catch (e) {
      setConfigError(e instanceof Error ? e.message : '预览失败')
      setPreviewGroups([])
      setPreviewDiagnostics(null)
    } finally {
      setConfigPreviewing(false)
    }
  }

  const saveDraftRules = async () => {
    const payload = normalizeRules(draftRules)
    setConfigSaving(true)
    setConfigError('')
    try {
      const res = await sectorFlowApi.updateGroups(payload)
      setRules(res.groups || [])
      setConfigOpen(false)
      await load()
    } catch (e) {
      setConfigError(e instanceof Error ? e.message : '保存失败')
    } finally {
      setConfigSaving(false)
    }
  }

  const restoreDefaults = async () => {
    setConfigError('')
    try {
      const res = await sectorFlowApi.getDefaultGroups()
      setDraftRules(normalizeRules(res.groups || []))
      setDraftIndex(0)
      setPreviewGroups([])
      setPreviewDiagnostics(null)
    } catch (e) {
      setConfigError(e instanceof Error ? e.message : '读取默认配置失败')
    }
  }

  return (
    <div className="page-container pb-10">
      <div className="mb-4 flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
        <div>
          <div className="flex items-center gap-2">
            <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-primary/10 text-primary">
              <BarChart3 className="h-4 w-4" />
            </div>
            <div>
              <h1 className="text-[21px] font-bold tracking-tight text-foreground md:text-[23px]">mozz 分析</h1>
              <div className="mt-0.5 text-[12px] text-muted-foreground">资金聚类 · {summary?.date || todayString()} · {sourceText(summary?.source)}</div>
            </div>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <div className="flex h-8 items-center gap-1.5 rounded-xl border border-border bg-card px-2">
            <Clock3 className="h-3.5 w-3.5 text-muted-foreground" />
            <Select
              value={String(refreshIntervalMin)}
              onValueChange={(v) => setRefreshIntervalMin(Number(v) as RefreshIntervalMinute)}
            >
              <SelectTrigger className="h-7 w-[94px] border-0 bg-transparent px-1 text-[12px] shadow-none focus:ring-0">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {REFRESH_INTERVAL_OPTIONS.map((min) => (
                  <SelectItem key={min} value={String(min)}>
                    {min} 分钟
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <Button variant="outline" size="sm" onClick={() => load()} disabled={loading || refreshing} className="h-8 text-[12px]">
            <RefreshCw className={`h-3.5 w-3.5 ${loading ? 'animate-spin' : ''}`} />
            实时刷新
          </Button>
          <Button variant="secondary" size="sm" onClick={refreshSnapshot} disabled={loading || refreshing} className="h-8 text-[12px]">
            <Database className={`h-3.5 w-3.5 ${refreshing ? 'animate-pulse' : ''}`} />
            刷新快照
          </Button>
          <Button variant="outline" size="sm" onClick={openConfig} className="h-8 text-[12px]">
            <Settings2 className="h-3.5 w-3.5" />
            聚类配置
          </Button>
        </div>
      </div>

      <div className="mb-4 grid grid-cols-2 gap-3 lg:grid-cols-4">
        <StatTile label="聚类净流入" value={money(totals.totalNet)} tone={flowColor(totals.totalNet)} icon={BarChart3} />
        <StatTile label="流入组数" value={`${totals.inflowCount}`} tone="text-rose-500" icon={ArrowUpRight} />
        <StatTile label="流出组数" value={`${totals.outflowCount}`} tone="text-emerald-500" icon={ArrowDownRight} />
        <StatTile label="配置组数" value={`${rules.length}`} icon={GitBranch} />
      </div>

      {error && (
        <div className="mb-4 rounded-xl border border-destructive/30 bg-destructive/10 px-4 py-3 text-[13px] text-destructive">
          {error}
        </div>
      )}

      <div className="mb-3 flex items-center gap-1.5">
        <span className="rounded bg-primary px-2.5 py-1 text-[11px] font-medium text-primary-foreground">资金聚类</span>
        <span className="text-[11px] text-muted-foreground">
          自动刷新 {refreshIntervalMin} 分钟
          {lastLoadedAt ? ` · 最近 ${lastLoadedAt.toLocaleTimeString('zh-CN', { hour12: false })}` : ''}
        </span>
      </div>

      <div className="grid gap-4 xl:grid-cols-[minmax(0,1.4fr)_minmax(360px,0.8fr)]">
        <section className="card overflow-hidden">
          <div className="flex items-center justify-between border-b border-border/60 px-4 py-3">
            <div>
              <h2 className="text-[14px] font-semibold text-foreground">聚类资金流</h2>
              <div className="mt-0.5 text-[11px] text-muted-foreground">按配置文件聚合行业/概念板块</div>
            </div>
            <div className="font-mono text-[12px] text-muted-foreground">{groups.length} 组</div>
          </div>
          <div className="min-h-[360px]">
            {loading ? (
              <div className="flex h-[360px] items-center justify-center text-[13px] text-muted-foreground">
                <span className="mr-2 h-4 w-4 animate-spin rounded-full border-2 border-primary/20 border-t-primary" />
                加载资金聚类
              </div>
            ) : groups.length === 0 ? (
              <div className="flex h-[360px] items-center justify-center text-[13px] text-muted-foreground">
                暂无聚类资金数据
              </div>
            ) : (
              <div className="divide-y divide-border/50">
                {groups.map((group, idx) => {
                  const net = group.main_net_inflow || 0
                  const width = Math.max(4, Math.round((Math.abs(net) / maxAbs) * 100))
                  const active = idx === selectedIndex
                  return (
                    <button
                      key={`${group.name}:${idx}`}
                      onClick={() => setSelectedIndex(idx)}
                      className={`grid w-full cursor-pointer grid-cols-[minmax(110px,180px)_minmax(0,1fr)_120px] items-center gap-3 px-4 py-3 text-left transition-colors hover:bg-accent/35 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring md:grid-cols-[minmax(150px,220px)_minmax(0,1fr)_150px] ${
                        active ? 'bg-primary/8' : ''
                      }`}
                    >
                      <div className="min-w-0">
                        <div className="truncate text-[13px] font-semibold text-foreground">{group.name}</div>
                        <div className="mt-1 flex flex-wrap gap-1">
                          {(group.aliases || []).slice(0, 2).map((alias) => (
                            <span key={alias} className="rounded bg-accent px-1.5 py-0.5 text-[10px] text-muted-foreground">
                              {alias}
                            </span>
                          ))}
                          <span className="rounded bg-accent/60 px-1.5 py-0.5 text-[10px] text-muted-foreground">{group.sector_count} 板块</span>
                        </div>
                      </div>
                      <div className="min-w-0">
                        <div className="h-2 w-full overflow-hidden rounded-full bg-accent">
                          <div className={`h-full rounded-full ${flowTone(net)}`} style={{ width: `${width}%` }} />
                        </div>
                        <div className="mt-1 truncate text-[11px] text-muted-foreground">
                          成交额 {money(group.turnover)} · 强度 {pct(group.main_net_inflow_pct)}
                        </div>
                      </div>
                      <div className="text-right">
                        <div className={`font-mono text-[14px] font-semibold ${flowColor(net)}`}>{money(net)}</div>
                        <div className="mt-1 text-[11px] text-muted-foreground">净流入</div>
                      </div>
                    </button>
                  )
                })}
              </div>
            )}
          </div>
        </section>

        <section className="card overflow-hidden">
          <div className="border-b border-border/60 px-4 py-3">
            <h2 className="text-[14px] font-semibold text-foreground">{selected?.name || '贡献板块'}</h2>
            <div className="mt-0.5 text-[11px] text-muted-foreground">资金贡献拆解</div>
          </div>
          {!selected ? (
            <div className="flex h-[360px] items-center justify-center text-[13px] text-muted-foreground">暂无明细</div>
          ) : (
            <div className="max-h-[520px] divide-y divide-border/50 overflow-y-auto scrollbar">
              {(selected.contributors || []).map((item) => (
                <div key={`${item.type}:${item.code}`} className="px-4 py-3">
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <div className="truncate text-[13px] font-medium text-foreground">{item.name}</div>
                      <div className="mt-1 flex items-center gap-1.5 text-[11px] text-muted-foreground">
                        <span className="font-mono">{item.code}</span>
                        <span className="rounded bg-accent px-1.5 py-0.5">{item.type === 'concept' ? '概念' : '行业'}</span>
                      </div>
                    </div>
                    <div className="shrink-0 text-right">
                      <div className={`font-mono text-[13px] font-semibold ${flowColor(item.main_net_inflow)}`}>
                        {money(item.main_net_inflow)}
                      </div>
                      <div className="mt-1 text-[11px] text-muted-foreground">{pct(item.main_net_inflow_pct)}</div>
                    </div>
                  </div>
                </div>
              ))}
              {!selected.contributors?.length && (
                <div className="flex h-[280px] items-center justify-center text-[13px] text-muted-foreground">暂无贡献板块</div>
              )}
            </div>
          )}
        </section>
      </div>

      <Dialog open={configOpen} onOpenChange={setConfigOpen}>
        <DialogContent className="max-w-6xl p-0">
          <DialogHeader className="mb-0 border-b border-border/60 px-5 pb-4 pt-5">
            <DialogTitle>资金聚类配置</DialogTitle>
            <DialogDescription>
              点选行业/概念板块维护自定义聚类；保存后会写入运行时配置并刷新当前页面。
            </DialogDescription>
          </DialogHeader>

          <div className="grid gap-0 lg:grid-cols-[280px_minmax(0,1fr)]">
            <aside className="border-b border-border/60 p-4 lg:border-b-0 lg:border-r">
              <div className="mb-3 flex items-center justify-between gap-2">
                <div className="text-[12px] font-semibold text-foreground">聚类组</div>
                <Button size="sm" variant="outline" onClick={addDraftRule} className="h-7 px-2 text-[11px]">
                  <Plus className="h-3.5 w-3.5" />
                  新增
                </Button>
              </div>
              <div className="max-h-[420px] space-y-2 overflow-y-auto pr-1 scrollbar">
                {draftRules.map((rule, idx) => {
                  const active = idx === draftIndex
                  return (
                    <button
                      key={rule.id || `${rule.name}:${idx}`}
                      type="button"
                      onClick={() => setDraftIndex(idx)}
                      className={`w-full cursor-pointer rounded-xl border px-3 py-2 text-left transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring ${
                        active ? 'border-primary/50 bg-primary/10' : 'border-border bg-card hover:bg-accent/35'
                      }`}
                    >
                      <div className="flex items-center justify-between gap-2">
                        <div className="min-w-0">
                          <div className="truncate text-[12px] font-semibold text-foreground">{rule.name}</div>
                          <div className="mt-1 text-[10px] text-muted-foreground">
                            {(rule.include_items?.length || rule.includes?.length || 0)} 包含 · {rule.enabled === false ? '停用' : '启用'}
                          </div>
                        </div>
                        <span
                          className="h-3 w-3 shrink-0 rounded-full border border-white/20"
                          style={{ backgroundColor: rule.color || GROUP_COLORS[idx % GROUP_COLORS.length] }}
                        />
                      </div>
                    </button>
                  )
                })}
                {!draftRules.length && (
                  <div className="rounded-xl border border-dashed border-border px-3 py-8 text-center text-[12px] text-muted-foreground">
                    暂无聚类组
                  </div>
                )}
              </div>
            </aside>

            <section className="p-4">
              {!activeDraft ? (
                <div className="flex h-[420px] items-center justify-center text-[13px] text-muted-foreground">
                  请选择或新增一个聚类组
                </div>
              ) : (
                <div className="space-y-4">
                  <div className="grid gap-3 md:grid-cols-[minmax(0,1.2fr)_120px_120px]">
                    <div>
                      <div className="mb-1.5 text-[11px] text-muted-foreground">名称</div>
                      <Input
                        value={activeDraft.name}
                        onChange={(e) => updateDraft({ name: e.target.value })}
                        className="h-9 text-[12px]"
                      />
                    </div>
                    <div>
                      <div className="mb-1.5 text-[11px] text-muted-foreground">排序</div>
                      <Input
                        type="number"
                        value={activeDraft.order}
                        onChange={(e) => updateDraft({ order: Number(e.target.value) || 0 })}
                        className="h-9 text-[12px]"
                      />
                    </div>
                    <div>
                      <div className="mb-1.5 text-[11px] text-muted-foreground">权重</div>
                      <Input
                        type="number"
                        step="0.1"
                        value={activeDraft.weight}
                        onChange={(e) => updateDraft({ weight: Number(e.target.value) || 1 })}
                        className="h-9 text-[12px]"
                      />
                    </div>
                  </div>

                  <div className="grid gap-3 md:grid-cols-[minmax(0,1fr)_110px_90px]">
                    <div>
                      <div className="mb-1.5 text-[11px] text-muted-foreground">别名</div>
                      <Input
                        value={(activeDraft.aliases || []).join('，')}
                        onChange={(e) => updateDraft({ aliases: splitAliases(e.target.value) })}
                        placeholder="用逗号分隔，如 硬科技，TMT"
                        className="h-9 text-[12px]"
                      />
                    </div>
                    <div>
                      <div className="mb-1.5 text-[11px] text-muted-foreground">颜色</div>
                      <Input
                        type="color"
                        value={activeDraft.color || GROUP_COLORS[draftIndex % GROUP_COLORS.length]}
                        onChange={(e) => updateDraft({ color: e.target.value })}
                        className="h-9 px-2"
                      />
                    </div>
                    <div>
                      <div className="mb-1.5 text-[11px] text-muted-foreground">启用</div>
                      <div className="flex h-9 items-center rounded-xl border border-border bg-card px-3">
                        <Switch checked={activeDraft.enabled !== false} onCheckedChange={(checked) => updateDraft({ enabled: checked })} />
                      </div>
                    </div>
                  </div>

                  <div className="rounded-xl border border-border bg-background/40 p-3">
                    <div className="mb-3 grid gap-2 md:grid-cols-[120px_minmax(0,1fr)]">
                      <Select value={selectorKind} onValueChange={(v) => setSelectorKind(v as SelectorKind)}>
                        <SelectTrigger className="h-9 text-[12px]">
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          <SelectItem value="industry">行业</SelectItem>
                          <SelectItem value="concept">概念</SelectItem>
                        </SelectContent>
                      </Select>
                      <div className="relative">
                        <Search className="pointer-events-none absolute left-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
                        <Input
                          value={selectorQuery}
                          onChange={(e) => setSelectorQuery(e.target.value)}
                          placeholder="搜索板块名称或代码"
                          className="h-9 pl-8 text-[12px]"
                        />
                      </div>
                    </div>

                    <div className="max-h-[150px] overflow-y-auto rounded-lg border border-border/60 bg-card scrollbar">
                      {selectorLoading ? (
                        <div className="flex h-16 items-center justify-center text-[12px] text-muted-foreground">
                          <span className="mr-2 h-3.5 w-3.5 animate-spin rounded-full border-2 border-primary/20 border-t-primary" />
                          搜索中
                        </div>
                      ) : selectorResults.length ? (
                        selectorResults.map((item) => (
                          <div key={`${item.type}:${item.code}`} className="flex items-center justify-between gap-3 border-b border-border/50 px-3 py-2 last:border-b-0">
                            <div className="min-w-0">
                              <div className="truncate text-[12px] font-medium text-foreground">{item.name}</div>
                              <div className="mt-0.5 text-[10px] text-muted-foreground">
                                {item.type === 'concept' ? '概念' : '行业'} · {item.code}
                              </div>
                            </div>
                            <div className="flex shrink-0 items-center gap-1.5">
                              <Button size="sm" variant="outline" onClick={() => addSelector('include', sectorToSelector(item))} className="h-7 px-2 text-[11px]">
                                包含
                              </Button>
                              <Button size="sm" variant="ghost" onClick={() => addSelector('exclude', sectorToSelector(item))} className="h-7 px-2 text-[11px]">
                                排除
                              </Button>
                            </div>
                          </div>
                        ))
                      ) : (
                        <div className="flex h-16 items-center justify-center text-[12px] text-muted-foreground">
                          输入关键词后选择板块
                        </div>
                      )}
                    </div>
                  </div>

                  <div className="grid gap-3 md:grid-cols-2">
                    <div className="rounded-xl border border-border bg-card p-3">
                      <div className="mb-2 text-[12px] font-semibold text-foreground">包含板块</div>
                      <div className="flex min-h-[48px] flex-wrap gap-2">
                        {includeItems.map((item, idx) => (
                          <span key={`${item.type}:${item.code}:${item.name}:${idx}`} className="inline-flex items-center gap-1.5 rounded-lg bg-primary/10 px-2 py-1 text-[11px] text-primary">
                            {selectorText(item)}
                            <button type="button" onClick={() => removeSelector('include', idx)} className="cursor-pointer rounded hover:bg-primary/15">
                              <X className="h-3 w-3" />
                            </button>
                          </span>
                        ))}
                        {!includeItems.length && <span className="text-[12px] text-muted-foreground">未选择</span>}
                      </div>
                    </div>

                    <div className="rounded-xl border border-border bg-card p-3">
                      <div className="mb-2 text-[12px] font-semibold text-foreground">排除板块</div>
                      <div className="flex min-h-[48px] flex-wrap gap-2">
                        {excludeItems.map((item, idx) => (
                          <span key={`${item.type}:${item.code}:${item.name}:${idx}`} className="inline-flex items-center gap-1.5 rounded-lg bg-destructive/10 px-2 py-1 text-[11px] text-destructive">
                            {selectorText(item)}
                            <button type="button" onClick={() => removeSelector('exclude', idx)} className="cursor-pointer rounded hover:bg-destructive/15">
                              <X className="h-3 w-3" />
                            </button>
                          </span>
                        ))}
                        {!excludeItems.length && <span className="text-[12px] text-muted-foreground">未选择</span>}
                      </div>
                    </div>
                  </div>

                  {(previewGroups.length > 0 || previewDiagnostics) && (
                    <div className="rounded-xl border border-border bg-card p-3">
                      <div className="mb-2 flex items-center justify-between gap-3">
                        <div className="text-[12px] font-semibold text-foreground">预览诊断</div>
                        <div className={`text-[11px] ${diagnosticCount ? 'text-amber-500' : 'text-muted-foreground'}`}>
                          {diagnosticCount ? `${diagnosticCount} 个提示` : '未发现配置提示'}
                        </div>
                      </div>
                      <div className="grid gap-2 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
                        <div className="space-y-1.5">
                          {previewGroups.slice(0, 5).map((group) => (
                            <div key={group.id || group.name} className="flex items-center justify-between gap-3 rounded-lg bg-accent/40 px-2.5 py-2">
                              <div className="min-w-0">
                                <div className="truncate text-[12px] font-medium text-foreground">{group.name}</div>
                                <div className="text-[10px] text-muted-foreground">{group.sector_count} 板块 · 强度 {pct(group.main_net_inflow_pct)}</div>
                              </div>
                              <div className={`shrink-0 font-mono text-[12px] font-semibold ${flowColor(group.main_net_inflow)}`}>
                                {money(group.main_net_inflow)}
                              </div>
                            </div>
                          ))}
                          {!previewGroups.length && <div className="text-[12px] text-muted-foreground">暂无匹配聚类</div>}
                        </div>
                        <div className="space-y-1.5 text-[11px] text-muted-foreground">
                          {(previewDiagnostics?.duplicate_members || []).slice(0, 3).map((item) => (
                            <div key={`dup-${item.type}:${item.code}`} className="rounded-lg bg-amber-500/10 px-2.5 py-2 text-amber-500">
                              {item.name} 同时归属：{item.groups.join('、')}
                            </div>
                          ))}
                          {(previewDiagnostics?.empty_groups || []).slice(0, 3).map((item) => (
                            <div key={`empty-${item.id || item.name}`} className="rounded-lg bg-accent/50 px-2.5 py-2">
                              {item.name}：{item.reason}
                            </div>
                          ))}
                          {(previewDiagnostics?.invalid_selectors || []).slice(0, 3).map((item, idx) => (
                            <div key={`invalid-${idx}`} className="rounded-lg bg-accent/50 px-2.5 py-2">
                              {item.group_name} / {item.label}：{item.reason}
                            </div>
                          ))}
                          {previewDiagnostics && diagnosticCount === 0 && (
                            <div className="rounded-lg bg-emerald-500/10 px-2.5 py-2 text-emerald-500">
                              配置项均可匹配，未发现跨组重复
                            </div>
                          )}
                        </div>
                      </div>
                    </div>
                  )}

                  {configError && (
                    <div className="flex items-start gap-2 rounded-xl border border-destructive/30 bg-destructive/10 px-3 py-2 text-[12px] text-destructive">
                      <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                      {configError}
                    </div>
                  )}

                  <div className="flex flex-wrap items-center justify-between gap-2 border-t border-border/60 pt-3">
                    <Button
                      type="button"
                      variant="destructive"
                      size="sm"
                      onClick={() => removeDraftRule(draftIndex)}
                      disabled={!draftRules.length}
                      className="h-8 text-[12px]"
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                      删除当前组
                    </Button>
                    <div className="flex flex-wrap items-center gap-2">
                      <Button type="button" variant="outline" size="sm" onClick={restoreDefaults} className="h-8 text-[12px]">
                        <RotateCcw className="h-3.5 w-3.5" />
                        恢复默认
                      </Button>
                      <Button type="button" variant="outline" size="sm" onClick={previewDraftRules} disabled={configPreviewing} className="h-8 text-[12px]">
                        <Eye className={`h-3.5 w-3.5 ${configPreviewing ? 'animate-pulse' : ''}`} />
                        预览诊断
                      </Button>
                      <Button type="button" size="sm" onClick={saveDraftRules} disabled={configSaving} className="h-8 text-[12px]">
                        <Save className={`h-3.5 w-3.5 ${configSaving ? 'animate-pulse' : ''}`} />
                        保存配置
                      </Button>
                    </div>
                  </div>
                </div>
              )}
            </section>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  )
}
