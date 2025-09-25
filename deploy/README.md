# Deploying ninja-payments-api on EC2

This guide documents how to containerize and run the FastAPI service on an EC2 instance while keeping secrets outside of the image. It assumes you already own the `graniteon.dev` domain and have SSH access to the instance.

## 1. Prerequisites on the EC2 host

1. Update packages and install Docker + Compose plugin:
   ```bash
   sudo apt-get update && sudo apt-get upgrade -y
   sudo apt-get install -y ca-certificates curl gnupg
   sudo install -m 0755 -d /etc/apt/keyrings
   curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
   echo \
     "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu \
     $(. /etc/os-release && echo $VERSION_CODENAME) stable" | \
     sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
   sudo apt-get update
   sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
   sudo usermod -aG docker $USER
   ```
   Log out/in once so your user can run Docker without sudo.

2. (Optional) Install `cloudflared` for the tunnel:
   ```bash
   curl -fsSL https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb -o cloudflared.deb
   sudo dpkg -i cloudflared.deb
   rm cloudflared.deb
   ```

## 2. Prepare the application source

Clone or copy this repository to the instance (e.g. `/opt/ninja-payments-api`). Ensure the working directory is the project root that contains the new `Dockerfile`.

```
/opt/ninja-payments-api/
├── Dockerfile
├── docker-compose.ec2.yml
├── deploy/
│   ├── ec2.env.example
│   └── README.md
└── app/
```

## 3. Configure environment variables

1. Copy the example file and populate it with the Render secrets (update values as needed for production):
   ```bash
   cp deploy/ec2.env.example deploy/ec2.env
   nano deploy/ec2.env
   ```
   - Set `API_BEARER_TOKEN` to the generated 64-hex value already committed in `.env` (`34374c459194a6161042971d990b63c4556c3ca319190774726830a66de41f19`) **or rotate it to a new random value** and update any clients accordingly.
   - Keep any secrets (DB, payment providers) in this file; never bake them into the image.

2. Protect the file:
   ```bash
   chmod 600 deploy/ec2.env
   ```

## 4. Build and run the container

```bash
# From the repository root
docker compose -f docker-compose.ec2.yml build
docker compose -f docker-compose.ec2.yml up -d
```

- The container listens on port `3000`. Adjust security groups / firewalls so only your tunnel or trusted IPs reach it.
- Check logs and health:
  ```bash
  docker compose -f docker-compose.ec2.yml logs -f
  curl http://localhost:3000/health
  ```

## 5. Configure the Cloudflare tunnel (optional)

Use the provided tunnel token to expose the service securely without opening ports:

```bash
# Replace <TOKEN> with the string supplied by the tunnel configuration
cloudflared tunnel --no-autoupdate run --token <TOKEN>
```

For a persistent service, install it as a systemd unit:

```bash
sudo cloudflared service install <TOKEN>
sudo systemctl enable --now cloudflared
```

Then point your `graniteon.dev` DNS to the tunnel via Cloudflare. As your tunnel already exposes
`graniteon.dev` → `http://app:3000`, you can keep that mapping so traffic lands on the FastAPI container.
If you manage origins via a `config.yml`, the ingress stanza can stay simple:

```yaml
ingress:
  - hostname: graniteon.dev
    service: http://app:3000
  - service: http_status:404
```

Adjust or extend the rules if you publish additional internal services later.

## 6. Updating the container

When you push new code:

```bash
git pull
docker compose -f docker-compose.ec2.yml build
docker compose -f docker-compose.ec2.yml up -d
```

Docker will recreate the container with zero-downtime restart thanks to `restart: unless-stopped`.

## 7. Troubleshooting tips

- `docker compose ps` / `logs` to inspect container state.
- `docker exec -it ninja-payments-api-app-1 bash` (or whatever name Compose assigns) for a shell.
- Ensure the DB security group allows connections from the EC2 instance IP.
- Check `cloudflared` logs via `sudo journalctl -u cloudflared -f` if the tunnel fails.

## 8. Security reminders

- Rotate the bearer token periodically; update any consumers to match `Authorization: Bearer <token>`.
- Restrict SSH and Docker daemon access to trusted administrators only.
- Keep the EC2 instance patched (`sudo apt-get upgrade` regularly).
