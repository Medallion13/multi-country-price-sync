# syntax=docker/dockerfile:1.7

FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Mock has a deliberately narrow dependency set: no SQLAlchemy, no Prefect.
# This keeps the image small and the boundary clean.
RUN pip install \
    "fastapi>=0.115,<1" \
    "uvicorn[standard]>=0.32,<1" \
    "pydantic>=2.10,<3"

WORKDIR /app
COPY src/mock_woocommerce ./mock_woocommerce

EXPOSE 8000

CMD ["uvicorn", "mock_woocommerce.app:app", "--host", "0.0.0.0", "--port", "8000"]
