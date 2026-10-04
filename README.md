# 冰读 · baka都能用的日语学习阅读器

本机运行的 Satori Reader 式日语精读 Web 应用。导入无 DRM EPUB、UTF-8 TXT 或粘贴日文后，应用会生成：

- 词级分割、词典形、读音与振假名
- 按需切分当前章节，阅读时按需生成单句句意
- 当前章或全书的后台切分与后台逐句翻译
- 自动使用书内第一张图片作为封面，也可上传本地图片替换
- 内置 Jitendex 简体中文日中词典、可导入词典与个人修正词库
- 词典优先、AI 补缺的逐句词语释义
- 按需生成的简洁语法句法分析，明确标出句中使用的语法结构
- 本地阅读进度和用户词义修正
- 以句子为单位的本地书签，可从左侧书签栏直接定位
- 无密码的本机/访客资料空间、个人头像、昵称与会话管理
- 候选词卡、标签与筛选视图、卡片编辑/合并/撤销和每日学习上限
- 四档间隔复习、知识盲区追踪和会随熟练度减弱的阅读辅助
- 学习时间热力图、连续学习天数和 12 周趋势
- 可跨账号载入书籍资源的迁移包，以及带版本清单与 SHA-256 校验的完整备份和安全恢复
- 自建云端账号、邮箱验证、密码恢复、OAuth/OIDC 登录与跨设备增量同步
- 片假名树、服务端分页、虚拟列表、批量修正和自定义分组

书籍、分析结果、个人词库和阅读进度会增量保存到 `data/library.sqlite3`，词卡、知识状态和学习活动保存在 `data/learning.sqlite3`。每本 EPUB 都会迁移到 `data/books/<内容哈希>/` 专用目录，其中 `source/book.epub` 是本地原书归档，`assets/` 保存提取图片，`documents/` 保存原书预览；阅读时不再依赖导入前的文件路径。导入的日中词典保存在 `data/dictionary.sqlite3`，每个账号的 AI 接口配置保存在 `data/users/<user_id>/settings.json`。这些文件均被 Git 忽略，不会提交到仓库；浏览器 IndexedDB 仅缓存书籍索引与打开过的章节。启动时不再复制整套词元和释义数据。

页面使用 Hash 路由，可直接刷新或使用浏览器前进/后退返回书架、词库、词卡、复习、个人主页和设置。除书架与阅读器外的页面按需加载，降低首次打开时的脚本解析量。

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
- 内置词典使用 [greyindex/jitendex-yomitan-zh](https://github.com/greyindex/jitendex-yomitan-zh) 的固定版本，来源目录为 [MarvNC/yomitan-dictionaries](https://github.com/MarvNC/yomitan-dictionaries)。该词典派生自 Jitendex/JMdict，许可证为 CC BY-SA 4.0；安装时校验固定 SHA-256，来源、版本、许可证和主页会写入词典数据库。完整署名随发行包保存在 `THIRD-PARTY-NOTICES.txt`。项目不打包目录中许可证不明确的商业或抓取词典。

## 云端账号与同步

本机后端仍是浏览器唯一直接访问的数据入口。绑定云账号后，本机后端使用短期访问令牌和轮换刷新令牌连接独立的冰读云端服务；令牌使用本机 Fernet 密钥加密保存。云端同步学习状态、词卡、复习日志、书签、阅读进度、个人词义和学习设置，不上传 EPUB 正文、插图、AI Key 或 YMM4 配音文件。

启动自建云端服务：

```powershell
Copy-Item cloud.env.example cloud.env
docker compose -f compose.cloud.yml --env-file cloud.env up -d --build
```

生产环境必须配置公开 HTTPS 地址、随机 `BINGDU_CLOUD_SECRET` 和 SMTP。OIDC 提供商通过 `BINGDU_CLOUD_OIDC_PROVIDERS` 配置，回调地址为 `<云端地址>/v1/auth/oidc/callback/<provider-id>`；GitHub OAuth 可使用 `BINGDU_GITHUB_CLIENT_ID` 和 `BINGDU_GITHUB_CLIENT_SECRET`。登录页支持云端账号和第三方登录，“云端与同步”页面提供绑定、立即同步、仅拉取、冲突选择、邮箱验证、密码恢复和设备会话撤销。

认证流程使用 OAuth 2.0 Authorization Code + PKCE、state、OIDC nonce、验证邮箱、15 分钟访问令牌以及刷新令牌轮换和复用检测。云端可以单独部署 `backend.cloud_app:app`，本地桌面服务无需暴露到公网。

### Online 分支的公网 Web 模式

`Online` 分支在保留独立云端账号服务的同时，可以把完整阅读器作为同源 Web 应用运行。公网前端使用专用 Vite 模式构建：

```powershell
npm run build -- --mode online
$env:BINGDU_PUBLIC_ORIGIN = "https://reader.example.com"
$env:BINGDU_DATA_DIR = "C:\IceReader\web-data"
$env:BINGDU_PORT = "8000"
$env:BINGDU_OPEN_BROWSER = "0"
.\build\web-release\IceReaderWeb\IceReaderWeb.exe
```

公网模式会把未登录访问者标为“访客模式”，并为每个新浏览器创建隔离的数据空间；本地默认构建仍显示“本机模式”。反向代理应把 `/health`、`/v1/*`、`/verify-email` 和 `/password-reset` 交给云端账号服务，其余请求交给完整 Web 服务。

## 阅读流程

1. 导入书籍时只解析章节、版式和图片，不调用 AI，因此不需要等待全书切分。
2. 打开需要阅读的章节并点击“切分本章”；系统在本地完成日语句界和词元处理，仅把同一段落内无法确定的换行边界交给 AI。已切分章节直接复用。
3. 选择一句话，在右侧点击“释义本句”。系统只把本地词典未收录的词交给 AI，并生成简短句意；“语法句法分析”按需单独生成，只发送句子文本，不重复生成翻译和词义。
4. 已生成的章节切分、词义和单句结果保存在本地，再次查看时直接复用。
5. 如需预先处理，可在左侧启动全书后台任务，或在章节标题下启动本章后台任务；可选择只生成中文句意的“快速句意”，或同时补齐未登录词释义的“完整释义”。页面会显示进度，处理期间仍可阅读和切换章节。
   任务可以随时取消；已完成结果会保留，当前请求中止后不再处理后续句子。
6. 书架中的“更换封面”支持 JPG、PNG、WebP 和 GIF，最大 10 MB；删除书籍时需要在对话框中再次确认。
   书架每次启动默认隐藏实际封面，可用主页右上方的“显示封面 / 隐藏封面”按钮统一切换。
7. 当前句可以加入书签；左侧栏可在“目录”和“书签”之间切换，点击书签会打开对应章节并定位到该句。左右侧栏均可收起。

## AI 用量优化

- 后台句意按最多 12 句、约 2500 个估算输入输出 token 动态组批；结果仍按句子 ID 独立保存，单批失败不会破坏已完成数据。JSON 截断时会自动二分批次，并只重试缺失的句子。
- 翻译采用“章节按需切分 → 待翻译队列 → 多工作器翻译 → 批量增量保存”的流水线，切出第一批句子后即可开始翻译，无需等待全书预加载。
- 并发数可在 1～8 之间设置，默认 4。遇到 429 或 503 会自动降低有效并发并指数退避，连续成功后再逐步恢复。
- “快速句意”不加载词元、不查询词典，也不要求 AI 输出词义；“完整释义”才按“本地词典命中项不发送、未命中项交给 AI”的流程补齐词义。语法分析仍按句单独生成。
- 句意结果以“原句、未登录词、模型、提示版本、注释模式”建立精确缓存；AI 补充的未登录词会进入个人词库，后续直接复用。
- DeepSeek 返回的输入、缓存命中、输出 token、耗时和失败次数记录在本地 `data/ai.sqlite3`。AI 设置页可填写当前接口的每百万 token 单价并查看估算费用。
- 固定规则和输出结构位于提示前缀，动态句子放在末尾，以便兼容 DeepSeek 的前缀缓存。
- 结构化释义请求明确关闭 DeepSeek 思考模式，避免推理内容占满输出预算后留下空正文；空正文、截断或非法 JSON 会自动重试一次，第二次使用双倍输出预算，失败时显示明确原因。
- 对 OpenAI 兼容接口会自动探测 `response_format`、`thinking` 和 `reasoning_effort`。模型拒绝可选参数时会移除对应字段并缓存能力结果，继续依靠提示词和严格 JSON 解析处理，不要求用户自行排查 400 错误。
- 可运行 `.\.venv64\Scripts\python.exe scripts\compare_segmentation.py`，把当前项目中最长章节的旧版已保存切分与优化后切分逐项比较。

句界策略参考 [Bunkai 日语文境界判定器](https://github.com/megagonlabs/bunkai) 以及 Hayashibe、Mitsuzawa 的 [Sentence Boundary Detection on Line Breaks in Japanese](https://aclanthology.org/2020.wnut-1.10/)。标点句界由本地确定性规则处理；只有同一 EPUB 文本块内、没有终止标点的单换行属于低置信度边界并交给 AI。句界审校每批最多 20 个边界，并设有 45 秒超时和最多 256 输出 token 的硬上限。

本地翻译已评估 CTranslate2 与 Argos Translate。它们能消除 API token，并支持 CPU 量化推理，但会明显增加安装包和模型体积，日中小说句意质量也需要另建测试集。因此当前版本保留“本地词典 + AI 未登录词和句意”的方案，没有把本地机器翻译引擎放入 P0。

## 长章节加载

- 打开章节时先读取正文和句子索引，不传输整章词元、注释和词义；首屏词元按 120 句读取，滚动接近末尾后再加载下一批。
- 服务端为章节、句子和词元关系建立 SQLite 复合表达式索引；浏览器只把当前窗口写入 IndexedDB，已经缓存的窗口不会再次下载。
- 后台翻译直接从服务端分页读取待处理句子，不先下载全书数据；快速句意模式不传输词元。每批不会跨章节，取消任务时也会中止尚未完成的读取。
- 可运行 `.\.venv64\Scripts\python.exe scripts\benchmark_translation_pipeline.py`，用本机最长已切分章节与历史 API 延迟离线比较串行、并发 4 和并发 6；脚本不调用 API，也不改动书籍。
- Sudachi core 字典在第一次分词或生成平假名读音时才加载；只打开书架和章节索引不会提前占用词典内存。

前端按书架、阅读器、学习数据和设置对话框拆分在 `src/features/` 下；`App.tsx` 只保留应用级状态、持久化协调和后台任务编排。

## Windows 目录版启动

首次构建启动程序：

```powershell
.\.venv64\Scripts\python -m pip install -r backend\requirements-build.txt
.\scripts\build_windows.ps1
```

生成 `build/release/冰读/` 目录，并将项目根目录的 `冰读.exe` 更新为轻量启动器。以后仍然双击根目录的 `冰读.exe`；它会启动目录版。目录版避免每次启动都解压约百兆资源；首次构建会复制当前项目的 `data`，后续重新构建会保留该目录已有的本地数据。

程序优先使用 `5173`。如果端口已被其他程序占用，会验证占用者是否为冰读；不是冰读时自动选择后续空闲端口，不会一闪而退。

## YMM4 单句配音

1. 在冰读的“配音设置”中确认 `YukkuriMovieMaker.exe` 路径，并导入一份包含目标角色的 `.ymmp` 项目。冰读会读取模板中的角色列表，可直接用下拉框切换角色。
2. 选择阅读器中的句子，点击“配音本句”；右侧词典卡也可播放单词读音。冰读会把自带的本机配音桥复制到该 YMM4 的 `user/plugin/BingduYmmBridge/`，启动模板项目，并在 YMM4 内部调用已获许可的语音引擎。
3. 语速可在配音设置中调整，默认是 `0.85×`；音高固定为 `1.00×`。语速调整由 YMM4 自带的 SoundTouch 组件完成，不会把音高一起改变。
4. 第一次安装或更新配音桥时，如果 YMM4 已在运行，请先保存项目并完全退出 YMM4，再点击一次配音按钮。之后相同文本、模板、语速和音量直接使用本地 WAV 缓存；点击“重新生成”会绕过缓存并生成新的音频。

YMM4 官方命令行只能导出项目中已有的语音缓存，不能为外部改写的台词生成新缓存，因此当前实现不再依赖“PNG + WAV 序列导出”。配音桥只监听 `127.0.0.1`，每次启动生成随机令牌；它不读取、复制或导出许可证。音频通过 YMM4 预览播放并录制，所以生成时请保持系统输出设备可用。

配音任务可取消；切换句子或离开章节时会停止当前等待。静音或短于 0.2 秒的文件会判定为失败，不写入缓存。缓存和模板保存在本地 `data/voice/`，不会上传。

## 检查

```powershell
npm run build
.\.venv64\Scripts\python -m pytest backend
```

书籍资源迁移包与完整备份位于“设置 → 数据备份与恢复”。迁移包只合并书籍、章节、译文、封面和插图，重复书籍会跳过，不会覆盖目标资料空间的学习记录和设置。完整备份按当前用户隔离并包含 API Key；恢复前会自动在 `data/backups/` 留存当前数据副本。


## 第三方与内容许可

项目代码采用 **AGPL-3.0-or-later**。修改和分发时请保留版权与许可声明，并提供相应源码；将修改版部署为网络服务时，也需要向使用者提供实际运行版本的对应源码。EbookLib 保留，不改变已有 EPUB 解析实现。

| 范围 | 授权与发布材料 |
| --- | --- |
| 冰读代码、脚本与文档 | [LICENSE](LICENSE)、[COPYRIGHT](COPYRIGHT) |
| 第三方依赖 | [组件清单](third_party_licenses/inventory.json)、[第三方声明](THIRD-PARTY-NOTICES.txt) 及清单中的原始许可文件 |
| 发行版对应源码 | 应用内「下载对应源码」；公网提供 `/api/legal/source`，版本及校验值见 `/api/legal` |
| 重建发行版 | [源码构建说明](SOURCE-BUILD.txt)；源码包包含文件哈希、依赖源码和版本锁定信息 |
| 配音桥 | [桥接代码附加许可](ymm4-bridge/COPYRIGHT)；外部 YMM4、SDK 和语音包遵循各自条款 |

**授权范围尚待补充**：logo、图标及点击音频的来源和公开分发授权尚未独立核实，不能将代码的 AGPL 许可理解为这些素材的授权。第三方清单还如实标注了少数仅发布许可证声明的构建／测试依赖。以下材料覆盖当前发行版本，不表示旧安装包已自动补齐，也不表示用户书籍或外部语音软件获得了重新授权。

- 冰读自身代码、构建脚本和文档采用 **AGPL-3.0-or-later**，详见 [LICENSE](LICENSE) 与 [COPYRIGHT](COPYRIGHT)。程序按现状提供，不提供任何担保。
- 保留 EbookLib 0.19 作为 EPUB 解析器，遵守其 AGPL 许可。完整的组件版本、许可证原文和版权信息见 [第三方清单](third_party_licenses/inventory.json) 和 [THIRD-PARTY-NOTICES.txt](THIRD-PARTY-NOTICES.txt)。
- 登录页、导航和设置页的「下载对应源码」提供该发行版本的完整源码包，包含当前应用源码、构建脚本、依赖锁定版本及 Python/JavaScript 运行依赖的原始源码包。
- 部署修改版本时，也须向网络用户提供**实际运行版本**的对应源码；请保留 `/api/legal/source` 及界面入口，不要用不断变化的 `main` 分支链接替代。
- logo、图标、点击音频和商标不包含在代码的 AGPL 授权中；其素材授权范围尚未独立核实。
- 书籍、词典和语音包的使用与分享遵循各自版权和许可；仅导入或分享你有权处理的内容。
- 客户端使用 Electron、React、Python、FastAPI、Sudachi 等组件，相关说明见 [THIRD-PARTY-NOTICES.txt](THIRD-PARTY-NOTICES.txt)。
- YMM4 和专有语音包由用户自行安装，不随冰读安装包分发。
- 独立配音桥的有限互操作附加许可见 [ymm4-bridge/COPYRIGHT](ymm4-bridge/COPYRIGHT)，仅覆盖冰读自己开发的桥接代码，不修改 EbookLib 或外部 SDK 的许可证。
- 桌面架构参考 [Aozora](https://github.com/meokisama/aozora)，未复制其应用源码；词卡与复习功能由冰读独立实现。

### 许可与对应源码的发布流程

```powershell
# 初次准备或升级依赖后，下载固定版本的上游源码并校验哈希。
.\.venv64\Scripts\python.exe scripts/prepare_legal.py --fetch
# 桌面构建会离线重生成源码快照，并验证安装包内的许可材料。
npm run desktop:build
```

`build/legal/corresponding-source.zip` 随客户端安装，可离线获取；ZIP 内的 `SOURCE-MANIFEST.json` 记录版本、基准提交和每个应用文件的 SHA-256。依赖归档位于 `dependency-sources/`；准确的 Python 版本位于 `IceReader/backend/requirements-release.txt`。解压后，在 `IceReader/` 内使用 Python 3.12 创建 `.venv64`，安装该 Python 锁文件，运行 `npm ci`、`scripts/prepare_legal.py --fetch` 和 `npm run desktop:build`。常规重建需联网下载安装工具及相同版本的官方 wheel；原生依赖源码编译另需各上游规定的 C/C++、Rust 工具链。YMM 桥的编译需要合法安装的 YMM4 SDK。完整步骤见 [SOURCE-BUILD.txt](SOURCE-BUILD.txt)。

云端 Docker 构建会为容器中实际安装的 Python 依赖重新生成源码包。服务首页及 `/api/legal/source` 无需登录即可下载，下载入口应由反向代理保持可访问。

Windows 公网版从 `Online` 源码快照运行 `scripts/build_online.ps1 -FetchSources`，分别生成 Web 阅读服务和云账号服务；两者随附相同的对应源码包。运行时将 `BINGDU_LEGAL_DIR` 指向服务旁的 `legal/` 目录，数据和私密环境配置使用独立目录，完整步骤见 [SOURCE-BUILD.txt](SOURCE-BUILD.txt)。

历史安装包缺少声明的问题不会因新版本发布而自动消失；继续分发旧包时，需同时提供该旧版本的许可证、对应源码和构建材料。
