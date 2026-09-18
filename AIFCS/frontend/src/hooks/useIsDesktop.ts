import { useEffect, useState } from 'react'

/** Tailwind's `lg` breakpoint, where the Command Center switches layout. */
const DESKTOP_QUERY = '(min-width: 1024px)'

/**
 * Whether the desktop layout applies.
 *
 * The Command Center used to render both layouts and let CSS hide one. That is
 * fine for text panels and wrong for the 3D view: it mounts a second WebGL
 * context that is never seen, doubling the GPU cost and risking the browser's
 * per-page context limit. Mounting only the layout in use avoids that.
 */
export function useIsDesktop(): boolean {
  const [isDesktop, setIsDesktop] = useState(
    () => typeof window !== 'undefined' && window.matchMedia(DESKTOP_QUERY).matches,
  )

  useEffect(() => {
    const query = window.matchMedia(DESKTOP_QUERY)
    const update = (event: MediaQueryListEvent) => setIsDesktop(event.matches)

    setIsDesktop(query.matches)
    query.addEventListener('change', update)
    return () => query.removeEventListener('change', update)
  }, [])

  return isDesktop
}
