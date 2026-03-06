FROM python:3.11-slim

WORKDIR /app

# Install git since some agent commands might rely on git
RUN apt-get update && apt-get install -y git && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml .
COPY carapace/ ./carapace/

RUN pip install --no-cache-dir .

# Default command can be overridden, but serves as a quick test
CMD ["carapace", "--help"]
