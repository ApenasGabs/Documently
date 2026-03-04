FROM python:3.12-slim

WORKDIR /app

RUN pip install --no-cache-dir \
    requests \
    tree-sitter==0.21.3 \
    tree-sitter-javascript \
    tree-sitter-typescript \
    tree-sitter-python \
    tree-sitter-java \
    tree-sitter-rust \
    tree-sitter-go

COPY . .

CMD ["python", "-u", "main.py"]