WORKDIR /app
FROM python:3.11-slim
COPY requirements.txt .
RUN python -m pip install --no-cache-dir -r requirements.txt
COPY src ./src
CMD ["python", "src/app.py"]
