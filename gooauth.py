#This demos the Google Login functionality with a Flask App.
from flask import Flask, redirect, url_for, session, jsonify
from authlib.integrations.flask_client import OAuth
import json
from datetime import timedelta


app = Flask(__name__)
app.secret_key = 'MCPTEST09812345789'  # Replace with a random secret key
app.config.update(
    SESSION_COOKIE_SECURE=False,  # Set to True in production with HTTPS
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax',
    PERMANENT_SESSION_LIFETIME=timedelta(minutes=5)
)

# Add this import at the top of the file
from datetime import timedelta

with open('webcredentials.json', 'r') as f:
    credentials = json.load(f)
    google_client_id = credentials['web']['client_id']
    google_client_secret = credentials['web']['client_secret']

google_redirect_uri = 'http://localhost:5000/login/authorized'

oauth = OAuth(app)
google = oauth.register(
    name='google',
    client_id=google_client_id,
    client_secret=google_client_secret,
    access_token_url='https://accounts.google.com/o/oauth2/token',
    authorize_url='https://accounts.google.com/o/oauth2/auth',
    api_base_url='https://www.googleapis.com/oauth2/v1/',
    client_kwargs={
        'scope': 'email',
    },
)

@app.route('/')
def index():
    try:
        if 'google_token' not in session:
            return 'Hello! Log in with your Google account: <a href="/login">Log in</a>'
            
        token = session.get('google_token')
        if not token:
            session.pop('google_token', None)
            return redirect(url_for('index'))
            
        # Use token explicitly when making the request
        resp = google.get('userinfo', token=token)
        return jsonify({'data': resp.json()})
    except Exception as e:
        print(f"Index error: {str(e)}")
        session.pop('google_token', None)
        return redirect(url_for('index'))

@app.route('/login')
def login():
    return google.authorize_redirect(google_redirect_uri)

@app.route('/login/authorized')
def authorized():
    try:
        token = google.authorize_access_token()
        if not token:
            print("Failed to get access token")
            return redirect(url_for('index'))
            
        # Store the entire token object in session
        session['google_token'] = token
        
        # Fetch user info with the token
        resp = google.get('userinfo', token=token)
        user_info = resp.json()
        print("User info:", user_info)
        
        return redirect(url_for('index'))
    except Exception as e:
        print(f"Authorization error: {str(e)}")
        return redirect(url_for('index'))

@app.route('/logout')
def logout():
    session.pop('google_token', None)
    return redirect(url_for('index'))

if __name__ == '__main__':
    app.run(debug=True)