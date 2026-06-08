'use client'
import { useRouter } from 'next/navigation'
import { RefreshCw } from 'lucide-react'
import { useState } from 'react'

export function RefreshButton() {
  const router = useRouter()
  const [spinning, setSpinning] = useState(false)

  function refresh() {
    setSpinning(true)
    router.refresh()
    setTimeout(() => setSpinning(false), 800)
  }

  return (
    <button onClick={refresh} className="btn-ghost text-xs gap-1.5">
      <RefreshCw className={`h-3.5 w-3.5 ${spinning ? 'animate-spin' : ''}`} />
      Refresh
    </button>
  )
}
