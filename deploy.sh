#!/bin/bash

# Exit immediately if a command exits with a non-zero status
set -e

echo "🚀 Starting deployment process..."

# 1. Configure Git to remember passwords permanently (asks only once)
# This saves the credentials so you don't have to type them again next time
git config credential.helper store

# 2. Fetch the latest code from GitHub
echo "📥 Fetching latest code from GitHub..."
git pull origin main

# 3. Check and install latest requirements
echo "📦 Checking and installing Python requirements..."
cd backend

# Create virtual environment if it doesn't exist
if [ ! -d "venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv venv
fi

# Activate the virtual environment
source venv/bin/activate

# Install requirements (pip automatically checks if packages are missing or need updating)
pip install -r requirements.txt

# 4. Stop any existing running server
echo "🛑 Stopping any existing server instances..."
pkill -f "uvicorn api:app" || true

# 5. Start the server
echo "🟢 Starting the FastAPI server..."
# Run in background and save output to server.log so it keeps running when you close the terminal
nohup uvicorn api:app --host 0.0.0.0 --port 8000 > server.log 2>&1 &

echo "✅ Deployment complete! Server is running in the background."
echo "📄 You can view logs using: tail -f backend/server.log"
