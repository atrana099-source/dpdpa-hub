FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /srv/hub
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .
RUN useradd -r -u 10001 hub && mkdir -p /srv/hub/data && chown -R hub /srv/hub/data
USER hub
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/healthz')"
CMD ["sh", "-c", "python manage.py init && gunicorn -b 0.0.0.0:8000 -w 3 --access-logfile - 'app:create_app()'"]
