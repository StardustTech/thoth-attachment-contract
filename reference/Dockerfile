# SPDX-License-Identifier: GPL-3.0-only
# Local single-node integration profile. Deployment policy is supplied externally.
ARG PYTHON_IMAGE=python:3.14-alpine@sha256:9e9fde4d32eedce0b661d9ab91e826b62dddf28e928c230ec55f1866cac66b01
FROM ${PYTHON_IMAGE}
WORKDIR /app
COPY reference/requirements.lock /app/reference/requirements.lock
RUN pip install --no-cache-dir --only-binary=:all: -r reference/requirements.lock
COPY spec /app/spec
COPY reference /app/reference
COPY LICENSES /app/LICENSES
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
EXPOSE 18120
ENTRYPOINT ["python", "reference/server.py"]
CMD ["--host", "0.0.0.0", "--port", "18120", "--data", "/data", "--policy", "/policy/policy.json"]
