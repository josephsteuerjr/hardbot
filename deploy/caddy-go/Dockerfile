# Caddy 2.11.7 with the patched Go 1.26.10 verifier.
# The release archive vendors the same dependencies as the official Caddy binary.
FROM debian:bookworm-slim AS build
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /build
RUN curl --fail --location --output go.tar.gz https://go.dev/dl/go1.26.10.linux-amd64.tar.gz \
    && echo 'ccfe7ae822d2c2f41f4fe7c956199967a4dac22d0ee0d2b2bfdb440aab3b20c5  go.tar.gz' | sha256sum -c - \
    && tar -xzf go.tar.gz -C /usr/local && rm go.tar.gz
RUN curl --fail --location --output caddy.tar.gz https://github.com/caddyserver/caddy/releases/download/v2.11.7/caddy_2.11.7_buildable-artifact.tar.gz \
    && echo 'dc0a26e498ad03a72f5cc89044310d5a860373799ca5fc5123f1abdbde73164ec80bdb18dcb0dace52095d74b98673e83ade53ed08c85b2618b415d1af341ae7  caddy.tar.gz' | sha512sum -c - \
    && mkdir source && tar -xzf caddy.tar.gz -C source && rm caddy.tar.gz
ENV PATH=/usr/local/go/bin:$PATH GOTOOLCHAIN=local GOPROXY=off GOSUMDB=off \
    CGO_ENABLED=0 GOOS=linux GOARCH=amd64 GOAMD64=v1 GOMAXPROCS=2
WORKDIR /build/source
RUN --network=none go build -p 2 -trimpath -mod=vendor -buildvcs=false \
    -tags=nobadger,nomysql,nopgx -ldflags='-s -w' -o /build/caddy .

FROM caddy:2.11.7
COPY --from=build --chmod=0755 /build/caddy /usr/bin/caddy
RUN setcap cap_net_bind_service=+ep /usr/bin/caddy
LABEL ru.rimborso.security.go="1.26.10"
