# 日读 · AI 日语精读器

本机运行的 Satori Reader 式日语精读 Web 应用。导入无 DRM EPUB、UTF-8 TXT 或粘贴日文后，应用会生成：

- 词级分割、词典形、读音与振假名
- 固定为简体中文的逐句译文
- 当前语境词义与日中个人词典
- 面向 N1 学习者的语法、语气、省略和文化注释
- 本地阅读进度、用户词义修正和上下文词卡

书籍、分析结果、个人词库和词卡保存在当前浏览器的 IndexedDB。API Key 只保存在当前页面内存，刷新页面后清除。

## 本地启动

需要 Node.js 20+ 和 Python 3.9+。

```powershell
python -m venv .venv64
.\.venv64\Scripts\python -m pip install -r backend\requirements.txt
npm install
```

启动本地 API：

```powershell
.\.venv64\Scripts\python -m uvicorn backend.app:app --reload --host 127.0.0.1 --port 8000
```

另开终端启动页面：

```powershell
npm run dev
```

打开 `http://127.0.0.1:5173`，在“AI 设置”中输入自己的 API Key。默认接口为 `https://api.deepseek.com`，模型为 `deepseek-chat`。

## 数据与版权

- 只导入你有权处理的无 DRM 内容。
- EPUB 脚本、样式、插图和外部资源不会进入阅读视图；正文会转换为便于分词和对齐的重排版。
- AI 生成的词义会标明来源。用户修正会覆盖个人词库中的自动释义。

## 检查

```powershell
npm run build
.\.venv64\Scripts\python -m pytest backend
```
