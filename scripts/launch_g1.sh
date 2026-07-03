#!/bin/bash
cd /home/unitree/neon-the-g1
source env.sh >/dev/null 2>&1
exec /home/unitree/neon-the-g1/.venv/bin/python /home/unitree/neon-the-g1/g1_remote.py
