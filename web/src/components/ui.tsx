import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from 'react'
import { errMsg } from '../api'

// ------------------------------ Icons (inline SVG, no deps) ------------------------------
type IconProps = { className?: string }
const svg = (d: ReactNode) =>
  function Icon({ className = 'h-4 w-4' }: IconProps) {
    return (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round" className={className} aria-hidden>
        {d}
      </svg>
    )
  }
export const Icons = {
  plus: svg(<path d="M12 5v14M5 12h14" />),
  edit: svg(<path d="M4 20h4L19 9l-4-4L4 16v4zM14 6l4 4" />),
  trash: svg(<path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3" />),
  play: svg(<path d="M7 5v14l12-7z" />),
  stop: svg(<rect x="6" y="6" width="12" height="12" rx="1.5" />),
  copy: svg(<path d="M9 9h10v10H9zM5 15V5h10" />),
  check: svg(<path d="M5 13l4 4L19 7" />),
  x: svg(<path d="M6 6l12 12M18 6L6 18" />),
  chevron: svg(<path d="M9 6l6 6-6 6" />),
  refresh: svg(<path d="M20 11a8 8 0 10-2.3 5.7M20 5v6h-6" />),
  bolt: svg(<path d="M13 3L5 14h6l-1 7 8-11h-6z" />),
  alert: svg(<path d="M12 9v4M12 17h.01M10.3 3.9L2 18a2 2 0 001.7 3h16.6a2 2 0 001.7-3L13.7 3.9a2 2 0 00-3.4 0z" />),
  bot: svg(<><rect x="4" y="8" width="16" height="12" rx="3" /><path d="M12 4v4M9 13h.01M15 13h.01M9 17h6" /></>),
  brain: svg(<path d="M9 4a3 3 0 00-3 3 3 3 0 00-2 5 3 3 0 002 5 3 3 0 006 1V5a2 2 0 00-3-1zM15 4a3 3 0 013 3 3 3 0 012 5 3 3 0 01-2 5 3 3 0 01-6 1" />),
  exchange: svg(<path d="M4 8h13l-3-3M20 16H7l3 3" />),
  code: svg(<path d="M8 8l-4 4 4 4M16 8l4 4-4 4M14 5l-4 14" />),
  chart: svg(<path d="M4 19h16M7 15l4-4 3 3 5-6" />),
  news: svg(<path d="M4 5h13v14H6a2 2 0 01-2-2zM17 9h3v8a2 2 0 01-2 2M8 9h5M8 13h5" />),
  menu: svg(<path d="M4 7h16M4 12h16M4 17h16" />),
  logout: svg(<path d="M15 4h4v16h-4M10 8l-4 4 4 4M6 12h10" />),
  shield: svg(<path d="M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z" />),
  sparkle: svg(<path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8zM19 16l.8 2.2L22 19l-2.2.8L19 22l-.8-2.2L16 19l2.2-.8z" />),
  webhook: svg(<path d="M9 17a4 4 0 11-3-6.5M15 7a4 4 0 11-6 3.5l3 5.5M18 17h-6a4 4 0 107-2.6" />),
}

// ------------------------------ Toast ------------------------------
type ToastKind = 'success' | 'error' | 'info'
interface ToastItem {
  id: number
  kind: ToastKind
  text: string
}
interface ToastApi {
  success: (t: string) => void
  error: (t: unknown) => void
  info: (t: string) => void
}
const ToastCtx = createContext<ToastApi | null>(null)

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([])
  const idRef = useRef(0)
  const push = useCallback((kind: ToastKind, text: string) => {
    const id = ++idRef.current
    setItems((xs) => [...xs.slice(-4), { id, kind, text }])
    setTimeout(() => setItems((xs) => xs.filter((x) => x.id !== id)), kind === 'error' ? 7000 : 3500)
  }, [])
  const api = useRef<ToastApi>({
    success: (t) => push('success', t),
    error: (t) => push('error', typeof t === 'string' ? t : errMsg(t)),
    info: (t) => push('info', t),
  })
  return (
    <ToastCtx.Provider value={api.current}>
      {children}
      <div className="pointer-events-none fixed top-16 right-4 z-[100] flex w-[min(92vw,380px)] flex-col gap-2">
        {items.map((t) => (
          <div
            key={t.id}
            role="status"
            className={`anim-in pointer-events-auto flex items-start gap-2.5 rounded-xl border px-3.5 py-3 text-sm shadow-2xl backdrop-blur ${
              t.kind === 'error'
                ? 'border-down/40 bg-[#2a1216]/95 text-red-100'
                : t.kind === 'success'
                  ? 'border-up/40 bg-[#0f2219]/95 text-green-100'
                  : 'border-line bg-panel2/95 text-slate-100'
            }`}
          >
            <span className={`mt-0.5 ${t.kind === 'error' ? 'text-down' : t.kind === 'success' ? 'text-up' : 'text-gold'}`}>
              {t.kind === 'error' ? <Icons.alert /> : t.kind === 'success' ? <Icons.check /> : <Icons.bolt />}
            </span>
            <span className="min-w-0 flex-1 break-words">{t.text}</span>
            <button className="text-muted hover:text-slate-200" onClick={() => setItems((xs) => xs.filter((x) => x.id !== t.id))} aria-label="關閉">
              <Icons.x className="h-3.5 w-3.5" />
            </button>
          </div>
        ))}
      </div>
    </ToastCtx.Provider>
  )
}

export function useToast(): ToastApi {
  const c = useContext(ToastCtx)
  if (!c) throw new Error('ToastProvider missing')
  return c
}

// ------------------------------ Modal ------------------------------
export function Modal({
  open,
  onClose,
  title,
  children,
  footer,
  size = 'md',
}: {
  open: boolean
  onClose: () => void
  title: ReactNode
  children: ReactNode
  footer?: ReactNode
  size?: 'sm' | 'md' | 'lg' | 'xl'
}) {
  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    const prev = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => {
      window.removeEventListener('keydown', onKey)
      document.body.style.overflow = prev
    }
  }, [open, onClose])
  if (!open) return null
  const w = { sm: 'max-w-md', md: 'max-w-xl', lg: 'max-w-3xl', xl: 'max-w-6xl' }[size]
  return (
    <div className="anim-fade fixed inset-0 z-50 flex items-end justify-center bg-black/70 backdrop-blur-sm sm:items-center sm:p-4" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className={`anim-in flex max-h-[94vh] w-full ${w} flex-col rounded-t-2xl border border-line bg-panel shadow-2xl sm:rounded-2xl`}>
        <div className="flex items-center justify-between gap-3 border-b border-line px-5 py-3.5">
          <h3 className="text-base font-semibold text-slate-100">{title}</h3>
          <button className="btn btn-ghost btn-sm" onClick={onClose} aria-label="關閉">
            <Icons.x />
          </button>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">{children}</div>
        {footer && <div className="flex flex-wrap items-center justify-end gap-2 border-t border-line px-5 py-3">{footer}</div>}
      </div>
    </div>
  )
}

// ------------------------------ Confirm ------------------------------
interface ConfirmOpts {
  title: string
  message?: ReactNode
  confirmText?: string
  danger?: boolean
}
type ConfirmFn = (o: ConfirmOpts) => Promise<boolean>
const ConfirmCtx = createContext<ConfirmFn | null>(null)

export function ConfirmProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<(ConfirmOpts & { resolve: (v: boolean) => void }) | null>(null)
  const confirm = useCallback<ConfirmFn>((o) => new Promise((resolve) => setState({ ...o, resolve })), [])
  const close = (v: boolean) => {
    state?.resolve(v)
    setState(null)
  }
  return (
    <ConfirmCtx.Provider value={confirm}>
      {children}
      <Modal
        open={!!state}
        onClose={() => close(false)}
        size="sm"
        title={
          <span className="flex items-center gap-2">
            {state?.danger && <Icons.alert className="h-5 w-5 text-down" />}
            {state?.title}
          </span>
        }
        footer={
          <>
            <button className="btn btn-secondary" onClick={() => close(false)}>
              取消
            </button>
            <button className={`btn ${state?.danger ? 'border-down bg-down text-white hover:brightness-110' : 'btn-primary'}`} onClick={() => close(true)} autoFocus>
              {state?.confirmText ?? '確定'}
            </button>
          </>
        }
      >
        <div className="text-sm leading-relaxed text-slate-300">{state?.message}</div>
      </Modal>
    </ConfirmCtx.Provider>
  )
}

export function useConfirm(): ConfirmFn {
  const c = useContext(ConfirmCtx)
  if (!c) throw new Error('ConfirmProvider missing')
  return c
}

// ------------------------------ Small pieces ------------------------------
export function Spinner({ className = 'h-4 w-4' }: { className?: string }) {
  return (
    <svg className={`animate-spin ${className}`} viewBox="0 0 24 24" fill="none" aria-hidden>
      <circle cx="12" cy="12" r="9" stroke="currentColor" strokeOpacity="0.2" strokeWidth="3" />
      <path d="M21 12a9 9 0 00-9-9" stroke="currentColor" strokeWidth="3" strokeLinecap="round" />
    </svg>
  )
}

export function Card({
  title,
  icon,
  actions,
  children,
  className = '',
  bodyClass = 'card-body',
}: {
  title?: ReactNode
  icon?: ReactNode
  actions?: ReactNode
  children: ReactNode
  className?: string
  bodyClass?: string
}) {
  return (
    <section className={`card ${className}`}>
      {(title || actions) && (
        <header className="card-header">
          <h2 className="card-title flex items-center gap-2">
            {icon && <span className="text-gold">{icon}</span>}
            {title}
          </h2>
          {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
        </header>
      )}
      <div className={bodyClass}>{children}</div>
    </section>
  )
}

export function Empty({ title, hint, icon, action }: { title: string; hint?: ReactNode; icon?: ReactNode; action?: ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 px-4 py-10 text-center">
      <div className="mb-1 flex h-11 w-11 items-center justify-center rounded-xl border border-line bg-panel2 text-muted">{icon ?? <Icons.chart className="h-5 w-5" />}</div>
      <div className="text-sm font-medium text-slate-300">{title}</div>
      {hint && <div className="max-w-sm text-xs text-muted">{hint}</div>}
      {action && <div className="mt-2">{action}</div>}
    </div>
  )
}

export function Skeleton({ rows = 3, className = '' }: { rows?: number; className?: string }) {
  return (
    <div className={`space-y-2.5 ${className}`}>
      {Array.from({ length: rows }).map((_, i) => (
        <div key={i} className="skeleton h-10" style={{ opacity: 1 - i * 0.15 }} />
      ))}
    </div>
  )
}

export function Toggle({ checked, onChange, label, disabled }: { checked: boolean; onChange: (v: boolean) => void; label?: ReactNode; disabled?: boolean }) {
  return (
    <label className={`inline-flex cursor-pointer select-none items-center gap-2.5 ${disabled ? 'opacity-50' : ''}`}>
      <button
        type="button"
        role="switch"
        aria-checked={checked}
        disabled={disabled}
        onClick={() => onChange(!checked)}
        className={`relative h-5 w-9 shrink-0 rounded-full border transition-colors duration-200 ${checked ? 'border-gold bg-gold' : 'border-line bg-[#2b3139]'}`}
      >
        <span className={`absolute top-0.5 h-3.5 w-3.5 rounded-full shadow transition-all duration-200 ${checked ? 'left-[18px] bg-black' : 'left-0.5 bg-slate-400'}`} />
      </button>
      {label && <span className="text-sm text-slate-200">{label}</span>}
    </label>
  )
}

export function Field({ label, hint, children, className = '' }: { label: ReactNode; hint?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <div className={className}>
      <label className="label">{label}</label>
      {children}
      {hint && <div className="hint">{hint}</div>}
    </div>
  )
}

export function Collapsible({ title, children, defaultOpen = false, icon }: { title: ReactNode; children: ReactNode; defaultOpen?: boolean; icon?: ReactNode }) {
  const [open, setOpen] = useState(defaultOpen)
  return (
    <div className="rounded-xl border border-line bg-[#12161b]">
      <button type="button" className="flex w-full items-center gap-2 px-4 py-2.5 text-left text-sm font-medium text-slate-200 hover:text-gold" onClick={() => setOpen(!open)}>
        <Icons.chevron className={`h-4 w-4 text-muted transition-transform duration-200 ${open ? 'rotate-90' : ''}`} />
        {icon && <span className="text-muted">{icon}</span>}
        {title}
      </button>
      {open && <div className="anim-fade border-t border-line px-4 py-4">{children}</div>}
    </div>
  )
}

export function CopyButton({ text, label = '複製' }: { text: string; label?: string }) {
  const [ok, setOk] = useState(false)
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text)
    } catch {
      const ta = document.createElement('textarea')
      ta.value = text
      document.body.appendChild(ta)
      ta.select()
      document.execCommand('copy')
      ta.remove()
    }
    setOk(true)
    setTimeout(() => setOk(false), 1500)
  }
  return (
    <button type="button" className={`btn btn-sm ${ok ? 'btn-success' : 'btn-secondary'}`} onClick={copy}>
      {ok ? <Icons.check className="h-3.5 w-3.5" /> : <Icons.copy className="h-3.5 w-3.5" />}
      {ok ? '已複製' : label}
    </button>
  )
}

export function StatCard({ label, value, sub, accent }: { label: string; value: ReactNode; sub?: ReactNode; accent?: boolean }) {
  return (
    <div className={`card relative overflow-hidden px-4 py-4 ${accent ? 'border-gold/30' : ''}`}>
      {accent && <div className="pointer-events-none absolute -right-10 -top-10 h-28 w-28 rounded-full bg-gold/10 blur-2xl" />}
      <div className="text-[11px] font-medium uppercase tracking-[0.12em] text-muted">{label}</div>
      <div className="num mt-2 font-mono text-2xl font-semibold tracking-tight text-slate-50 sm:text-[26px]">{value}</div>
      {sub && <div className="num mt-1.5 font-mono text-xs text-muted">{sub}</div>}
    </div>
  )
}

export function Change({ v, suffix = '%', digits = 2 }: { v: number | null | undefined; suffix?: string; digits?: number }) {
  if (v === null || v === undefined || Number.isNaN(v)) return <span className="text-muted">—</span>
  const up = v > 0
  const cls = v === 0 ? 'text-slate-300' : up ? 'text-up' : 'text-down'
  return (
    <span className={`num font-mono ${cls}`}>
      {v !== 0 && (up ? '▲ ' : '▼ ')}
      {up ? '+' : v < 0 ? '−' : ''}
      {Math.abs(v).toLocaleString('en-US', { minimumFractionDigits: digits, maximumFractionDigits: digits })}
      {suffix}
    </span>
  )
}

export function StatusDot({ status }: { status: 'running' | 'stopped' | 'error' }) {
  const c = status === 'running' ? 'bg-up' : status === 'error' ? 'bg-down' : 'bg-slate-500'
  return (
    <span className="relative inline-flex h-2.5 w-2.5">
      {status === 'running' && <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-up opacity-50" />}
      <span className={`relative inline-flex h-2.5 w-2.5 rounded-full ${c}`} />
    </span>
  )
}

export function BotStatusBadge({ running, status, error }: { running: boolean; status: string; error?: string | null }) {
  if (running) return <span className="badge badge-green"><StatusDot status="running" />運行中</span>
  if (status === 'error' || (error && status !== 'stopped')) return <span className="badge badge-red"><StatusDot status="error" />錯誤</span>
  return <span className="badge badge-gray"><StatusDot status="stopped" />已停止</span>
}

export function SideBadge({ side }: { side: string }) {
  const s = side.toLowerCase()
  const long = s === 'long' || s === 'buy'
  const label = { long: 'LONG', short: 'SHORT', buy: '買', sell: '賣' }[s] ?? side
  return <span className={`inline-block rounded px-1.5 py-0.5 text-[11px] font-semibold ${long ? 'bg-up/15 text-up' : 'bg-down/15 text-down'}`}>{label}</span>
}

/** AI 交易員（AI 自主決策）或 AI 審核（你的策略 + AI 把關） */
export function AiBadge({ mode = 'review' }: { mode?: 'trader' | 'review' }) {
  return (
    <span className="badge border-gold/40 bg-gold/10 text-gold">
      <Icons.sparkle className="h-3 w-3" />
      {mode === 'trader' ? 'AI 交易員' : 'AI 審核'}
    </span>
  )
}

export function BotAiBadge({ bot }: { bot: { mode?: string; copilot_active?: boolean; copilot_enabled?: boolean } }) {
  if (bot.mode === 'ai_trader') return <AiBadge mode="trader" />
  if (bot.copilot_active || bot.copilot_enabled) return <AiBadge mode="review" />
  return null
}

// ------------------------------ Hooks ------------------------------
/** Load data with optional auto refresh. Errors are toasted once per change. */
export function useLoader<T>(fn: () => Promise<T>, deps: unknown[], refreshMs = 0) {
  const [data, setData] = useState<T | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const fnRef = useRef(fn)
  fnRef.current = fn
  const toast = useToast()
  const lastErr = useRef<string | null>(null)

  const reload = useCallback(async () => {
    try {
      const d = await fnRef.current()
      setData(d)
      setError(null)
      lastErr.current = null
    } catch (e) {
      const m = errMsg(e)
      setError(m)
      if (lastErr.current !== m) toast.error(m)
      lastErr.current = m
    } finally {
      setLoading(false)
    }
  }, [toast])

  useEffect(() => {
    setLoading(true)
    setData(null)
    void reload()
    if (!refreshMs) return
    const t = setInterval(() => {
      if (document.visibilityState === 'visible') void reload()
    }, refreshMs)
    return () => clearInterval(t)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps)

  return { data, loading, error, reload, setData }
}

/** Wrap an async action with busy state + toast on error. */
export function useAction() {
  const toast = useToast()
  const [busy, setBusy] = useState<string | null>(null)
  const run = useCallback(
    async <T,>(key: string, fn: () => Promise<T>, okMsg?: string): Promise<T | undefined> => {
      setBusy(key)
      try {
        const r = await fn()
        if (okMsg) toast.success(okMsg)
        return r
      } catch (e) {
        toast.error(e)
        return undefined
      } finally {
        setBusy(null)
      }
    },
    [toast],
  )
  return { busy, run }
}

// ------------------------------ List row (bordered card) ------------------------------
export function ListRow({
  icon,
  title,
  subtitle,
  badges,
  actions,
  children,
  active,
}: {
  icon: ReactNode
  title: ReactNode
  subtitle?: ReactNode
  badges?: ReactNode
  actions?: ReactNode
  children?: ReactNode
  active?: boolean
}) {
  return (
    <div className={`rounded-xl border bg-[#0f1216] transition-colors ${active ? 'border-gold/40' : 'border-line hover:border-[#3a414b]'}`}>
      <div className="flex flex-wrap items-center gap-3 px-4 py-3">
        <div className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-lg ${active ? 'bg-gold/15 text-gold' : 'bg-panel2 text-muted'}`}>{icon}</div>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="truncate font-semibold text-slate-100">{title}</span>
            {badges}
          </div>
          {subtitle && <div className="mt-0.5 truncate text-xs text-gold/90">{subtitle}</div>}
        </div>
        {actions && <div className="flex flex-wrap items-center gap-1.5">{actions}</div>}
      </div>
      {children && <div className="border-t border-line px-4 py-3">{children}</div>}
    </div>
  )
}
