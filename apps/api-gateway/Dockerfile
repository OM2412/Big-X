FROM python:3.12-slim AS production
WORKDIR /app
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir --retries 10 --timeout 60 -r requirements.txt
COPY . /app
EXPOSE 8000
CMD ["python", "-m", "src.main"]
