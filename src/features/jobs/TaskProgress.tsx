export interface DurableJob {
  updated_at: number
  id: string; kind: string; status: 'queued' | 'running' | 'complete' | 'failed' | 'canceled' | 'paused'
  progress_current: number; progress_total: number; message: string; cancel_requested: boolean
  result: { chapters?: Array<{ chapter_id: string; generation_id: string; target_revision: number }>; completed?: number; total?: number; [key: string]: unknown }
  scope?: { book_id?: string; chapter_id?: string; mode?: 'local' | 'ai'; max_calls?: number; max_tokens?: number; max_estimated_tokens?: number; provider_base_url?: string; provider_model?: string }
}

export default function TaskProgress({ job, onAction, busy = false }: { job: DurableJob; onAction: (action: 'cancel' | 'resume') => void; busy?: boolean }) {
  if (job.status === 'canceled') return null
  const active = job.status === 'queued' || job.status === 'running'
  const labels = { queued: '等待处理', running: '处理中', complete: '已完成', failed: '未完成', paused: '已暂停，可继续' }
  return <section className="background-progress task-progress" aria-label="任务进度" role="status">
    <strong>{labels[job.status]}</strong><p>{job.message}</p>
    {active && <progress value={job.progress_current} max={Math.max(1, job.progress_total)} />}
    <div className="task-progress-footer">
      <small>{job.progress_current} / {job.progress_total || '待统计'} 个步骤</small>
      <div className="task-progress-actions">
        {(active || ['paused', 'failed'].includes(job.status)) && <button type="button" className="button small" disabled={busy || active && job.cancel_requested} onClick={() => onAction('cancel')}>{active && job.cancel_requested ? '正在安全停止' : '取消任务'}</button>}
        {['paused','failed'].includes(job.status) && <button type="button" className="button small" disabled={busy} onClick={() => onAction('resume')}>从断点继续</button>}
      </div>
    </div>
  </section>
}
