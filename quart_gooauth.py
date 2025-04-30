#Working demo of OAuth flow with Google using Starlette and Authlib
from starlette.applications import Starlette
from starlette.responses import HTMLResponse, RedirectResponse, JSONResponse
from starlette.middleware.sessions import SessionMiddleware
from starlette.routing import Route
from authlib.integrations.starlette_client import OAuth
import json
from datetime import timedelta
import uvicorn
import logging

# Set up logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger('oauth_flow')

# Initialize OAuth
oauth = OAuth()
google = None

async def init_oauth(credentials_path='webcredentials.json'):
    global google
    logger.debug("init_oauth: Starting OAuth initialization")
    try:
        with open(credentials_path, 'r') as f:
            logger.debug(f"init_oauth: Reading credentials from {credentials_path}")
            credentials = json.load(f)
            client_id = credentials['web']['client_id']
            client_secret = credentials['web']['client_secret']

        google = oauth.register(
            name='google',
            client_id=client_id,
            client_secret=client_secret,
            server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
            client_kwargs={
                'scope': 'openid email profile'
            }
        )
        logger.debug("init_oauth: OAuth initialization completed successfully")
    except Exception as e:
        logger.error(f"init_oauth: Failed to initialize OAuth: {str(e)}")
        raise

async def index(request):
    logger.debug("index: Handler started")
    try:
        if 'google_token' not in request.session:
            logger.debug("index: No token in session, showing login page")
            return HTMLResponse('Hello! Log in with your Google account: <a href="/login">Log in</a>')

        token = request.session.get('google_token')
        user_info = request.session.get('user_info', {})
        
        logger.debug(f"index: Session contains token: {bool(token)}, user_info: {bool(user_info)}")
        
        if not token or not user_info:
            logger.debug("index: Missing token or user_info, clearing session")
            request.session.pop('google_token', None)
            request.session.pop('user_info', None)
            return RedirectResponse(url='/')

        # Display user information in a more formatted way
        logger.debug(f"index: Displaying info for user: {user_info.get('email')}")
        html_content = f"""
        <html>
            <head>
                <style>
                    body {{ font-family: Arial, sans-serif; margin: 20px; }}
                    .user-info {{ border: 1px solid #ddd; padding: 20px; max-width: 500px; }}
                    .logout {{ margin-top: 20px; }}
                    .logout a {{ color: red; text-decoration: none; }}
                </style>
            </head>
            <body>
                <div class="user-info">
                    <h2>Welcome!</h2>
                    <p><strong>Email:</strong> {user_info.get('email', 'N/A')}</p>
                    <p><strong>Name:</strong> {user_info.get('name', 'N/A')}</p>
                    <p><strong>Picture:</strong> <img src="{user_info.get('picture', '')}" width="50" height="50"></p>
                </div>
                <div class="logout">
                    <a href="/logout">Logout</a>
                </div>
            </body>
        </html>
        """
        return HTMLResponse(html_content)
    except Exception as e:
        logger.error(f"index: Error occurred: {str(e)}")
        request.session.pop('google_token', None)
        request.session.pop('user_info', None)
        return RedirectResponse(url='/')

async def login(request):
    logger.debug("login: Handler started")
    try:
        # Get the base URL from request headers
        host = request.headers.get('host', 'localhost:5000')
        protocol = request.headers.get('x-forwarded-proto', 'http')
        
        # Construct full redirect URI
        redirect_uri = f"{protocol}://{host}/login/authorized"
        
        logger.debug(f"login: Using host: {host}, protocol: {protocol}")
        logger.debug(f"login: Constructed redirect_uri: {redirect_uri}")
        
        # Store the redirect URI in session for verification
        request.session['redirect_uri'] = redirect_uri
        
        logger.debug("login: Starting Google authorization redirect")
        return await google.authorize_redirect(request, redirect_uri)
    except Exception as e:
        logger.error(f"login: Error occurred: {str(e)}")
        return RedirectResponse(url='/')

async def authorized(request):
    logger.debug("authorized: Handler started")
    try:
        # Get the stored redirect URI from session
        redirect_uri = request.session.get('redirect_uri')
        logger.debug(f"authorized: Retrieved redirect_uri from session: {redirect_uri}")
        
        if not redirect_uri:
            logger.warning("authorized: No redirect URI found in session")
            return RedirectResponse(url='/')

        # Ensure the current URL has the correct protocol
        current_url = str(request.url)
        logger.debug(f"authorized: Initial current_url: {current_url}")
        
        if not current_url.startswith(('http://', 'https://')):
            host = request.headers.get('host', 'localhost:5000')
            protocol = request.headers.get('x-forwarded-proto', 'http')
            path_and_query = current_url.split('?', 1)
            path = path_and_query[0].lstrip('/')
            query = f"?{path_and_query[1]}" if len(path_and_query) > 1 else ""
            current_url = f"{protocol}://{host}/{path}{query}"
            request.scope['url'] = current_url
            logger.debug(f"authorized: Fixed current_url: {current_url}")

        logger.debug("authorized: Attempting to get access token")
        token = await google.authorize_access_token(request)
        if not token:
            logger.warning("authorized: Failed to get access token")
            return RedirectResponse(url='/')
        
        logger.debug("authorized: Successfully obtained access token")
        request.session['google_token'] = token
        
        logger.debug("authorized: Fetching user info")
        # Use the userinfo_endpoint from server metadata or hardcode the endpoint
        userinfo_endpoint = "https://www.googleapis.com/oauth2/v1/userinfo"
        logger.debug(f"authorized: Using userinfo endpoint: {userinfo_endpoint}")
        
        headers = {
            'Authorization': f"Bearer {token['access_token']}"
        }
        logger.debug("authorized: Making userinfo request")
        resp = await google.get(userinfo_endpoint, token=token)
        user_info = resp.json()  # Fix: Removed 'await' from .json()
        logger.debug(f"authorized: Successfully fetched user info for: {user_info.get('email')}")
        
        # Store user info in session
        request.session['user_info'] = user_info
        
        # Clean up the redirect URI from session
        request.session.pop('redirect_uri', None)
        logger.debug("authorized: Completed successfully, redirecting to index")
        
        return RedirectResponse(url='/')
    except Exception as e:
        logger.error(f"authorized: Error occurred: {str(e)}")
        request.session.pop('redirect_uri', None)
        request.session.pop('google_token', None)
        request.session.pop('user_info', None)
        return RedirectResponse(url='/')

async def logout(request):
    logger.debug("logout: Handler started")
    try:
        request.session.pop('google_token', None)
        request.session.pop('user_info', None)
        logger.debug("logout: Session cleared successfully")
        return RedirectResponse(url='/')
    except Exception as e:
        logger.error(f"logout: Error occurred: {str(e)}")
        return RedirectResponse(url='/')

routes = [
    Route('/', endpoint=index),
    Route('/login', endpoint=login),
    Route('/login/authorized', endpoint=authorized),
    Route('/logout', endpoint=logout)
]

app = Starlette(
    routes=routes,
    on_startup=[init_oauth]
)

# Add session middleware
app.add_middleware(
    SessionMiddleware,
    secret_key='MCPTEST09812345789',  # Replace with a secure secret key
    max_age=300  # 5 minutes in seconds
)

if __name__ == '__main__':
    uvicorn.run(app, host='0.0.0.0', port=5000, log_level="debug")