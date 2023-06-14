FROM python:3.11.4-slim
WORKDIR /app
COPY requirements.txt requirements.txt
RUN apt-get update && apt-get install -y libolm-dev
RUN pip install -r requirements.txt
COPY . .
COPY .__config_docker.py config.py
CMD ["python", "main.py"]
