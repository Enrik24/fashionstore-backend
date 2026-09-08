#!/usr/bin/env bash
set -e

pip install -r requirements.txt

alembic upgrade head

python seed.py
