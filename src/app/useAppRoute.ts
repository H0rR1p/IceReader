import { useCallback, useEffect, useState } from 'react'

import type { AppPage } from './AppNavigation'


const PAGE_PATHS: Record<AppPage, string> = {
  library: '/library',
  dictionary: '/dictionary',
  cards: '/cards',
  review: '/review',
  profile: '/profile',
  cloud: '/cloud',
  admin: '/admin',
  settings: '/settings',
}

const PATH_PAGES = new Map(Object.entries(PAGE_PATHS).map(([page, path]) => [path, page as AppPage]))

function pageFromHash(): AppPage {
  const path = window.location.hash.slice(1).split('?')[0].replace(/\/$/, '') || '/library'
  return PATH_PAGES.get(path) ?? 'library'
}

export function useAppRoute() {
  const [page, setPageState] = useState<AppPage>(pageFromHash)

  useEffect(() => {
    const sync = () => setPageState(pageFromHash())
    window.addEventListener('hashchange', sync)
    if (!window.location.hash || !PATH_PAGES.has(window.location.hash.slice(1).split('?')[0].replace(/\/$/, ''))) {
      window.history.replaceState(null, '', `${window.location.pathname}${window.location.search}#${PAGE_PATHS.library}`)
    }
    return () => window.removeEventListener('hashchange', sync)
  }, [])

  const setPage = useCallback((page: AppPage, replace = false) => {
    const hash = `#${PAGE_PATHS[page]}`
    if (window.location.hash === hash) {
      setPageState(page)
      return
    }
    if (replace) window.history.replaceState(null, '', hash)
    else window.location.hash = PAGE_PATHS[page]
    setPageState(page)
  }, [])

  return { page, setPage }
}

