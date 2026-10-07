FROM python:3.11-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
    procps tmux fonts-dejavu-core libusb-1.0-0 xvfb openbox wmctrl xterm x11-utils \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /maixy
COPY pyproject.toml README.md LICENSE THIRD-PARTY.txt MANIFEST.in ./
COPY src ./src
RUN python -m pip install --no-cache-dir .
COPY tests ./tests
CMD ["python", "-W", "error::ResourceWarning", "-m", "unittest", "discover", "-s", "tests", "-v"]
