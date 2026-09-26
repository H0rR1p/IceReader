# 冰读 · baka都能用的日语学习阅读器

本机运行的 Satori Reader 式日语精读 Web 应用。导入无 DRM EPUB、UTF-8 TXT 或粘贴日文后，应用会生成：

- 词级分割、词典形、读音与振假名
- 按需切分当前章节，阅读时按需生成单句句意
- 当前章或全书的后台切分与后台逐句翻译
- 自动使用书内第一张图片作为封面，也可上传本地图片替换
- 可导入的本地日中词典与个人修正词库
- 词典优先、AI 补缺的逐句词语释义
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
- 释义按“个人词库 → 导入的 Yomitan 日中词典 → AI 补缺”顺序取得。AI 补充义写入个人词库并标明来源；用户修正始终优先。

## 阅读流程

1. 导入书籍时只解析章节、版式和图片，不调用 AI，因此不需要等待全书切分。
2. 打开需要阅读的章节并点击“切分本章”；系统只为当前章生成候选句界和词元，再由 AI 审校句界。已切分章节直接复用。
3. 选择一句话，在右侧点击“释义本句”。系统只处理当前句，先匹配词典，再让 AI 补充未收录词并生成句意和必要注释。
4. 已生成的章节切分、词义和单句结果保存在本地，再次查看时直接复用。
5. 如需预先处理，可在左侧启动全书后台任务，或在章节标题下启动本章后台任务；页面会显示进度，处理期间仍可阅读和切换章节。
   任务可以随时取消；已完成结果会保留，当前请求中止后不再处理后续句子。
6. 书架中的“更换封面”支持 JPG、PNG、WebP 和 GIF，最大 10 MB；删除书籍时需要在对话框中再次确认。

## Windows 单文件启动

首次构建启动程序：

```powershell
.\.venv64\Scripts\python -m pip install -r backend\requirements-build.txt
.\scripts\build_windows.ps1
```

生成项目根目录下的 `冰读.exe`。双击后会启动同源的本地 API 和生产页面，并自动打开 `http://127.0.0.1:5173`。EXE 使用页面左上角角色头像作为图标，书库、词典、封面和 API 设置继续读取 EXE 同目录的 `data` 文件夹。

## 检查

```powershell
npm run build
.\.venv64\Scripts\python -m pytest backend
```
