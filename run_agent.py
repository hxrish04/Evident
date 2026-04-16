"""
Command-line runner for testing Evident directly.
"""

import argparse
import json
import os
import sys

from dotenv import load_dotenv


sys.path.insert(0, os.path.dirname(__file__))
load_dotenv()

from agent.pipeline import AgentPipeline  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Evident CLI")
    parser.add_argument("--url", required=True, help="Target URL to scrape")
    parser.add_argument("--interest", required=True, help="Research/interest area")
    parser.add_argument("--goal", default="", help="Optional: more detail about the goal")
    parser.add_argument("--profile", default="", help="Student profile text used for personalization")
    parser.add_argument("--top", type=int, default=5, help="Number of emails to generate")
    args = parser.parse_args()

    user_goal = args.interest
    if args.goal:
        user_goal += f"\n\nAdditional context: {args.goal}"

    pipeline = AgentPipeline(
        user_goal=user_goal,
        student_profile=args.profile,
        top_n_emails=args.top,
    )
    result = pipeline.run(url=args.url)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
