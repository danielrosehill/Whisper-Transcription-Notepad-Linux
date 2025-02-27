#!/bin/bash

# Linux Cloud STT Notepad Setup Script

echo "Setting up Linux Cloud STT Notepad..."

# Check if Python is installed
if ! command -v python3 &> /dev/null; then
    echo "Python 3 is not installed. Please install Python 3 and try again."
    exit 1
fi

# Check if pip is installed
if ! command -v pip3 &> /dev/null; then
    echo "pip3 is not installed. Please install pip3 and try again."
    exit 1
fi

# Install dependencies
echo "Installing dependencies..."
pip3 install -r requirements.txt

# Check if .env file exists
if [ ! -f .env ]; then
    if [ -f .env.example ]; then
        echo "Creating .env file from .env.example..."
        cp .env.example .env
        echo "Please edit the .env file and add your Gladia API key."
    else
        echo "Creating .env file..."
        echo 'GLADIA_API_KEY="your_api_key_here"' > .env
        echo "Please edit the .env file and add your Gladia API key."
    fi
fi

# Make app.py executable
chmod +x app.py

echo "Setup complete! You can now run the application with: ./app.py"
