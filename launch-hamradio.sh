#!/bin/bash
# Launch CQRLOG then the QRZ Uploader

if ! pgrep -x cqrlog > /dev/null; then
    cqrlog &
    # Wait for CQRLOG's "New QSO" window, then minimize all Cqrlog windows
    (
        for i in $(seq 1 30); do
            if xdotool search --name "database:" 2>/dev/null | grep -q .; then
                sleep 2
                for wid in $(xdotool search --name "database:" 2>/dev/null); do
                    xdotool windowminimize "$wid" 2>/dev/null
                done
                break
            fi
            sleep 1
        done
    ) &
fi

if ! pgrep -f cqrlog_qrz.py > /dev/null; then
    python3 "$HOME/hamradio-linux/cqrlog_qrz.py" &
fi
