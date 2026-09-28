import { createContext, useContext, useEffect, useState, type ReactNode } from 'react'
import { api, errMsg, type Meta, type StrategyTypeMeta } from '../api'
import { Spinner } from '../components/ui'

const MetaCtx = createContext<Meta | null>(null)

export function MetaProvider({ children }: { children: ReactNode }) {
  const [meta, setMeta] = useState<Meta | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [attempt, setAttempt] = useState(0)
  useEffect(() => {
    api.meta().then(setMeta).catch((e) => setError(errMsg(e)))
  }, [attempt])
  if (!meta) {
    return (
      <div className="flex min-h-[60vh] flex-col items-center justify-center gap-3 text-muted">
        {error ? (
          <>
            <div className="text-sm text-red-300">{error}</div>
            <button className="btn btn-secondary" onClick={() => { setError(null); setAttempt((a) => a + 1) }}>重試</button>
          </>
        ) : (
          <>
            <Spinner className="h-6 w-6 text-gold" />
            <div className="text-sm">載入中…</div>
          </>
        )}
      </div>
    )
  }
  return <MetaCtx.Provider value={meta}>{children}</MetaCtx.Provider>
}

export function useMeta(): Meta {
  const m = useContext(MetaCtx)
  if (!m) throw new Error('MetaProvider missing')
  return m
}

export function useStrategyType(kind: string | null | undefined): StrategyTypeMeta | undefined {
  const meta = useMeta()
  return meta.strategy_types.find((t) => t.type === kind)
}
