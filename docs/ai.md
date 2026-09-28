# AI 分类与图片生成

从侧栏进入“AI 设置与任务”→“AI 设置”。分类和生成可使用不同服务。分类服务先选择 API 协议，再填写完整的 API Base URL、模型 ID 和 API Key，然后保存。模型不预设，避免将用户绑定到特定型号或服务价格。

| 分类 API 协议 | 分类请求接口 | 官方 Base URL |
| --- | --- | --- |
| `openai-completions` | `POST /chat/completions` | `https://api.openai.com/v1` |
| `openai-responses` | `POST /responses` | `https://api.openai.com/v1` |
| `anthropic-messages` | `POST /messages` | `https://api.anthropic.com/v1` |

协议名称代表请求格式，并不限制服务商。兼容网关填写其文档指定的 Base URL 与模型；地址包含 `/v1` 等 API 前缀，但不要填写完整的 `/responses` 或 `/messages` 路径。旧版未保存协议的分类配置继续使用 `openai-completions`。协议切换只在选择官方默认地址时自动切换默认地址，保留自定义网关地址；更换默认地址时清空表单中的模型和密钥，需填写对应服务的配置。

“检查已保存服务”只调用 `GET /models`，按所选协议发送鉴权，不上传图片、不进行推理。列表只代表服务公布的当前一页模型，并不保证视觉或生成能力；可直接输入未列出的模型。服务返回 404、405、501 时会提示当前地址不提供列表，并保留手动填写能力，不能据此认定模型接口已经验证成功。鉴权和网络错误正常报错，不会以生成请求替代连接检查。

## 分类

如果不想逐张采纳，可以使用[自动整理](automatic-classification.md)：本地规则无需模型，AI 模式可在导入后自动保存分类，也可主动批量处理已有图库。下面保留的是人工审核式流程。

1. 等待图片预览完成。
2. 在详情中点击“AI 分类与建议”，或在图库勾选多张图片后点击“AI 分类”。
3. 核对界面显示的服务地址与模型，点击“开始分析”。此时只发送预览图片，不发送源文件、文件路径、项目名称或整库内容。多页 PDF/TIFF 当前只分析第一页。
4. 查看任务的分类、标签、描述和识别文字。选择标签，决定是否将描述追加到备注，然后采纳。已有手动标签与备注保留，采纳的内容可通过现有搜索找到。

未采纳的结果保存在独立任务记录中，不影响用户整理。文件预览变化后不能采纳旧结果，需重新分析。三种协议都支持人工审核分类和自动整理，模型须支持视觉输入并能返回 JSON。Chat Completions 和 Responses 发送 JPEG data URL；Anthropic Messages 发送带媒体类型的 JPEG Base64 内容。Responses 使用 `store: false`，从响应消息的 `output_text` 内容块读取结果；Anthropic 使用 `x-api-key`、`anthropic-version: 2023-06-01` 和 4096 个输出 token 上限，从文本内容块读取结果。推理过程和工具调用结果不会作为分类正文；已标记截断或未完成的响应不能被当成完整分类保存。[OpenAI 图片输入文档](https://developers.openai.com/api/docs/guides/images-vision)、[Responses 接口](https://developers.openai.com/api/reference/typescript/resources/beta/subresources/responses/methods/create)、[Anthropic Messages 接口](https://platform.claude.com/docs/en/api/messages/create)。

保存分类服务配置（包括修改协议）会关闭自动 AI 分类，并撤销尚未提交的自动任务。核对新配置后，可在“自动整理”中重新开启。手动分类、原有结果、内容去重和滚动 24 小时额度继续沿用现有规则。

## 生成

从侧栏打开“AI 生成”，填写提示词和目标项目，可选尺寸与质量。默认不发送尺寸和质量参数，以便采用服务默认值。服务如不支持某个选项，请改回默认。每次生成一张图片，完成后自动保存到托管原文件目录，标记“AI生成”，并保留提示词、模型、服务地址、尺寸、质量、时间和服务返回的修订提示词。

图片生成固定使用 OpenAI Images 兼容的 `POST /images/generations`，独立配置服务和密钥，不随分类协议切换；当前生成入口不使用 Anthropic Messages。支持 `data[0].b64_json` 返回的 PNG、JPEG、WebP；仅返回图片 URL 的服务暂不支持。生成结果上限为 32 MB、4000 万像素。返回结果可在图库与既有图片手动归组为不同版本；本版不支持参考图编辑。

AI 队列独立于扫描/预览队列。任务排队时可以取消；运行中不提供强制取消。失败和超时均不自动重试，因为服务商可能已处理并计费。服务重启后，排队中或运行中的 AI 任务会标记失败，需检查服务商记录后手动重新提交。修改配置后旧排队任务也不会使用新地址或新密钥自动执行。

## 密钥、备份与运行范围

密钥保存在后端数据目录的 `ai-credentials.json`，不进入 SQLite，不返回浏览器，不进入 FigTrace 的 ZIP 图库备份。在支持 POSIX 权限的系统上该文件仅当前用户可读写；Windows 请使用个人账号的数据目录。它是本地明文配置文件，并非系统钥匙串。更换服务地址会清除旧密钥，需明确填写新服务的密钥。本机无鉴权的兼容服务可留空。

远程 AI 地址要求 HTTPS，本机 `localhost`/回环地址可用 HTTP。服务连接不自动跟随重定向，不自动读取系统代理变量。远程部署 FigTrace 时，用户浏览器到 FigTrace 本身也应使用 HTTPS（见运行说明）。

图库信息备份保留项目和 AI 记录，但不包含原图片、AI 生成的原图、预览或密钥；请另行备份当前[图片存储目录](storage.md)与源文件目录。恢复旧版备份会自动建立新表并迁移项目。AI 运行期间拒绝恢复；恢复后不自动重发备份中的 AI 任务。

验证范围：自动测试使用三种协议的模拟 HTTP 响应，检查图片请求、鉴权、文本解析、模型列表、分类和生成工作流、失败处理、密钥隔离、旧配置兼容及恢复；不需要真实 API Key，也不产生外部 AI 费用。实际服务效果、可用模型和额度需用户配置后验证。Anthropic 的模型列表格式依据[官方 Models 接口](https://platform.claude.com/docs/en/api/models/list)。

接口依据：[OpenAI 图片理解文档](https://developers.openai.com/api/docs/guides/images-vision)、[OpenAI 图片生成文档](https://developers.openai.com/api/docs/guides/image-generation)。
