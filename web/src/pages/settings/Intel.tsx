import { useEffect, useState, type ReactNode } from 'react'
import { api, type IntelSettings, type IntelSnapshot } from '../../api'
import { SymbolPicker } from '../../components/forms'
import { Card, Collapsible, CopyButton, Empty, Field, Icons, Skeleton, Spinner, Toggle, useAction, useLoader } from '../../components/ui'
import { fmtNum, fmtTime } from '../../lib/format'
import { useMeta } from '../../lib/meta'

const SOURCES: { key: 'derivatives' | 'fear_greed' | 'news' | 'macro' | 'calendar'; title: string; desc: string }[] = [
  { key: 'derivatives', title: '合約數據', desc: '資金費率、未平倉量、多空比（交易所公開資料）' },
  { key: 'fear_greed', title: '恐懼貪婪指數', desc: '加密市場情緒指標（alternative.me）' },
  { key: 'news', title: '新聞', desc: 'RSS 新聞標題，可選 CryptoPanic' },
  { key: 'macro', title: '總經 FRED', desc: 'CPI、利率、美債殖利率、美元指數、失業率（需 FRED Key）' },
  { key: 'calendar', title: '經濟日曆', desc: '未來 48 小時高影響事件（CPI、FOMC、非農…）' },
]

export default function Intel() {
  const meta = useMeta()
  const { data, loading, setData } = useLoader(() => api.intelSettings(), [])
  const [draft, setDraft] = useState<IntelSettings | null>(null)
  const [fred, setFred] = useState('')
  const [cp, setCp] = useState('')
  const [rssText, setRssText] = useState('')
  const [symbol, setSymbol] = useState('crypto:BTC/USDT:perp')
  const cryptoEx = meta.exchanges.filter((e) => e.market === 'crypto' && !e.planned)
  const [exchange, setExchange] = useState(cryptoEx[0]?.id ?? 'binance')
  const [snap, setSnap] = useState<IntelSnapshot | null>(null)
  const { busy, run } = useAction()

  useEffect(() => {
    if (data) {
      setDraft(data)
      setRssText(data.rss_urls.join('\n'))
    }
  }, [data])

  const save = async () => {
    if (!draft) return
    const r = await run(
      'save',
      () =>
        api.saveIntelSettings({
          derivatives: draft.derivatives,
          fear_greed: draft.fear_greed,
          news: draft.news,
          macro: draft.macro,
          calendar: draft.calendar,
          rss_urls: rssText.split('\n').map((s) => s.trim()).filter(Boolean),
          fred_api_key: fred || null,
          cryptopanic_token: cp || null,
        }),
      '已儲存',
    )
    if (r) {
      setData(r)
      setFred('')
      setCp('')
    }
  }

  const preview = async () => {
    const r = await run('preview', () => api.intelSnapshot(symbol, exchange))
    if (r) setSnap(r)
  }

  return (
    <div className="grid grid-cols-1 gap-5 xl:grid-cols-[minmax(0,5fr)_minmax(0,7fr)]">
      <Card
        title="資料來源"
        icon={<Icons.news />}
        actions={
          <button className="btn btn-primary btn-sm" onClick={save} disabled={!draft || busy === 'save'}>
            {busy === 'save' && <Spinner className="h-3.5 w-3.5" />} 儲存
          </button>
        }
      >
        {loading || !draft ? (
          <Skeleton rows={5} />
        ) : (
          <div className="space-y-4">
            <p className="text-xs leading-relaxed text-muted">AI 交易員與 AI 審核做判斷時會讀取以下市場情報。來源失敗時會自動略過，不影響交易。</p>
            <div className="space-y-2">
              {SOURCES.map((s) => (
                <div key={s.key} className={`flex items-center justify-between gap-3 rounded-xl border px-3.5 py-2.5 transition-colors ${draft[s.key] ? 'border-gold/30 bg-gold/[0.04]' : 'border-line bg-[#12161b]'}`}>
                  <div className="min-w-0">
                    <div className="text-sm font-medium text-slate-100">{s.title}</div>
                    <div className="text-xs text-muted">{s.desc}</div>
                  </div>
                  <Toggle checked={draft[s.key]} onChange={(v) => setDraft({ ...draft, [s.key]: v })} />
                </div>
              ))}
            </div>
            <Field
              label="FRED API Key"
              hint={
                <>
                  免費申請：fred.stlouisfed.org（My Account → API Keys）。{draft.fred_api_key ? `目前：${draft.fred_api_key}，留空＝不修改` : '尚未設定'}
                </>
              }
            >
              <input className="input font-mono" type="password" autoComplete="new-password" value={fred} onChange={(e) => setFred(e.target.value)} />
            </Field>
            <Field label="CryptoPanic Token（選填）" hint={draft.cryptopanic_token ? `目前：${draft.cryptopanic_token}，留空＝不修改` : '未設定時只使用 RSS 新聞'}>
              <input className="input font-mono" type="password" autoComplete="new-password" value={cp} onChange={(e) => setCp(e.target.value)} />
            </Field>
            <Field label="RSS 新聞來源" hint="一行一個網址">
              <textarea className="input min-h-[88px] font-mono text-xs" value={rssText} onChange={(e) => setRssText(e.target.value)} spellCheck={false} />
            </Field>
          </div>
        )}
      </Card>

      <Card
        title="預覽 AI 看到的情報"
        icon={<Icons.sparkle />}
        actions={
          <button className="btn btn-primary btn-sm" onClick={preview} disabled={busy === 'preview'}>
            {busy === 'preview' ? <Spinner className="h-3.5 w-3.5" /> : <Icons.refresh className="h-3.5 w-3.5" />} 預覽
          </button>
        }
      >
        <div className="mb-4 grid grid-cols-1 gap-3 sm:grid-cols-2">
          <Field label="交易對">
            <SymbolPicker value={symbol} onChange={setSymbol} />
          </Field>
          <Field label="交易所（合約數據來源）">
            <select className="input" value={exchange} onChange={(e) => setExchange(e.target.value)}>
              {cryptoEx.map((e) => (
                <option key={e.id} value={e.id}>{e.label}</option>
              ))}
            </select>
          </Field>
        </div>
        {busy === 'preview' && !snap ? (
          <Skeleton rows={4} />
        ) : !snap ? (
          <Empty icon={<Icons.news className="h-5 w-5" />} title="按「預覽」查看目前 AI 會讀到的市場情報" hint="儲存設定後再預覽，才會套用新的來源設定。" />
        ) : (
          <IntelView snap={snap} />
        )}
      </Card>
    </div>
  )
}

function FearGreed({ v }: { v: NonNullable<IntelSnapshot['fear_greed']> }) {
  const color = v.value <= 25 ? '#f6465d' : v.value <= 45 ? '#f0883b' : v.value <= 55 ? '#b7bdc6' : v.value <= 75 ? '#8fd14f' : '#0ecb81'
  const pct = Math.max(0, Math.min(100, v.value))
  const delta = v.yesterday !== null ? v.value - v.yesterday : null
  return (
    <div className="rounded-xl border border-line bg-[#12161b] p-4">
      <div className="text-[11px] font-medium uppercase tracking-[0.12em] text-muted">恐懼貪婪指數</div>
      <div className="mt-2 flex items-end gap-2">
        <span className="num font-mono text-3xl font-semibold" style={{ color }}>{v.value}</span>
        <span className="mb-1 text-sm text-slate-300">{v.label}</span>
      </div>
      <div className="relative mt-3 h-2 rounded-full bg-gradient-to-r from-[#f6465d] via-[#b7bdc6] to-[#0ecb81] opacity-80">
        <span className="absolute -top-1 h-4 w-1.5 -translate-x-1/2 rounded-full border-2 border-[#12161b] bg-white" style={{ left: `${pct}%` }} />
      </div>
      <div className="mt-1.5 flex justify-between text-[10px] text-slate-500">
        <span>極度恐懼</span>
        <span>極度貪婪</span>
      </div>
      {delta !== null && (
        <div className="mt-1 text-xs text-muted">
          昨日 {v.yesterday}（{delta >= 0 ? '+' : ''}
          {delta}）
        </div>
      )}
    </div>
  )
}

function IntelView({ snap }: { snap: IntelSnapshot }) {
  const d = snap.derivatives ?? {}
  const macro = Object.entries(snap.macro ?? {})
  return (
    <div className="space-y-4">
      <div className="text-xs text-muted">
        {snap.instrument} · 取得時間 {snap.fetched_at} UTC
      </div>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        {snap.fear_greed ? <FearGreed v={snap.fear_greed} /> : <div className="rounded-xl border border-dashed border-line p-4 text-xs text-muted">恐懼貪婪指數：無資料</div>}
        <div className="rounded-xl border border-line bg-[#12161b] p-4">
          <div className="text-[11px] font-medium uppercase tracking-[0.12em] text-muted">合約數據</div>
          {Object.keys(d).length === 0 ? (
            <div className="mt-3 text-xs text-muted">無資料</div>
          ) : (
            <dl className="mt-2 space-y-1.5 text-sm">
              <Row k="資金費率" v={d.funding_rate_pct !== undefined ? <span className={d.funding_rate_pct >= 0 ? 'text-up' : 'text-down'}>{d.funding_rate_pct.toFixed(4)}%</span> : '—'} />
              <Row k="下次結算" v={d.next_funding ? fmtTime(d.next_funding) : '—'} />
              <Row k="未平倉量" v={d.open_interest !== undefined ? fmtNum(d.open_interest, 0) : '—'} />
              <Row k="多空比" v={d.long_short_ratio !== undefined && d.long_short_ratio !== null ? fmtNum(Number(d.long_short_ratio), 3) : '—'} />
            </dl>
          )}
        </div>
      </div>

      {macro.length > 0 && (
        <div>
          <div className="label">總經數據</div>
          <div className="tbl-wrap">
            <table className="tbl">
              <thead>
                <tr>
                  <th>項目</th>
                  <th className="r">最新</th>
                  <th className="r">前值</th>
                  <th className="r">年增</th>
                  <th>日期</th>
                </tr>
              </thead>
              <tbody>
                {macro.map(([k, m]) => (
                  <tr key={k}>
                    <td>{m.label}</td>
                    <td className="r font-mono">{fmtNum(m.value, 2)}</td>
                    <td className="r font-mono text-muted">{m.previous !== undefined ? fmtNum(m.previous, 2) : '—'}</td>
                    <td className="r font-mono">{m.yoy_pct !== undefined ? `${m.yoy_pct}%` : '—'}</td>
                    <td className="font-mono text-xs text-muted">{m.date}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      <div>
        <div className="label">近期高影響事件</div>
        {snap.events.length === 0 ? (
          <div className="text-xs text-muted">未來 48 小時內沒有高影響事件（或日曆未啟用）</div>
        ) : (
          <ul className="space-y-1.5">
            {snap.events.map((e, i) => (
              <li key={i} className="flex flex-wrap items-center gap-2 rounded-lg border border-line bg-[#12161b] px-3 py-2 text-sm">
                <span className="num font-mono text-xs text-gold">{fmtTime(e.time)}</span>
                <span className="badge badge-gray">{e.country}</span>
                <span className="text-slate-100">{e.title}</span>
                <span className="ml-auto text-xs text-muted">
                  預期 {e.forecast || '—'} · 前值 {e.previous || '—'}
                </span>
              </li>
            ))}
          </ul>
        )}
      </div>

      <div>
        <div className="label">新聞標題</div>
        {snap.news.length === 0 ? (
          <div className="text-xs text-muted">無新聞</div>
        ) : (
          <ul className="divide-y divide-line/60 rounded-lg border border-line">
            {snap.news.map((n, i) => (
              <li key={i} className="flex gap-3 px-3 py-2 text-sm">
                <span className="num w-[86px] shrink-0 font-mono text-xs text-muted">{n.published ? fmtTime(n.published).slice(5) : '—'}</span>
                <span className="min-w-0 flex-1 text-slate-200">{n.title}</span>
                <span className="hidden shrink-0 text-xs text-slate-500 sm:inline">{n.source}</span>
              </li>
            ))}
          </ul>
        )}
      </div>

      <Collapsible title="AI 實際讀到的文字（prompt）" icon={<Icons.code className="h-4 w-4" />}>
        <div className="mb-2 flex justify-end">
          <CopyButton text={snap.prompt} />
        </div>
        <pre className="code-area max-h-[360px] overflow-auto whitespace-pre-wrap">{snap.prompt}</pre>
      </Collapsible>
    </div>
  )
}

function Row({ k, v }: { k: string; v: ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-3">
      <dt className="text-muted">{k}</dt>
      <dd className="num font-mono text-slate-100">{v}</dd>
    </div>
  )
}
