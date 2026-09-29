import { useEffect, useState, type ReactNode } from 'react'
import { Navigate, NavLink, Route, Routes, useLocation, useNavigate } from 'react-router-dom'
import { DEMO, getToken, setToken, setUnauthorizedHandler } from './api'
import { Logo } from './components/Logo'
import { Icons } from './components/ui'
import { MetaProvider } from './lib/meta'
import Backtest from './pages/Backtest'
import Dashboard from './pages/Dashboard'
import Login from './pages/Login'
import Monitor from './pages/Monitor'
import Settings from './pages/Settings'

const NAV = [
  { to: '/', label: '總覽', end: true },
  { to: '/settings', label: '設定' },
  { to: '/backtest', label: '回測' },
  { to: '/monitor', label: '監控' },
]

function Shell({ children }: { children: ReactNode }) {
  const [open, setOpen] = useState(false)
  const loc = useLocation()
  const nav = useNavigate()
  useEffect(() => setOpen(false), [loc.pathname])
  const logout = () => {
    setToken(null)
    nav('/login')
  }
  const linkCls = ({ isActive }: { isActive: boolean }) =>
    `rounded-lg px-3.5 py-1.5 text-sm font-medium transition-colors ${
      isActive ? 'bg-gold/15 text-gold' : 'text-slate-300 hover:bg-panel2 hover:text-white'
    }`
  return (
    <div className="min-h-full">
      {DEMO && (
        <div className="border-b border-gold/30 bg-gold/10 px-4 py-2 text-center text-xs text-gold">
          展示模式：畫面資料來自模擬行情與模擬 AI 回覆，所有操作只在這個頁面內生效，不會連線任何交易所。
        </div>
      )}
      <header className="sticky top-[env(safe-area-inset-top,0px)] z-40 border-b border-line bg-black/80 backdrop-blur-md">
        <div className="mx-auto flex h-14 max-w-[1440px] items-center gap-4 px-4 sm:px-6">
          <NavLink to="/" className="flex items-center gap-2.5">
            <Logo />
            <span className="text-lg font-bold tracking-tight text-gold">GoldHunter</span>
            <span className="hidden text-xs text-muted lg:inline">智能量化交易策略平台</span>
          </NavLink>
          <nav className="ml-4 hidden items-center gap-1 md:flex">
            {NAV.map((n) => (
              <NavLink key={n.to} to={n.to} end={n.end} className={linkCls}>
                {n.label}
              </NavLink>
            ))}
          </nav>
          <div className="ml-auto flex items-center gap-2">
            {!DEMO && <button className="btn btn-ghost btn-sm hidden md:inline-flex" onClick={logout} title="登出">
              <Icons.logout /> 登出
            </button>}
            <button className="btn btn-secondary btn-sm md:hidden" onClick={() => setOpen(!open)} aria-label="選單">
              {open ? <Icons.x /> : <Icons.menu />}
            </button>
          </div>
        </div>
        {open && (
          <nav className="anim-fade flex flex-col gap-1 border-t border-line px-4 py-3 md:hidden">
            {NAV.map((n) => (
              <NavLink key={n.to} to={n.to} end={n.end} className={linkCls}>
                {n.label}
              </NavLink>
            ))}
            {!DEMO && (
              <button className="rounded-lg px-3.5 py-1.5 text-left text-sm text-muted hover:bg-panel2" onClick={logout}>
                登出
              </button>
            )}
          </nav>
        )}
      </header>
      <main className="mx-auto max-w-[1440px] px-4 py-5 sm:px-6 sm:py-6">{children}</main>
    </div>
  )
}

export default function App() {
  const nav = useNavigate()
  const loc = useLocation()
  useEffect(() => {
    setUnauthorizedHandler(() => {
      setToken(null)
      if (window.location.pathname !== '/login') nav('/login', { replace: true })
    })
  }, [nav])

  if (loc.pathname !== '/login' && !getToken()) return <Navigate to="/login" replace />

  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route
        path="*"
        element={
          <Shell>
            <MetaProvider>
              <Routes>
                <Route path="/" element={<Dashboard />} />
                <Route path="/settings" element={<Settings />} />
                <Route path="/backtest" element={<Backtest />} />
                <Route path="/monitor" element={<Monitor />} />
                <Route path="*" element={<Navigate to="/" replace />} />
              </Routes>
            </MetaProvider>
          </Shell>
        }
      />
    </Routes>
  )
}
