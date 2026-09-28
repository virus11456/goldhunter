import { useState, type FormEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, errMsg, getToken, setToken } from '../api'
import { Logo } from '../components/Logo'
import { Spinner } from '../components/ui'

export default function Login() {
  const nav = useNavigate()
  const [token, setTok] = useState(getToken() ?? '')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    const t = token.trim()
    if (!t) return
    setBusy(true)
    setError(null)
    setToken(t)
    try {
      await api.meta()
      nav('/', { replace: true })
    } catch (err) {
      setToken(null)
      setError(errMsg(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="relative flex min-h-full items-center justify-center overflow-hidden px-4 py-10">
      <div className="pointer-events-none absolute left-1/2 top-1/3 h-[420px] w-[420px] -translate-x-1/2 -translate-y-1/2 rounded-full bg-gold/10 blur-[120px]" />
      <form onSubmit={submit} className="card anim-in relative w-full max-w-md p-7">
        <div className="mb-6 flex items-center gap-3">
          <Logo size={40} />
          <div>
            <div className="text-2xl font-bold tracking-tight text-gold">GoldHunter</div>
            <div className="text-xs text-muted">單人量化交易平台</div>
          </div>
        </div>
        <label className="label" htmlFor="token">API Token</label>
        <input
          id="token"
          type="password"
          autoComplete="current-password"
          className="input font-mono"
          placeholder="貼上 API Token"
          value={token}
          onChange={(e) => setTok(e.target.value)}
          autoFocus
        />
        <p className="hint leading-relaxed">
          後端啟動時會在終端機顯示 Token 檔案位置（預設為 <code className="rounded bg-panel2 px-1 text-slate-300">data/api_token</code>）。Token 只存在此瀏覽器。
        </p>
        {error && <div className="mt-3 rounded-lg border border-down/30 bg-down/10 px-3 py-2 text-sm text-red-200">{error}</div>}
        <button className="btn btn-primary mt-5 w-full py-2.5" disabled={busy || !token.trim()}>
          {busy && <Spinner />} 登入
        </button>
      </form>
    </div>
  )
}
