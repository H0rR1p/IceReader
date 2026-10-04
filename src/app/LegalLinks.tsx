import { useState } from 'react'

export default function LegalLinks() {
  const [open, setOpen] = useState(false)
  const [license, setLicense] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  async function showLicense() {
    setOpen(true); setError('')
    if (license) return
    try {
      const response = await fetch('/api/legal/license')
      if (!response.ok) throw new Error('许可证尚未生成，请使用完整发行构建。')
      setLicense(await response.text())
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)) }
  }

  async function downloadSource() {
    setBusy(true); setError('')
    try {
      const response = await fetch('/api/legal/source')
      if (!response.ok) throw new Error('对应源码包尚未生成，请使用完整发行构建。')
      const url = URL.createObjectURL(await response.blob())
      const link = document.createElement('a')
      link.href = url; link.download = 'IceReader-corresponding-source.zip'
      document.body.appendChild(link); link.click(); link.remove()
      window.setTimeout(() => URL.revokeObjectURL(url), 60000)
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); setOpen(true) }
    finally { setBusy(false) }
  }

  return <div className="legal-links">
    <button onClick={() => void showLicense()}>源码与许可证</button>
    <button disabled={busy} onClick={() => void downloadSource()}>{busy ? '正在下载…' : '下载对应源码'}</button>
    <small className="touhou-logo-notice">Logo：东方 Project 非官方二次创作</small>
    {open && <div className="dialog-backdrop"><section className="dialog legal-dialog" role="dialog" aria-modal="true" aria-labelledby="legal-title">
      <header><h2 id="legal-title">源码与许可证</h2><button aria-label="关闭" onClick={() => setOpen(false)}>×</button></header>
      <p>冰读代码采用 AGPL-3.0-or-later，按现状提供，不提供任何担保。第三方组件保留各自许可；logo、音频和用户内容的版权另行处理。</p>
      <p>Logo 为 H0rR1p 自行绘制的 Q 版琪露诺头像，属于「东方 Project」的非官方二次创作。东方 Project 及相关角色的原作权利归 ZUN／上海アリス幻樂団所有，绘制作者仅对自己的创作部分主张相应权利。冰读与上海アリス幻樂団不存在官方隶属、赞助或认可关系。</p>
      <p>Logo 与衍生视觉素材不属于 AGPL 代码授权范围，软件许可证不授予角色或品牌的使用及再授权权利。素材再利用需另获作者许可，并遵守东方 Project 官方二次创作指引。</p>
      <button className="button" disabled={busy} onClick={() => void downloadSource()}>{busy ? '正在下载…' : '下载当前版本对应源码'}</button>
      {error && <p role="alert">{error}</p>}
      <pre className="legal-license-text">{license || '正在读取许可证…'}</pre>
    </section></div>}
  </div>
}
