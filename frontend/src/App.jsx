import React, { useState, useEffect, useCallback, useRef } from 'react'
import { api, setToken, setRole, getRole, clearToken } from './api'
import { ThemeProvider, useTheme } from './contexts'
import { LanguageProvider, useLanguage } from './contexts'

// ---------------------------------------------------------------------------
// Icons (inline SVG, no dependency)
// ---------------------------------------------------------------------------
const Icon = ({ name, size = 16 }) => {
  const icons = {
    dashboard: <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/><rect x="14" y="14" width="7" height="7"/></svg>,
    article: <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/></svg>,
    log: <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>,
    play: <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><polygon points="5 3 19 12 5 21 5 3"/></svg>,
    refresh: <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/></svg>,
    sun: <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="5"/><line x1="12" y1="1" x2="12" y2="3"/><line x1="12" y1="21" x2="12" y2="23"/><line x1="4.22" y1="4.22" x2="5.64" y2="5.64"/><line x1="18.36" y1="18.36" x2="19.78" y2="19.78"/><line x1="1" y1="12" x2="3" y2="12"/><line x1="21" y1="12" x2="23" y2="12"/><line x1="4.22" y1="19.78" x2="5.64" y2="18.36"/><line x1="18.36" y1="5.64" x2="19.78" y2="4.22"/></svg>,
    moon: <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/></svg>,
    clock: <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/></svg>,
    edit: <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>,
    plus: <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>,
    trash: <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>,
    up: <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><line x1="12" y1="19" x2="12" y2="5"/><polyline points="5 12 12 5 19 12"/></svg>,
    down: <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><line x1="12" y1="5" x2="12" y2="19"/><polyline points="19 12 12 19 5 12"/></svg>,
    image: <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><rect x="3" y="3" width="18" height="18" rx="2" ry="2"/><circle cx="8.5" cy="8.5" r="1.5"/><polyline points="21 15 16 10 5 21"/></svg>,
  }
  return icons[name] || null
}

// ---------------------------------------------------------------------------
// Login Page
// ---------------------------------------------------------------------------
function LoginPage({ onLogin }) {
  const { t } = useLanguage()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  const handleSubmit = async (e) => {
    e.preventDefault()
    setLoading(true)
    setError('')
    try {
      const data = await api.login(username, password)
      setToken(data.token)
      setRole(data.role)
      onLogin()
    } catch (err) {
      setError(err.message || t('loginFailed'))
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="login-page">
      <div className="login-card">
        <div className="login-header">
          <h1>{t('loginTitle')}</h1>
          <p>{t('loginSubtitle')}</p>
        </div>
        <form onSubmit={handleSubmit}>
          {error && <div className="login-error">{error}</div>}
          <div className="login-field">
            <label>{t('username')}</label>
            <input
              type="text"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              autoComplete="username"
              autoFocus
            />
          </div>
          <div className="login-field">
            <label>{t('password')}</label>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete="current-password"
            />
          </div>
          <button className="btn btn-primary login-btn" type="submit" disabled={loading || !username || !password}>
            {loading ? '...' : t('login')}
          </button>
        </form>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Header
// ---------------------------------------------------------------------------
function Header({ onLogout, onNavigateProfile }) {
  const { theme, toggleTheme } = useTheme()
  const { lang, toggleLang } = useLanguage()
  const { t } = useLanguage()
  const isGuest = getRole() === 'guest'
  const [profile, setProfile] = useState(null)

  useEffect(() => {
    api.getProfile().then(setProfile).catch(() => {})
  }, [])

  const initial = profile?.username ? profile.username[0].toUpperCase() : 'U'

  return (
    <header className="header">
      {isGuest && (
        <span className="badge" style={{ background: 'var(--info)', color: '#fff', fontSize: 12, padding: '3px 10px', borderRadius: 10, marginRight: 'auto', marginLeft: 12 }}>
          {t('guestMode')}
        </span>
      )}
      <button className="header-btn lang-toggle" onClick={toggleLang} title={lang === 'zh' ? 'Switch to English' : '切换到中文'}>
        {lang === 'zh' ? 'EN' : '中文'}
      </button>
      <button className="header-btn" onClick={toggleTheme} title={theme === 'dark' ? 'Switch to Light Mode' : '切换到深色模式'}>
        <Icon name={theme === 'dark' ? 'sun' : 'moon'} size={16} />
      </button>
      <button className="header-user-btn" onClick={onNavigateProfile} title={lang === 'zh' ? '个人设置' : 'Profile'}>
        {initial}
      </button>
      <button className="header-btn logout-btn" onClick={onLogout} title={lang === 'zh' ? '退出登录' : 'Logout'}>
        <svg width={16} height={16} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><polyline points="16 17 21 12 16 7"/><line x1="21" y1="12" x2="9" y2="12"/></svg>
        <span>{lang === 'zh' ? '退出' : 'Logout'}</span>
      </button>
    </header>
  )
}

// ---------------------------------------------------------------------------
// Pages
// ---------------------------------------------------------------------------

function DashboardPage() {
  const { t } = useLanguage()
  const isGuest = getRole() === 'guest'
  const [status, setStatus] = useState(null)
  const [loading, setLoading] = useState(true)
  const [running, setRunning] = useState(false)
  const [schedules, setSchedules] = useState({})
  const [scheduleIntervals, setScheduleIntervals] = useState({})

  const SOURCE_KEYS = ['stcn', 'techflow', 'blockbeats', 'chaincatcher']

  const fetchStatus = useCallback(async () => {
    try {
      const [data, schedData] = await Promise.all([api.getStatus(), api.getSchedules()])
      setStatus(data)
      setRunning(data.running)
      setSchedules(schedData.schedules || {})
      setScheduleIntervals(prev => {
        const next = { ...prev }
        for (const [k, v] of Object.entries(schedData.schedules || {})) {
          if (next[k] === undefined) next[k] = v.interval_minutes
        }
        return next
      })
    } catch (e) {
      console.error(e)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { fetchStatus(); const iv = setInterval(fetchStatus, 5000); return () => clearInterval(iv) }, [fetchStatus])

  const handleRun = async (source, dryRun = false) => {
    setRunning(true)
    try {
      await api.run(source, dryRun)
    } catch (e) {
      alert(e.message)
      setRunning(false)
    }
  }

  const handleToggleSchedule = async (sourceKey) => {
    try {
      const sched = schedules[sourceKey]
      const newEnabled = !sched?.enabled
      const interval = scheduleIntervals[sourceKey] || sched?.interval_minutes || 60
      const result = await api.updateSchedule(sourceKey, newEnabled, interval)
      setSchedules(result.schedules || {})
    } catch (e) {
      alert(e.message)
    }
  }

  const handleIntervalChange = async (sourceKey, newInterval) => {
    setScheduleIntervals(prev => ({ ...prev, [sourceKey]: newInterval }))
    const sched = schedules[sourceKey]
    if (sched?.enabled) {
      try {
        const result = await api.updateSchedule(sourceKey, true, newInterval)
        setSchedules(result.schedules || {})
      } catch (e) {
        alert(e.message)
      }
    }
  }

  if (loading) return <div className="empty">{t('loading')}</div>

  const lastResult = status?.last_result

  return (
    <div>
      <h1 style={{ fontSize: 22, fontWeight: 700, marginBottom: 24 }}>{t('dashboard')}</h1>

      <div className="stats">
        <div className="stat">
          <div className="label">{t('totalPublished')}</div>
          <div className="value">{status?.total_published ?? 0}</div>
        </div>
        <div className="stat">
          <div className="label">{t('status')}</div>
          <div className="value" style={{ fontSize: 16, display: 'flex', alignItems: 'center', gap: 8 }}>
            {running ? (
              <><span className="spinner" /><span style={{ color: 'var(--warning)' }}>{t('running')}</span></>
            ) : (
              <span style={{ color: 'var(--success)' }}>{t('idle')}</span>
            )}
          </div>
        </div>
        <div className="stat">
          <div className="label">{t('lastRun')}</div>
          <div className="value" style={{ fontSize: 13, color: 'var(--text2)' }}>{status?.last_updated || t('notAvailable')}</div>
        </div>
      </div>

      <div className="card">
        <div className="card-header">
          <h2>{t('actions')}</h2>
        </div>
        {!isGuest ? (
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          <button className="btn btn-primary" disabled={running} onClick={() => handleRun('all')}>
            <Icon name="play" /> {t('runAllSources')}
          </button>
          <button className="btn btn-outline" disabled={running} onClick={() => handleRun('stcn')}>
            <Icon name="play" /> {t('stcnOnly')}
          </button>
          <button className="btn btn-outline" disabled={running} onClick={() => handleRun('techflow')}>
            <Icon name="play" /> {t('techFlowOnly')}
          </button>
          <button className="btn btn-outline" disabled={running} onClick={() => handleRun('blockbeats')}>
            <Icon name="play" /> {t('blockBeatsOnly')}
          </button>
          <button className="btn btn-outline" disabled={running} onClick={() => handleRun('chaincatcher')}>
            <Icon name="play" /> {t('chainCatcherOnly')}
          </button>
          <button className="btn btn-outline" disabled={running} onClick={() => handleRun('all', true)}>
            <Icon name="refresh" /> {t('dryRun')}
          </button>
        </div>
        ) : (
          <div style={{ color: 'var(--text2)', fontSize: 13, padding: '8px 0' }}>{t('guestModeHint')}</div>
        )}
      </div>

      <div className="card">
        <div className="card-header">
          <h2><Icon name="clock" size={14} style={{ marginRight: 6 }} />{t('sourceSettings')}</h2>
        </div>
        <div style={{ overflowX: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
            <thead>
              <tr style={{ borderBottom: '1px solid var(--border)' }}>
                <th style={{ textAlign: 'left', padding: '8px 12px', color: 'var(--text2)' }}>{t('source')}</th>
                <th style={{ textAlign: 'center', padding: '8px 12px', color: 'var(--text2)' }}>{t('interval')}</th>
                <th style={{ textAlign: 'center', padding: '8px 12px', color: 'var(--text2)' }}>{t('nextRun')}</th>
                <th style={{ textAlign: 'center', padding: '8px 12px', color: 'var(--text2)' }}>{t('action')}</th>
              </tr>
            </thead>
            <tbody>
              {SOURCE_KEYS.map(key => {
                const sched = schedules[key] || { enabled: false, interval_minutes: 60, next_run_time: null }
                const srcName = t(`sourceName_${key}`) || key
                return (
                  <tr key={key} style={{ borderBottom: '1px solid var(--border)' }}>
                    <td style={{ padding: '8px 12px', fontWeight: 500 }}>
                      {srcName}
                      <span
                        style={{
                          marginLeft: 8,
                          padding: '1px 6px',
                          borderRadius: 3,
                          fontSize: 11,
                          background: sched.enabled ? 'var(--success)' : 'var(--surface)',
                          color: sched.enabled ? '#fff' : 'var(--text2)',
                          border: `1px solid ${sched.enabled ? 'var(--success)' : 'var(--border)'}`,
                        }}
                      >
                        {sched.enabled ? t('schedulerEnabled') : t('schedulerDisabled')}
                      </span>
                    </td>
                    <td style={{ textAlign: 'center', padding: '8px 12px' }}>
                      <div style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
                        <input
                          type="number"
                          min="1"
                          max="1440"
                          value={scheduleIntervals[key] ?? sched.interval_minutes}
                          onChange={e => handleIntervalChange(key, Math.max(1, Math.min(1440, parseInt(e.target.value) || 1)))}
                          disabled={isGuest}
                          style={{
                            width: 60,
                            padding: '3px 6px',
                            background: 'var(--surface)',
                            border: `1px solid ${sched.enabled ? 'var(--success)' : 'var(--border)'}`,
                            borderRadius: 4,
                            color: 'var(--text)',
                            fontSize: 13,
                            textAlign: 'center',
                          }}
                        />
                        <span style={{ color: 'var(--text2)', fontSize: 12 }}>{t('minutes')}</span>
                      </div>
                    </td>
                    <td style={{ textAlign: 'center', padding: '8px 12px', fontSize: 12, color: sched.enabled ? 'var(--text)' : 'var(--text2)' }}>
                      {sched.next_run_time ? new Date(sched.next_run_time).toLocaleString() : t('schedulerNotSet')}
                    </td>
                    <td style={{ textAlign: 'center', padding: '8px 12px' }}>
                      {isGuest ? (
                        <span style={{ fontSize: 12, color: 'var(--text2)' }}>—</span>
                      ) : (
                      <button
                        className={`btn btn-sm ${sched.enabled ? 'btn-primary' : 'btn-outline'}`}
                        onClick={() => handleToggleSchedule(key)}
                        title={sched.enabled ? t('disableScheduler') : t('enableScheduler')}
                      >
                        {sched.enabled ? t('disable') : t('enable')}
                      </button>
                      )}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      </div>


      {lastResult && (
        <div className="card">
          <div className="card-header"><h2>{t('lastRunResult')}</h2></div>
          <div className="stats" style={{ marginBottom: 0 }}>
            <div className="stat">
              <div className="label">{t('published')}</div>
              <div className="value" style={{ color: 'var(--success)' }}>{lastResult.published?.length ?? 0}</div>
            </div>
            <div className="stat">
              <div className="label">{t('skipped')}</div>
              <div className="value" style={{ color: 'var(--text2)' }}>{lastResult.skipped?.length ?? 0}</div>
            </div>
            <div className="stat">
              <div className="label">{t('failed')}</div>
              <div className="value" style={{ color: lastResult.failed?.length ? 'var(--danger)' : 'var(--text2)' }}>{lastResult.failed?.length ?? 0}</div>
            </div>
          </div>
          {lastResult.failed?.length > 0 && (
            <div style={{ marginTop: 12 }}>
              <strong style={{ fontSize: 13, color: 'var(--danger)' }}>{t('failures')}:</strong>
              <ul style={{ fontSize: 12, color: 'var(--text2)', marginTop: 4, paddingLeft: 20 }}>
                {lastResult.failed.map((f, i) => <li key={i}>{f.id}: {f.error}</li>)}
              </ul>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

function ArticleEditor({ article, onSave, onCancel }) {
  const { t } = useLanguage()
  const isEdit = !!article

  const [title, setTitle] = useState(article?.title || '')
  const [coverSrc, setCoverSrc] = useState(article?.cover_src || article?.cover_image || '')
  const [abstract, setAbstract] = useState(article?.abstract || '')
  // Merge all text blocks into one string; image blocks become ![alt](src)
  const initialBody = (article?.blocks || []).map(b => {
    if (b.type === 'img') return `![${b.alt || ''}](${b.src || ''})`
    return b.text || ''
  }).join('\n\n')
  const [body, setBody] = useState(initialBody)
  const [sourceKey, setSourceKey] = useState(article?.source_key || 'techflow')
  const [saving, setSaving] = useState(false)

  const handleSave = async () => {
    if (!title.trim()) return
    setSaving(true)
    try {
      // Parse body text into blocks
      const blocks = body.split(/\n\n+/).filter(s => s.trim()).map(paragraph => {
        const imgMatch = paragraph.match(/^!\[([^\]]*)\]\(([^)]+)\)$/)
        if (imgMatch) return { type: 'img', alt: imgMatch[1], src: imgMatch[2] }
        return { type: 'p', text: paragraph.trim() }
      })
      const data = { title, cover_src: coverSrc, abstract, blocks, source_key: sourceKey }
      if (isEdit) {
        await api.updateArticle(article.article_id, data)
      } else {
        await api.createArticle(data)
      }
      onSave()
    } catch (e) {
      alert(e.message)
    } finally {
      setSaving(false)
    }
  }

  const handleDelete = async () => {
    if (!isEdit) return
    if (!confirm(t('confirmDelete'))) return
    try {
      await api.deleteArticle(article.article_id)
      onSave()
    } catch (e) {
      alert(e.message)
    }
  }

  return (
    <div className="editor-page">
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
        <h1 style={{ fontSize: 22, fontWeight: 700 }}>{isEdit ? t('editArticle') : t('createArticle')}</h1>
        <button className="btn btn-outline btn-sm" onClick={onCancel}>&larr; {t('back')}</button>
      </div>

      {!isEdit && (
        <div className="source-select">
          <button className={sourceKey === 'stcn' ? 'active' : ''} onClick={() => setSourceKey('stcn')}>STCN</button>
          <button className={sourceKey === 'techflow' ? 'active' : ''} onClick={() => setSourceKey('techflow')}>TechFlow</button>
          <button className={sourceKey === 'blockbeats' ? 'active' : ''} onClick={() => setSourceKey('blockbeats')}>BlockBeats</button>
          <button className={sourceKey === 'chaincatcher' ? 'active' : ''} onClick={() => setSourceKey('chaincatcher')}>ChainCatcher</button>
        </div>
      )}

      <div className="card">
        <div className="editor-field">
          <label>{t('title')}</label>
          <input id="editor-title" type="text" value={title} onChange={e => setTitle(e.target.value)} placeholder={t('title')} />
        </div>

        <div className="editor-field">
          <label>{t('coverImage')}</label>
          <input type="text" value={coverSrc} onChange={e => setCoverSrc(e.target.value)} placeholder="URL" />
          {coverSrc && (
            <div className="cover-preview">
              <img src={coverSrc} alt="cover" />
            </div>
          )}
        </div>

        <div className="editor-field">
          <label>{t('abstract')}</label>
          <textarea value={abstract} onChange={e => setAbstract(e.target.value)} rows={3} />
        </div>

        <div className="editor-field">
          <label>{t('content')}</label>
          <textarea value={body} onChange={e => setBody(e.target.value)} rows={15} placeholder={t('content')} style={{ minHeight: 300 }} />
        </div>

        <div className="editor-actions">
          <button className="btn btn-primary" onClick={handleSave} disabled={saving || !title.trim()}>
            {saving ? t('saving') : t('save')}
          </button>
          <button className="btn btn-outline" onClick={onCancel}>{t('cancel')}</button>
          {isEdit && (
            <button className="btn btn-danger" style={{ marginLeft: 'auto' }} onClick={handleDelete}>
              <Icon name="trash" size={14} /> {t('deleteArticle')}
            </button>
          )}
        </div>
      </div>
    </div>
  )
}

function ArticlesPage() {
  const { t } = useLanguage()
  const isGuest = getRole() === 'guest'
  const [source, setSource] = useState('all')
  const [page, setPage] = useState(1)
  const [total, setTotal] = useState(0)
  const [articles, setArticles] = useState([])
  const [loading, setLoading] = useState(true)
  const [selected, setSelected] = useState(null)  // article_id for detail view
  const [detailArticle, setDetailArticle] = useState(null)
  const [detailLoading, setDetailLoading] = useState(false)
  const [editor, setEditor] = useState(null) // null | article object for edit, 'new' for create
  const PAGE_SIZE = 20
  const totalPages = Math.ceil(total / PAGE_SIZE)

  const fetchArticles = useCallback(async () => {
    setLoading(true)
    try {
      const data = await api.getArticles(source, page, PAGE_SIZE)
      setTotal(data.total || 0)
      setArticles(data.articles || [])
    } catch (e) {
      console.error(e)
    } finally {
      setLoading(false)
    }
  }, [source, page])

  useEffect(() => { setPage(1) }, [source])
  useEffect(() => { fetchArticles() }, [fetchArticles])

  const handleSourceChange = (s) => {
    setSource(s)
    setPage(1)
  }

  // Load article detail on demand
  const handleSelectArticle = async (articleId) => {
    setSelected(articleId)
    setDetailLoading(true)
    try {
      const data = await api.getArticle(articleId)
      setDetailArticle(data)
    } catch (e) {
      alert(e.message)
      setSelected(null)
    } finally {
      setDetailLoading(false)
    }
  }

  const handleEditorSave = () => {
    setEditor(null)
    setSelected(null)
    setDetailArticle(null)
    fetchArticles()
  }

  const handleEditorOpen = async (articleId) => {
    // Need full article for editor
    try {
      const data = await api.getArticle(articleId)
      setEditor(data)
    } catch (e) {
      alert(e.message)
    }
  }

  // Editor mode
  if (editor === 'new') {
    return <ArticleEditor onSave={handleEditorSave} onCancel={() => setEditor(null)} />
  }
  if (editor && editor !== 'new') {
    return <ArticleEditor article={editor} onSave={handleEditorSave} onCancel={() => setEditor(null)} />
  }

  // Detail view
  if (selected) {
    if (detailLoading) return <div className="empty">{t('loading')}</div>
    const a = detailArticle
    if (!a) return null
    return (
      <div>
        <div style={{ display: 'flex', gap: 8, marginBottom: 16 }}>
          <button className="btn btn-outline btn-sm" onClick={() => { setSelected(null); setDetailArticle(null) }}>
            &larr; {t('back')}
          </button>
          {!isGuest && (<>
          <button className="btn btn-outline btn-sm" onClick={() => setEditor(a)}>
            <Icon name="edit" size={14} /> {t('editArticle')}
          </button>
          <button className="btn btn-sm" style={{ background: 'var(--danger)', color: 'white', border: 'none' }} onClick={async () => {
            if (!confirm(t('confirmDelete'))) return
            try { await api.deleteArticle(a.article_id); setSelected(null); setDetailArticle(null); fetchArticles() } catch (e) { alert(e.message) }
          }}>
            <Icon name="trash" size={14} /> {t('deleteArticle')}
          </button>
          </>)}
        </div>
        <div className="card">
          <div className="article-detail">
            <h1>{a.title}</h1>
            <div className="article-meta">
              <span>{a.source}</span>
              {a.author && <span>{t('author')}: {a.author}</span>}
              <span>{a.publish_time}</span>
              <a href={a.original_url} target="_blank" rel="noreferrer" style={{ color: 'var(--primary)' }}>
                {t('original')}
              </a>
            </div>
            {a.published ? <span className="badge badge-success">{t('published')}</span> : <span className="badge badge-default">{t('draft')}</span>}
          </div>
          <div style={{ marginTop: 16, borderTop: '1px solid var(--border)', paddingTop: 16 }}>
            <div className="article-body">
              {a.blocks?.map((b, i) => {
                if (b.type === 'img') return <p key={i}><img src={b.src} alt={b.alt} style={{ maxWidth: '100%' }} /></p>
                if (b.type === 'h2') return <h3 key={i}>{b.text}</h3>
                if (b.type === 'h3') return <h4 key={i}>{b.text}</h4>
                if (b.type === 'h4') return <h5 key={i}>{b.text}</h5>
                return <p key={i}>{b.text}</p>
              })}
            </div>
          </div>
        </div>
      </div>
    )
  }

  // List view
  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
        <h1 style={{ fontSize: 22, fontWeight: 700 }}>{t('articles')}</h1>
        <div style={{ display: 'flex', gap: 4 }}>
          {!isGuest && (
          <button className="btn btn-primary btn-sm" onClick={() => setEditor('new')}>
            <Icon name="plus" size={14} /> {t('createArticle')}
          </button>
          )}
          {['all', 'stcn', 'techflow', 'blockbeats', 'chaincatcher'].map(s => (
            <button key={s} className={`btn btn-sm ${source === s ? 'btn-primary' : 'btn-outline'}`} onClick={() => handleSourceChange(s)}>
              {s.toUpperCase()}
            </button>
          ))}
        </div>
      </div>

      {loading ? <div className="empty">{t('loading')}</div> : articles.length === 0 ? <div className="empty">{t('noArticlesFound')}</div> : (
        <>
          <div className="article-grid">
            {articles.map(a => (
              <div key={a.article_id} className="article-card" style={{ position: 'relative' }} onClick={() => handleSelectArticle(a.article_id)}>
                {!isGuest && (
                <button className="card-action-btn" title={t('editArticle')} onClick={e => { e.stopPropagation(); handleEditorOpen(a.article_id) }}>
                  <Icon name="edit" size={14} />
                </button>
                )}
                {a.cover_image ? (
                  <img className="card-cover" src={a.cover_image} alt={a.title} />
                ) : (
                  <div className="card-cover-placeholder">
                    {a.source_key === 'stcn' ? 'STCN' : a.source_key === 'blockbeats' ? 'BB' : a.source_key === 'chaincatcher' ? 'CC' : 'TF'}
                  </div>
                )}
                <div className="card-body">
                  <h3>{a.title}</h3>
                  {a.abstract && <p className="card-abstract">{a.abstract}</p>}
                </div>
                <div className="card-meta">
                  <span>
                    <span className={`badge ${a.source_key === 'stcn' ? 'badge-info' : a.source_key === 'blockbeats' ? 'badge-warning' : a.source_key === 'chaincatcher' ? 'badge-danger' : 'badge-warning'}`} style={{ marginRight: 6 }}>{a.source_key}</span>
                    {a.published ? <span className="badge badge-success">{t('published')}</span> : <span className="badge badge-default">{t('draft')}</span>}
                  </span>
                  <span>{a.publish_time || ''}</span>
                </div>
              </div>
            ))}
          </div>
          {totalPages > 1 && (
            <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', gap: 12, marginTop: 20 }}>
              <button className="btn btn-outline btn-sm" disabled={page <= 1} onClick={() => setPage(page - 1)}>
                &larr; {t('prev') || 'Prev'}
              </button>
              <span style={{ fontSize: 13, color: 'var(--text2)' }}>
                {page} / {totalPages} ({total})
              </span>
              <button className="btn btn-outline btn-sm" disabled={page >= totalPages} onClick={() => setPage(page + 1)}>
                {t('next') || 'Next'} &rarr;
              </button>
            </div>
          )}
        </>
      )}
    </div>
  )
}

function LogsPage() {
  const { t } = useLanguage()
  const [logs, setLogs] = useState([])
  const [lineCount, setLineCount] = useState(200)

  // Load initial log history
  useEffect(() => {
    api.getLogs(lineCount).then(data => setLogs(data.lines || [])).catch(console.error)
  }, [lineCount])

  // Subscribe to real-time log updates via SSE
  useEffect(() => {
    const es = new EventSource('/api/logs/stream')
    es.onmessage = (e) => {
      try {
        const line = JSON.parse(e.data)
        setLogs(prev => [...prev, line].slice(-1000))
      } catch { /* ignore parse errors */ }
    }
    return () => es.close()
  }, [])

  const escapeHtml = (text) => {
    const div = document.createElement('div')
    div.textContent = text
    return div.innerHTML
  }

  const highlightLevel = (line) => {
    // First escape HTML to prevent XSS
    const safe = escapeHtml(line)
    const m = safe.match(/\[(\w+)\]/)
    if (!m) return safe
    const level = m[1].toLowerCase()
    const cls = `level-${level}`
    return safe.replace(`[${m[1]}]`, `<span class="${cls}">[${m[1]}]</span>`)
  }

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
        <h1 style={{ fontSize: 22, fontWeight: 700 }}>{t('logs')}</h1>
        <select value={lineCount} onChange={e => setLineCount(Number(e.target.value))}
          style={{ background: 'var(--surface)', color: 'var(--text)', border: '1px solid var(--border)', borderRadius: 4, padding: '4px 8px', fontSize: 13, fontFamily: 'inherit' }}>
          <option value={100}>100 {t('lines')}</option>
          <option value={200}>200 {t('lines')}</option>
          <option value={500}>500 {t('lines')}</option>
          <option value={1000}>1000 {t('lines')}</option>
        </select>
      </div>
      <div className="logs">
        {logs.map((line, i) => (
          <div key={i} dangerouslySetInnerHTML={{ __html: highlightLevel(line) }} />
        ))}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Profile Page (full page — username, password, AI config)
// ---------------------------------------------------------------------------
function ProfilePage({ onLogout }) {
  const { t } = useLanguage()
  const isGuest = getRole() === 'guest'

  // -- profile section --
  const [username, setUsername] = useState('')
  const [usernameLoading, setUsernameLoading] = useState(true)
  const [usernameSaving, setUsernameSaving] = useState(false)

  useEffect(() => {
    api.getProfile().then(data => {
      setUsername(data.username || '')
    }).catch(console.error).finally(() => setUsernameLoading(false))
  }, [])

  const handleSaveUsername = async () => {
    if (!username.trim()) return
    setUsernameSaving(true)
    try {
      await api.updateProfile(username.trim())
      alert(t('usernameUpdated'))
    } catch (e) {
      alert(e.message)
    } finally {
      setUsernameSaving(false)
    }
  }

  // -- password section --
  const [pwForm, setPwForm] = useState({ old: '', new: '', confirm: '' })
  const [pwError, setPwError] = useState('')
  const [pwSaving, setPwSaving] = useState(false)

  const handleChangePassword = async (e) => {
    e.preventDefault()
    setPwError('')
    if (pwForm.new.length < 4) { setPwError(t('passwordTooShort')); return }
    if (pwForm.new !== pwForm.confirm) { setPwError(t('passwordMismatch')); return }
    setPwSaving(true)
    try {
      await api.changePassword(pwForm.old, pwForm.new)
      alert(t('passwordChanged'))
      onLogout()
    } catch (err) {
      setPwError(err.message || t('passwordChangeFailed'))
    } finally {
      setPwSaving(false)
    }
  }

  // -- AI config section --
  const [aiSettings, setAiSettings] = useState({ llm_api_url: '', llm_api_key: '', llm_model: '' })
  const [aiLoading, setAiLoading] = useState(true)
  const [aiSaving, setAiSaving] = useState(false)
  const [aiTesting, setAiTesting] = useState(false)

  useEffect(() => {
    api.getSettings().then(data => {
      setAiSettings({
        llm_api_url: data.llm_api_url || '',
        llm_api_key: data.llm_api_key || '',
        llm_model: data.llm_model || '',
      })
    }).catch(console.error).finally(() => setAiLoading(false))
  }, [])

  const handleSaveAi = async () => {
    setAiSaving(true)
    try {
      await api.updateSettings(aiSettings)
      alert(t('saveSuccess'))
    } catch (e) {
      alert(e.message)
    } finally {
      setAiSaving(false)
    }
  }

  const handleTestAi = async () => {
    // Save first, then test
    setAiTesting(true)
    try {
      await api.updateSettings(aiSettings)
      const result = await api.testLlm()
      alert(`${t('testConnection')}: OK\nModel: ${result.model}\nReply: ${result.reply}`)
    } catch (e) {
      alert(`${t('testConnection')}: FAIL\n${e.message}`)
    } finally {
      setAiTesting(false)
    }
  }

  if (usernameLoading || aiLoading) return <div className="empty">{t('loading')}</div>

  return (
    <div className="settings-page">
      <h1>{t('userProfile')}</h1>

      {/* Username section */}
      <div className="card" style={{ marginBottom: 20 }}>
        <div className="card-header"><h2>{t('username')}</h2></div>
        {isGuest ? (
          <div style={{ padding: '8px 0', fontSize: 14, color: 'var(--text)' }}>{username}</div>
        ) : (
        <div style={{ display: 'flex', gap: 8, alignItems: 'flex-end' }}>
          <div style={{ flex: 1 }}>
            <input
              type="text"
              value={username}
              onChange={e => setUsername(e.target.value)}
              style={{
                width: '100%', padding: '10px 12px', background: 'var(--surface)',
                border: '1px solid var(--border)', borderRadius: 'var(--radius)',
                color: 'var(--text)', fontSize: 14, fontFamily: 'inherit', boxSizing: 'border-box',
              }}
            />
          </div>
          <button className="btn btn-primary" onClick={handleSaveUsername} disabled={usernameSaving || !username.trim()}>
            {usernameSaving ? '...' : t('save')}
          </button>
        </div>
        )}
      </div>

      {/* Password section */}
      {!isGuest && (<div className="card" style={{ marginBottom: 20 }}>
        <div className="card-header"><h2>{t('changePassword')}</h2></div>
        <form onSubmit={handleChangePassword}>
          {pwError && <div className="login-error">{pwError}</div>}
          <div className="login-field">
            <label>{t('oldPassword')}</label>
            <input type="password" value={pwForm.old} onChange={e => setPwForm(p => ({ ...p, old: e.target.value }))} />
          </div>
          <div className="login-field">
            <label>{t('newPassword')}</label>
            <input type="password" value={pwForm.new} onChange={e => setPwForm(p => ({ ...p, new: e.target.value }))} />
          </div>
          <div className="login-field">
            <label>{t('confirmPassword')}</label>
            <input type="password" value={pwForm.confirm} onChange={e => setPwForm(p => ({ ...p, confirm: e.target.value }))} />
          </div>
          <div className="editor-actions" style={{ borderTop: 'none', paddingTop: 0 }}>
            <button type="submit" className="btn btn-primary" disabled={pwSaving || !pwForm.old || !pwForm.new || !pwForm.confirm}>
              {pwSaving ? '...' : t('save')}
            </button>
          </div>
        </form>
      </div>)}

      {/* AI Config section */}
      {!isGuest && (<div className="card">
        <div className="card-header"><h2>{t('llmConfig')}</h2></div>
        <div className="settings-group">
          <label>{t('llmApiUrl')}</label>
          <input
            type="text"
            value={aiSettings.llm_api_url}
            onChange={e => setAiSettings(p => ({ ...p, llm_api_url: e.target.value }))}
            placeholder="https://api.openai.com/v1"
          />
        </div>
        <div className="settings-group">
          <label>{t('llmApiKey')}</label>
          <input
            type="password"
            value={aiSettings.llm_api_key}
            onChange={e => setAiSettings(p => ({ ...p, llm_api_key: e.target.value }))}
            placeholder="sk-..."
          />
        </div>
        <div className="settings-group">
          <label>{t('llmModel')}</label>
          <input
            type="text"
            value={aiSettings.llm_model}
            onChange={e => setAiSettings(p => ({ ...p, llm_model: e.target.value }))}
            placeholder="gpt-4o / claude-3-sonnet"
          />
        </div>
        <div className="editor-actions" style={{ borderTop: 'none', paddingTop: 0 }}>
          <button className="btn btn-outline" onClick={handleTestAi} disabled={aiTesting}>
            {aiTesting ? '...' : t('testConnection')}
          </button>
          <button className="btn btn-primary" onClick={handleSaveAi} disabled={aiSaving}>
            {aiSaving ? '...' : t('save')}
          </button>
        </div>
      </div>)}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Main App
// ---------------------------------------------------------------------------
const PAGES = {
  dashboard: DashboardPage,
  articles: ArticlesPage,
  logs: LogsPage,
  profile: ProfilePage,
}

export default function App() {
  const [page, setPage] = useState('dashboard')
  const [authed, setAuthed] = useState(() => {
    try { return !!localStorage.getItem('auth_token') } catch { return false }
  })

  // Listen for auth:logout events from api.js (401 responses)
  useEffect(() => {
    const handler = () => setAuthed(false)
    window.addEventListener('auth:logout', handler)
    return () => window.removeEventListener('auth:logout', handler)
  }, [])

  const handleLogin = () => setAuthed(true)
  const handleLogout = () => {
    clearToken()
    setAuthed(false)
  }

  // Always wrap with Providers (LoginPage needs them)
  return (
    <ThemeProvider>
      <LanguageProvider>
        {!authed ? (
          <LoginPage onLogin={handleLogin} />
        ) : (
          <MainApp page={page} setPage={setPage} onLogout={handleLogout} />
        )}
      </LanguageProvider>
    </ThemeProvider>
  )
}

// Separate component for logged-in state (can use hooks safely)
function MainApp({ page, setPage, onLogout }) {
  const { t } = useLanguage()
  const PageComponent = PAGES[page] || DashboardPage

  // ProfilePage needs onLogout prop
  const pageProps = page === 'profile' ? { onLogout } : {}

  return (
    <div className="layout">
      <aside className="sidebar">
        <div className="sidebar-brand">
          <h1>{t('appName')}</h1>
        </div>
        <nav>
          <a href="#" className={page === 'dashboard' ? 'active' : ''} onClick={(e) => { e.preventDefault(); setPage('dashboard') }}>
            <Icon name="dashboard" /> <span>{t('dashboard')}</span>
          </a>
          <a href="#" className={page === 'articles' ? 'active' : ''} onClick={(e) => { e.preventDefault(); setPage('articles') }}>
            <Icon name="article" /> <span>{t('articles')}</span>
          </a>
          <a href="#" className={page === 'logs' ? 'active' : ''} onClick={(e) => { e.preventDefault(); setPage('logs') }}>
            <Icon name="log" /> <span>{t('logs')}</span>
          </a>
        </nav>
      </aside>
      <div className="main-area">
        <Header onLogout={onLogout} onNavigateProfile={() => setPage('profile')} />
        <main className="main">
          <PageComponent {...pageProps} />
        </main>
      </div>
    </div>
  )
}

// End of App.jsx
