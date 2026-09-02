Git仓库地址：https://github.com/kxdf/coding-agent-nju

一、如何运行
Python 3.9或更高版本，无第三方依赖。

在PowerShell中设置环境变量：
  $env:OPENAI_API_KEY="你的模型API Key"
  $env:OPENAI_BASE_URL="https://api.deepseek.com"
  $env:MODEL_NAME="deepseek-chat"

单Agent模式：
  python -m coding_agent_nju "创建一个Python函数并编写测试"

三Agent协同模式：
  python -m coding_agent_nju --multi-agent --yes "创建一个Python函数并编写测试"

运行项目测试：
  python -m unittest discover -s tests -v

二、特色功能
1. 不使用Agent框架或SDK；自行实现对话历史、工具定义、tool calls解析、执行循环、终止和错误处理。
2. 本地提供目录、文件读写与替换、命令执行和显式结束工具。模型提出结构化调用，ToolBox负责真实执行。
3. 文件限制在独立工作区；写入、替换和命令默认需要确认，并拦截删除、关机、目录跳转、git push等操作。
4. 工具调用写入JSON Lines日志，记录角色、参数摘要、批准和拦截状态；内容截断、凭据脱敏，日志与工作区不入库。
5. 支持单Agent和Planner、Executor、Reviewer三Agent模式。三者共用模型，但提示词、工具权限和历史独立，仅传递结构化结果。
6. Planner只读规划；Executor编写代码并测试；Reviewer不能修改文件，必须读取文件并独立复测。失败时最多返工一次，避免无限循环和费用失控。

三、其它说明
项目最初实现单Agent闭环，随后增加三Agent协同。职责分离减少同时规划、实现和自我验证产生的偏差；独立Reviewer提供第二次证据检查，结构化交接可控制上下文并提高可解释性。--yes只跳过人工确认，不关闭安全策略。
