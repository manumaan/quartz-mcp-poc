from starlette.applications import Starlette
from starlette.responses import HTMLResponse, RedirectResponse, JSONResponse
from starlette.middleware.sessions import SessionMiddleware
from starlette.routing import Route
from authlib.integrations.starlette_client import OAuth
from starlette.templating import Jinja2Templates
import logging
import json
import os
from openai import OpenAI
import re
from typing import Union, Any
import uvicorn
import asyncio
from agents import Agent, Runner, trace, gen_trace_id
from agents.mcp import MCPServer, MCPServerStdio
from markdown import markdown

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger('app')

# Initialize OAuth
oauth = OAuth()
google = None

# Initialize templates
templates = Jinja2Templates(directory="templates")

async def init_oauth():
    global google
    with open('webcredentials.json', 'r') as f:
        credentials = json.load(f)
        client_id = credentials['web']['client_id']
        client_secret = credentials['web']['client_secret']

    google = oauth.register(
        name='google',
        client_id=client_id,
        client_secret=client_secret,
        server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
        client_kwargs={
            'scope': 'openid email profile https://www.googleapis.com/auth/gmail.readonly https://www.googleapis.com/auth/gmail.send https://www.googleapis.com/auth/gmail.modify'
        }
    )

TOOLS = [
    {"name": "send_email", "description": "Send an email using Gmail"},
    {"name": "get_recent_emails", "description": "Retrieve recent emails"},
    {"name": "refresh_token", "description": "Refresh Gmail API token"}
]

openai_client = OpenAI(
    api_key=os.environ.get("OPENAI_API_KEY"),
)

async def index(request):
    # Check if the user is logged in by verifying the presence of 'google_token' in the session
    if 'google_token' not in request.session or not request.session['google_token']:
        # If not logged in, render the login CTA template
        return templates.TemplateResponse("login_cta.html", {"request": request})

    # If logged in, show the text box and buttons
    user_info = request.session.get('user_info', {})
    return templates.TemplateResponse("index.html", {"request": request, "show_textbox": True, "user_info": user_info})

async def login(request):
    redirect_uri = request.url_for('authorized')
    return await google.authorize_redirect(request, redirect_uri)

async def authorized(request):
    token = await google.authorize_access_token(request)
    if not token or 'id_token' not in token:
        logger.error("Missing id_token in the token response")
        return RedirectResponse(url='/')

    request.session['google_token'] = token
    try:
        user_info = await google.parse_id_token(request, token)
    except Exception as e:
        logger.error(f"Error parsing id_token: {e}")
        return RedirectResponse(url='/')

    request.session['user_info'] = user_info

    # Create MCP server
    tool_args = {
        "google_access_token": request.session['google_token']['access_token'],
        "google_refresh_token": request.session['google_token'].get('refresh_token', ''),
        "google_client_id": os.environ.get('GOOGLE_CLIENT_ID', ''),
        "google_client_secret": os.environ.get('GOOGLE_CLIENT_SECRET', ''),
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

    return RedirectResponse(url='/')

async def logout(request):
    request.session.clear()
    response = templates.TemplateResponse("logoff_confirmation.html", {"request": request})
    response.headers['Cache-Control'] = 'no-store'
    return response

def parse_email_result(result_string: str) -> list[dict]:
    """Parse the formatted email string into a list of email dictionaries."""
    emails_raw = re.split(r'\d+\.\s+', result_string)[1:]  # Skip the header part
    emails = []
    
    for email_raw in emails_raw:
        email_fields = {
            'Subject': None,
            'From': None,
            'Date': None,
            'Snippet': None
        }
        
        lines = email_raw.split('\n')
        
        for line in lines:
            for field in email_fields.keys():
                marker = f'**{field}:**'
                if marker in line:
                    email_fields[field] = line.split(marker, 1)[1].strip()
                    break
        
        email = {
            'subject': email_fields['Subject'] or 'No Subject',
            'sender': email_fields['From'] or 'Unknown Sender',
            'timestamp': email_fields['Date'] or '',
            'body': email_fields['Snippet'] or 'No content available'
        }
        emails.append(email)
    
    return emails

async def run_with_mcp(tool_args, message):
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

async def process_prompt(request):
    # Check if 'google_token' exists in the session
    if 'google_token' not in request.session:
        # Redirect to login with a GET request
        return RedirectResponse(url='/login', status_code=303)

    form = await request.form()
    user_prompt = form.get('prompt')
    if not user_prompt:
        return JSONResponse({"error": "No prompt provided"}, status_code=400)

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

    output_parts = response.output_text.strip().split(':')
    tool_name = output_parts[0]

    if tool_name not in [tool['name'] for tool in TOOLS]:
        return JSONResponse({"error": "No suitable tool found for the prompt."}, status_code=400)

    print(tool_name)
    print(output_parts)
    
    # Handle tool execution as before
    if tool_name == 'send_email':
        tool_args = {
            "to": output_parts[1],
            "subject": output_parts[3],
            "body": output_parts[2],
            "html_body": f"<p>{output_parts[2]}</p><br><br> This email has been sent by MCP Server.",
            "google_access_token": request.session['google_token']['access_token']
        }
        message = f"Send the emails with these parameters: {json.dumps(tool_args)}"
    elif tool_name == 'get_recent_emails':
        tool_args = {
            "max_results": 5,
            "unread_only": False,
            "google_access_token": request.session['google_token']['access_token']
        }
        message = f"Get the recent emails with these parameters: {json.dumps(tool_args)}"

    print(tool_args)
    print(f"Running with message: {json.dumps(message)}")
    
    # Simulate MCP server interaction
    result = await run_with_mcp(tool_args, message)

    if tool_name == 'get_recent_emails':
        # emails_data = parse_email_result(result)
        # return JSONResponse({"emails": emails_data})
        
        # Convert the result from markdown to HTML
        html_result = markdown(result)

        # Render the emails using the emails.html template
        return templates.TemplateResponse("emails.html", {"request": request, "emails": html_result})
    elif tool_name == 'send_email':
        # Render the email confirmation using the email_confirmation.html template
        return templates.TemplateResponse("email_confirmation.html", {"request": request, "response": {
            'to': tool_args['to'],
            'subject': tool_args['subject']
        }})
    else:
        # Handle cases where no specific tool logic is implemented
        return JSONResponse({"result": result})

routes = [
    Route('/', endpoint=index),
    Route('/login', endpoint=login),
    Route('/login/authorized', endpoint=authorized),
    Route('/logout', endpoint=logout),
    Route('/process_prompt', endpoint=process_prompt, methods=['POST'])
]

app = Starlette(
    routes=routes,
    on_startup=[init_oauth]
)

app.add_middleware(
    SessionMiddleware,
    secret_key='MCPTEST09812345789',
    max_age=300
)

if __name__ == '__main__':
    uvicorn.run(app, host='0.0.0.0', port=5000, log_level="info")