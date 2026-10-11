FROM python:3.11

COPY pyproject.toml README.md LICENSE ./
COPY scflows scflows

RUN pip install --upgrade pip && pip install -e .

WORKDIR /scflows
