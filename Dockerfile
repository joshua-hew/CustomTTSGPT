# Change to python 3.12
# Use an official Python runtime as a parent image
FROM python:3.9-slim

# Set the working directory in the container to /server
WORKDIR /server

# Copy the current directory contents (server directory) into the container at /server
COPY server/ .

# Install any needed packages specified in requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Make port 5000 available to the world outside this container
EXPOSE 5000

# Define environment variable
ENV NAME World

# Run run.py when the container launches
CMD ["python", "run.py"]

