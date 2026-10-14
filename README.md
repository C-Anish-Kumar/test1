# OpenStock, self-hosted

A Docker Compose stack that runs [OpenStock](https://github.com/Open-Dev-Society/OpenStock), the open-source stock tracker, on your own machine or server. It needs no hosted services except a free Finnhub API key.

| Service     | What it does                                                                        |
|-------------|-------------------------------------------------------------------------------------|
| `openstock` | The Next.js app, built from upstream source at a pinned commit (`openstock/Dockerfile`) |
| `mongodb`   | Users, watchlists, alerts (data kept in the `mongo-data` volume)                     |
| `inngest`   | Self-hosted [Inngest](https://www.inngest.com/docs/self-hosting) server that runs the welcome email, weekly news and inactive-user jobs (and price alerts in realtime mode), replacing Inngest Cloud |
| `caddy`     | Optional automatic HTTPS (`https` profile)                                           |

## Quick start

Requirements: Docker with Compose v2, `openssl`, and about 2 GB of free RAM for the build.

```bash
git clone https://github.com/C-Anish-Kumar/test1.git openstock && cd openstock
./setup.sh                      # creates .env and generates all secrets
$EDITOR .env                    # set NEXT_PUBLIC_FINNHUB_API_KEY (free: https://finnhub.io/register)
docker compose up -d --build    # the first build takes a few minutes
```

Open http://localhost:3000 and create an account.

The Inngest dashboard (job runs and cron history) is at http://localhost:8288.

## Exposing it

By default the app listens on `127.0.0.1` only.

- **LAN / behind your own reverse proxy:** set `OPENSTOCK_BIND=0.0.0.0` and set `BETTER_AUTH_URL` to the URL people will use, such as `http://192.168.1.20:3000`. Then run `docker compose up -d`.
- **Public domain with automatic HTTPS:** point DNS at the server and open ports 80 and 443. In `.env`, set `DOMAIN=stocks.example.com` and `BETTER_AUTH_URL=https://stocks.example.com`, then run:
  ```bash
  docker compose --profile https up -d
  ```

`BETTER_AUTH_URL` must match the address in the browser, or sign-in fails.

## Configuration

Everything lives in `.env`; `.env.example` documents every option. Optional extras:

- **Email** (welcome emails, password reset, news digests): `NODEMAILER_EMAIL` and `NODEMAILER_PASSWORD`, using a Gmail App Password.
- **AI summaries in emails:** `GEMINI_API_KEY`, or another provider set with `AI_PROVIDER`.
- **Realtime quotes and email price alerts:** `NEXT_PUBLIC_OPENSTOCK_DATA_MODE=realtime`. Adding more keys to `FINNHUB_API_KEYS` raises the rate limit.
- **Google / GitHub sign-in:** the `*_CLIENT_ID` and `*_CLIENT_SECRET` pairs.

Variables starting with `NEXT_PUBLIC_` are baked in at build time. After changing one, rebuild with `docker compose up -d --build`. Other variables only need `docker compose up -d`.

## Operations

```bash
docker compose logs -f openstock          # app logs
docker compose ps                         # status

# Upgrade: set OPENSTOCK_REF in .env to a newer upstream commit, then
docker compose up -d --build

# Backup / restore MongoDB
docker compose exec -T mongodb sh -c 'mongodump -u root -p "$MONGO_INITDB_ROOT_PASSWORD" --authenticationDatabase admin --archive' > backup.archive
docker compose exec -T mongodb sh -c 'mongorestore -u root -p "$MONGO_INITDB_ROOT_PASSWORD" --authenticationDatabase admin --archive' < backup.archive
```

## Notes

- `MONGO_ROOT_PASSWORD` only takes effect when the `mongo-data` volume is first created. Changing it later requires changing the password inside MongoDB too.
- OpenStock connects to MongoDB while it builds. The Dockerfile starts a temporary local `mongod` for the build, so you don't need to run the database first. Nothing from that temporary database ends up in the image.
- MongoDB is not published on any host port; only the app container can reach it.
- OpenStock is AGPL-3.0. If you run a modified version for other people, you must offer them its source.
