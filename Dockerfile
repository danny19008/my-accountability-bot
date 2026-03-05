# Use Python 3.11 base image
FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Copy all files
COPY . .

# Install dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Set environment variables (optional)
ENV DB_FILE=/data/accountability.db
ENV PERSISTENCE_FILE=/data/bot_persistence

# Command to run your bot
CMD ["python", "main.py"]
