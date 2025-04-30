#from flask import Flask, render_template, request, jsonify
from quart import Quart, render_template, request, jsonify, Response
from typing import Union, Any
import re

import subprocess
import requests
import json
import os
from openai import OpenAI
from google_auth_oauthlib.flow import InstalledAppFlow
import asyncio
from agents import Agent, Runner, trace, gen_trace_id
from agents.mcp import MCPServer, MCPServerStdio

#app = Flask(__name__)
app = Quart(__name__) 

GOOGLE_CLIENT_ID = None
GOOGLE_CLIENT_SECRET = None
GOOGLE_ACCESS_TOKEN = None
GOOGLE_REFRESH_TOKEN = None
# Define the TOOLS list with tool names and descriptions
TOOLS = [
    {"name": "send_email", "description": "Send an email using Gmail"},
    {"name": "get_recent_emails", "description": "Retrieve recent emails"},
    {"name": "refresh_token", "description": "Refresh Gmail API token"}
]

@app.errorhandler(TypeError)
async def handle_type_error(error: TypeError) -> Union[Response, tuple[Response, int]]:
    """Handle TypeError exceptions globally."""
    return {
        "error": "Invalid response type",
        "message": str(error)
    }, 500

@app.before_request
def initialize():
    pass

@app.route('/')
async def index():
    return await render_template('index.html')

#get gmail tokens 
with open('credentials.json', 'r') as f:
    credentials = json.load(f)
    GOOGLE_CLIENT_ID = credentials['installed']['client_id']
    GOOGLE_CLIENT_SECRET = credentials['installed']['client_secret']


    # Use InstalledAppFlow to get the access_token and refresh_token
    flow = InstalledAppFlow.from_client_config(
        credentials,
        scopes=["https://www.googleapis.com/auth/gmail.readonly", "https://www.googleapis.com/auth/gmail.send"],
        redirect_uri="http://localhost:5000",
    )
    
    #creds = flow.run_local_server(port=0)
   
    # Add authorization prompt and more secure settings
    creds = flow.run_local_server(
        port=5000,
        prompt='consent',  # Force consent prompt
        authorization_prompt_message='Please authenticate with Google',
        success_message='Authentication successful! You can close this window.',
        open_browser=True
    )

    GOOGLE_ACCESS_TOKEN = creds.token
    GOOGLE_REFRESH_TOKEN = creds.refresh_token

#create openai client
    openai_client = OpenAI(
        api_key=os.environ.get("OPENAI_API_KEY"),
)

#create mcp server 
    tool_args = {
        "google_access_token": GOOGLE_ACCESS_TOKEN,
        "google_refresh_token": GOOGLE_REFRESH_TOKEN,
        "google_client_id": GOOGLE_CLIENT_ID,
        "google_client_secret": GOOGLE_CLIENT_SECRET,
    }

    async def start_mcp_server():
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


    asyncio.run(start_mcp_server())

def parse_email_result(result_string: str) -> list[dict]:
    """Parse the formatted email string into a list of email dictionaries."""
    # Split the string into individual email entries
    emails_raw = re.split(r'\d+\.\s+', result_string)[1:]  # Skip the header part
    emails = []
    
    for email_raw in emails_raw:
        # Initialize an empty dictionary to store the fields
        email_fields = {
            'Subject': None,
            'From': None,
            'Date': None,
            'Snippet': None
        }
        
        # Split the email into lines
        lines = email_raw.split('\n')
        
        # Process each line to find fields
        for line in lines:
            for field in email_fields.keys():
                marker = f'**{field}:**'
                if marker in line:
                    email_fields[field] = line.split(marker, 1)[1].strip()
                    break
        
        # Map the fields to the expected output format
        email = {
            'subject': email_fields['Subject'] or 'No Subject',
            'sender': email_fields['From'] or 'Unknown Sender',
            'timestamp': email_fields['Date'] or '',
            'body': email_fields['Snippet'] or 'No content available'
        }
        emails.append(email)
    
    return emails


@app.route('/process_prompt', methods=['POST'])
async def process_prompt():
    data = await request.form
    user_prompt = data.get('prompt')
    if not user_prompt:
        return jsonify({"error": "No prompt provided"}), 400

    print(user_prompt)

    tool_descriptions = "\n".join(
        [f"{tool['name']}: {tool['description']}" for tool in TOOLS]
    )

    response = openai_client.responses.create(
        model="gpt-4o",
        instructions=(
            "You are an intelligent assistant that helps with Gmail operations. First, analyze the user query "
            "and determine which tool from the provided list should be used. "
            "\n\nIf the appropriate tool is 'send_email', you MUST: "
            "1. Extract the recipient email address "
            "2. Extract or identify the email subject "
            "3. Extract or identify the email body "
            "4. Return ONLY in this exact format: 'email:body:subject' "
            "\n\nFor all other tools, return ONLY the tool name. "
            "\n\nExample: "
            "If user says 'send an email to john@example.com about meeting with subject team sync', "
            "you should return: send_email:john@example.com:about meeting:team sync. "
            "if no tool is appropriate for the user query, return 'no_tool'. "
        ),
        input=f"Here are the available tools:\n{tool_descriptions}\n\nUser's query: \"{user_prompt}\"\n\nRespond with the name of the tool that best matches the query"
    )
    print(response.output_text)

    output_parts = response.output_text.strip().split(':')
    tool_name = output_parts[0]

    if tool_name not in [tool['name'] for tool in TOOLS]:
        return jsonify({"error": "No suitable tool found for the prompt."}), 400

    async def run_with_mcp():
        async with MCPServerStdio(
            name="gmail server",
            params={
                "command": "python",
                "args": ["server.py", json.dumps(tool_args)],
            },
        ) as mcp_server:
            agent = Agent(
                name="Assistant",
                instructions="Use the tools to help user with gmail",
                mcp_servers=[mcp_server],
            )
            result = await Runner.run(starting_agent=agent, input=message)
            return result.final_output

    if tool_name == 'send_email':
        tool_args = {
            "google_access_token": GOOGLE_ACCESS_TOKEN,
            "google_refresh_token": GOOGLE_REFRESH_TOKEN,
            "google_client_id": GOOGLE_CLIENT_ID,
            "google_client_secret": GOOGLE_CLIENT_SECRET,
            "to": output_parts[1],
            "subject": output_parts[3],
            "body": output_parts[2],
            "html_body": f"<p>{output_parts[2]}</p><br><br> This email has been sent by MCP Server."
        }
        message = f"Send the emails with these parameters: {json.dumps(tool_args)}"
    elif tool_name == 'get_recent_emails':
        tool_args = {
            "google_access_token": GOOGLE_ACCESS_TOKEN,
            "google_refresh_token": GOOGLE_REFRESH_TOKEN,
            "google_client_id": GOOGLE_CLIENT_ID,
            "google_client_secret": GOOGLE_CLIENT_SECRET,
            "max_results": 5,
            "unread_only": False
        }
        message = f"Get the recent emails with these parameters: {json.dumps(tool_args)}"

    print(tool_args)
    print(f"Running with message: {json.dumps(message)}")
    
    result = await run_with_mcp()
    
    if tool_name == 'get_recent_emails':
        print(result)
        # Parse the result string into structured data
        emails_data = parse_email_result(result)
        return await render_template('emails.html', emails=emails_data)
    elif tool_name == 'send_email':
        print(result)
        # Parse the result to get email details
        try:
            result_dict = json.loads(result)
            return await render_template('email_confirmation.html', response={
                'to': tool_args['to'],
                'subject': tool_args['subject']
            })
        except json.JSONDecodeError:
            # If the result is not JSON, return a generic confirmation
            return await render_template('email_confirmation.html', response={
                'to': tool_args['to'],
                'subject': tool_args['subject']
            })
    else:
        return jsonify({"result": result})

if __name__ == '__main__':
    app.run(debug=True)