# Use Python base image
FROM --platform=linux/amd64 python:3.12-slim

# Install required system packages including a minimal browser
RUN apt-get update && apt-get install -y \
    wget \
    gnupg \
    && wget -q -O - https://dl-ssl.google.com/linux/linux_signing_key.pub | apt-key add - \
    && echo "deb [arch=amd64] http://dl.google.com/linux/chrome/deb/ stable main" >> /etc/apt/sources.list.d/google.list \
    && apt-get update \
    && apt-get install -y google-chrome-stable \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Install the project into `/app`
WORKDIR /app
ENV  BROWSER=/usr/bin/google-chrome 

# Copy the entire project
COPY . /app

# Install the package
RUN pip install -r requirements.txt

EXPOSE 5000
# Run the server
#CMD python app.py
CMD sleep 2000