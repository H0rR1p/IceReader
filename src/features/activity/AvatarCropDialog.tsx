import { useEffect, useRef, useState } from 'react'

const PREVIEW_SIZE = 280
const OUTPUT_SIZE = 512

export default function AvatarCropDialog({ file, onCancel, onConfirm }: {
  file: File
  onCancel: () => void
  onConfirm: (file: File) => Promise<void>
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const imageRef = useRef<HTMLImageElement | null>(null)
  const dragRef = useRef<{ x: number; y: number; startX: number; startY: number } | null>(null)
  const [imageUrl, setImageUrl] = useState('')
  const [zoom, setZoom] = useState(1)
  const [offset, setOffset] = useState({ x: 0, y: 0 })
  const [saving, setSaving] = useState(false)
  const [imageRevision, setImageRevision] = useState(0)

  useEffect(() => {
    const url = URL.createObjectURL(file)
    setImageUrl(url)
    return () => URL.revokeObjectURL(url)
  }, [file])

  function clampOffset(image: HTMLImageElement, next: { x: number; y: number }, nextZoom = zoom) {
    const base = Math.max(PREVIEW_SIZE / image.naturalWidth, PREVIEW_SIZE / image.naturalHeight)
    const width = image.naturalWidth * base * nextZoom
    const height = image.naturalHeight * base * nextZoom
    return {
      x: Math.max(-(width - PREVIEW_SIZE) / 2, Math.min((width - PREVIEW_SIZE) / 2, next.x)),
      y: Math.max(-(height - PREVIEW_SIZE) / 2, Math.min((height - PREVIEW_SIZE) / 2, next.y)),
    }
  }

  useEffect(() => {
    const canvas = canvasRef.current; const image = imageRef.current
    if (!canvas || !image || !image.complete) return
    const context = canvas.getContext('2d'); if (!context) return
    const base = Math.max(PREVIEW_SIZE / image.naturalWidth, PREVIEW_SIZE / image.naturalHeight)
    const width = image.naturalWidth * base * zoom; const height = image.naturalHeight * base * zoom
    context.clearRect(0, 0, PREVIEW_SIZE, PREVIEW_SIZE)
    context.save(); context.beginPath(); context.arc(PREVIEW_SIZE / 2, PREVIEW_SIZE / 2, PREVIEW_SIZE / 2, 0, Math.PI * 2); context.clip()
    context.drawImage(image, (PREVIEW_SIZE - width) / 2 + offset.x, (PREVIEW_SIZE - height) / 2 + offset.y, width, height)
    context.restore()
  }, [imageRevision, imageUrl, offset, zoom])

  function drawOutput(): Promise<Blob> {
    return new Promise((resolve, reject) => {
      const image = imageRef.current
      if (!image) { reject(new Error('头像图片尚未加载')); return }
      const canvas = document.createElement('canvas'); canvas.width = OUTPUT_SIZE; canvas.height = OUTPUT_SIZE
      const context = canvas.getContext('2d'); if (!context) { reject(new Error('无法创建头像裁剪画布')); return }
      const ratio = OUTPUT_SIZE / PREVIEW_SIZE
      const base = Math.max(PREVIEW_SIZE / image.naturalWidth, PREVIEW_SIZE / image.naturalHeight)
      const width = image.naturalWidth * base * zoom * ratio; const height = image.naturalHeight * base * zoom * ratio
      context.beginPath(); context.arc(OUTPUT_SIZE / 2, OUTPUT_SIZE / 2, OUTPUT_SIZE / 2, 0, Math.PI * 2); context.clip()
      context.drawImage(image, (OUTPUT_SIZE - width) / 2 + offset.x * ratio, (OUTPUT_SIZE - height) / 2 + offset.y * ratio, width, height)
      canvas.toBlob((blob) => blob ? resolve(blob) : reject(new Error('头像裁剪失败')), 'image/png')
    })
  }

  async function confirm() {
    setSaving(true)
    try { const blob = await drawOutput(); await onConfirm(new File([blob], 'avatar.png', { type: 'image/png' })) }
    finally { setSaving(false) }
  }

  return <div className="modal-backdrop" role="presentation">
    <section className="dialog avatar-crop-dialog" role="dialog" aria-modal="true" aria-labelledby="avatar-crop-title">
      <header><div><p className="eyebrow">个人头像</p><h2 id="avatar-crop-title">选择头像区域</h2></div><button className="icon-button" onClick={onCancel} aria-label="关闭">×</button></header>
      <p className="avatar-crop-help">拖动图片选择圆圈内的部分，使用滑块放大或缩小。</p>
      <div className="avatar-crop-stage">
        {imageUrl && <img ref={imageRef} src={imageUrl} alt="" onLoad={() => { setOffset({ x: 0, y: 0 }); setZoom(1); setImageRevision((value) => value + 1) }} />}
        <canvas ref={canvasRef} width={PREVIEW_SIZE} height={PREVIEW_SIZE}
          onPointerDown={(event) => { const image = imageRef.current; if (!image) return; dragRef.current = { x: event.clientX, y: event.clientY, startX: offset.x, startY: offset.y }; event.currentTarget.setPointerCapture(event.pointerId) }}
          onPointerMove={(event) => { const image = imageRef.current; const drag = dragRef.current; if (!image || !drag) return; setOffset(clampOffset(image, { x: drag.startX + event.clientX - drag.x, y: drag.startY + event.clientY - drag.y })) }}
          onPointerUp={() => { dragRef.current = null }} onPointerCancel={() => { dragRef.current = null }} />
      </div>
      <label className="avatar-zoom"><span>缩放</span><input type="range" min="1" max="3" step="0.05" value={zoom} onChange={(event) => { const image = imageRef.current; const next = Number(event.target.value); setZoom(next); if (image) setOffset((current) => clampOffset(image, current, next)) }} /><output>{zoom.toFixed(2)}×</output></label>
      <div className="modal-actions"><button className="button ghost" onClick={onCancel}>取消</button><button className="button primary" disabled={saving || !imageUrl} onClick={() => void confirm()}>{saving ? '正在保存…' : '使用此头像'}</button></div>
    </section>
  </div>
}
