FROM python:3.12-slim
RUN apt-get update && apt-get upgrade -y && rm -rf /var/lib/apt/lists/* && useradd --system --uid 10001 watch && pip install --no-cache-dir pymysql==1.1.1
WORKDIR /app
COPY watch/app.py /app/app.py
USER 10001
EXPOSE 8080
HEALTHCHECK --interval=10s --timeout=3s CMD python -c "import urllib.request as u; u.urlopen('http://127.0.0.1:8080/healthz', timeout=2)"
CMD ["python", "/app/app.py"]
