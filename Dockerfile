# Use the official Microsoft Playwright image (Includes all necessary Chromium dependencies)
FROM mcr.microsoft.com/playwright/python:v1.42.0-jammy

# Set the working directory
WORKDIR /app

# Copy requirement files first (for Docker layer caching)
COPY requirements.txt .

# Install Python packages
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application code
COPY . .

# Set environment variables for production cloud deployment
ENV HEADLESS="true"
ENV PORT=8000

# Expose the application port
EXPOSE $PORT

# Run the FastAPI server via Uvicorn
CMD ["python", "main.py"]