# 日语教学分析初始原创样例

80 条为本项目原创合成例句，不来源于用户书籍、数据库或第三方语料。
例句和初始标注采用 AGPL-3.0-or-later，与项目源码许可一致。
官方 Sudachi 和 Universal Dependencies 文档仅用于确认接口和标注边界，未复制其语料：

- https://github.com/WorksApplications/SudachiPy/blob/develop/docs/tutorial.md
- https://github.com/UniversalDependencies/UD_Japanese-GSD

标注状态为 **初始作者标注，尚未经过独立人工裁决**，不能用于宣称 95% 精确率或全文理解质量。
每条 `expected.spans` 只列需要验证的教学单位，并非完整句法树。
`required_features` 是语义特征标签，不能用同义标签的任意扩张提高命中率。
`unknown` 表示上下文仍不足以确定的语义，不要求不确定例子凭空产生唯一答案。
`translation_policy` 指向源文证据；机器译文中的性别判断不能反过来充当事实。
`rejected_matches` 覆盖数次后缀、字面身体部位、引用和跨句匹配等负例。

## 运行

```powershell
python scripts/evaluate_linguistics.py --engine atoms --output build/linguistics/current-atoms.json
python scripts/evaluate_linguistics.py --engine atoms --baseline-source build/baseline/nlp_before.py --output build/linguistics/baseline.json
python scripts/evaluate_linguistics.py --engine structure --output build/linguistics/structure.json
```

坐标为原文 Python Unicode codepoint 半开区间，报告另输出 UTF-16 坐标。
不得先 NFC/NFKC 规范化原文再复用原坐标。Ruby 注音为独立元数据。
评估脚本只读清单中的测试文件和显式指定的预测/usage JSONL，不自动查找数据库。
原词素模式输出缺失的新教学跨度，以复现辅助片段问题；这不是旧分词器的语义错误率。
新结构模式的边界/特征召回率也仅针对这些部分标注，精确率保留 `null`。

## 实际 token 口径

需要成本比较时显式传入 `--usage-jsonl`。每一请求尝试单独一行，失败请求若有 usage 同样计入。
没返回 usage 的请求保留未知，不能把其消耗按 0 计算。分组键为 phase/operation/model。
字符数、请求 `max_tokens`、离线预算不作为真实输入/输出 token。

```json
{"request_id":"unique-attempt","phase":"after","operation":"explanation","model":"provider-model","success":true,"duration_ms":500,"usage":{"prompt_tokens":120,"completion_tokens":40,"prompt_tokens_details":{"cached_tokens":20}}}
```

可选价格文件 `--pricing-json` 是以 model 为键的对象，`cache_hit`、`cache_miss`、`output` 均为 USD/百万实际 token。
价格不会自动联网获取；配置时记录其日期和服务提供者。输出费用只覆盖具有 usage 的尝试。

## 敬语与活用组合审校矩阵

`review-v1.jsonl` 是固定的原创合成矩阵：25个独立编写的动词活用表、每词32种组合、12条负例，共812条。生成器不读取解析器或语法规则，不从预测反写标注。覆盖完整活用、敬语、授受、使役与辅助动词；负例区分普通名词和实体的给予／接收。

它检查教学单位边界、词典形、特征及原文位置。组合压力测试中某些机械敬语形式不常用，实际教学应优先使用「ご覧になる」等惯用敬语。本矩阵不是自然文章语料，也没有外部专家裁决，不能用其通过率宣称全部日语语法准确率。原80条部分标注样例不变。

运行：`python -m pytest tests/test_grammar_review.py backend/test_honorific_morphology.py`。
