#!/bin/bash
# Launch CQRLOG then the QRZ Uploader
cqrlog &
echo "Starting CQRLOG, waiting 8 seconds..."
sleep 8
python3 /home/dparker100/cqrlog-qrz/cqrlog_qrz.py &
