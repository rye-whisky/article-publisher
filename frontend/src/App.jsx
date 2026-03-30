import React, { useState, useEffect, useCallback } from 'react'
import { api } from './api'
import { useTheme } from './contexts'
import { useLanguage } from './contexts'

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
// Header
// ---------------------------------------------------------------------------
function Header() {
  const { theme, toggleTheme } = useTheme()
  const { lang, toggleLang } = useLanguage()

  return (
    <header className="header">
      <button className="header-btn lang-toggle" onClick={toggleLang} title={lang === 'zh' ? 'Switch to English' : '切换到中文'}>
        {lang === 'zh' ? 'EN' : '中文'}
      </button>
      <button className="header-btn" onClick={toggleTheme} title={theme === 'dark' ? 'Switch to Light Mode' : '切换到深色模式'}>
        <Icon name={theme === 'dark' ? 'sun' : 'moon'} size={16} />
      </button>
    </header>
  )
}

// ---------------------------------------------------------------------------
// Pages
// ---------------------------------------------------------------------------

function DashboardPage() {
  const { t } = useLanguage()
  const [status, setStatus] = useState(null)
  const [loading, setLoading] = useState(true)
  const [running, setRunning] = useState(false)
  const [scheduler, setScheduler] = useState({ enabled: false, interval_minutes: 60, next_run_time: null })
  const [schedulerInterval, setSchedulerInterval] = useState(60)

  const fetchStatus = useCallback(async () => {
    try {
      const data = await api.getStatus()
      setStatus(data)
      setRunning(data.running)
      if (data.scheduler) setScheduler(data.scheduler)
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

  const handleToggleScheduler = async () => {
    try {
      const newEnabled = !scheduler.enabled
      const result = await api.updateScheduler(newEnabled, schedulerInterval)
      setScheduler(result)
    } catch (e) {
      alert(e.message)
    }
  }

  const handleUpdateScheduler = async () => {
    try {
      const result = await api.updateScheduler(scheduler.enabled, schedulerInterval)
      setScheduler(result)
    } catch (e) {
      alert(e.message)
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
          <button className="btn btn-outline" disabled={running} onClick={() => handleRun('all', true)}>
            <Icon name="refresh" /> {t('dryRun')}
          </button>
        </div>
      </div>

      <div className="card">
        <div className="card-header">
          <h2><Icon name="clock" size={14} style={{ marginRight: 6 }} />{t('scheduler')}</h2>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
          <button
            className={`btn ${scheduler.enabled ? 'btn-primary' : 'btn-outline'}`}
            onClick={handleToggleScheduler}
          >
            {scheduler.enabled ? <Icon name="clock" /> : <Icon name="play" />}
            {scheduler.enabled ? t('schedulerEnabled') : t('enableScheduler')}
          </button>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <label style={{ fontSize: 13, color: 'var(--text2)' }}>{t('intervalMinutes')}:</label>
            <input
              type="number"
              min="1"
              max="1440"
              value={schedulerInterval}
              onChange={e => setSchedulerInterval(Math.max(1, Math.min(1440, parseInt(e.target.value) || 1)))}
              disabled={scheduler.enabled}
              style={{
                width: 70,
                padding: '4px 8px',
                background: 'var(--surface)',
                border: '1px solid var(--border)',
                borderRadius: 4,
                color: 'var(--text)',
                fontSize: 13,
              }}
            />
            <button
              className="btn btn-sm btn-outline"
              onClick={handleUpdateScheduler}
              disabled={scheduler.enabled}
            >
              {t('updateScheduler')}
            </button>
          </div>
          <div style={{ marginLeft: 'auto', fontSize: 12, color: 'var(--text2)' }}>
            {t('nextRun')}: {scheduler.next_run_time ? new Date(scheduler.next_run_time).toLocaleString() : t('schedulerNotSet')}
          </div>
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
  const [source, setSource] = useState('all')
  const [articles, setArticles] = useState([])
  const [loading, setLoading] = useState(true)
  const [selected, setSelected] = useState(null)
  const [editor, setEditor] = useState(null) // null | article object for edit, 'new' for create

  const fetchArticles = useCallback(async () => {
    setLoading(true)
    try {
      const data = await api.getArticles(source)
      setArticles(data.articles || [])
    } catch (e) {
      console.error(e)
    } finally {
      setLoading(false)
    }
  }, [source])

  useEffect(() => { fetchArticles() }, [fetchArticles])

  const handleRemove = async (id) => {
    if (!confirm(`${t('removeFromPublishedConfirm')} — ${id}?`)) return
    try {
      await api.removeFromState(id)
      fetchArticles()
    } catch (e) {
      alert(e.message)
    }
  }

  const handleEditorSave = () => {
    setEditor(null)
    setSelected(null)
    fetchArticles()
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
    const a = selected
    return (
      <div>
        <div style={{ display: 'flex', gap: 8, marginBottom: 16 }}>
          <button className="btn btn-outline btn-sm" onClick={() => setSelected(null)}>
            &larr; {t('back')}
          </button>
          <button className="btn btn-outline btn-sm" onClick={() => setEditor(a)}>
            <Icon name="edit" size={14} /> {t('editArticle')}
          </button>
          <button className="btn btn-sm" style={{ background: 'var(--danger)', color: 'white', border: 'none' }} onClick={async () => {
            if (!confirm(t('confirmDelete'))) return
            try { await api.deleteArticle(a.article_id); setSelected(null); fetchArticles() } catch (e) { alert(e.message) }
          }}>
            <Icon name="trash" size={14} /> {t('deleteArticle')}
          </button>
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
          <button className="btn btn-primary btn-sm" onClick={() => setEditor('new')}>
            <Icon name="plus" size={14} /> {t('createArticle')}
          </button>
          {['all', 'stcn', 'techflow'].map(s => (
            <button key={s} className={`btn btn-sm ${source === s ? 'btn-primary' : 'btn-outline'}`} onClick={() => setSource(s)}>
              {s.toUpperCase()}
            </button>
          ))}
        </div>
      </div>

      {loading ? <div className="empty">{t('loading')}</div> : articles.length === 0 ? <div className="empty">{t('noArticlesFound')}</div> : (
        <div className="article-grid">
          {articles.map(a => (
            <div key={a.article_id} className="article-card" style={{ position: 'relative' }} onClick={() => setSelected(a)}>
              <button className="card-action-btn" title={t('editArticle')} onClick={e => { e.stopPropagation(); setEditor(a) }}>
                <Icon name="edit" size={14} />
              </button>
              {a.cover_image ? (
                <img className="card-cover" src={a.cover_image} alt={a.title} />
              ) : (
                <div className="card-cover-placeholder">
                  {a.source_key === 'stcn' ? 'STCN' : 'TechFlow'}
                </div>
              )}
              <div className="card-body">
                <h3>{a.title}</h3>
                {a.abstract && <p className="card-abstract">{a.abstract}</p>}
              </div>
              <div className="card-meta">
                <span>
                  <span className={`badge ${a.source_key === 'stcn' ? 'badge-info' : 'badge-warning'}`} style={{ marginRight: 6 }}>{a.source_key}</span>
                  {a.published ? <span className="badge badge-success">{t('published')}</span> : <span className="badge badge-default">{t('draft')}</span>}
                </span>
                <span>{a.publish_time || ''}</span>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

function LogsPage() {
  const { t } = useLanguage()
  const [logs, setLogs] = useState([])
  const [lineCount, setLineCount] = useState(200)

  useEffect(() => {
    api.getLogs(lineCount).then(data => setLogs(data.lines || [])).catch(console.error)
    const iv = setInterval(() => {
      api.getLogs(lineCount).then(data => setLogs(data.lines || [])).catch(console.error)
    }, 3000)
    return () => clearInterval(iv)
  }, [lineCount])

  const highlightLevel = (line) => {
    const m = line.match(/\[(\w+)\]/)
    if (!m) return line
    const level = m[1].toLowerCase()
    const cls = `level-${level}`
    return line.replace(`[${m[1]}]`, `<span class="${cls}">[${m[1]}]</span>`)
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
// Main App
// ---------------------------------------------------------------------------
const PAGES = {
  dashboard: DashboardPage,
  articles: ArticlesPage,
  logs: LogsPage,
}

export default function App() {
  const { t } = useLanguage()
  const [page, setPage] = useState('dashboard')

  const PageComponent = PAGES[page] || DashboardPage

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
        <Header />
        <main className="main">
          <PageComponent />
        </main>
      </div>
    </div>
  )
}
