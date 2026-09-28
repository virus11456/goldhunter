import { Fragment, useMemo, useState, type ReactNode } from 'react'
import { useSearchParams } from 'react-router-dom'
import { api, type Bot, type DecisionLog, type DecisionPayload, type TuningRun } from '../api'
import { EquityChart } from '../components/charts'
import {
  BotAiBadge,
  BotStatusBadge,
  Card,
  Change,
  Empty,
  Icons,
  SideBadge,
  Skeleton,
  Spinner,
  useAction,
  useConfirm,
  useLoader,
  useToast,
} from '../components/ui'
import { fmtNum, fmtPrice, fmtQty, fmtShortTime, fmtSigned, fmtTime, kindLabel, paramLabel, parseDate, pnlClass, shortSymbol, timeAgo } from '../lib/format'

const REFRESH = 10_000

// ------------------------------ labels ------------------------------
const SOURCE: Record<string, { label: string; cls: string }> = {
  strategy: { label: '策略', cls: 'badge-gray' },
  'strategy+ai': { label: '策略+AI審核', cls: 'badge-gold' },
  ai: { label: 'AI策略', cls: 'badge-blue' },
  copilot: { label: 'AI持倉管理', cls: 'badge-gold' },
  stop: { label: '止損止盈', cls: 'badge-red' },
  tradingview: { label: 'TradingView', cls: 'badge-blue' },
  manual: { label: '手動', cls: 'badge-gray' },
}
export function SourceChip({ source }: { source: string }) {
  let s = SOURCE[source]
  if (!s && source.endsWith('+ai')) s = { label: `${SOURCE[source.slice(0, -3)]?.label ?? source.slice(0, -3)}+AI審核`, cls: 'badge-gold' }
  s = s ?? { label: source, cls: 'badge-gray' }
  return <span className={`badge ${s.cls}`}>{s.label}</span>
}

function actionLabel(d: DecisionLog): { text: string; cls: string } {
  const a = d.action
  if (a === 'open_long') return { text: '開多', cls: 'text-up' }
  if (a === 'open_short') return { text: '開空', cls: 'text-down' }
  if (a === 'close') {
    const pct = d.decision?.close_pct
    return pct !== undefined && pct < 100 ? { text: `減倉 ${fmtNum(pct, 0)}%`, cls: 'text-amber-200' } : { text: '平倉', cls: 'text-slate-100' }
  }
  if (a === 'hold') return { text: d.source === 'copilot' ? '維持' : '觀望', cls: 'text-muted' }
  if (a === 'move_stop') return { text: '移動止損', cls: 'text-gold' }
  if (a === 'reduce') return { text: '減倉', cls: 'text-amber-200' }
  return { text: a, cls: 'text-slate-200' }
}

const VERDICT: Record<string, { label: string; cls: string }> = {
  approve: { label: '放行', cls: 'badge-green' },
  adjust: { label: '調整', cls: 'badge-gold' },
  veto: { label: '否決', cls: 'badge-red' },
}

const TUNE_STATUS: Record<string, { label: string; cls: string }> = {
  proposed: { label: '建議中', cls: 'badge-gold' },
  applied: { label: '已套用', cls: 'badge-green' },
  rejected: { label: '未通過', cls: 'badge-gray' },
  failed: { label: '失敗', cls: 'badge-red' },
}

// ------------------------------ page ------------------------------
export default function Monitor() {
  const [sp, setSp] = useSearchParams()
  const botId = sp.get('bot') ? Number(sp.get('bot')) : null
  const select = (id: number | null) => setSp(id ? { bot: String(id) } : {})
  const bots = useLoader(() => api.listBots(), [], REFRESH)
  const bot = bots.data?.find((b) => b.id === botId) ?? null
  const [tab, setTab] = useState<'trades' | 'decisions'>('decisions')
  const { busy, run } = useAction()

  const toggle = async (b: Bot) => {
    const r = await run(`run-${b.id}`, () => (b.running ? api.stopBot(b.id) : api.startBot(b.id)), b.running ? '已停止' : '已啟動')
    if (r) bots.reload()
  }

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold text-slate-50">監控</h1>
          <p className="text-xs text-muted">每 10 秒自動更新</p>
        </div>
      </div>

      {/* bot cards */}
      {bots.loading && !bots.data ? (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-3">
          {[0, 1, 2].map((i) => <div key={i} className="skeleton h-[150px] rounded-xl" />)}
        </div>
      ) : !bots.data?.length ? (
        <div className="card">
          <Empty icon={<Icons.bot className="h-5 w-5" />} title="還沒有 Bot" hint="到「設定 → Bots 機器人」建立第一個 Bot。" />
        </div>
      ) : (
        <>
          <div className="flex flex-wrap gap-1.5">
            <button className={`rounded-lg border px-3 py-1.5 text-sm transition-colors ${!botId ? 'border-gold/50 bg-gold/15 text-gold' : 'border-line text-muted hover:text-slate-200'}`} onClick={() => select(null)}>
              全部
            </button>
            {bots.data.map((b) => (
              <button key={b.id} className={`flex items-center gap-1.5 rounded-lg border px-3 py-1.5 text-sm transition-colors ${botId === b.id ? 'border-gold/50 bg-gold/15 text-gold' : 'border-line text-muted hover:text-slate-200'}`} onClick={() => select(b.id)}>
                <span className={`h-1.5 w-1.5 rounded-full ${b.running ? 'bg-up' : 'bg-slate-600'}`} />
                {b.name}
              </button>
            ))}
          </div>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-3">
            {(bot ? [bot] : bots.data).map((b) => (
              <BotCard key={b.id} b={b} selected={botId === b.id} onSelect={() => select(b.id)} onToggle={() => toggle(b)} busy={busy === `run-${b.id}`} />
            ))}
          </div>
        </>
      )}

      {bot && (
        <div className="grid grid-cols-1 gap-5 xl:grid-cols-2">
          <EquityPanel bot={bot} />
          <PositionsPanel bot={bot} />
        </div>
      )}

      {bots.data && bots.data.length > 0 && <TuningPanel bot={bot} botsById={Object.fromEntries(bots.data.map((b) => [b.id, b]))} />}

      {bots.data && bots.data.length > 0 && (
        <section className="card">
          <div className="flex items-center gap-1 border-b border-line px-3">
            <button className={`tab ${tab === 'decisions' ? 'tab-active' : ''}`} onClick={() => setTab('decisions')}>
              AI 決策紀錄
            </button>
            <button className={`tab ${tab === 'trades' ? 'tab-active' : ''}`} onClick={() => setTab('trades')}>
              成交紀錄
            </button>
            <span className="ml-auto hidden text-xs text-muted sm:inline">{bot ? bot.name : '全部 Bot'}</span>
          </div>
          {tab === 'trades' ? <TradesTable botId={botId} names={Object.fromEntries(bots.data.map((b) => [b.id, b.name]))} /> : <DecisionsTable botId={botId} names={Object.fromEntries(bots.data.map((b) => [b.id, b.name]))} />}
        </section>
      )}
    </div>
  )
}

// ------------------------------ bot card ------------------------------
function BotCard({ b, selected, onSelect, onToggle, busy }: { b: Bot; selected: boolean; onSelect: () => void; onToggle: () => void; busy: boolean }) {
  const diff = b.equity !== null && b.baseline_equity !== null ? b.equity - b.baseline_equity : null
  return (
    <div
      role="button"
      tabIndex={0}
      onClick={onSelect}
      onKeyDown={(e) => e.key === 'Enter' && onSelect()}
      className={`card cursor-pointer p-4 transition-all ${selected ? 'border-gold/50 shadow-[0_0_0_1px_rgba(240,185,11,0.15)]' : 'hover:border-[#3a414b]'}`}
    >
      <div className="flex items-start gap-3">
        <div className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-lg ${b.running ? 'bg-gold/15 text-gold' : 'bg-panel2 text-muted'}`}>
          <Icons.bot className="h-5 w-5" />
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="truncate font-semibold text-slate-100">{b.name}</span>
            <BotAiBadge bot={b} />
          </div>
          <div className="mt-0.5 truncate text-xs">
            <span className="text-gold/90">{b.strategy_name ?? '—'}</span>
            <span className="text-muted"> · {kindLabel(b.strategy_kind)}</span>
          </div>
        </div>
        <BotStatusBadge running={b.running} status={b.status} error={b.last_error} />
      </div>

      <div className="mt-3 flex flex-wrap gap-1">
        {b.symbols.map((s) => (
          <span key={s} className="rounded border border-line bg-black/30 px-1.5 py-0.5 font-mono text-[11px] text-slate-300">
            {shortSymbol(s)}
          </span>
        ))}
        <span className="rounded border border-line px-1.5 py-0.5 font-mono text-[11px] text-muted">{b.timeframe}</span>
      </div>

      <div className="mt-3 grid grid-cols-2 gap-2">
        <div>
          <div className="text-[10.5px] uppercase tracking-wider text-muted">權益</div>
          <div className="num font-mono text-lg font-semibold text-slate-50">{b.equity !== null ? fmtNum(b.equity) : '—'}</div>
        </div>
        <div>
          <div className="text-[10.5px] uppercase tracking-wider text-muted">{diff !== null ? 'AI 貢獻' : '最後執行'}</div>
          {diff !== null ? (
            <div className={`num font-mono text-lg font-semibold ${pnlClass(diff)}`}>{fmtSigned(diff)}</div>
          ) : (
            <div className="mt-1 text-sm text-slate-300" title={fmtTime(b.last_run_at, true)}>{b.last_run_at ? timeAgo(b.last_run_at) : '—'}</div>
          )}
        </div>
      </div>

      {b.halted_reason && (
        <div className="mt-3 flex gap-1.5 rounded-lg border border-down/30 bg-down/10 px-2.5 py-1.5 text-xs text-red-200">
          <Icons.shield className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          熔斷：{b.halted_reason}
        </div>
      )}
      {b.last_error && (
        <div className="mt-2 line-clamp-2 break-all rounded-lg border border-down/20 bg-down/5 px-2.5 py-1.5 font-mono text-[11px] text-red-300" title={b.last_error}>
          {b.last_error}
        </div>
      )}

      <div className="mt-3 flex items-center justify-between gap-2 border-t border-line pt-3">
        <span className="text-[11px] text-muted">{b.last_run_at ? `最後執行 ${fmtShortTime(b.last_run_at)}` : '尚未執行'}</span>
        <button
          className={`btn btn-sm ${b.running ? 'btn-danger' : 'btn-success'}`}
          onClick={(e) => {
            e.stopPropagation()
            onToggle()
          }}
          disabled={busy}
        >
          {busy ? <Spinner className="h-3.5 w-3.5" /> : b.running ? <Icons.stop className="h-3.5 w-3.5" /> : <Icons.play className="h-3.5 w-3.5" />}
          {b.running ? '停止' : '啟動'}
        </button>
      </div>
    </div>
  )
}

// ------------------------------ equity ------------------------------
function EquityPanel({ bot }: { bot: Bot }) {
  const [hours, setHours] = useState(168)
  const { data, loading } = useLoader(() => api.equity(bot.id, hours), [bot.id, hours], REFRESH)
  const points = useMemo(
    () => (data ?? []).map((p) => ({ t: parseDate(p.ts)?.getTime() ?? 0, equity: p.equity, baseline: p.baseline_equity })),
    [data],
  )
  const last = data?.[data.length - 1]
  const first = data?.[0]
  const hasBaseline = !!last && last.baseline_equity !== null
  const diff = hasBaseline ? last.equity - (last.baseline_equity as number) : null
  const diffPct = hasBaseline && last.baseline_equity ? (diff! / last.baseline_equity) * 100 : null
  const chg = first && last && first.equity ? ((last.equity - first.equity) / first.equity) * 100 : null
  return (
    <Card
      title="權益曲線"
      icon={<Icons.chart />}
      actions={
        <div className="flex rounded-lg border border-line p-0.5">
          {[
            [24, '1D'],
            [168, '7D'],
            [720, '30D'],
          ].map(([h, l]) => (
            <button key={h} className={`rounded-md px-2 py-0.5 text-xs transition-colors ${hours === h ? 'bg-gold/15 text-gold' : 'text-muted hover:text-slate-200'}`} onClick={() => setHours(h as number)}>
              {l}
            </button>
          ))}
        </div>
      }
    >
      <div className="mb-3 grid grid-cols-2 gap-3 sm:grid-cols-3">
        <div>
          <div className="text-[10.5px] uppercase tracking-wider text-muted">目前權益</div>
          <div className="num font-mono text-xl font-semibold text-slate-50">{last ? fmtNum(last.equity) : '—'}</div>
          <div className="text-xs"><Change v={chg} /></div>
        </div>
        {hasBaseline && (
          <>
            <div>
              <div className="text-[10.5px] uppercase tracking-wider text-muted">無 AI 對照組</div>
              <div className="num font-mono text-xl font-semibold text-slate-400">{fmtNum(last!.baseline_equity)}</div>
            </div>
            <div className="rounded-lg border border-gold/30 bg-gold/[0.06] px-3 py-1.5">
              <div className="flex items-center gap-1 text-[10.5px] uppercase tracking-wider text-gold">
                <Icons.sparkle className="h-3 w-3" /> AI 貢獻
              </div>
              <div className={`num font-mono text-xl font-semibold ${pnlClass(diff)}`}>{fmtSigned(diff)}</div>
              <div className="text-xs"><Change v={diffPct} /></div>
            </div>
          </>
        )}
      </div>
      {loading && !data ? <div className="skeleton h-[260px]" /> : points.length ? <EquityChart data={points} /> : <Empty title="尚無權益資料" hint="Bot 啟動後每次輪詢會記錄一次權益。" />}
    </Card>
  )
}

// ------------------------------ positions ------------------------------
function PositionsPanel({ bot }: { bot: Bot }) {
  const { data, loading, reload } = useLoader(() => api.botPositions(bot.id), [bot.id, bot.running], REFRESH)
  const { busy, run } = useAction()
  const confirm = useConfirm()
  const toast = useToast()
  const close = async (instrument: string) => {
    const ok = await confirm({ title: `平倉 ${shortSymbol(instrument)}？`, message: '將以市價全部平倉（同樣經過風控流程）。', confirmText: '平倉', danger: true })
    if (!ok) return
    const r = await run(`close-${instrument}`, () => api.botSignal(bot.id, { instrument, action: 'close' }))
    if (r) {
      if (r.approved) {
        toast.success('已送出平倉')
        reload()
      } else toast.error(`平倉未執行：${r.reasons.join('；')}`)
    }
  }
  const bal = data?.balance
  return (
    <Card
      title="目前持倉"
      icon={<Icons.exchange />}
      actions={
        data?.positions.length ? <span className="badge badge-gold">{data.positions.length} 筆</span> : undefined
      }
      bodyClass="p-0"
    >
      {!bot.running ? (
        <Empty icon={<Icons.stop className="h-5 w-5" />} title="Bot 未運行" hint="持倉與餘額只在 Bot 運行中時可查詢。" />
      ) : loading && !data ? (
        <Skeleton className="p-4" />
      ) : (
        <>
          {bal && (
            <div className="grid grid-cols-2 gap-3 border-b border-line px-4 py-3">
              <div>
                <div className="text-[10.5px] uppercase tracking-wider text-muted">權益（{bal.currency}）</div>
                <div className="num font-mono text-lg font-semibold">{fmtNum(bal.total)}</div>
              </div>
              <div>
                <div className="text-[10.5px] uppercase tracking-wider text-muted">可用資金</div>
                <div className="num font-mono text-lg font-semibold">{fmtNum(bal.free)}</div>
                <div className="text-[11px] text-muted">{bal.total ? `${fmtNum((bal.free / bal.total) * 100, 1)}% 可用` : ''}</div>
              </div>
            </div>
          )}
          {!data?.positions.length ? (
            <Empty title="目前沒有持倉" />
          ) : (
            <div className="tbl-wrap rounded-none border-0">
              <table className="tbl">
                <thead>
                  <tr>
                    <th>交易對</th>
                    <th>方向</th>
                    <th className="r">數量</th>
                    <th className="r">開倉價</th>
                    <th className="r">標記價</th>
                    <th className="r">槓桿</th>
                    <th className="r">未實現損益</th>
                    <th className="r">止損 / 止盈</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {data.positions.map((p) => {
                    const pct = p.entry_price && p.quantity ? (p.unrealized_pnl / (Math.abs(p.quantity) * p.entry_price)) * 100 * (p.leverage || 1) : null
                    return (
                      <tr key={p.instrument}>
                        <td className="font-mono font-semibold text-slate-100">{shortSymbol(p.instrument)}</td>
                        <td><SideBadge side={p.side} /></td>
                        <td className="r font-mono">{fmtQty(Math.abs(p.quantity))}</td>
                        <td className="r font-mono">{fmtPrice(p.entry_price)}</td>
                        <td className="r font-mono">{fmtPrice(p.mark_price)}</td>
                        <td className="r font-mono text-gold">{p.leverage}x</td>
                        <td className={`r font-mono ${pnlClass(p.unrealized_pnl)}`}>
                          {fmtSigned(p.unrealized_pnl)}
                          {pct !== null && <div className="text-[11px]">({fmtSigned(pct, 2, '%')})</div>}
                        </td>
                        <td className="r whitespace-nowrap font-mono text-xs">
                          <span className="text-down">{fmtPrice(p.stop_loss)}</span>
                          <span className="text-muted"> / </span>
                          <span className="text-up">{fmtPrice(p.take_profit)}</span>
                        </td>
                        <td className="r">
                          <button className="btn btn-danger btn-sm" onClick={() => close(p.instrument)} disabled={busy === `close-${p.instrument}`}>
                            {busy === `close-${p.instrument}` && <Spinner className="h-3 w-3" />} 平倉
                          </button>
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}
    </Card>
  )
}

// ------------------------------ tuning ------------------------------
const METRIC_ROWS: [keyof TuningRun['current_metrics'], string, 'pct' | 'num' | 'int'][] = [
  ['total_return_pct', '總報酬', 'pct'],
  ['max_drawdown_pct', '最大回撤', 'pct'],
  ['sharpe', 'Sharpe', 'num'],
  ['win_rate_pct', '勝率', 'pct'],
  ['trades', '成交數', 'int'],
]

function TuningPanel({ bot, botsById }: { bot: Bot | null; botsById: Record<number, Bot> }) {
  const { data, loading, reload } = useLoader(() => api.tuning(bot?.id), [bot?.id], REFRESH)
  const { busy, run } = useAction()
  const confirm = useConfirm()
  const toast = useToast()
  const tuneOn = bot?.copilot?.tune
  const tuneNow = async () => {
    if (!bot) return
    const r = await run('tune', () => api.tuneNow(bot.id))
    if (r) {
      reload()
      if (r.status === 'failed') toast.error(`微調失敗：${r.note ?? ''}`)
      else toast.success(`微調完成：${TUNE_STATUS[r.status]?.label ?? r.status}`)
    }
  }
  const apply = async (t: TuningRun) => {
    const ok = await confirm({ title: '套用這組參數？', message: '新參數會寫入此 Bot 的策略參數覆寫，運行中的 Bot 立即生效。', confirmText: '套用' })
    if (!ok) return
    if (await run(`apply-${t.id}`, () => api.applyTuning(t.id), '已套用')) reload()
  }
  if (!bot && !loading && !data?.length) return null
  return (
    <Card
      title="AI 參數微調"
      icon={<Icons.sparkle />}
      actions={
        bot && (
          <button className="btn btn-secondary btn-sm" onClick={tuneNow} disabled={busy === 'tune' || !bot.running} title={!bot.running ? 'Bot 需在運行中' : undefined}>
            {busy === 'tune' ? <Spinner className="h-3.5 w-3.5" /> : <Icons.bolt className="h-3.5 w-3.5" />}
            {busy === 'tune' ? '微調中（回測比較，約數分鐘）…' : '立即微調'}
          </button>
        )
      }
    >
      {loading && !data ? (
        <Skeleton rows={2} />
      ) : !data?.length ? (
        <Empty
          icon={<Icons.sparkle className="h-5 w-5" />}
          title="尚無微調紀錄"
          hint={bot && !tuneOn ? '此 Bot 未開啟「參數微調」，可在 Bot 設定的「AI 審核」區塊開啟。' : 'AI 會依設定的間隔自動提出參數建議，並以樣本外回測比較。'}
        />
      ) : (
        <div className="space-y-3">
          {data.map((t) => {
            const st = TUNE_STATUS[t.status] ?? { label: t.status, cls: 'badge-gray' }
            const keys = Array.from(new Set([...Object.keys(t.current_params ?? {}), ...Object.keys(t.proposed_params ?? {})])).filter(
              (k) => JSON.stringify(t.current_params?.[k]) !== JSON.stringify(t.proposed_params?.[k]),
            )
            const hasMetrics = Object.keys(t.current_metrics ?? {}).length > 0 || Object.keys(t.proposed_metrics ?? {}).length > 0
            return (
              <div key={t.id} className="rounded-xl border border-line bg-[#0f1216] p-4">
                <div className="flex flex-wrap items-center gap-2">
                  <span className={`badge ${st.cls}`}>{st.label}</span>
                  <span className="font-mono text-xs text-muted">{fmtTime(t.ts)}</span>
                  {!bot && <span className="text-xs text-gold/90">{botsById[t.bot_id]?.name ?? `Bot #${t.bot_id}`}</span>}
                  {(t.status === 'proposed' || t.status === 'rejected') && (
                    <button className="btn btn-primary btn-sm ml-auto" onClick={() => apply(t)} disabled={busy === `apply-${t.id}`}>
                      {busy === `apply-${t.id}` ? <Spinner className="h-3.5 w-3.5" /> : <Icons.check className="h-3.5 w-3.5" />} 套用
                    </button>
                  )}
                </div>
                <div className="mt-3 grid grid-cols-1 gap-3 lg:grid-cols-2">
                  <div>
                    <div className="label">參數變更</div>
                    {keys.length === 0 ? (
                      <div className="text-xs text-muted">沒有變更</div>
                    ) : (
                      <table className="tbl rounded-lg border border-line">
                        <tbody>
                          {keys.map((k) => (
                            <tr key={k}>
                              <td className="text-slate-300">
                                {paramLabel(k)} <span className="font-mono text-[10px] text-slate-500">{k}</span>
                              </td>
                              <td className="r font-mono text-muted">{String(t.current_params?.[k] ?? '—')}</td>
                              <td className="w-6 text-center text-muted">→</td>
                              <td className="font-mono font-semibold text-gold">{String(t.proposed_params?.[k] ?? '—')}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    )}
                  </div>
                  {hasMetrics && (
                    <div>
                      <div className="label">樣本外回測比較</div>
                      <table className="tbl rounded-lg border border-line">
                        <thead>
                          <tr>
                            <th>指標</th>
                            <th className="r">目前參數</th>
                            <th className="r">建議參數</th>
                          </tr>
                        </thead>
                        <tbody>
                          {METRIC_ROWS.map(([k, label, kind]) => {
                            const a = t.current_metrics?.[k] as number | undefined
                            const b = t.proposed_metrics?.[k] as number | undefined
                            const better = a !== undefined && b !== undefined && (k === 'max_drawdown_pct' ? b < a : k === 'trades' ? null : b > a)
                            const f = (v: number | undefined) => (v === undefined || v === null ? '—' : kind === 'pct' ? `${fmtNum(v)}%` : kind === 'int' ? String(v) : fmtNum(v))
                            return (
                              <tr key={k}>
                                <td className="text-slate-300">{label}</td>
                                <td className="r font-mono text-muted">{f(a)}</td>
                                <td className={`r font-mono ${better === true ? 'text-up' : better === false ? 'text-down' : 'text-slate-200'}`}>{f(b)}</td>
                              </tr>
                            )
                          })}
                        </tbody>
                      </table>
                    </div>
                  )}
                </div>
                {t.reasoning && <p className="mt-3 text-sm leading-relaxed text-slate-300">{t.reasoning}</p>}
                {t.note && <p className="mt-1 text-xs text-muted">{t.note}</p>}
              </div>
            )
          })}
        </div>
      )}
    </Card>
  )
}

// ------------------------------ trades ------------------------------
function TradesTable({ botId, names }: { botId: number | null; names: Record<number, string> }) {
  const { data, loading } = useLoader(() => api.trades(botId, 200), [botId], REFRESH)
  if (loading && !data) return <Skeleton className="p-4" />
  if (!data?.length) return <Empty title="尚無成交紀錄" />
  return (
    <div className="tbl-wrap max-h-[560px] rounded-none border-0">
      <table className="tbl">
        <thead>
          <tr>
            <th>時間</th>
            {!botId && <th>Bot</th>}
            <th>交易對</th>
            <th>方向</th>
            <th className="r">數量</th>
            <th className="r">價格</th>
            <th className="r">手續費</th>
            <th className="r">已實現損益</th>
            <th>來源</th>
            <th>狀態</th>
            <th>備註</th>
          </tr>
        </thead>
        <tbody>
          {data.map((t) => (
            <tr key={t.id}>
              <td className="whitespace-nowrap font-mono text-xs text-muted">{fmtTime(t.ts, true)}</td>
              {!botId && <td className="whitespace-nowrap text-xs text-gold/90">{names[t.bot_id] ?? `#${t.bot_id}`}</td>}
              <td className="font-mono font-semibold text-slate-100">{shortSymbol(t.instrument)}</td>
              <td className="whitespace-nowrap">
                <SideBadge side={t.side} />
                {t.reduce_only && <span className="ml-1 text-[11px] text-muted">平倉</span>}
              </td>
              <td className="r font-mono">{fmtQty(t.quantity)}</td>
              <td className="r font-mono">{fmtPrice(t.price)}</td>
              <td className="r font-mono text-muted">{fmtNum(t.fee, 4)}</td>
              <td className={`r font-mono ${pnlClass(t.realized_pnl)}`}>{t.realized_pnl === null ? '—' : fmtSigned(t.realized_pnl)}</td>
              <td><SourceChip source={t.source} /></td>
              <td>
                <span className={`badge ${t.status === 'filled' ? 'badge-green' : t.status === 'error' || t.status === 'rejected' ? 'badge-red' : 'badge-gray'}`}>
                  {{ filled: '成交', error: '錯誤', rejected: '拒絕', open: '掛單', canceled: '取消' }[t.status] ?? t.status}
                </span>
              </td>
              <td className="max-w-[240px] truncate text-xs text-muted" title={t.note ?? ''}>{t.note ?? '—'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

// ------------------------------ decisions ------------------------------
function SlTp({ d }: { d: DecisionPayload }) {
  if (d.new_stop !== undefined && d.new_stop !== null) return <span className="text-gold">→ {fmtPrice(d.new_stop)}</span>
  if (d.stop_loss == null && d.take_profit == null) return <span className="text-muted">—</span>
  return (
    <>
      <span className="text-down">{fmtPrice(d.stop_loss)}</span>
      <span className="text-muted"> / </span>
      <span className="text-up">{fmtPrice(d.take_profit)}</span>
    </>
  )
}

function Compare({ a, b }: { a: DecisionPayload; b: DecisionPayload }) {
  const rows: [string, (d: DecisionPayload) => ReactNode][] = [
    ['動作', (d) => ({ open_long: '開多', open_short: '開空', close: '平倉', hold: '觀望' } as Record<string, string>)[String(d.action)] ?? String(d.action ?? '—')],
    ['倉位 %', (d) => (d.size_pct !== undefined ? fmtNum(d.size_pct) : '—')],
    ['槓桿', (d) => (d.leverage ? `${d.leverage}x` : '—')],
    ['止損', (d) => fmtPrice(d.stop_loss)],
    ['止盈', (d) => fmtPrice(d.take_profit)],
    ['信心', (d) => (d.confidence !== undefined ? fmtNum(d.confidence) : '—')],
  ]
  return (
    <table className="tbl rounded-lg border border-line">
      <thead>
        <tr>
          <th />
          <th className="r">原始訊號</th>
          <th className="r">AI 調整後</th>
        </tr>
      </thead>
      <tbody>
        {rows.map(([label, f]) => {
          const x = f(a)
          const y = f(b)
          return (
            <tr key={label}>
              <td className="text-muted">{label}</td>
              <td className="r font-mono">{x}</td>
              <td className={`r font-mono ${String(x) !== String(y) ? 'font-semibold text-gold' : ''}`}>{y}</td>
            </tr>
          )
        })}
      </tbody>
    </table>
  )
}

function DecisionsTable({ botId, names }: { botId: number | null; names: Record<number, string> }) {
  const [hideHold, setHideHold] = useState(true)
  const { data, loading } = useLoader(() => api.decisions(botId, 100, hideHold), [botId, hideHold], REFRESH)
  const [open, setOpen] = useState<Record<number, boolean>>({})
  const toggle = (
    <label className="flex cursor-pointer items-center gap-2 border-b border-line px-4 py-2 text-xs text-muted">
      <input type="checkbox" className="accent-[#F0B90B]" checked={hideHold} onChange={(e) => setHideHold(e.target.checked)} />
      隱藏「觀望 / 維持」（AI 沒有動作的判斷）
    </label>
  )
  if (loading && !data) return <Skeleton className="p-4" />
  if (!data?.length)
    return (
      <>
        {toggle}
        <Empty icon={<Icons.brain className="h-5 w-5" />} title="尚無決策紀錄" hint="策略、AI 或 TradingView 產生訊號後會記錄在這裡（含被風控拒絕的）。" />
      </>
    )
  const cols = botId ? 9 : 10
  return (
    <>
    {toggle}
    <div className="tbl-wrap max-h-[640px] rounded-none border-0">
      <table className="tbl">
        <thead>
          <tr>
            <th className="w-6" />
            <th>時間</th>
            {!botId && <th>Bot</th>}
            <th>交易對</th>
            <th>來源</th>
            <th>動作</th>
            <th>結果</th>
            <th className="r">倉位 / 信心</th>
            <th className="r">止損 / 止盈</th>
            <th>說明</th>
          </tr>
        </thead>
        <tbody>
          {data.map((d) => {
            const act = actionLabel(d)
            const cp = d.decision?.copilot
            const v = cp ? VERDICT[cp.verdict] ?? { label: cp.verdict, cls: 'badge-gray' } : null
            const isOpen = !!open[d.id]
            const summary = cp?.reasoning || d.decision?.reasoning || d.reasons[0] || ''
            return (
              <Fragment key={d.id}>
                <tr className="cursor-pointer" onClick={() => setOpen((o) => ({ ...o, [d.id]: !isOpen }))}>
                  <td className="text-muted">
                    <Icons.chevron className={`h-3.5 w-3.5 transition-transform ${isOpen ? 'rotate-90 text-gold' : ''}`} />
                  </td>
                  <td className="whitespace-nowrap font-mono text-xs text-muted">{fmtTime(d.ts, true)}</td>
                  {!botId && <td className="whitespace-nowrap text-xs text-gold/90">{names[d.bot_id] ?? `#${d.bot_id}`}</td>}
                  <td className="font-mono font-semibold text-slate-100">{shortSymbol(d.instrument)}</td>
                  <td><SourceChip source={d.source} /></td>
                  <td className={`whitespace-nowrap font-medium ${act.cls}`}>{act.text}</td>
                  <td className="whitespace-nowrap">
                    {d.action === 'hold' ? (
                      <span className="mr-1.5 inline-flex h-5 w-5 items-center justify-center rounded-full bg-slate-500/15 text-muted" title="觀望，無需執行">—</span>
                    ) : (
                      <span className={`mr-1.5 inline-flex h-5 w-5 items-center justify-center rounded-full ${d.approved ? 'bg-up/15 text-up' : 'bg-down/15 text-down'}`} title={d.approved ? '已執行' : '未執行'}>
                        {d.approved ? <Icons.check className="h-3 w-3" /> : <Icons.x className="h-3 w-3" />}
                      </span>
                    )}
                    {v && <span className={`badge ${v.cls}`}>{v.label}</span>}
                  </td>
                  <td className="r whitespace-nowrap font-mono text-xs">
                    {d.decision?.size_pct ? `${fmtNum(d.decision.size_pct, 1)}%` : '—'}
                    <span className="text-muted"> / {d.decision?.confidence !== undefined ? fmtNum(d.decision.confidence) : '—'}</span>
                  </td>
                  <td className="r whitespace-nowrap font-mono text-xs"><SlTp d={d.decision ?? {}} /></td>
                  <td className="max-w-[340px]">
                    <div className="truncate text-xs text-slate-300" title={summary}>{summary || '—'}</div>
                    {d.reasons.length > 0 && <div className="truncate text-[11px] text-muted">{d.reasons.join('；')}</div>}
                  </td>
                </tr>
                {isOpen && (
                  <tr className="!bg-[#0d1014]">
                    <td colSpan={cols} className="px-4 py-4">
                      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
                        <div className="space-y-3 text-sm">
                          {d.decision?.reasoning && (
                            <div>
                              <div className="label">決策理由</div>
                              <p className="whitespace-pre-wrap leading-relaxed text-slate-200">{d.decision.reasoning}</p>
                            </div>
                          )}
                          {cp && (
                            <div className="rounded-lg border border-gold/30 bg-gold/[0.05] p-3">
                              <div className="mb-1 flex items-center gap-2 text-xs font-semibold text-gold">
                                <Icons.sparkle className="h-3.5 w-3.5" /> AI 審核 {v && <span className={`badge ${v.cls}`}>{v.label}</span>}
                              </div>
                              <p className="whitespace-pre-wrap leading-relaxed text-slate-200">{cp.reasoning}</p>
                              {cp.notes?.length > 0 && (
                                <ul className="mt-1.5 list-inside list-disc text-xs text-muted">
                                  {cp.notes.map((n, i) => <li key={i}>{n}</li>)}
                                </ul>
                              )}
                            </div>
                          )}
                          {d.reasons.length > 0 && (
                            <div>
                              <div className="label">風控 / 執行說明</div>
                              <ul className="list-inside list-disc space-y-0.5 text-xs text-slate-300">
                                {d.reasons.map((r, i) => <li key={i}>{r}</li>)}
                              </ul>
                            </div>
                          )}
                          <div className="flex flex-wrap gap-3 text-xs text-muted">
                            <span>模型：<span className="font-mono text-slate-300">{d.ai_model ?? '—'}</span></span>
                            <span>Tokens：<span className="font-mono text-slate-300">{d.input_tokens} in / {d.output_tokens} out</span></span>
                            {d.decision?.leverage && <span>槓桿：<span className="font-mono text-gold">{d.decision.leverage}x</span></span>}
                          </div>
                        </div>
                        <div className="space-y-3">
                          {cp?.original && (
                            <div>
                              <div className="label">原始訊號 vs AI 調整</div>
                              <Compare a={cp.original} b={d.decision} />
                            </div>
                          )}
                          {d.ai_raw ? (
                            <div>
                              <div className="label">AI 原始回覆</div>
                              <pre className="code-area max-h-[280px] overflow-auto whitespace-pre-wrap">{d.ai_raw}</pre>
                            </div>
                          ) : (
                            !cp && <div className="text-xs text-muted">此決策沒有 AI 原始回覆。</div>
                          )}
                        </div>
                      </div>
                    </td>
                  </tr>
                )}
              </Fragment>
            )
          })}
        </tbody>
      </table>
    </div>
    </>
  )
}
