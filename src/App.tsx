import { lazy, Suspense, useEffect, useRef, useState } from 'react'
import { checkHealth, loadApiSettings, loadVoiceSettings, saveApiSettings, saveVoiceSettings, uploadVoiceTemplate } from './api'
import AppNavigation from './app/AppNavigation'
import type { AppPage } from './app/AppNavigation'
import { loadCurrentUser, logoutCurrentAccount } from './app/session'
import { useActivityTracker } from './features/activity/useActivityTracker'
import LoginPage from './features/auth/LoginPage'
import Library from './features/library/Library'
import { useLibraryController } from './features/library/useLibraryController'
import Workspace from './features/reader/Workspace'
import { useAnalysisController } from './features/translation/useAnalysisController'
import type { ApiSettings, CurrentUser, VoiceSettings } from './types'
import type { Book } from './types'
import UserAvatar from './app/UserAvatar'
import { useAppRoute } from './app/useAppRoute'

const ProfilePage = lazy(() => import('./features/activity/ProfilePage'))
const CardCenterPage = lazy(() => import('./features/cards/CardCenterPage'))
const ReviewPage = lazy(() => import('./features/cards/ReviewPage'))
const SettingsPage = lazy(() => import('./features/settings/SettingsPage'))
const LexiconManagerPage = lazy(() => import('./features/study/LexiconManagerPage'))
const CloudAccountPage = lazy(() => import('./features/sync/CloudAccountPage'))
const AdminPage = lazy(() => import('./features/admin/AdminPage'))
const ImportDialog = lazy(() => import('./features/settings/Dialogs').then((module) => ({ default: module.ImportDialog })))

const DEFAULT_SETTINGS: ApiSettings = { apiKey: '', baseUrl: 'https://api.deepseek.com', model: 'deepseek-chat', hasStoredApiKey: false, cacheHitUsdPerMillion: 0, cacheMissUsdPerMillion: 0, outputUsdPerMillion: 0 }
const DEFAULT_VOICE_SETTINGS: VoiceSettings = { ymmPath: '', ymmFound: false, templateFound: false, characterName: '', characterNames: [], playbackRate: 85, volume: 50, ready: false }

function App() {
  const [serverReady, setServerReady] = useState<boolean | null>(null)
  const [currentUser, setCurrentUser] = useState<CurrentUser | null>(null)
  const [entered, setEntered] = useState(false)
  useEffect(() => { void checkHealth().then(setServerReady); void loadCurrentUser().then(setCurrentUser).catch(() => undefined) }, [])
  if (!entered || !currentUser) return <LoginPage currentUser={currentUser} serverReady={serverReady} onEnter={async (user) => { setCurrentUser(user); setEntered(true) }} />
  return <AuthenticatedApp currentUser={currentUser} serverReady={serverReady} onUserChange={setCurrentUser} onExit={(user) => { setCurrentUser(user); setEntered(false) }} />
}

function AuthenticatedApp({ currentUser, serverReady, onUserChange, onExit }: { currentUser: CurrentUser; serverReady: boolean | null; onUserChange: (user: CurrentUser) => void; onExit: (user: CurrentUser) => void }) {
  const { page, setPage } = useAppRoute()
  const [showImport, setShowImport] = useState(false)
  const [settings, setSettings] = useState<ApiSettings>(DEFAULT_SETTINGS)
  const [voiceSettings, setVoiceSettings] = useState<VoiceSettings>(DEFAULT_VOICE_SETTINGS)
  const [notice, setNotice] = useState('')
  const [logoBouncing, setLogoBouncing] = useState(false)
  const [resumeBook, setResumeBook] = useState<Book | null>(null)
  const logoAudiosRef = useRef(new Set<HTMLAudioElement>())
  const library = useLibraryController(setNotice)
  const { books, activeBook, activeChapter, loadingBookId, loadingChapterId, libraryLoading, setActiveBook, setActiveChapter, refreshBooks, returnToLibrary, saveImportedBook, openBook, selectChapter, deleteBook, changeBookCover, setBookImageVisibility, saveBookCollection, dissolveBookCollection } = library
  const analysis = useAnalysisController({ userId: currentUser.user_id, activeBook, settings, setActiveBook, setActiveChapter, refreshBooks, setNotice })
  const { backgroundJob, dataRevision, translationMode, translationConcurrency, setTranslationMode, setTranslationConcurrency, processChapter, explainAndStoreSentence, runBackground, cancelBackground } = analysis

  useActivityTracker(currentUser.user_id, page === 'review' ? 'review' : page === 'cards' ? 'cards' : page === 'dictionary' ? 'dictionary' : activeBook && activeChapter ? 'reading' : null)
  useEffect(() => { void Promise.all([loadApiSettings(), loadVoiceSettings()]).then(([nextApi, nextVoice]) => { setSettings(nextApi); setVoiceSettings(nextVoice) }).catch(() => undefined) }, [])
  useEffect(() => { if (!notice) return; const timer = window.setTimeout(() => setNotice(''), 5000); return () => window.clearTimeout(timer) }, [notice])

  function navigate(target: AppPage) {
    if (activeBook) setResumeBook(activeBook)
    returnToLibrary(); setPage(target)
  }
  function resumeReading() {
    if (!resumeBook) return
    const latest = books.find((book) => book.id === resumeBook.id) ?? resumeBook
    setPage('library')
    void openBook(latest)
  }
  function updateTranslationMode(mode: typeof translationMode) { setTranslationMode(mode); localStorage.setItem(`bingdu:${currentUser.user_id}:translation-mode`, mode) }
  function updateTranslationConcurrency(value: number) { setTranslationConcurrency(value); localStorage.setItem(`bingdu:${currentUser.user_id}:translation-concurrency`, String(value)) }
  function bounceLogoAndOpenLibrary() {
    navigate('library')
    const audio = new Audio('/bingdu-logo-click.wav'); logoAudiosRef.current.add(audio)
    audio.addEventListener('ended', () => logoAudiosRef.current.delete(audio), { once: true }); void audio.play().catch(() => logoAudiosRef.current.delete(audio))
    setLogoBouncing(false); window.requestAnimationFrame(() => setLogoBouncing(true))
  }
  async function logout() { cancelBackground(); const guest = await logoutCurrentAccount(); onUserChange(guest); onExit(guest) }

  const overlays = <Suspense fallback={null}>
    {showImport && <ImportDialog onClose={() => setShowImport(false)} onImported={async (book) => { setShowImport(false); await saveImportedBook(book) }} />}
  </Suspense>

  if (activeBook) return <div className="app-shell">
    <header className="topbar reader-topbar"><button className="brand" onClick={bounceLogoAndOpenLibrary}><span className={`brand-mark ${logoBouncing ? 'is-bouncing' : ''}`} onAnimationEnd={() => setLogoBouncing(false)}><img src="/bingdu-logo.png" alt="" /></span><span><strong>冰读</strong><small>baka都能用的日语学习阅读器</small></span></button><div className="top-actions"><span className={`server-dot ${serverReady ? 'ready' : 'down'}`} /><button className="reader-user-avatar" onClick={() => navigate('profile')} aria-label={`打开${currentUser.display_name}的个人主页`} title={currentUser.display_name}><UserAvatar user={currentUser} /></button><button className="button ghost" onClick={() => navigate('settings')}>设置</button><button className="button primary" onClick={() => setShowImport(true)}>导入书籍</button></div></header>
    {notice && <div className="notice" role="status">{notice}<button onClick={() => setNotice('')}>×</button></div>}
    <Workspace userId={currentUser.user_id} book={activeBook} activeChapter={activeChapter} loadingChapterId={loadingChapterId} onSelectChapter={(chapter, sentenceId) => void selectChapter(chapter, sentenceId)} onProcessChapter={processChapter} onExplainSentence={explainAndStoreSentence} backgroundJob={backgroundJob} onCancelBackground={cancelBackground} dataRevision={dataRevision} onBackgroundBook={(kind) => void runBackground(kind, 'book')} onBackgroundChapter={(kind, chapter) => void runBackground(kind, 'chapter', chapter)} onBookImageVisibility={setBookImageVisibility} onNotice={setNotice} />
    {overlays}
  </div>

  return <div className="app-shell app-dashboard-shell">
    <AppNavigation page={page} user={currentUser} bookCount={books.length} canResumeReading={Boolean(resumeBook)} onResumeReading={resumeReading} onNavigate={navigate} onImport={() => setShowImport(true)} />
    <div className="app-page-column">
      {notice && <div className="notice" role="status">{notice}<button onClick={() => setNotice('')}>×</button></div>}
      <Suspense fallback={<section className="page-loading" aria-live="polite"><div className="loading-dango" /><span>正在打开页面…</span></section>}>
        {page === 'library' && <Library books={books} loading={libraryLoading} loadingBookId={loadingBookId} onOpen={openBook} onDelete={deleteBook} onChangeCover={changeBookCover} onImport={() => setShowImport(true)} onSaveCollection={saveBookCollection} onDissolveCollection={dissolveBookCollection} />}
        {page === 'dictionary' && <LexiconManagerPage />}
        {page === 'cards' && <CardCenterPage onNotice={setNotice} onStartReview={() => setPage('review')} />}
        {page === 'review' && <ReviewPage onNotice={setNotice} onManageCards={() => setPage('cards')} />}
        {page === 'profile' && <ProfilePage user={currentUser} books={books} onUserChange={onUserChange} onLogout={logout} onOpenCards={() => setPage('cards')} onOpenReview={() => setPage('review')} />}
        {page === 'cloud' && <CloudAccountPage onNotice={setNotice} />}
        {page === 'admin' && currentUser.cloud_role === 'admin' && <AdminPage onNotice={setNotice} />}
        {page === 'settings' && <SettingsPage apiSettings={settings} voiceSettings={voiceSettings} books={books} translationMode={translationMode} translationConcurrency={translationConcurrency} onTranslationModeChange={updateTranslationMode} onTranslationConcurrencyChange={updateTranslationConcurrency} onBookImageVisibility={setBookImageVisibility} onSaveApi={async (next) => setSettings(await saveApiSettings(next))} onSaveVoice={async (next, template) => { if (template) await uploadVoiceTemplate(template); setVoiceSettings(await saveVoiceSettings(next)) }} onNotice={setNotice} />}
      </Suspense>
    </div>
    {overlays}
  </div>
}

export default App

