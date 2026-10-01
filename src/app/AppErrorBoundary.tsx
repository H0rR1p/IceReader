import { Component, type ErrorInfo, type ReactNode } from 'react'


export default class AppErrorBoundary extends Component<{ children: ReactNode }, { error: Error | null }> {
  state: { error: Error | null } = { error: null }

  static getDerivedStateFromError(error: Error) { return { error } }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error('IceReader render error', error, info.componentStack)
  }

  render() {
    if (!this.state.error) return this.props.children
    return <main className="fatal-error"><img src="/bingdu-logo.png" alt="" /><p className="eyebrow">页面暂时无法显示</p><h1>冰读遇到了一个界面错误</h1><p>{this.state.error.message}</p><div><button className="button" onClick={() => { this.setState({ error: null }); window.location.hash = '/library' }}>返回书架</button><button className="button primary" onClick={() => window.location.reload()}>重新加载</button></div></main>
  }
}
