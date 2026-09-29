import { useState } from 'react'
import { api, type CheckItem, type DiagnosticsResult } from '../../api'
import { Card, Empty, Icons, Spinner, Toggle, useAction } from '../../components/ui'
import { fmtTime } from '../../lib/format'

const STATUS: Record<string, { icon: string; cls: string; label: string }> = {
  ok: { icon: '✓', cls: 'text-up', label: '正常' },
  warn: { icon: '!', cls: 'text-gold', label: '注意' },
  fail: { icon: '✗', cls: 'text-down', label: '失敗' },
  skip: { icon: '–', cls: 'text-muted', label: '略過' },
}

function Row({ it }: { it: CheckItem }) {
  const st = STATUS[it.status] ?? STATUS.skip
  return (
    <li className="flex items-start gap-3 py-2">
      <span className={`mt-0.5 w-4 shrink-0 text-center font-bold ${st.cls}`} title={st.label}>{st.icon}</span>
      <div className="min-w-0 flex-1">
        <div className="text-sm text-slate-100">{it.name}</div>
        {it.detail && <div className={`mt-0.5 break-words text-xs ${it.status === 'fail' ? 'text-red-300' : 'text-muted'}`}>{it.detail}</div>}
      </div>
      {it.ms != null && <span className="shrink-0 font-mono text-[11px] text-slate-500">{it.ms} ms</span>}
    </li>
  )
}

export default function Diagnostics() {
  const [res, setRes] = useState<DiagnosticsResult | null>(null)
  const [includeAi, setIncludeAi] = useState(true)
  const { busy, run } = useAction()
  const check = async () => {
    const r = await run('check', () => api.diagnostics(includeAi))
    if (r) setRes(r)
  }
  const s = res?.summary

  return (
    <div className="space-y-4">
      <Card
        title="連線檢查"
        icon={<Icons.shield className="h-4 w-4" />}
        actions={
          <button className="btn btn-primary" onClick={check} disabled={busy === 'check'}>
            {busy === 'check' ? <Spinner className="h-4 w-4" /> : <Icons.refresh className="h-4 w-4" />} {res ? '重新檢查' : '開始檢查'}
          </button>
        }
      >
        <p className="text-sm text-muted">
          一鍵測試所有串接：交易所公開行情（含有沒有黃金合約）、每個交易所帳戶的 API Key、AI 模型、新聞 / 總經 / 情緒資料來源。
          <span className="text-slate-300">只讀取資料，不會下任何單。</span>部署後第一次使用，或交易出錯時，先來這裡檢查；失敗的項目截圖給我即可。
        </p>
        <div className="mt-3">
          <Toggle checked={includeAi} onChange={setIncludeAi} label="一併檢查 AI 模型（每個模型送一次極短請求，費用極少）" />
        </div>
        {s && (
          <div className="mt-4 flex flex-wrap items-center gap-2 text-xs">
            <span className="badge badge-green">正常 {s.ok}</span>
            {s.warn > 0 && <span className="badge badge-gold">注意 {s.warn}</span>}
            {s.fail > 0 && <span className="badge badge-red">失敗 {s.fail}</span>}
            {s.skip > 0 && <span className="badge badge-gray">略過 {s.skip}</span>}
            <span className="ml-auto text-muted">
              {fmtTime(res.checked_at)}・耗時 {(res.duration_ms / 1000).toFixed(1)} 秒
            </span>
          </div>
        )}
      </Card>

      {busy === 'check' && !res && (
        <Card>
          <div className="flex items-center gap-2 text-sm text-muted"><Spinner /> 檢查中，約需 5～30 秒…</div>
        </Card>
      )}

      {!res && busy !== 'check' && (
        <Card>
          <Empty title="還沒檢查過" hint="按右上角「開始檢查」。" icon={<Icons.shield className="h-5 w-5" />} />
        </Card>
      )}

      {res?.groups.map((g) => {
        const fails = g.items.filter((i) => i.status === 'fail').length
        return (
          <Card
            key={g.key}
            title={g.label}
            actions={fails > 0 ? <span className="badge badge-red">{fails} 項失敗</span> : <span className="badge badge-green">通過</span>}
          >
            {g.note && <p className="mb-1 text-xs text-muted">{g.note}</p>}
            <ul className="divide-y divide-line">
              {g.items.map((it, i) => <Row key={i} it={it} />)}
            </ul>
          </Card>
        )
      })}
    </div>
  )
}
