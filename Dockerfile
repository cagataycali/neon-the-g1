# ── Stage 0: build the dashboard frontend (React/Vite) ──────────────────
FROM node:20-slim AS dashboard-build
WORKDIR /dash
COPY docs/dashboard/frontend/package*.json ./
RUN npm install
COPY docs/dashboard/frontend/ ./
RUN npm run build

# neon — Strands agent image for Unitree G1+ (pc4)
#
# This image runs the interactive REPL (`agent.py`) with the full toolkit.
# Works on:
#   - Jetson aarch64 (real robot PC2)  — target
#   - amd64 dev machine                 — for offline testing
#
# Canonical entrypoint is the Makefile. From the host:
#   make build       # docker compose build
#   make up          # docker compose up -d (robot)
#   make logs / make exec / make down
#
# Manual build (if you don't have make):
#   docker build -t neon -f Dockerfile .
#
# Manual run (on the robot):
#   docker run -it --rm --network host \
#     --device /dev/bus/usb --device /dev/snd \
#     -v ~/.aws:/root/.aws:ro \
#     -v /home/unitree/cyclonedds_ws:/cyclonedds:ro \
#     -e CYCLONEDDS_URI=/cyclonedds/cyclonedds.xml \
#     neon

FROM python:3.12-slim

# ── System deps for: cyclonedds build, librealsense build, audio, GL for cv2 ──
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential cmake git curl ca-certificates pkg-config \
    libssl-dev libusb-1.0-0-dev python3-dev \
    libasound2-dev libportaudio2 portaudio19-dev \
    libxcb1 libgl1 libglib2.0-0 \
    ffmpeg \
 && rm -rf /var/lib/apt/lists/*

# ── CycloneDDS 0.10.2 (matches unitree_sdk2py's wire format) ─────────────
RUN git clone --branch 0.10.2 --depth 1 \
      https://github.com/eclipse-cyclonedds/cyclonedds.git /tmp/cdds \
 && cmake -S /tmp/cdds -B /tmp/cdds/build -DCMAKE_BUILD_TYPE=Release \
 && cmake --build /tmp/cdds/build --target install \
 && rm -rf /tmp/cdds
ENV CYCLONEDDS_HOME=/usr/local

# ── librealsense with Python bindings (aarch64 has NO pip wheel!) ────────
# This is the ONLY way to get pyrealsense2 on the Jetson. On amd64 the pip
# wheel would work, but we build from source unconditionally for consistency.
# Pin to v2.56.5 — newer master has zstd qsort_r implicit-decl error
# on glibc 2.36+ (bookworm). 2.56.5 builds cleanly.
# CFLAGS workaround: glibc 2.36+ moved qsort_r out of <stdlib.h> default exposure.
RUN git clone --branch v2.56.5 --depth 1 https://github.com/IntelRealSense/librealsense.git /tmp/librealsense \
 && CFLAGS="-Wno-error=implicit-function-declaration -D_GNU_SOURCE" \
    cmake -S /tmp/librealsense -B /tmp/librealsense/build \
      -DCMAKE_BUILD_TYPE=Release \
      -DBUILD_EXAMPLES=false \
      -DBUILD_GRAPHICAL_EXAMPLES=false \
      -DBUILD_PYTHON_BINDINGS=true \
      -DPYTHON_EXECUTABLE=$(which python3) \
      -DFORCE_RSUSB_BACKEND=true \
      -DCMAKE_C_FLAGS="-Wno-error=implicit-function-declaration -D_GNU_SOURCE" \
 && cmake --build /tmp/librealsense/build -j$(nproc) --target install \
 && rm -rf /tmp/librealsense

WORKDIR /app

# ── Python deps (cache-friendly: copy requirements first) ────────────────
COPY requirements.txt .
RUN pip install --no-cache-dir --timeout 120 --retries 10 -r requirements.txt

# kiss-icp: PyPI now ships working aarch64 wheels (installed via requirements.txt).
# The old git@v1.2.3 install broke on scikit-build-core>=0.8 (cmake.minimum-version
# rejected). Verify the wheel imports; only fall back to a compatible git tag if not.
RUN python -c "import kiss_icp; print('kiss-icp OK', kiss_icp.__version__ if hasattr(kiss_icp,'__version__') else '')" \
 || pip install --no-cache-dir \
      "kiss-icp @ git+https://github.com/PRBonn/kiss-icp.git@v1.3.0#subdirectory=python"

# ── unitree_sdk2py (editable — wheel is broken, must use clone) ──────────
# Cloned fresh into /opt to keep image self-contained. The host-side
# Makefile clones to ./unitree_sdk2_python/ for bare-metal dev (.venv).
RUN git clone --depth 1 https://github.com/unitreerobotics/unitree_sdk2_python.git /opt/unitree_sdk2_python \
 && pip install --no-cache-dir -e /opt/unitree_sdk2_python
ENV PYTHONPATH=/opt/unitree_sdk2_python:${PYTHONPATH}

# ── App code ─────────────────────────────────────────────────────────────
COPY . .

# ── Dashboard frontend (built in stage 0) ───────────────────────────────
COPY --from=dashboard-build /dash/dist /app/docs/dashboard/frontend/dist

# Default DDS interface (override at runtime with -e G1_IFACE=...)
ENV G1_IFACE=eth0 \
    CYCLONEDDS_URI=/cyclonedds/cyclonedds.xml \
    PYTHONUNBUFFERED=1

# Canonical entrypoint: agent.py (interactive REPL).
# Override with `docker run ... neon python -m pytest tests/` etc.
ENTRYPOINT ["python", "agent.py"]
