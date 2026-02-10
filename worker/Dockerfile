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
  && mkdir -p /home/worker \
  && chown -R 1000:1000 /home/worker

COPY worker.py /usr/local/bin/worker.py
RUN chmod +x /usr/local/bin/worker.py
