import { useState } from 'react'
import { BootScreen } from '@/pages/BootScreen'
import { CommandCenter } from '@/pages/CommandCenter'
import { useSystemPolling } from '@/hooks/useSystemPolling'

export default function App() {
  const [entered, setEntered] = useState(false)
  useSystemPolling(5000)

  return entered ? <CommandCenter /> : <BootScreen onEnter={() => setEntered(true)} />
}
