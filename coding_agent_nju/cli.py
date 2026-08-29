import argparse
import sys

from .agent import CodingAgent
from .config import load_config
from .llm import ChatClient
from .tools import ToolBox


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a small coding agent.")
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
    toolbox = ToolBox(config.workspace, config.timeout_seconds)
    agent = CodingAgent(client, toolbox, config.max_steps)

    print(f"Workspace: {config.workspace}")
    print(f"Model: {config.model}")
    answer = agent.run(task)
    print("\nFinal answer:")
    print(answer)


if __name__ == "__main__":
    main()
