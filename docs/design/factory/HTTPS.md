# Serving a team factory over HTTPS

`codeaf factory serve` carries access tokens, GitHub sign-in sessions and the
team's work. Off this machine it serves HTTPS by default. There are three ways
to run it, chosen by `tls:` in the host config (`~/.codeaf/factory/config.yaml`).

| `tls:` | Who holds the certificate | Use it when |
| --- | --- | --- |
| `auto` | codeaf, from Let's Encrypt | the machine has a public DNS name and can be reached on 443 (and ideally 80) |
| `files` | you, as PEM files | your organisation issues certificates, or another tool renews them |
| `off` | a reverse proxy in front | you already run Caddy, nginx or a load balancer that serves HTTPS |

With no `tls:` key the mode is `files` when `cert_file` is set, `off` when
`listen` is a loopback address, and `auto` everywhere else. `auto` without a
`domain` refuses to start and says which key to set: plain HTTP off this
machine is now something a host chooses, never something it falls into.

## auto — an automatic certificate

```yaml
listen: 0.0.0.0:443
tls: auto
domain: factory.example.com
# cache_dir: tls          # default: tls beside this config file
```

- The certificate for `domain` is requested from Let's Encrypt on the first
  HTTPS connection, renewed before it expires without a restart, and kept in
  `cache_dir` (folder 0700, files 0600). No other name is ever requested.
- Validation uses TLS-ALPN-01 on the HTTPS listener (which must be port 443)
  and HTTP-01 on port 80 of the same interface when that port can be bound.
- Port 80, when bound, answers the certificate challenge and redirects every
  other request to `https://<domain>` with a 308. Binding ports below 1024
  needs root or `CAP_NET_BIND_SERVICE`
  (`sudo setcap cap_net_bind_service=+ep bin/codeaf`).
- DNS for `domain` must point at this machine before the first connection.

## files — your own certificate

```yaml
listen: 0.0.0.0:443
tls: files
cert_file: /etc/codeaf/factory.crt   # full chain, PEM
key_file: /etc/codeaf/factory.key
domain: factory.example.com          # optional: the name printed and redirected to
```

The pair is read at start (a pair that cannot be loaded refuses to start) and
again whenever either file's modification time changes, so a renewal by
another tool needs no restart. A renewal that cannot be loaded keeps the last
good certificate in service. Relative paths are read beside the config file.
On port 443 the service also opens the port-80 redirect.

## off — behind a reverse proxy

```yaml
listen: 127.0.0.1:7420
tls: off
domain: factory.example.com   # optional: the public name codeaf prints
trusted_proxies: [127.0.0.1, ::1]
```

The proxy terminates HTTPS and forwards to the factory. `trusted_proxies`
lists the addresses (or CIDR ranges such as `10.0.0.0/8`) whose
`X-Forwarded-For` and `X-Forwarded-Proto` the factory believes. From those
peers it takes the caller's address from the rightmost untrusted hop of
`X-Forwarded-For` and the scheme from `X-Forwarded-Proto`; from any other peer
it ignores both. That one reading decides the session cookie's `Secure`
flag, the HSTS header and the scheme `/v1/about` reports.

**A forwarded request is never "this machine".** A request from a loopback
peer is the owner's open floor, so a proxy on the same machine would otherwise
hand that floor to everyone. Any request carrying `X-Forwarded-For`,
`X-Forwarded-Proto`, `X-Real-IP` or `Forwarded` from a peer outside
`trusted_proxies` loses the loopback grant and must present a token or
session, and a trusted proxy's request is local only when the caller it names
is. List the proxy anyway, so cookies are marked Secure.

When `tls: off` and `listen` is not loopback, `codeaf factory serve` prints a
warning naming this option.

### Caddy

```caddyfile
factory.example.com {
	reverse_proxy 127.0.0.1:7420 {
		# The event stream (/v1/events) must not be buffered.
		flush_interval -1
	}
}
```

Caddy obtains and renews its own certificate and sets `X-Forwarded-For` and
`X-Forwarded-Proto` itself.

### nginx

```nginx
server {
    listen 443 ssl;
    http2 on;
    server_name factory.example.com;
    ssl_certificate     /etc/ssl/factory.example.com/fullchain.pem;
    ssl_certificate_key /etc/ssl/factory.example.com/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:7420;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        # The event stream: no buffering, and long reads.
        proxy_buffering off;
        proxy_read_timeout 1h;
    }
}

server {
    listen 80;
    server_name factory.example.com;
    return 308 https://$host$request_uri;
}
```

The factory also sends `X-Accel-Buffering: no` on `/v1/events`, which nginx
honours even where `proxy_buffering` is left on.

## What TLS turns on

- `Strict-Transport-Security: max-age=31536000` on every response that reached
  the factory over HTTPS (directly or through a trusted proxy). Never on plain
  HTTP, where it means nothing.
- The session cookie is always `HttpOnly` and `SameSite=Strict`, and `Secure`
  whenever the caller's connection was HTTPS.
- The redirect after `/access` sign-in is relative (`/`), so it stays on the
  proxy's public name.
- `/v1/about` reports `scheme` (`https` or `http`) for the caller's connection,
  and `factory.address` is the public URL.
- GitHub device sign-in has no callback URL: the factory talks to GitHub
  outbound, so it works unchanged over HTTPS and through a proxy. Access links
  (`/access?token=…`) carry no scheme of their own.

## This machine's own clients

The certificate names the domain, not `127.0.0.1`, so when the factory
terminates TLS it also opens a plain-HTTP listener on a random loopback port
and names that one in `serve.json`. A window on the same machine,
`codeaf factory revoke-sessions` and an attached local worker keep dialling
loopback exactly as before. A loopback socket never crosses a network.

## Not here

There is no access log in the factory service today. When one is added it
reads the caller's address from `serve.ClientAddr`, which applies the same
proxy trust as everything above.
