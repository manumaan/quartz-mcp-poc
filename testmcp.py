from agents import Agent, Runner, set_trace_processors,trace, gen_trace_id
from agents.mcp import MCPServer, MCPServerStdio
import asyncio
import json
import os
from openai import OpenAI


async def run(mcp_server: MCPServer):
    agent = Agent(
        name="Assistant",
        instructions="Use the tools to help user with gmail",
        mcp_servers=[mcp_server],
    )

    # Define tool arguments
    # tool_args = {
    #     "google_access_token": os.environ["GOOGLE_ACCESS_TOKEN"],
    #     "max_results": 5,
    #     "unread_only": False
    # }

    tool_args = {
        "google_access_token": os.environ["GOOGLE_ACCESS_TOKEN"],
        "google_refresh_token": os.environ["GOOGLE_REFRESH_TOKEN"],
        "google_client_id": os.environ["GOOGLE_CLIENT_ID"],
        "google_client_secret": os.environ["GOOGLE_CLIENT_SECRET"],
        "max_results": 5,
        "unread_only": False
    }

    # List the files it can read
    # message = {
    #     "command": "Get the recent emails.",
    #     "arguments": tool_args
    # }
    message = f"Get the recent emails with these parameters: {json.dumps(tool_args)}"


    # List the files it can read
    print(f"Running: {message}")
    result = await Runner.run(starting_agent=agent, input=message)
    print(result.final_output)

async def main():

    tool_args = {
        "google_access_token": os.environ["GOOGLE_ACCESS_TOKEN"],
        "google_refresh_token": os.environ["GOOGLE_REFRESH_TOKEN"],
        "google_client_id": os.environ["GOOGLE_CLIENT_ID"],
        "google_client_secret": os.environ["GOOGLE_CLIENT_SECRET"],
    }



    async with MCPServerStdio(
        name="gmail server",
        params={
            "command": "python",
            "args": ["server.py", json.dumps(tool_args)],  # Pass the arguments as a JSON string
        },
    ) as server:
        trace_id = gen_trace_id()
        with trace(workflow_name="gmail mcp server poc", trace_id=trace_id):
            print(f"View trace: https://platform.openai.com/traces/trace?trace_id={trace_id}\n")
            await run(server)


if __name__ == "__main__":
    asyncio.run(main())
