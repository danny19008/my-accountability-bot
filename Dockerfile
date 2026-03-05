# Use Python 3.11
FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Copy all files into container
COPY . .

# Install dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Environment variables (optional)
ENV DB_FILE=/data/accountability.db
ENV PERSISTENCE_FILE=/data/bot_persistence

# Run your bot
CMD ["python", "main.py"]
