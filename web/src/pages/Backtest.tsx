import { useEffect, useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { api, errMsg, type BacktestIn, type BacktestRun, type EntryResult, type Params, type RiskConfig } from '../api'
import { CandleChart, EquityChart } from '../components/charts'
import { MetricsPanel } from '../components/metrics'
import { DirectionBadge, EntryAnalysisView } from '../components/entry'
import { NumInput, ParamsForm, RiskForm, SymbolPicker, baseOf } from '../components/forms'
import { Card, Change, Collapsible, Empty, Field, Icons, SideBadge, Skeleton, Spinner, useAction, useConfirm, useLoader, useToast } from '../components/ui'
import { fmtNum, fmtPrice, fmtQty, fmtSigned, fmtTime, kindLabel, pnlClass } from '../lib/format'
import { useMeta } from '../lib/meta'
import { StrategyStatusBadge } from './settings/Strategies'
import { useStrategyUsesAi } from './settings/Bots'

const isoDate = (d: Date) => d.toISOString().slice(0, 10)

export default function Backtest() {
  const meta = useMeta()
  const [sp] = useSearchParams()
  const { data: strategies } = useLoader(() => api.listStrategies(), [])
  const { data: models } = useLoader(() => api.listAIModels(), [])
  const history = useLoader(() => api.listBacktests(), [])
  const cryptoEx = meta.exchanges.filter((e) => e.market === 'crypto' && !e.planned)

  const [strategyId, setStrategyId] = useState<number | ''>(sp.get('strategy') ? Number(sp.get('strategy')) : '')
  const [params, setParams] = useState<Params>({})
  const [aiModelId, setAiModelId] = useState<number | ''>('')
  const [maxAiCalls, setMaxAiCalls] = useState(200)
  const [exchange, setExchange] = useState(cryptoEx[0]?.id ?? 'binance')
  const [symbol, setSymbol] = useState('crypto:BTC/USDT:perp')
  const [timeframe, setTimeframe] = useState('1h')
  const [start, setStart] = useState(isoDate(new Date(Date.now() - 90 * 86400_000)))
  const [end, setEnd] = useState(isoDate(new Date()))
  const [cash, setCash] = useState(10000)
  const [fee, setFee] = useState(0.0005)
  const [slip, setSlip] = useState(0.0005)
  const [risk, setRisk] = useState<Partial<RiskConfig>>({})
  const [result, setResult] = useState<BacktestRun | null>(null)
  const [viewId, setViewId] = useState<number | null>(null)
  const { busy, run } = useAction()
  const confirm = useConfirm()
  const toast = useToast()

  const selectable = (strategies ?? []).filter((s) => s.kind !== 'tradingview')
  const strat = selectable.find((s) => s.id === strategyId)
  const usesAi = useStrategyUsesAi(strat)

  // 從策略頁「回測」按鈕進來：/backtest?strategy={id} → 預選該策略，並沿用它上次回測 / 審查的交易對與週期
  const qsStrategy = sp.get('strategy')
  useEffect(() => {
    if (!strategies) return
    const want = qsStrategy ? selectable.find((s) => s.id === Number(qsStrategy)) : undefined
    if (want) {
      setStrategyId(want.id)
      if (want.metrics?.symbol) setSymbol(want.metrics.symbol)
      if (want.metrics?.timeframe && meta.timeframes.includes(want.metrics.timeframe)) setTimeframe(want.metrics.timeframe)
    } else if (!selectable.some((s) => s.id === strategyId) && selectable.length) {
      setStrategyId(selectable[0].id)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [strategies, qsStrategy])
  useEffect(() => {
    setParams({ ...(strat?.params ?? {}) })
  }, [strat])
  useEffect(() => {
    if (usesAi && !aiModelId && models?.length) setAiModelId(models[0].id)
  }, [usesAi, models, aiModelId])

  const submit = async () => {
    if (!strat) return toast.error('請選擇策略')
    if (usesAi && !aiModelId) return toast.error('AI 策略回測需要選擇 AI 模型')
    if (start >= end) return toast.error('結束日期需晚於開始日期')
    const changed: Params = {}
    for (const [k, v] of Object.entries(params)) if (JSON.stringify(strat.params?.[k]) !== JSON.stringify(v)) changed[k] = v
    const body: BacktestIn = {
      strategy_id: strat.id,
      params: Object.keys(changed).length ? changed : null,
      ai_model_id: usesAi && aiModelId ? Number(aiModelId) : null,
      exchange_id: exchange,
      symbol,
      timeframe,
      start: `${start}T00:00:00`,
      end: `${end}T00:00:00`,
      initial_cash: cash,
      fee_rate: fee,
      slippage: slip,
      risk,
      max_ai_calls: maxAiCalls,
    }
    const r = await run('run', () => api.runBacktest(body))
    if (r) {
      setResult(r)
      setViewId(r.id)
      history.reload()
      toast.success('回測完成')
    }
  }

  const view = async (id: number) => {
    setViewId(id)
    const r = await run(`view-${id}`, () => api.getBacktest(id))
    if (r) setResult(r)
  }

  const remove = async (id: number) => {
    if (!(await confirm({ title: '刪除此回測紀錄？', danger: true, confirmText: '刪除' }))) return
    if (await run(`del-${id}`, () => api.deleteBacktest(id))) {
      if (viewId === id) {
        setResult(null)
        setViewId(null)
      }
      history.reload()
    }
  }

  return (
    <div className="space-y-5">
      <h1 className="text-xl font-semibold text-slate-50">回測</h1>
      <div className="grid grid-cols-1 gap-5 lg:grid-cols-[360px_minmax(0,1fr)]">
        <div className="space-y-5">
          <Card title="回測設定" icon={<Icons.chart />}>
            <div className="space-y-3.5">
              <Field label="策略" hint={strat ? kindLabel(strat.kind) : 'TradingView 訊號策略無法回測'}>
                <select className="input" value={strategyId} onChange={(e) => setStrategyId(e.target.value ? Number(e.target.value) : '')}>
                  {!selectable.length && <option value="">（尚無可回測的策略）</option>}
                  {selectable.map((s) => (
                    <option key={s.id} value={s.id}>
                      {s.name}
                      {s.status === 'pending_review' ? '（待審核）' : s.status === 'paper_only' ? '（模擬期）' : ''}
                    </option>
                  ))}
                </select>
              </Field>
              {strat && strat.status === 'pending_review' && (
                <div className="flex items-center gap-2 text-xs text-amber-200">
                  <StrategyStatusBadge status={strat.status} /> 尚未通過審查，到「設定 → 策略」查看審查報告。
                </div>
              )}
              {usesAi && (
                <div className="space-y-3 rounded-xl border border-gold/30 bg-gold/[0.06] p-3">
                  <div className="flex gap-2 text-xs text-amber-100">
                    <Icons.alert className="mt-0.5 h-4 w-4 shrink-0 text-gold" />
                    此策略使用 AI，回測每根 K 棒都可能呼叫 AI，會產生 API 費用。以「AI 呼叫上限」控制成本。
                  </div>
                  <Field label="AI 模型">
                    <select className="input" value={aiModelId} onChange={(e) => setAiModelId(e.target.value ? Number(e.target.value) : '')}>
                      <option value="">請選擇</option>
                      {models?.map((m) => (
                        <option key={m.id} value={m.id}>{m.name}</option>
                      ))}
                    </select>
                  </Field>
                  <Field label="AI 呼叫上限（max_ai_calls）">
                    <NumInput value={maxAiCalls} step={10} onChange={(v) => setMaxAiCalls(v ?? 200)} />
                  </Field>
                </div>
              )}
              <div className="grid grid-cols-2 gap-3">
                <Field label="交易所（行情）">
                  <select className="input" value={exchange} onChange={(e) => setExchange(e.target.value)}>
                    {cryptoEx.map((e) => (
                      <option key={e.id} value={e.id}>{e.label}</option>
                    ))}
                  </select>
                </Field>
                <Field label="K 線週期">
                  <select className="input" value={timeframe} onChange={(e) => setTimeframe(e.target.value)}>
                    {meta.timeframes.map((t) => (
                      <option key={t} value={t}>{t}</option>
                    ))}
                  </select>
                </Field>
              </div>
              <Field label="交易對">
                <SymbolPicker value={symbol} onChange={setSymbol} />
              </Field>
              <div className="grid grid-cols-2 gap-3">
                <Field label="開始日期（UTC）">
                  <input type="date" className="input font-mono" value={start} max={end} onChange={(e) => setStart(e.target.value)} />
                </Field>
                <Field label="結束日期（UTC）">
                  <input type="date" className="input font-mono" value={end} min={start} onChange={(e) => setEnd(e.target.value)} />
                </Field>
              </div>
              <Field label="初始資金（USDT）">
                <NumInput value={cash} step={100} onChange={(v) => setCash(v ?? 10000)} />
              </Field>
              <Collapsible title="進階設定" icon={<Icons.shield className="h-4 w-4" />}>
                <div className="space-y-4">
                  <div className="grid grid-cols-2 gap-3">
                    <Field label="手續費率" hint="0.0005 = 0.05%">
                      <NumInput value={fee} step={0.0001} onChange={(v) => setFee(v ?? 0)} />
                    </Field>
                    <Field label="滑價" hint="0.0005 = 0.05%">
                      <NumInput value={slip} step={0.0001} onChange={(v) => setSlip(v ?? 0)} />
                    </Field>
                  </div>
                  {strat && Object.keys(strat.params ?? {}).length > 0 && (
                    <div>
                      <div className="mb-2 flex items-center justify-between">
                        <span className="label mb-0">策略參數（本次覆寫）</span>
                        <button className="btn btn-ghost btn-sm" onClick={() => setParams({ ...(strat.params ?? {}) })}>重設</button>
                      </div>
                      <ParamsForm defaults={strat.params} value={params} onChange={setParams} />
                    </div>
                  )}
                  <div>
                    <div className="label mb-2">風控覆寫</div>
                    <div className="mb-2 text-xs text-muted">回測預設關閉單日熔斷與每小時下單上限。</div>
                    <RiskForm value={risk} defaults={{ ...meta.risk_defaults, daily_loss_limit_pct: 0, max_orders_per_hour: 10000 }} onChange={setRisk} />
                  </div>
                </div>
              </Collapsible>
              <button className="btn btn-primary w-full py-2.5" onClick={submit} disabled={busy === 'run' || !strat}>
                {busy === 'run' ? <Spinner /> : <Icons.play className="h-4 w-4" />}
                {busy === 'run' ? '回測中，請稍候…' : '開始回測'}
              </button>
            </div>
          </Card>

          <Card title="歷史紀錄" icon={<Icons.refresh />} bodyClass="p-2">
            {history.loading ? (
              <Skeleton rows={3} className="p-2" />
            ) : !history.data?.length ? (
              <Empty title="尚無回測紀錄" />
            ) : (
              <ul className="max-h-[480px] space-y-1 overflow-y-auto">
                {history.data.map((h) => (
                  <li key={h.id}>
                    <div
                      role="button"
                      tabIndex={0}
                      onClick={() => view(h.id)}
                      onKeyDown={(e) => e.key === 'Enter' && view(h.id)}
                      className={`group flex cursor-pointer items-center gap-2 rounded-lg px-3 py-2 transition-colors ${viewId === h.id ? 'bg-gold/10 ring-1 ring-gold/30' : 'hover:bg-panel2'}`}
                    >
                      <div className="min-w-0 flex-1">
                        <div className="truncate text-sm text-slate-100">{h.strategy_name}</div>
                        <div className="truncate font-mono text-[11px] text-muted">
                          {baseOf(h.instrument)} · {h.timeframe} · {fmtTime(h.created_at)}
                        </div>
                      </div>
                      <div className="text-right">
                        <Change v={h.metrics?.total_return_pct} />
                      </div>
                      {busy === `view-${h.id}` ? (
                        <Spinner className="h-3.5 w-3.5 text-gold" />
                      ) : (
                        <button
                          className="rounded p-1 text-slate-600 opacity-0 transition-opacity hover:bg-down/15 hover:text-down group-hover:opacity-100"
                          onClick={(e) => {
                            e.stopPropagation()
                            remove(h.id)
                          }}
                          aria-label="刪除"
                        >
                          <Icons.trash className="h-3.5 w-3.5" />
                        </button>
                      )}
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </Card>
        </div>

        <div className="min-w-0 space-y-5">
          {strat && <EntryNowCard strategyId={strat.id} exchange={exchange} symbol={symbol} timeframe={timeframe} usesAi={usesAi} />}
          {busy === 'run' ? (
            <div className="card flex min-h-[420px] flex-col items-center justify-center gap-3 text-muted">
              <Spinner className="h-8 w-8 text-gold" />
              <div className="text-sm">下載歷史資料並執行回測中…</div>
              {usesAi && <div className="text-xs">AI 策略回測可能需要數分鐘</div>}
            </div>
          ) : result ? (
            <Result r={result} />
          ) : (
            <div className="card">
              <Empty icon={<Icons.chart className="h-5 w-5" />} title="設定好參數後按「開始回測」" hint="或從左側歷史紀錄點選查看過去的結果。" />
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

function EntryNowCard({ strategyId, exchange, symbol, timeframe, usesAi }: { strategyId: number; exchange: string; symbol: string; timeframe: string; usesAi: boolean }) {
  const [res, setRes] = useState<EntryResult | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [err, setErr] = useState<string | null>(null)
  useEffect(() => {
    setRes(null)
    setErr(null)
  }, [strategyId, exchange, symbol, timeframe])
  const go = async (direction: 'long' | 'short' | null) => {
    setBusy(direction ?? 'signal')
    setErr(null)
    try {
      setRes(await api.entryAnalysis({ strategy_id: strategyId, exchange_id: exchange, symbol, timeframe, direction }))
    } catch (e) {
      setErr(errMsg(e))
    } finally {
      setBusy(null)
    }
  }
  const sig = res?.signal
  return (
    <Card
      title="現在進場分析"
      icon={<Icons.bolt />}
      actions={
        <>
          <button className="btn btn-primary btn-sm" onClick={() => go(null)} disabled={!!busy}>
            {busy === 'signal' ? <Spinner className="h-3.5 w-3.5" /> : <Icons.play className="h-3.5 w-3.5" />} 分析
          </button>
          <button className="btn btn-secondary btn-sm" onClick={() => go('long')} disabled={!!busy} title="沒有訊號時，假設現在做多">
            {busy === 'long' && <Spinner className="h-3.5 w-3.5" />} 假設做多
          </button>
          <button className="btn btn-secondary btn-sm" onClick={() => go('short')} disabled={!!busy} title="沒有訊號時，假設現在做空">
            {busy === 'short' && <Spinner className="h-3.5 w-3.5" />} 假設做空
          </button>
        </>
      }
    >
      {err ? (
        <div className="rounded-lg border border-down/40 bg-down/10 px-3 py-2 text-xs text-red-200">{err}</div>
      ) : !res ? (
        <div className="text-xs text-muted">
          用左側的策略、交易對與週期，看「現在」進場的盈虧比，以及掛單等更好價位的成交機率與期望值。{usesAi && 'AI 策略不會實際呼叫 AI，請用假設做多 / 做空。'}
        </div>
      ) : (
        <div className="space-y-3">
          {sig && (sig.action === 'open_long' || sig.action === 'open_short') ? (
            <div className="flex flex-wrap items-center gap-2 text-xs">
              <span className="text-muted">策略目前訊號：</span>
              <DirectionBadge d={sig.action === 'open_long' ? 'long' : 'short'} />
              {sig.reasoning && <span className="truncate text-slate-300">{sig.reasoning}</span>}
            </div>
          ) : (
            res.note && <div className="text-xs text-muted">{res.note}</div>
          )}
          {res.analysis && <EntryAnalysisView a={res.analysis} hypothetical={res.hypothetical} />}
        </div>
      )}
    </Card>
  )
}

function Result({ r }: { r: BacktestRun }) {
  const m = r.metrics ?? {}
  const equity = useMemo(() => r.equity_curve.map(([t, e]) => ({ t, equity: e })), [r])
  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <span className="font-semibold text-slate-100">{r.strategy_name}</span>
        <span className="font-mono text-gold">{baseOf(r.instrument)}/USDT</span>
        <span className="badge badge-gray">{r.timeframe}</span>
        {r.config?.start && (
          <span className="font-mono text-xs text-muted">
            {String(r.config.start).slice(0, 10)} → {String(r.config.end ?? '').slice(0, 10)}
          </span>
        )}
        <span className="ml-auto text-xs text-muted">#{r.id} · {fmtTime(r.created_at)}</span>
      </div>

      <MetricsPanel m={m} />

      <Card title="權益曲線" icon={<Icons.chart />}>
        {equity.length ? <EquityChart data={equity} initial={m.initial_equity} /> : <Empty title="沒有權益資料" />}
      </Card>

      {r.candles ? (
        <Card title="K 線與交易點位" icon={<Icons.chart />} actions={<span className="text-xs text-muted">▲ 買 ▼ 賣 · 灰色＝平倉</span>}>
          <CandleChart candles={r.candles} trades={r.trades} />
        </Card>
      ) : (
        <div className="rounded-xl border border-dashed border-line px-4 py-3 text-xs text-muted">歷史紀錄不含 K 線資料，重新執行回測即可查看 K 線與交易點位。</div>
      )}

      <Card title={`交易明細（${r.trades.length}）`} icon={<Icons.exchange />} bodyClass="p-0">
        {r.trades.length === 0 ? (
          <Empty title="此回測沒有任何成交" hint="可調整參數、拉長期間，或檢查下方被拒絕的訊號。" />
        ) : (
          <div className="tbl-wrap max-h-[440px] rounded-none border-0">
            <table className="tbl">
              <thead>
                <tr>
                  <th>時間</th>
                  <th>方向</th>
                  <th className="r">數量</th>
                  <th className="r">價格</th>
                  <th className="r">手續費</th>
                  <th className="r">已實現損益</th>
                  <th>原因</th>
                </tr>
              </thead>
              <tbody>
                {r.trades.map((t, i) => (
                  <tr key={i}>
                    <td className="whitespace-nowrap font-mono text-xs text-muted">{fmtTime(t.ts)}</td>
                    <td className="whitespace-nowrap">
                      <SideBadge side={t.side} />
                      {t.reduce_only && <span className="ml-1 text-[11px] text-muted">平倉</span>}
                    </td>
                    <td className="r font-mono">{fmtQty(t.quantity)}</td>
                    <td className="r font-mono">{fmtPrice(t.price)}</td>
                    <td className="r font-mono text-muted">{fmtNum(t.fee, 4)}</td>
                    <td className={`r font-mono ${pnlClass(t.realized_pnl)}`}>{t.realized_pnl === null ? '—' : fmtSigned(t.realized_pnl)}</td>
                    <td className="max-w-[260px] truncate text-xs text-muted" title={t.reason}>{t.reason || '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      {r.rejected && r.rejected.length > 0 && (
        <Collapsible title={`被風控拒絕的訊號（${r.rejected.length}）`} icon={<Icons.shield className="h-4 w-4" />}>
          <ul className="max-h-[320px] space-y-1 overflow-y-auto font-mono text-xs text-slate-300">
            {r.rejected.map((x, i) => (
              <li key={i} className="rounded px-2 py-1 odd:bg-white/[0.02]">{x}</li>
            ))}
          </ul>
        </Collapsible>
      )}
    </div>
  )
}
