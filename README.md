# 日读 · AI 日语精读器

本机运行的 Satori Reader 式日语精读 Web 应用。导入无 DRM EPUB、UTF-8 TXT 或粘贴日文后，应用会生成：

- 词级分割、词典形、读音与振假名
- 可选的简体中文参考译文
- 可导入的本地日中词典与个人修正词库
- 面向 N1 学习者的语法、语气、省略和文化注释
- 本地阅读进度、用户词义修正和上下文词卡

书籍、分析结果、个人词库、词卡和阅读进度会同步保存到本地项目的 `data/library.sqlite3`，EPUB 原图保存在 `data/books/`，导入的日中词典保存在 `data/dictionary.sqlite3`。AI 接口配置保存在 `data/settings.json`。这些文件均被 Git 忽略，不会提交到仓库；浏览器 IndexedDB 仅作为页面运行时副本。

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
- EPUB 原图会原样保存；精读模式默认隐藏插图，用户开启后按原位置显示。原书预览会清理脚本、事件属性和外部资源。
- AI 不生成词典释义。用户可导入合法取得的 Yomitan 格式日中词典 ZIP，用户修正覆盖个人显示并保留来源。

## 检查

```powershell
npm run build
.\.venv64\Scripts\python -m pytest backend
```
