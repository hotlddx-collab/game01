FROM python:3.12-slim

WORKDIR /app

# 先装依赖，利用层缓存
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 拷贝后端源码
COPY . .

# PaaS 会注入 PORT；默认 8765。监听 0.0.0.0 对外可达。
ENV HOST=0.0.0.0
ENV PORT=8765
EXPOSE 8765

CMD ["python", "main.py"]
