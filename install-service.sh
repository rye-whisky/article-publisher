#!/bin/bash
# Article Publisher - Systemd Service Installation Script
# Run with sudo: sudo bash install-service.sh

set -e

INSTALL_DIR="/opt/article-publisher"
SERVICE_NAME="article-publisher"
VENV_DIR="$INSTALL_DIR/venv"
SERVICE_FILE="/etc/systemd/system/$SERVICE_NAME.service"

echo "=== Article Publisher Service Installation ==="

# Check if running as root
if [ "$EUID" -ne 0 ]; then
    echo "Error: Please run this script with sudo"
    exit 1
fi

# Check if installation directory exists
if [ ! -d "$INSTALL_DIR" ]; then
    echo "Error: Installation directory $INSTALL_DIR does not exist"
    echo "Please deploy the project to $INSTALL_DIR first"
    exit 1
fi

# Create dedicated user if not exists
if ! id "$SERVICE_NAME" &>/dev/null; then
    echo "Creating user $SERVICE_NAME..."
    useradd -r -s /bin/false -d "$INSTALL_DIR" $SERVICE_NAME
fi

# Create virtual environment if not exists
if [ ! -d "$VENV_DIR" ]; then
    echo "Creating virtual environment..."
    python3 -m venv "$VENV_DIR"
    "$VENV_DIR/bin/pip" install --upgrade pip
fi

# Install dependencies
echo "Installing Python dependencies..."
"$VENV_DIR/bin/pip" install -r "$INSTALL_DIR/requirements.txt" || {
    echo "Warning: requirements.txt not found, installing core dependencies..."
    "$VENV_DIR/bin/pip" install fastapi uvicorn requests pyyaml
}

# Set permissions
echo "Setting permissions..."
chown -R $SERVICE_NAME:$SERVICE_NAME "$INSTALL_DIR"
chmod -R 755 "$INSTALL_DIR"
chmod 644 "$INSTALL_DIR/config.yaml" 2>/dev/null || true
chmod 600 "$INSTALL_DIR/config.yaml" 2>/dev/null || true

# Create necessary directories
mkdir -p "$INSTALL_DIR/data" "$INSTALL_DIR/logs"
chown -R $SERVICE_NAME:$SERVICE_NAME "$INSTALL_DIR/data" "$INSTALL_DIR/logs"

# Install systemd service file
echo "Installing systemd service..."
cp "$INSTALL_DIR/article-publisher.service" "$SERVICE_FILE"
systemctl daemon-reload

echo ""
echo "=== Installation Complete ==="
echo ""
echo "Service commands:"
echo "  Enable auto-start:  sudo systemctl enable $SERVICE_NAME"
echo "  Start service:      sudo systemctl start $SERVICE_NAME"
echo "  Stop service:       sudo systemctl stop $SERVICE_NAME"
echo "  Restart service:    sudo systemctl restart $SERVICE_NAME"
echo "  View status:        sudo systemctl status $SERVICE_NAME"
echo "  View logs:          sudo journalctl -u $SERVICE_NAME -f"
echo ""
echo "To enable auto-start on boot, run:"
echo "  sudo systemctl enable $SERVICE_NAME"
echo "  sudo systemctl start $SERVICE_NAME"
