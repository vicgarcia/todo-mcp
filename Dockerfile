FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml .
COPY todo_mcp.py .

RUN pip install --no-cache-dir .

CMD ["todo-mcp"]
