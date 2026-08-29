Git仓库地址：https://github.com/kxdf/coding-agent-nju

运行方法：
1. 安装 Python 3.9 或更高版本。
2. 设置环境变量：
   OPENAI_API_KEY=你的模型 API Key
   OPENAI_BASE_URL=https://api.deepseek.com
   MODEL_NAME=deepseek-chat
3. 在项目根目录运行：
   python -m coding_agent_nju "创建一个 Python 函数并写测试"

项目说明：
本项目实现了一个简化编程智能体。它不使用 LangChain、AutoGen、OpenAI Agents SDK 等 agent 框架，而是直接调用 OpenAI 兼容的 Chat Completions 接口。程序自行维护对话历史，定义本地工具，解析模型返回的 tool calls，并循环执行读文件、写文件、列目录、运行命令等操作，直到模型给出最终答案或达到最大轮数。

特色功能：
1. 本地工具均由项目代码实现，包括 list_files、read_file、write_file、replace_in_file、run_command。
2. 文件操作限制在工作目录内，避免模型越界读写系统文件。
3. 命令执行设置超时并返回 stdout、stderr 和退出码，便于 agent 根据错误继续修正。
4. 支持通过 AGENT_WORKSPACE 指定独立工作区，方便演示真实编程任务。

凭据说明：
API Key 只通过环境变量或未入库配置提供，不写入仓库、README 或视频。
