import argparse
import sys

from .agent import CodingAgent
from .config import load_config
from .llm import ChatClient
from .multi_agent import MultiAgentCoordinator
from .tools import ToolBox


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a small coding agent.")
    parser.add_argument("--yes", action="store_true", help="Automatically approve write and command tools.")
    parser.add_argument("--no-confirm", action="store_true", help="Alias for --yes.")
    parser.add_argument("--multi-agent", action="store_true", help="Use Planner, Executor and Reviewer agents.")
    parser.add_argument("task", nargs="*", help="Programming task for the agent.")
    args = parser.parse_args()

    task = " ".join(args.task).strip()
    if not task:
        task = input("Task: ").strip()
    if not task:
        print("No task provided.", file=sys.stderr)
        raise SystemExit(2)

    try:
        config = load_config()
    except Exception as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        raise SystemExit(2)

    client = ChatClient(config.api_key, config.base_url, config.model)
    auto_approve = args.yes or args.no_confirm or config.auto_approve
    if args.multi_agent:
        agent = MultiAgentCoordinator(
            client,
            config.workspace,
            config.timeout_seconds,
            auto_approve=auto_approve,
            enable_logging=config.enable_logging,
            executor_max_steps=config.max_steps,
        )
    else:
        toolbox = ToolBox(
            config.workspace,
            config.timeout_seconds,
            auto_approve=auto_approve,
            enable_logging=config.enable_logging,
        )
        agent = CodingAgent(client, toolbox, config.max_steps)

    print(f"Workspace: {config.workspace}")
    print(f"Model: {config.model}")
    try:
        answer = agent.run(task)
    except Exception as exc:
        print(f"Agent error: {exc}", file=sys.stderr)
        raise SystemExit(1)
    print("\nFinal answer:")
    print(answer)


if __name__ == "__main__":
    main()
