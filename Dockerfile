FROM python:3.12-slim
WORKDIR /app

# Quarto CLI for beautiful .qmd reports
RUN apt-get update && apt-get install -y \
    curl libcurl4 libssl3 pandoc \
    && curl -LO https://quarto.org/download/latest/quarto-linux-amd64.deb \
    && dpkg -i quarto-linux-amd64.deb \
    && rm quarto-linux-amd64.deb \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
CMD ["python", "main.py"]