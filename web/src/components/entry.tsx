import type { EntryAnalysis, EntryCandidate } from '../api'
import { fmtNum, fmtPrice } from '../lib/format'

const pct = (v: number | null | undefined) => (v === null || v === undefined ? '—' : `${fmtNum(v * 100, 0)}%`)
const r = (v: number | null | undefined) => (v === null || v === undefined ? '—' : `${v > 0 ? '+' : ''}${fmtNum(v, 2)}R`)
const evCls = (v: number | null | undefined) => (v === null || v === undefined ? 'text-muted' : v > 0 ? 'text-up' : v < 0 ? 'text-down' : 'text-slate-300')

export function DirectionBadge({ d }: { d: string }) {
  const long = d === 'long'
  return <span className={`inline-block rounded px-1.5 py-0.5 text-[11px] font-semibold ${long ? 'bg-up/15 text-up' : 'bg-down/15 text-down'}`}>{long ? '做多' : '做空'}</span>
}

/** 一行摘要：方向・盈虧比・建議（決策紀錄用） */
export function EntryLine({ a, pending }: { a: EntryAnalysis; pending?: boolean }) {
  const rec = a.candidates.find((c) => c.key === a.recommended)
  const market = a.candidates.find((c) => c.key === 'market')
  return (
    <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[11px]">
      <span className="font-medium text-sky-300">進場分析</span>
      <span className="text-muted">
        市價 R:R <span className="font-mono text-slate-200">1:{fmtNum(market?.rr ?? rec?.rr ?? 0, 2)}</span>
      </span>
      {rec && rec.key !== 'market' && (
        <span className="text-muted">
          建議 {rec.label} <span className="font-mono text-slate-200">{fmtPrice(rec.price)}</span>（1:{fmtNum(rec.rr, 2)}）
        </span>
      )}
      {a.recommended === 'skip' && <span className="text-down">建議不進場</span>}
      {pending && <span className="badge badge-blue">掛單等待中</span>}
    </div>
  )
}

/** 完整分析：價位摘要 + 候選進場方式表（推薦列高亮）+ 建議文字 */
export function EntryAnalysisView({ a, hypothetical, assumedReason }: { a: EntryAnalysis; hypothetical?: boolean; assumedReason?: string | null }) {
  return (
    <div className="space-y-3">
      {assumedReason && <div className="text-xs text-muted">{assumedReason}</div>}
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs">
        <DirectionBadge d={a.direction} />
        {hypothetical && <span className="badge badge-gray">假設現在進場</span>}
        <span className="text-muted">
          現價 <span className="font-mono text-slate-100">{fmtPrice(a.price)}</span>
        </span>
        <span className="text-muted" title={a.stop_source}>
          止損 <span className="font-mono text-down">{fmtPrice(a.stop)}</span>
        </span>
        <span className="text-muted" title={a.target_source}>
          目標 <span className="font-mono text-up">{fmtPrice(a.target)}</span>
        </span>
        <span className="text-muted">
          ATR <span className="font-mono text-slate-300">{fmtPrice(a.atr)}</span>
        </span>
      </div>
      <div className="tbl-wrap">
        <table className="tbl">
          <thead>
            <tr>
              <th>進場方式</th>
              <th className="r">價格</th>
              <th className="r">盈虧比</th>
              <th className="r">成交機率</th>
              <th className="r">勝率</th>
              <th className="r" title="每次訊號的期望值（已考慮等不到的情況），以 R（一倍止損距離）計">期望值</th>
            </tr>
          </thead>
          <tbody>
            {a.candidates.map((c: EntryCandidate) => {
              const on = c.key === a.recommended
              const ev = c.ev_per_signal ?? c.ev_r
              return (
                <tr key={c.key} className={on ? '!bg-gold/[0.08]' : ''}>
                  <td className="whitespace-nowrap">
                    <span className={on ? 'font-semibold text-gold' : 'text-slate-200'}>{c.label}</span>
                    {on && <span className="badge badge-gold ml-1.5">建議</span>}
                  </td>
                  <td className="r font-mono">{fmtPrice(c.price)}</td>
                  <td className="r font-mono">1:{fmtNum(c.rr, 2)}</td>
                  <td className="r font-mono">{pct(c.fill_prob)}</td>
                  <td className="r font-mono">{pct(c.win_prob)}</td>
                  <td className={`r font-mono ${evCls(ev)}`}>{r(ev)}</td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
      <div className={`rounded-lg border px-3 py-2 text-sm ${a.recommended === 'skip' ? 'border-down/30 bg-down/[0.06] text-red-100' : 'border-gold/30 bg-gold/[0.06] text-amber-50'}`}>
        {a.recommendation}
        <div className="mt-0.5 text-[11px] text-muted">
          {a.sample_basis}（{a.signal_samples} 筆樣本）・掛單最多等 {a.wait_bars} 根 K 棒
        </div>
      </div>
    </div>
  )
}
