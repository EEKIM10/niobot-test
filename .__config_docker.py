# Docker configuration file that pulls in credentials for docker via environment variables
import os

if os.getenv("token"):
    TOKEN = os.environ["token"]

if os.getenv("password"):
    PASSWORD = os.environ["password"]

PREFIX = os.getenv("prefix", "?")
STORE_PATH = os.getenv("store_path", "/data" if os.path.exists("/data") else "./store")
LOG_LEVEL = os.getenv("log_level", "INFO")
OWNER_ID = os.getenv("owner_id", "@nex:nexy7574.co.uk")
DEVICE_ID = os.getenv("device_id", "nio-bot-docker")
