FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip -i https://mirrors.aliyun.com/pypi/simple/ --default-timeout=120 \
    && pip install --no-cache-dir -i https://mirrors.aliyun.com/pypi/simple/ --default-timeout=120 -r requirements.txt

COPY stock_flow/ stock_flow/
COPY templates/ templates/
COPY main.py web.py ./

ENV ACCESS_TOKEN=123456 SECRET_KEY=change-me-in-production

EXPOSE 5000

CMD ["python", "web.py"]
