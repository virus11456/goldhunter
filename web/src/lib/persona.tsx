import { useEffect, useState } from 'react'
import { api, hasFidelity, type Bot, type Persona, type UniverseRules } from '../api'
import { fmtNum } from './format'

export const SOURCE_LABEL: Record<string, string> = { nuwa: '女媧蒸餾', upload: '上傳', builtin: '內建', distill: '蒸餾' }
export const MARKET_LABEL: Record<string, string> = { crypto: '加密貨幣', us: '美股', tw: '台股' }
export const MARKETS = ['crypto', 'us', 'tw'] as const

/** 「傑西·李佛摩」→「李佛摩」（顯示用短名） */
export function shortPersonaName(name: string): string {
  const parts = name.split(/[·・]/).map((x) => x.trim()).filter(Boolean)
  return parts.length > 1 ? parts[parts.length - 1] : name
}

const GRADE_CLS: Record<string, string> = {
  A: 'border-up/40 bg-up/15 text-green-300',
  B: 'border-sky-500/40 bg-sky-500/15 text-sky-300',
  C: 'border-gold/40 bg-gold/15 text-amber-200',
  D: 'border-down/40 bg-down/15 text-red-300',
}

export function GradeBadge({ grade, score, size = 'sm' }: { grade: string; score?: number; size?: 'sm' | 'lg' }) {
  const cls = GRADE_CLS[grade] ?? 'border-line bg-panel2 text-muted'
  if (size === 'lg')
    return (
      <span className={`inline-flex items-baseline gap-1.5 rounded-xl border px-3 py-1.5 ${cls}`}>
        <span className="text-2xl font-bold leading-none">{grade}</span>
        {score !== undefined && <span className="num font-mono text-sm">{score} 分</span>}
      </span>
    )
  return (
    <span className={`inline-flex items-center gap-1 rounded-md border px-1.5 py-0.5 text-[11px] font-semibold leading-4 ${cls}`}>
      {grade}
      {score !== undefined && <span className="num font-mono font-normal">{score}</span>}
    </span>
  )
}

export function PersonaStatusBadge({ p }: { p: Pick<Persona, 'status' | 'paper_progress'> }) {
  if (p.status === 'active') return <span className="badge badge-green">已啟用</span>
  if (p.status === 'paper_only') {
    const g = p.paper_progress
    return (
      <span className="badge badge-blue" title="保真度已通過；模擬期間只能用在模擬帳戶，期滿自動開放實盤">
        模擬期
        {g && (
          <span className="num font-mono text-sky-200/80">
            ・模擬 {fmtNum(Math.min(g.days, g.days_required), 1)}/{g.days_required} 天・成交 {Math.min(g.trades, 999)}/{g.trades_required} 筆
          </span>
        )}
      </span>
    )
  }
  return (
    <span className="badge badge-gold" title="沒有附保真度評分或未達門檻，只能用在模擬帳戶">
      僅限模擬
    </span>
  )
}

export function personaScoreText(p: Persona): string {
  return hasFidelity(p.fidelity) ? `${p.fidelity.score} 分（${p.fidelity.grade}）` : '未附評分'
}

/** 規則模式的標的範圍描述；手動清單回傳 null */
export function universeLabel(u: UniverseRules | Record<string, never> | null | undefined): string | null {
  if (!u || (u as UniverseRules).mode !== 'rules') return null
  const r = u as UniverseRules
  const extra: string[] = []
  if (r.exclude_meme !== false) extra.push('排除迷因幣')
  if (r.include_only?.length) extra.push(`只在 ${r.include_only.join('、')}`)
  if (r.exclude?.length) extra.push(`排除 ${r.exclude.join('、')}`)
  return `規則：成交量前 ${r.top_n ?? 10}${extra.length ? `（${extra.join('，')}）` : ''}`
}

/** 讀取一次投資大師清單（id → 名字），給 Bot 卡片顯示「AI 交易員・李佛摩」；失敗時靜默 */
export function usePersonaNames(): Record<number, string> {
  const [names, setNames] = useState<Record<number, string>>({})
  useEffect(() => {
    let alive = true
    api
      .listPersonas()
      .then((ps) => alive && setNames(Object.fromEntries(ps.map((p) => [p.id, p.name]))))
      .catch(() => {})
    return () => {
      alive = false
    }
  }, [])
  return names
}

export function botPersonaName(b: Pick<Bot, 'mode' | 'ai_trader'>, names: Record<number, string>): string | undefined {
  const id = b.mode === 'ai_trader' ? b.ai_trader?.persona_id : null
  return id ? names[id] : undefined
}
