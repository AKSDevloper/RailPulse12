# Use the official Python 3.12 image
FROM python:3.12

# Set the working directory
WORKDIR /app

# Copy the requirements file and install dependencies
COPY railpulse_final_package/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy all your project files into the container
COPY railpulse_final_package/ /app/

# Hugging Face routes all traffic to port 7860 by default
# Note: Adjust 'backend.api.main:app' if your FastAPI app is located in a different file like 'api.index:app'
CMD ["uvicorn", "backend.api.main:app", "--host", "0.0.0.0", "--port", "7860"]
