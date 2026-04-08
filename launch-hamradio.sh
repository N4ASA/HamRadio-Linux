#!/bin/bash
# Launch CQRLOG then the QRZ Uploader
cqrlog &
echo "Starting CQRLOG, waiting 8 seconds..."
sleep 8
python3 "$HOME/hamradio-linux/cqrlog_qrz.py" &
