# Deployment Plan: Flask App with Gunicorn

This plan outlines the steps to run the Flask application as a systemd service using Gunicorn on port 5005.

## 1. Prerequisites
- Ensure `gunicorn` is installed in the virtual environment:
  ```bash
  ./.venv/bin/pip install gunicorn
  ```

## 2. Gunicorn Execution Command
The application can be started manually with:
```bash
./.venv/bin/gunicorn --bind 0.0.0.0:5005 app:app
```

## 3. Systemd Service Configuration
Create a service file at `/etc/systemd/system/project5.service` (requires sudo):

```ini
[Unit]
Description=Gunicorn instance to serve Project 5 Flask App
After=network.target

[Service]
User=root
Group=root
WorkingDirectory=/home/tony/project5
Environment="PATH=/home/tony/project5/.venv/bin"
ExecStart=/home/tony/project5/.venv/bin/gunicorn --workers 3 --bind 0.0.0.0:5005 app:app

[Install]
WantedBy=multi-user.target
```

## 4. Service Management
Commands to enable and start the service:
```bash
sudo systemctl daemon-reload
sudo systemctl enable project5.service
sudo systemctl start project5.service
sudo systemctl status project5.service
```

## 5. Verification
Check if the app is listening on port 5005:
```bash
ss -tulpn | grep 5005
```
