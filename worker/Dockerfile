FROM alpine:3.21

RUN apk add --no-cache \
    bash \
    coreutils \
    findutils \
    nodejs \
    npm \
    py3-pip \
    python3 \
    ripgrep \
    sed \
    unzip \
    zip \
  && npm install -g @anthropic-ai/claude-code \
  && adduser -D -u 1000 worker \
  && mkdir -p /home/worker /jobs/in /jobs/out /jobs/logs /runner /job/output \
  && chown -R 1000:1000 /home/worker

COPY worker.py /usr/local/bin/worker.py
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/worker.py /usr/local/bin/entrypoint.sh
