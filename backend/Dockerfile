FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev curl \
    && rm -rf /var/lib/apt/lists/*

# Install dependencies first (cache friendly).
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the source. `COPY . .` brings in app/, alembic/,
# alembic.ini and entrypoint.sh.
COPY . .

# Make the entrypoint executable. We don't bake an ENTRYPOINT into the
# image because the worker service overrides the command without needing
# alembic to run; backend's compose entry sets `command: ["./entrypoint.sh"]`
# explicitly.
RUN chmod +x /app/entrypoint.sh

EXPOSE 8000

# Default CMD = run the API server WITHOUT migrating (used as fallback if
# someone runs the image directly). Real deployments should invoke
# entrypoint.sh via compose so migrations happen first.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
