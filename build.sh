#!/bin/bash

# Linux Cloud STT Notepad Build Script
# This script builds a standalone executable using PyInstaller

echo "=== Linux Cloud STT Notepad Build Script ==="
echo "Building application with PyInstaller..."

# Make sure we're in the project directory
cd "$(dirname "$0")"

# Ensure virtual environment is activated (if you're using one)
# Uncomment the line below if you want the script to activate your venv
# source venv/bin/activate

# Clean previous build artifacts
echo "Cleaning previous build artifacts..."
rm -rf build/ dist/ *.spec

# Build the application with PyInstaller
echo "Building application..."
pyinstaller --name="Linux-Cloud-STT-Notepad" \
            --windowed \
            --onefile \
            --add-data=".env:." \
            --add-data=".env.example:." \
            --hidden-import=sounddevice \
            --hidden-import=numpy \
            --hidden-import=scipy \
            --hidden-import=openai \
            --hidden-import=python-dotenv \
            app.py

# Check if build was successful
if [ -f "dist/Linux-Cloud-STT-Notepad" ]; then
    echo "Build successful! Executable created at: dist/Linux-Cloud-STT-Notepad"
    echo "You can run the application with: ./dist/Linux-Cloud-STT-Notepad"
else
    echo "Build failed. Check the output above for errors."
    exit 1
fi

# Create a desktop entry file
echo "Creating desktop entry file..."
cat > "dist/Linux-Cloud-STT-Notepad.desktop" << EOL
[Desktop Entry]
Type=Application
Name=Linux Cloud STT Notepad
Comment=Speech-to-text notepad using OpenAI Whisper API
Exec=/usr/local/bin/Linux-Cloud-STT-Notepad
Icon=accessories-text-editor
Terminal=false
Categories=Utility;AudioVideo;TextEditor;
EOL

echo "Desktop entry file created at: dist/Linux-Cloud-STT-Notepad.desktop"
echo "To install system-wide, run:"
echo "sudo cp dist/Linux-Cloud-STT-Notepad /usr/local/bin/"
echo "sudo cp dist/Linux-Cloud-STT-Notepad.desktop /usr/share/applications/"

echo "=== Build Complete ==="
