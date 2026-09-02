Git仓库地址：https://github.com/kxdf/coding-agent-nju

运行方法：
1. 安装 Python 3.9 或更高版本。
2. 设置环境变量：
   OPENAI_API_KEY=你的模型 API Key
   OPENAI_BASE_URL=https://api.deepseek.com
   MODEL_NAME=deepseek-chat
3. 在项目根目录运行：
   python -m coding_agent_nju "创建一个 Python 函数并写测试"
   三Agent模式：python -m coding_agent_nju --multi-agent --yes "创建一个 Python 函数并写测试"

项目说明：
本项目不使用任何 agent 框架，直接调用 OpenAI 兼容接口。程序自行维护历史、执行工具、解析 tool calls 和控制终止。可选三Agent模式由 Planner 制定计划、Executor 实现、Reviewer 独立复测；三个角色使用同一模型和独立历史，由本地协调器串联。详细原理见 DESIGN.md。

特色功能：
1. 自行实现目录、读写、替换、命令执行和任务结束工具。
2. 文件路径限制在独立工作区内，防止越界读写。
3. 写文件、替换文件和执行命令默认需要用户确认；录屏演示可加 --yes 自动批准。
4. 命令安全策略拦截删除、关机、git push、目录跳转等危险操作。
5. 工具调用写入 JSONL 审计日志，大段内容会截断，凭据会脱敏。
6. 支持通过 AGENT_WORKSPACE 指定工作区。
7. Reviewer 只有读文件和运行测试权限；审查失败时最多自动返工一次。

凭据说明：
API Key 只通过环境变量或未入库配置提供，不写入仓库、README 或视频。
