#!/bin/bash
# =============================================================================
#   MSME 4.0 WAREHOUSE SECURITY MASTER CONTROLLER - RASPBERRY PI SETUP SCRIPT
# =============================================================================

echo "================================================================="
echo "   Setting up Master Controller on Fresh Raspberry Pi OS         "
echo "================================================================="

# 1. Update system packages
echo "\n[1/5] Updating package repositories..."
sudo apt update -y && sudo apt upgrade -y

# 2. Install essential build tools, camera libraries, and Python
echo "\n[2/5] Installing camera, video, and build dependencies..."
sudo apt install -y python3-pip python3-picamera2 python3-opencv \
                    libcamera-dev cmake build-essential \
                    libatlas-base-dev libopenblas-dev \
                    python3-serial python3-gpiozero python3-requests

# 3. Add user to hardware access groups
echo "\n[3/5] Granting hardware permissions to $USER (dialout, gpio, video)..."
sudo usermod -a -G dialout,gpio,video $USER

# 4. Install Python requirements
echo "\n[4/5] Installing Python libraries (face_recognition, dlib, flask)..."
pip3 install -r requirements.txt --break-system-packages 2>/dev/null || pip3 install -r requirements.txt

# 5. Create required runtime directories
echo "\n[5/5] Creating runtime directories (known_faces, unknown_faces)..."
mkdir -p known_faces unknown_faces

echo "================================================================="
echo "  Setup Complete! Place authorized photos in known_faces/        "
echo "  Then start the master controller with:                         "
echo "      python3 main.py                                            "
echo "================================================================="
