import { useEffect, useMemo, useState } from 'react'
import { api, type Bot, type BotIn, type CopilotConfig, type Params, type RiskConfig, type Strategy } from '../../api'
import { CopilotForm, NumInput, ParamsForm, RiskForm, SymbolsInput, baseOf, copilotNeedsAi } from '../../components/forms'
import {
  AiBadge,
  BotStatusBadge,
  Card,
  Collapsible,
  CopyButton,
  Empty,
  Field,
  Icons,
  ListRow,
  Modal,
  Skeleton,
  Spinner,
  useAction,
  useConfirm,
  useLoader,
  useToast,
} from '../../components/ui'
import { kindLabel } from '../../lib/format'
import { useMeta } from '../../lib/meta'

interface Form {
  id: number | null
  name: string
  account_id: number | ''
  strategy_id: number | ''
  ai_model_id: number | ''
  symbols: string[]
  timeframe: string
  interval_sec: number
  risk: Partial<RiskConfig>
  copilot: CopilotConfig
  params_override: Params
}

/** Cache of python-strategy uses_ai detection (via /strategies/validate) */
const pyUsesAi = new Map<number, boolean>()

export function useStrategyUsesAi(strategy: Strategy | undefined): boolean {
  const meta = useMeta()
  const [py, setPy] = useState<boolean | undefined>(strategy ? pyUsesAi.get(strategy.id) : undefined)
  useEffect(() => {
    if (!strategy || strategy.kind !== 'python' || !strategy.code) return
    const cached = pyUsesAi.get(strategy.id)
    if (cached !== undefined) {
      setPy(cached)
      return
    }
    let alive = true
    api
      .validateStrategy(strategy.code)
      .then((r) => {
        const v = r.ok ? Boolean(r.uses_ai) : false
        pyUsesAi.set(strategy.id, v)
        if (alive) setPy(v)
      })
      .catch(() => {})
    return () => {
      alive = false
    }
  }, [strategy])
  if (!strategy) return false
  if (strategy.kind === 'python') return Boolean(py)
  return Boolean(meta.strategy_types.find((t) => t.type === strategy.kind)?.uses_ai)
}

export function webhookUrl(botId: number): string {
  return `${window.location.origin}/api/tradingview/webhook/${botId}`
}

export function webhookMessage(secret: string): string {
  return JSON.stringify(
    {
      passphrase: secret,
      action: '{{strategy.order.action}}',
      market_position: '{{strategy.market_position}}',
      size_pct: 10,
    },
    null,
    2,
  )
}

export function WebhookInfo({ bot, onRegenerated }: { bot: Bot; onRegenerated: (secret: string) => void }) {
  const { busy, run } = useAction()
  const confirm = useConfirm()
  const url = webhookUrl(bot.id)
  const msg = webhookMessage(bot.webhook_secret)
  const regen = async () => {
    const ok = await confirm({ title: '重新產生密語？', message: '舊的密語會立即失效，TradingView 上的 Alert 訊息需要一併更新。', confirmText: '重新產生', danger: true })
    if (!ok) return
    const r = await run('regen', () => api.regenWebhookSecret(bot.id), '已重新產生密語')
    if (r) onRegenerated(r.webhook_secret)
  }
  return (
    <div className="space-y-3 text-sm">
      <div>
        <div className="mb-1 flex items-center justify-between gap-2">
          <span className="label mb-0">Webhook URL</span>
          <CopyButton text={url} />
        </div>
        <div className="break-all rounded-lg border border-line bg-black/40 px-3 py-2 font-mono text-xs text-slate-200">{url}</div>
      </div>
      <div>
        <div className="mb-1 flex items-center justify-between gap-2">
          <span className="label mb-0">Alert 訊息（Message）</span>
          <div className="flex gap-1.5">
            <button className="btn btn-ghost btn-sm" onClick={regen} disabled={busy === 'regen'}>
              {busy === 'regen' ? <Spinner className="h-3.5 w-3.5" /> : <Icons.refresh className="h-3.5 w-3.5" />} 重新產生密語
            </button>
            <CopyButton text={msg} />
          </div>
        </div>
        <pre className="overflow-x-auto rounded-lg border border-line bg-black/40 px-3 py-2 font-mono text-xs leading-5 text-slate-200">{msg}</pre>
      </div>
      <ul className="list-inside list-disc space-y-0.5 text-xs text-muted">
        <li>action 支援 buy / sell / long / short / close；有 market_position（long / short / flat）時以它為準。</li>
        <li>可選欄位：symbol（覆寫交易對，如 BTCUSDT）、leverage、stop_loss、take_profit、comment。</li>
        <li>Bot 必須是「運行中」才會接收訊號；所有訊號同樣經過風控。TradingView 需能連到此網址（公開 HTTPS）。</li>
      </ul>
    </div>
  )
}

export default function Bots() {
  const meta = useMeta()
  const { data: bots, loading, reload, setData } = useLoader(() => api.listBots(), [])
  const { data: accounts } = useLoader(() => api.listAccounts(), [])
  const { data: strategies } = useLoader(() => api.listStrategies(), [])
  const { data: models } = useLoader(() => api.listAIModels(), [])
  const [form, setForm] = useState<Form | null>(null)
  const [openHook, setOpenHook] = useState<Record<number, boolean>>({})
  const { busy, run } = useAction()
  const confirm = useConfirm()
  const toast = useToast()

  const strat = useMemo(() => strategies?.find((s) => s.id === form?.strategy_id), [strategies, form?.strategy_id])
  const stratUsesAi = useStrategyUsesAi(strat)
  const aiRequired = form ? stratUsesAi || (strat?.kind !== 'tradingview' && copilotNeedsAi(form.copilot)) : false
  const strategyParams: Params = useMemo(() => ({ ...(strat?.params ?? {}) }), [strat])

  const openCreate = () =>
    setForm({
      id: null,
      name: '',
      account_id: accounts?.[0]?.id ?? '',
      strategy_id: strategies?.find((s) => s.status === 'active')?.id ?? '',
      ai_model_id: models?.[0]?.id ?? '',
      symbols: ['crypto:BTC/USDT:perp'],
      timeframe: '15m',
      interval_sec: 60,
      risk: {},
      copilot: { ...meta.copilot_defaults, tune_ranges: {} },
      params_override: {},
    })

  const openEdit = (b: Bot) =>
    setForm({
      id: b.id,
      name: b.name,
      account_id: b.account_id,
      strategy_id: b.strategy_id,
      ai_model_id: b.ai_model_id ?? '',
      symbols: [...b.symbols],
      timeframe: b.timeframe,
      interval_sec: b.interval_sec,
      risk: diffFrom(b.risk as Record<string, unknown>, meta.risk_defaults as unknown as Record<string, unknown>) as Partial<RiskConfig>,
      copilot: { ...meta.copilot_defaults, ...b.copilot, tune_ranges: { ...(b.copilot?.tune_ranges ?? {}) } },
      params_override: { ...(b.params_override ?? {}) },
    })

  const save = async () => {
    if (!form) return
    if (!form.name.trim()) return toast.error('請輸入 Bot 名稱')
    if (!form.account_id) return toast.error('請選擇交易所帳戶')
    if (!form.strategy_id) return toast.error('請選擇策略')
    if (!form.symbols.length) return toast.error('至少需要一個交易對')
    if (aiRequired && !form.ai_model_id) return toast.error('此設定需要 AI 模型（AI 策略或已開啟 AI 副駕駛）')
    const base = strat?.params ?? {}
    const override: Params = {}
    for (const [k, v] of Object.entries(form.params_override)) if (JSON.stringify(base[k]) !== JSON.stringify(v)) override[k] = v
    const body: BotIn = {
      name: form.name.trim(),
      account_id: Number(form.account_id),
      strategy_id: Number(form.strategy_id),
      ai_model_id: form.ai_model_id ? Number(form.ai_model_id) : null,
      symbols: form.symbols,
      timeframe: form.timeframe,
      interval_sec: form.interval_sec,
      risk: form.risk,
      copilot: form.copilot,
      params_override: override,
    }
    const r = await run('save', () => (form.id ? api.updateBot(form.id, body) : api.createBot(body)), '已儲存')
    if (r) {
      setForm(null)
      reload()
    }
  }

  const toggleRun = async (b: Bot) => {
    const r = await run(`run-${b.id}`, () => (b.running ? api.stopBot(b.id) : api.startBot(b.id)), b.running ? '已停止' : '已啟動')
    if (r) reload()
  }

  const remove = async (b: Bot) => {
    if (!(await confirm({ title: `刪除 Bot「${b.name}」？`, danger: true, confirmText: '刪除', message: '會一併刪除此 Bot 的成交紀錄、決策紀錄與權益資料，無法復原。' }))) return
    if (await run(`del-${b.id}`, () => api.deleteBot(b.id), '已刪除')) reload()
  }

  const accName = (id: number) => accounts?.find((a) => a.id === id)?.name ?? `#${id}`
  const selectableStrategies = (strategies ?? []).filter((s) => s.status === 'active' || s.id === form?.strategy_id)
  const pendingCount = (strategies ?? []).filter((s) => s.status !== 'active').length

  return (
    <Card
      title="Bots 機器人"
      icon={<Icons.bot />}
      actions={
        <button className="btn btn-primary btn-sm" onClick={openCreate}>
          <Icons.plus className="h-3.5 w-3.5" /> 建立 Bot
        </button>
      }
    >
      {loading ? (
        <Skeleton />
      ) : !bots?.length ? (
        <Empty icon={<Icons.bot className="h-5 w-5" />} title="尚未建立 Bot" hint="Bot = 交易所帳戶 + 策略 + 交易對 + 風控 +（選用）AI 副駕駛。" />
      ) : (
        <div className="space-y-2.5">
          {bots.map((b) => {
            const hookOpen = openHook[b.id] ?? b.strategy_kind === 'tradingview'
            return (
              <ListRow
                key={b.id}
                active={b.running}
                icon={<Icons.bot className="h-5 w-5" />}
                title={b.name}
                subtitle={
                  <>
                    {b.strategy_name ?? '—'}
                    <span className="text-muted">
                      {' '}· {kindLabel(b.strategy_kind)} · {accName(b.account_id)} · {b.symbols.map(baseOf).join(', ')} · {b.timeframe}
                    </span>
                  </>
                }
                badges={
                  <>
                    <BotStatusBadge running={b.running} status={b.status} error={b.last_error} />
                    {(b.copilot_active || (b.ai_model_id && (b.copilot?.review || b.copilot?.manage || b.copilot?.tune))) && <AiBadge />}
                  </>
                }
                actions={
                  <>
                    <button className={`btn btn-sm ${b.running ? 'btn-danger' : 'btn-success'}`} onClick={() => toggleRun(b)} disabled={busy === `run-${b.id}`}>
                      {busy === `run-${b.id}` ? <Spinner className="h-3.5 w-3.5" /> : b.running ? <Icons.stop className="h-3.5 w-3.5" /> : <Icons.play className="h-3.5 w-3.5" />}
                      {b.running ? '停止' : '啟動'}
                    </button>
                    <button className="btn btn-ghost btn-sm" onClick={() => setOpenHook((x) => ({ ...x, [b.id]: !hookOpen }))}>
                      <Icons.webhook className="h-3.5 w-3.5" /> Webhook
                    </button>
                    <button className="btn btn-ghost btn-sm" onClick={() => (b.running ? toast.info('請先停止 Bot 再修改設定') : openEdit(b))}>
                      <Icons.edit className="h-3.5 w-3.5" /> 編輯
                    </button>
                    <button className="btn btn-danger btn-sm" onClick={() => remove(b)} aria-label="刪除">
                      <Icons.trash className="h-3.5 w-3.5" />
                    </button>
                  </>
                }
              >
                {hookOpen ? (
                  <div>
                    <div className="mb-2 flex items-center gap-2 text-sm font-medium text-slate-200">
                      <Icons.webhook className="h-4 w-4 text-gold" /> TradingView Webhook
                    </div>
                    <WebhookInfo bot={b} onRegenerated={(s) => setData((xs) => (xs ? xs.map((x) => (x.id === b.id ? { ...x, webhook_secret: s } : x)) : xs))} />
                  </div>
                ) : undefined}
              </ListRow>
            )
          })}
        </div>
      )}

      <Modal
        open={!!form}
        onClose={() => setForm(null)}
        size="xl"
        title={form?.id ? '編輯 Bot' : '建立 Bot'}
        footer={
          <>
            <button className="btn btn-secondary" onClick={() => setForm(null)}>取消</button>
            <button className="btn btn-primary" onClick={save} disabled={busy === 'save'}>
              {busy === 'save' && <Spinner />} 儲存
            </button>
          </>
        }
      >
        {form && (
          <div className="space-y-5">
            {/* 基本設定 */}
            <section className="grid grid-cols-1 gap-3 md:grid-cols-2">
              <Field label="名稱">
                <input className="input" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="例如：BTC 均線 + AI" autoFocus />
              </Field>
              <Field label="交易所帳戶">
                <select className="input" value={form.account_id} onChange={(e) => setForm({ ...form, account_id: e.target.value ? Number(e.target.value) : '' })}>
                  <option value="">請選擇</option>
                  {accounts?.map((a) => (
                    <option key={a.id} value={a.id}>
                      {a.name}
                      {a.paper ? '（模擬）' : '（實盤）'}
                    </option>
                  ))}
                </select>
              </Field>
              <Field label="策略" hint={pendingCount ? `有 ${pendingCount} 個策略待審核，需先審核啟用才能選擇` : undefined}>
                <select
                  className="input"
                  value={form.strategy_id}
                  onChange={(e) => setForm({ ...form, strategy_id: e.target.value ? Number(e.target.value) : '', params_override: {}, copilot: { ...form.copilot, tune_ranges: {} } })}
                >
                  <option value="">請選擇</option>
                  {selectableStrategies.map((s) => (
                    <option key={s.id} value={s.id}>
                      {s.name}（{kindLabel(s.kind)}）{s.status !== 'active' ? '— 待審核' : ''}
                    </option>
                  ))}
                </select>
              </Field>
              <div className="grid grid-cols-2 gap-3">
                <Field label="K 線週期">
                  <select className="input" value={form.timeframe} onChange={(e) => setForm({ ...form, timeframe: e.target.value })}>
                    {meta.timeframes.map((t) => (
                      <option key={t} value={t}>{t}</option>
                    ))}
                  </select>
                </Field>
                <Field label="輪詢間隔（秒）">
                  <NumInput value={form.interval_sec} step={5} onChange={(v) => setForm({ ...form, interval_sec: v ?? 60 })} />
                </Field>
              </div>
              <Field label="交易對（USDT 永續）" className="md:col-span-2">
                <SymbolsInput value={form.symbols} onChange={(v) => setForm({ ...form, symbols: v })} />
              </Field>
            </section>

            {/* AI 副駕駛 */}
            {strat?.kind !== 'tradingview' && (
              <section className="rounded-2xl border border-gold/30 bg-gradient-to-b from-gold/[0.06] to-transparent p-4">
                <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
                  <div>
                    <div className="flex items-center gap-2 text-base font-semibold text-gold">
                      <Icons.sparkle className="h-5 w-5" /> AI 副駕駛
                    </div>
                    <div className="text-xs text-muted">策略決定方向，AI 負責把關與微調。AI 永遠不能改變方向或放寬止損。</div>
                  </div>
                  <div className="w-full sm:w-64">
                    <select
                      className={`input ${aiRequired && !form.ai_model_id ? 'border-down/70' : ''}`}
                      value={form.ai_model_id}
                      onChange={(e) => setForm({ ...form, ai_model_id: e.target.value ? Number(e.target.value) : '' })}
                    >
                      <option value="">{aiRequired ? '請選擇 AI 模型（必填）' : '不使用 AI'}</option>
                      {models?.map((m) => (
                        <option key={m.id} value={m.id}>{m.name}</option>
                      ))}
                    </select>
                  </div>
                </div>
                <CopilotForm value={form.copilot} defaults={meta.copilot_defaults} onChange={(c) => setForm({ ...form, copilot: c })} strategyParams={{ ...strategyParams, ...form.params_override }} strategyUsesAi={stratUsesAi} />
              </section>
            )}
            {strat?.kind === 'tradingview' && (
              <div className="rounded-xl border border-sky-500/30 bg-sky-500/10 p-3 text-xs text-sky-100">
                TradingView 訊號策略由 Webhook 觸發，儲存後可在 Bot 清單按「Webhook」取得網址與 Alert 訊息範本。
              </div>
            )}

            {/* 進階設定 */}
            <div className="space-y-2">
              <div className="text-xs font-medium uppercase tracking-wider text-muted">進階設定</div>
              <Collapsible title="風控設定" icon={<Icons.shield className="h-4 w-4" />}>
                <RiskForm value={form.risk} defaults={meta.risk_defaults} onChange={(r) => setForm({ ...form, risk: r })} />
              </Collapsible>
              {strat && strat.kind !== 'tradingview' && Object.keys(strategyParams).length > 0 && (
                <Collapsible title="策略參數（僅此 Bot）" icon={<Icons.chart className="h-4 w-4" />}>
                  <div className="mb-3 text-xs text-muted">只影響此 Bot，不會修改策略本身；AI 參數微調套用後也會寫在這裡。</div>
                  <ParamsForm defaults={strategyParams} value={{ ...strategyParams, ...form.params_override }} onChange={(p) => setForm({ ...form, params_override: p })} />
                </Collapsible>
              )}
            </div>
          </div>
        )}
      </Modal>
    </Card>
  )
}

function diffFrom(v: Record<string, unknown> | undefined, defaults: Record<string, unknown>): Record<string, unknown> {
  const out: Record<string, unknown> = {}
  for (const [k, x] of Object.entries(v ?? {})) if (JSON.stringify(defaults[k]) !== JSON.stringify(x)) out[k] = x
  return out
}
