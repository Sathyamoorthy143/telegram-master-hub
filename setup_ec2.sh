#!/bin/bash

# --- Telegram Dashboard EC2 Setup Script ---
# This script automates the installation of dependencies and environment setup.

echo "🚀 Starting Telegram Dashboard Setup..."

# 1. Update and install system dependencies
echo "📦 Installing system dependencies..."
sudo apt-get update
sudo apt-get install -y python3-pip python3-venv gcc python3-dev build-essential libffi-dev libssl-dev

# 2. Setup Virtual Environment
echo "🐍 Setting up Python virtual environment..."
python3 -m venv venv
source venv/bin/activate

# 3. Install Python requirements
echo "📥 Installing Python dependencies..."
pip install --upgrade pip
if [ -f "requirements.txt" ]; then
    pip install -r requirements.txt
else
    echo "⚠️ requirements.txt not found! Installing default list..."
    pip install python-telegram-bot[job-queue] fastapi uvicorn requests python-dotenv telethon
fi

# 4. Check for .env file
if [ ! -f ".env" ]; then
    echo "⚠️ Warning: .env file not found. Please ensure it is present before running."
fi

echo "✅ Setup Complete!"
echo "👉 To run the dashboard, use: source venv/bin/activate && python super_dashboard.py"
echo "👉 Or use the systemd service for production."
