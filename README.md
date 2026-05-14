# 🏰 Telegram Master Hub & Dashboard

A centralized, automated Hub-and-Spoke dashboard for managing multiple Telegram bots from a single interface.

## 🚀 Features

- **Automated Sync**: Automatically fetches all your owned bots from @BotFather using MTProto.
- **Real-time Monitoring**: Monitor bot status (Online/Offline) and last seen times.
- **Usage Tracking**: Track AI token usage and storage metrics.
- **Live Logs**: Stream live terminal logs directly to the Master Hub.

## 🛠️ Configuration

Create a `.env` file with the following:

| Variable | Description |
|----------|-------------|
| `DASHBOARD_BOT_TOKEN` | The token for your Master Hub Bot |
| `API_ID` | Your Telegram API ID (from my.telegram.org) |
| `API_HASH` | Your Telegram API Hash |
| `ADMIN_IDS` | Comma-separated Telegram IDs allowed access |
| `HUB_SECRET` | A secret token for security |

## 📦 Quick Start (Native Python)

### 🐧 Ubuntu / Linux (Primary Target)
1. **Prepare**: Upload the folder to your Ubuntu server.
2. **Permissions**: Make the scripts executable:
   ```bash
   chmod +x setup_ec2.sh run.sh
   ```
3. **Setup**: Run the setup script to install dependencies:
   ```bash
   ./setup_ec2.sh
   ```
4. **Run**: Start the dashboard:
   ```bash
   ./run.sh
   ```

### 🪟 Windows (Local Testing)
1. **Setup**: Run `pip install -r requirements.txt`
2. **Run**: Double-click `run.bat` or run `python super_dashboard.py`

## 🛡️ Production Deployment (Ubuntu Systemd)
A `telegram_dashboard.service` template is provided for 24/7 background execution.
1. Edit the paths in the file to match your server.
2. Copy to systemd: `sudo cp telegram_dashboard.service /etc/systemd/system/`
3. Start service: `sudo systemctl enable --now telegram-dashboard`

---
*Built with Python, FastAPI, and Telethon.*
