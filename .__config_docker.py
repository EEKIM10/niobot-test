# Docker configuration file that pulls in credentials for docker via environment variables
import os
import logging

TOKEN = os.environ["token"]
PASSWORD = os.environ["password"]
PREFIX = os.getenv("prefix", "?")
STORE_PATH = os.getenv("store_path", "/data" if os.path.exists("/data") else "./store")
LOG_LEVEL = getattr(logging, os.getenv("log_level", "INFO"))
OWNER_ID = os.getenv("owner_id", "@nex:nexy7574.co.uk")
DEVICE_ID = os.getenv("device_id", "nio-bot-docker")

if not TOKEN and not PASSWORD:
    raise RuntimeError("You must specify either token or password in env vars.")
