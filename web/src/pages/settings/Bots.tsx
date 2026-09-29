import { useEffect, useMemo, useState } from 'react'
import { api, type AITraderIn, type Bot, type BotIn, type CopilotConfig, type EntryConfig, type Params, type Persona, type RiskConfig, type Strategy, type UniverseRules } from '../../api'
import { CoinChipsInput, CopilotForm, Segmented, NumInput, ParamsForm, RiskForm, SymbolsInput, baseOf, copilotNeedsAi } from '../../components/forms'
import {
  BotAiBadge,
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
  Toggle,
  useAction,
  useConfirm,
  useLoader,
  useToast,
} from '../../components/ui'
import { kindLabel } from '../../lib/format'
import { useMeta } from '../../lib/meta'
import { botPersonaName, personaScoreText, universeLabel } from '../../lib/persona'

type Mode = 'ai_trader' | 'strategy'

const AI_TRADER_DEFAULT: AITraderIn = {
  instructions: '順勢交易為主，嚴格止損，盈虧比至少 1:2；重大數據公布前不追價。',
  reference_strategy_id: null,
  min_confidence: 0.6,
  persona_id: null,
}

const ENTRY_DEFAULT: EntryConfig = { mode: 'market', max_wait_bars: null, skip_negative_ev: false }

const UNIVERSE_DEFAULT: Required<UniverseRules> = { mode: 'list', top_n: 10, exclude_meme: true, exclude: [], include_only: [], refresh_hours: 6 }


const isTraderStrategy = (s: Strategy) => s.kind === 'ai' && s.name.startsWith('AI 交易員｜')

interface Form {
  id: number | null
  mode: Mode
  ai_trader: AITraderIn
  name: string
  account_id: number | ''
  strategy_id: number | ''
  ai_model_id: number | ''
  symbols: string[]
  universe: Required<UniverseRules>
  entry: EntryConfig
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
  const { data: personas } = useLoader(() => api.listPersonas(), [])
  const personaNames = useMemo(() => Object.fromEntries((personas ?? []).map((p) => [p.id, p.name])), [personas])
  const [form, setForm] = useState<Form | null>(null)
  const [openHook, setOpenHook] = useState<Record<number, boolean>>({})
  const { busy, run } = useAction()
  const confirm = useConfirm()
  const toast = useToast()

  const isTrader = form?.mode === 'ai_trader'
  const strat = useMemo(() => (isTrader ? undefined : strategies?.find((s) => s.id === form?.strategy_id)), [strategies, form?.strategy_id, isTrader])
  const stratUsesAi = useStrategyUsesAi(strat)
  const aiRequired = form ? isTrader || stratUsesAi || (strat?.kind !== 'tradingview' && copilotNeedsAi(form.copilot)) : false
  const strategyParams: Params = useMemo(() => ({ ...(strat?.params ?? {}) }), [strat])

  const openCreate = () =>
    setForm({
      id: null,
      mode: 'ai_trader',
      ai_trader: { ...AI_TRADER_DEFAULT },
      name: '',
      account_id: accounts?.[0]?.id ?? '',
      strategy_id: strategies?.find((s) => (s.status === 'active' || s.status === 'paper_only') && !isTraderStrategy(s) && s.kind !== 'ai')?.id ?? '',
      ai_model_id: models?.[0]?.id ?? '',
      symbols: ['crypto:BTC/USDT:perp'],
      universe: { ...UNIVERSE_DEFAULT },
      entry: { ...ENTRY_DEFAULT },
      timeframe: '15m',
      interval_sec: 60,
      risk: {},
      copilot: { ...meta.copilot_defaults, tune_ranges: {} },
      params_override: {},
    })

  const openEdit = (b: Bot) =>
    setForm({
      id: b.id,
      mode: b.mode === 'ai_trader' ? 'ai_trader' : 'strategy',
      ai_trader: {
        ...AI_TRADER_DEFAULT,
        ...(b.ai_trader ?? {}),
        instructions: b.ai_trader?.instructions || AI_TRADER_DEFAULT.instructions,
        persona_id: b.ai_trader?.persona_id ?? null,
      },
      name: b.name,
      account_id: b.account_id,
      strategy_id: b.strategy_id,
      ai_model_id: b.ai_model_id ?? '',
      symbols: [...b.symbols],
      universe: { ...UNIVERSE_DEFAULT, ...(b.universe ?? {}), mode: (b.universe as UniverseRules | undefined)?.mode === 'rules' ? 'rules' : 'list' },
      entry: { ...ENTRY_DEFAULT, ...(b.entry ?? {}) },
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
    if (!isTrader && !form.strategy_id) return toast.error('請選擇策略')
    const byRules = form.universe.mode === 'rules'
    if (!byRules && !form.symbols.length) return toast.error('至少需要一個交易對（或改用規則自動挑選）')
    if (aiRequired && !form.ai_model_id) return toast.error(isTrader ? 'AI 交易員需要選擇 AI 模型' : '此設定需要 AI 模型（AI 策略或已開啟 AI 審核）')
    const base = strat?.params ?? {}
    const override: Params = {}
    for (const [k, v] of Object.entries(form.params_override)) if (JSON.stringify(base[k]) !== JSON.stringify(v)) override[k] = v
    const body: BotIn = {
      name: form.name.trim(),
      account_id: Number(form.account_id),
      strategy_id: isTrader ? null : Number(form.strategy_id),
      ai_trader: isTrader ? form.ai_trader : null,
      ai_model_id: form.ai_model_id ? Number(form.ai_model_id) : null,
      symbols: byRules ? [] : form.symbols,
      universe: byRules ? { ...form.universe, mode: 'rules' } : { mode: 'list' },
      entry: form.entry,
      timeframe: form.timeframe,
      interval_sec: form.interval_sec,
      risk: form.risk,
      copilot: form.copilot,
      params_override: isTrader ? {} : override,
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
  const selectableStrategies = (strategies ?? []).filter((s) => !isTraderStrategy(s) && (s.status === 'active' || s.status === 'paper_only' || s.id === form?.strategy_id))
  const referenceStrategies = (strategies ?? []).filter((s) => s.status === 'active' && s.kind !== 'ai' && s.kind !== 'tradingview')
  const modelSelect = form && (
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
  )
  const pendingCount = (strategies ?? []).filter((s) => s.status === 'pending_review' && !isTraderStrategy(s)).length
  const selAccount = accounts?.find((a) => a.id === form?.account_id)
  const paperOnlyOnLive = !isTrader && strat?.status === 'paper_only' && selAccount?.paper === false
  const brain = personas?.find((p) => p.id === form?.ai_trader.persona_id)
  const notLivePersonas: Persona[] = isTrader && selAccount?.paper === false && brain && brain.status !== 'active' ? [brain] : []
  const setTrader = (patch: Partial<AITraderIn>) => form && setForm({ ...form, ai_trader: { ...form.ai_trader, ...patch } })
  const setEntry = (patch: Partial<EntryConfig>) => form && setForm({ ...form, entry: { ...form.entry, ...patch } })
  const setUniverse = (patch: Partial<UniverseRules>) => form && setForm({ ...form, universe: { ...form.universe, ...patch } })

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
        <Empty icon={<Icons.bot className="h-5 w-5" />} title="尚未建立 Bot" hint="兩種模式：AI 交易員（AI 自主決策，不需要策略）或 你的策略 + AI 審核。" />
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
                      {' '}· {kindLabel(b.strategy_kind)} · {accName(b.account_id)} · {universeLabel(b.universe) ?? b.symbols.map(baseOf).join(', ')} · {b.timeframe}
                    </span>
                  </>
                }
                badges={
                  <>
                    <BotStatusBadge running={b.running} status={b.status} error={b.last_error} />
                    <BotAiBadge bot={b} persona={botPersonaName(b, personaNames)} />
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
            {/* 模式 */}
            <section className="grid grid-cols-1 gap-3 sm:grid-cols-2" role="radiogroup" aria-label="Bot 模式">
              {([
                ['ai_trader', 'AI 交易員', '推薦', 'AI 綜合行情、新聞、總經與合約數據，自己決定做多做空、倉位與止損。不需要寫策略。'],
                ['strategy', '我的策略 + AI 審核', '', '你的策略（內建 / Pine 轉換 / TradingView）決定方向，AI 負責把關、持倉管理與參數微調。'],
              ] as const).map(([m, title, tag, desc]) => {
                const on = form.mode === m
                return (
                  <button
                    key={m}
                    type="button"
                    role="radio"
                    aria-checked={on}
                    onClick={() => setForm({ ...form, mode: m })}
                    className={`rounded-2xl border p-4 text-left transition-colors ${on ? 'border-gold/60 bg-gold/10' : 'border-line bg-panel2/40 hover:border-slate-500'}`}
                  >
                    <div className="flex items-center gap-2">
                      <span className={`flex h-4 w-4 items-center justify-center rounded-full border ${on ? 'border-gold' : 'border-slate-500'}`}>
                        {on && <span className="h-2 w-2 rounded-full bg-gold" />}
                      </span>
                      <span className={`font-semibold ${on ? 'text-gold' : 'text-slate-100'}`}>{title}</span>
                      {tag && <span className="badge border-gold/40 bg-gold/10 text-gold">{tag}</span>}
                    </div>
                    <p className="mt-1.5 pl-6 text-xs leading-relaxed text-muted">{desc}</p>
                  </button>
                )
              })}
            </section>

            {/* 基本設定 */}
            <section className="grid grid-cols-1 gap-3 md:grid-cols-2">
              <Field label="名稱">
                <input className="input" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder={isTrader ? "例如：AI 交易員 · 主力" : "例如：BTC 均線 + AI 審核"} autoFocus />
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
              {!isTrader && (
              <Field label="策略" hint={pendingCount ? `有 ${pendingCount} 個策略尚未通過審查，通過後才能選擇` : undefined}>
                <select
                  className="input"
                  value={form.strategy_id}
                  onChange={(e) => setForm({ ...form, strategy_id: e.target.value ? Number(e.target.value) : '', params_override: {}, copilot: { ...form.copilot, tune_ranges: {} } })}
                >
                  <option value="">請選擇</option>
                  {selectableStrategies.map((s) => (
                    <option key={s.id} value={s.id}>
                      {s.name}（{kindLabel(s.kind)}）{s.status === 'paper_only' ? '（模擬期）' : s.status === 'pending_review' ? '— 待審核' : ''}
                    </option>
                  ))}
                </select>
                {paperOnlyOnLive && (
                  <div className="mt-1.5 flex items-start gap-1.5 rounded-lg border border-gold/40 bg-gold/10 px-2.5 py-1.5 text-xs text-amber-100">
                    <Icons.alert className="mt-0.5 h-3.5 w-3.5 shrink-0 text-gold" />
                    此策略仍在模擬期，只能用在模擬帳戶
                  </div>
                )}
              </Field>
              )}
              {isTrader && (
                <Field label="AI 模型" hint="AI 交易員的大腦；建議使用推理能力強的模型">
                  {modelSelect}
                </Field>
              )}
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
              <div className="md:col-span-2">
                <div className="mb-1.5 flex flex-wrap items-center justify-between gap-2">
                  <span className="label mb-0">標的範圍（USDT 永續）</span>
                  <Segmented
                    value={form.universe.mode === 'rules' ? 'rules' : 'list'}
                    options={[['list', '手動選擇'], ['rules', '依規則自動挑選']] as const}
                    onChange={(mode) => setUniverse({ mode })}
                  />
                </div>
                {form.universe.mode !== 'rules' ? (
                  <>
                    <SymbolsInput value={form.symbols} onChange={(v) => setForm({ ...form, symbols: v })} />
                    {isTrader && <div className="hint">可放多個幣種，AI 會逐一判斷每個幣種</div>}
                  </>
                ) : (
                  <div className="space-y-3 rounded-xl border border-line bg-[#101318] p-3">
                    <div className="flex flex-wrap items-end gap-x-6 gap-y-3">
                      <Field label="成交量前 N 名">
                        <div className="w-28">
                          <NumInput value={form.universe.top_n} step={1} onChange={(v) => setUniverse({ top_n: Math.max(1, Math.min(100, Math.round(v ?? 10))) })} />
                        </div>
                      </Field>
                      <div className="pb-2">
                        <Toggle checked={form.universe.exclude_meme} onChange={(v) => setUniverse({ exclude_meme: v })} label="排除迷因幣" />
                      </div>
                    </div>
                    <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
                      <Field label="排除這些幣">
                        <CoinChipsInput value={form.universe.exclude} onChange={(v) => setUniverse({ exclude: v })} placeholder="例如 LUNA，按 Enter" />
                      </Field>
                      <Field label="只在這些幣裡挑（選填）">
                        <CoinChipsInput value={form.universe.include_only} onChange={(v) => setUniverse({ include_only: v })} placeholder="不填＝全部幣種" />
                      </Field>
                    </div>
                    <Collapsible title="進階">
                      <Field label="更新頻率（小時）" hint="多久重新挑一次標的">
                        <div className="w-28">
                          <NumInput value={form.universe.refresh_hours} step={1} onChange={(v) => setUniverse({ refresh_hours: Math.max(1, Math.min(168, Math.round(v ?? 6))) })} />
                        </div>
                      </Field>
                    </Collapsible>
                    <div className="text-xs text-muted">依交易所 24 小時成交額挑選 USDT 永續；持倉中的幣會繼續管理到平倉。</div>
                  </div>
                )}
              </div>
              <div className="md:col-span-2">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <span className="label mb-0">進場方式</span>
                  <Segmented
                    value={form.entry.mode === 'smart' ? 'smart' : 'market'}
                    options={[['market', '市價進場'], ['smart', '智慧掛單']] as const}
                    onChange={(mode) => setEntry({ mode })}
                  />
                </div>
                {form.entry.mode === 'smart' ? (
                  <div className="mt-2 flex flex-wrap items-end gap-x-6 gap-y-3 rounded-xl border border-line bg-[#101318] p-3">
                    <Field label="最多等幾根 K 棒">
                      <div className="w-28">
                        <NumInput value={form.entry.max_wait_bars} step={1} placeholder="自動" onChange={(v) => setEntry({ max_wait_bars: v === null ? null : Math.max(1, Math.min(200, Math.round(v))) })} />
                      </div>
                    </Field>
                    <div className="pb-2">
                      <Toggle checked={form.entry.skip_negative_ev} onChange={(v) => setEntry({ skip_negative_ev: v })} label="期望值都為負時放棄進場" />
                    </div>
                    <div className="w-full text-xs text-muted">訊號出現時先做進場分析，在盈虧比更好的價位掛單等待；等不到就取消。留空＝依 K 線週期自動。</div>
                  </div>
                ) : (
                  <div className="hint">訊號出現就以市價進場。</div>
                )}
              </div>
            </section>

            {/* AI 交易員 */}
            {isTrader && (
              <section className="space-y-3 rounded-2xl border border-gold/30 bg-gradient-to-b from-gold/[0.06] to-transparent p-4">
                <div>
                  <div className="flex items-center gap-2 text-base font-semibold text-gold">
                    <Icons.sparkle className="h-5 w-5" /> AI 交易員
                  </div>
                  <div className="text-xs text-muted">
                    AI 每根 K 棒收盤時讀取行情、技術指標、新聞、總經、資金費率與恐懼貪婪指數，自主決定開倉、平倉或觀望。所有決策仍須通過風控。
                  </div>
                </div>
                <Field label="交易偏好（用中文描述即可）">
                  <textarea
                    className="input min-h-[88px]"
                    value={form.ai_trader.instructions}
                    onChange={(e) => setForm({ ...form, ai_trader: { ...form.ai_trader, instructions: e.target.value } })}
                    placeholder="例如：順勢交易、不逆勢抄底；重大數據前不開倉；最多 3 倍槓桿"
                  />
                </Field>
                <Field label="交易大腦" hint="用哪位投資大師的思維做判斷；到「設定 → 投資大師」上傳">
                  <select className="input" value={form.ai_trader.persona_id ?? ''} onChange={(e) => setTrader({ persona_id: e.target.value ? Number(e.target.value) : null })}>
                    <option value="">不使用（一般 AI 交易員）</option>
                    {personas?.map((p) => (
                      <option key={p.id} value={p.id}>
                        {p.name} · {personaScoreText(p)}
                      </option>
                    ))}
                  </select>
                  {brain && !brain.markets.includes('crypto') && (
                    <div className="mt-1.5 flex items-start gap-1.5 rounded-lg border border-gold/40 bg-gold/10 px-2.5 py-1.5 text-xs text-amber-100">
                      <Icons.alert className="mt-0.5 h-3.5 w-3.5 shrink-0 text-gold" />
                      這位大師不適合加密貨幣合約
                    </div>
                  )}
                  {notLivePersonas.map((p) => (
                    <div key={p.id} className="mt-1.5 flex items-start gap-1.5 rounded-lg border border-gold/40 bg-gold/10 px-2.5 py-1.5 text-xs text-amber-100">
                      <Icons.alert className="mt-0.5 h-3.5 w-3.5 shrink-0 text-gold" />
                      「{p.name}」尚未開放實盤（未通過評分或模擬期中），只能用在模擬帳戶
                    </div>
                  ))}
                </Field>
                <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
                  <Field label="參考策略（選填）" hint="AI 會把這個策略的訊號當成參考意見，不會盲從">
                    <select
                      className="input"
                      value={form.ai_trader.reference_strategy_id ?? ''}
                      onChange={(e) => setForm({ ...form, ai_trader: { ...form.ai_trader, reference_strategy_id: e.target.value ? Number(e.target.value) : null } })}
                    >
                      <option value="">不參考策略</option>
                      {referenceStrategies.map((s) => (
                        <option key={s.id} value={s.id}>{s.name}（{kindLabel(s.kind)}）</option>
                      ))}
                    </select>
                  </Field>
                  <Field label="最低信心" hint="AI 信心低於此值時改為觀望（0～1）">
                    <NumInput value={form.ai_trader.min_confidence} step={0.05} onChange={(v) => setForm({ ...form, ai_trader: { ...form.ai_trader, min_confidence: v ?? 0.6 } })} />
                  </Field>
                </div>
                <Field label="重大經濟事件避險（分鐘）" hint="CPI、FOMC、非農等公布前後幾分鐘不開新倉；0＝關閉">
                  <NumInput value={form.copilot.event_blackout_min} step={15} onChange={(v) => setForm({ ...form, copilot: { ...form.copilot, event_blackout_min: v ?? 0 } })} />
                </Field>
              </section>
            )}

            {/* 我的策略 + AI 審核 */}
            {!isTrader && strat?.kind !== 'tradingview' && (
              <section className="rounded-2xl border border-gold/30 bg-gradient-to-b from-gold/[0.06] to-transparent p-4">
                <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
                  <div>
                    <div className="flex items-center gap-2 text-base font-semibold text-gold">
                      <Icons.sparkle className="h-5 w-5" /> AI 審核
                    </div>
                    <div className="text-xs text-muted">策略決定方向，AI 負責把關與微調。AI 永遠不能改變方向或放寬止損。</div>
                  </div>
                  <div className="w-full sm:w-64">{modelSelect}</div>
                </div>
                <CopilotForm value={form.copilot} defaults={meta.copilot_defaults} onChange={(c) => setForm({ ...form, copilot: c })} strategyParams={{ ...strategyParams, ...form.params_override }} strategyUsesAi={stratUsesAi} />
              </section>
            )}
            {!isTrader && strat?.kind === 'tradingview' && (
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
              {!isTrader && strat && strat.kind !== 'tradingview' && Object.keys(strategyParams).length > 0 && (
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
