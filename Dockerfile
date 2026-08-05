FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir .
COPY migrations ./migrations
COPY alembic.ini ./
COPY rules ./rules
COPY 飞书文档 ./飞书文档
COPY source_profiles ./source_profiles
COPY scripts ./scripts
COPY 数字学画像2.xlsx ./数字学画像2.xlsx
EXPOSE 8000
CMD ["sh", "-c", "alembic upgrade head && profile-engine"]
